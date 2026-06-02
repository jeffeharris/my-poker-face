"""World-tick hook for autonomous Main Events (P3.7).

When a Main Event invite is declined or expires, it starts an AI-only tournament
that must play out at WORLD pace — a round per world tick, like the cash tables —
and its lifecycle beats (final table / bubble / winner) must surface on the lobby
ticker without the player polling. This module is the seam the world ticker calls
each tick (behind `economy_flags.TOURNAMENT_CIRCUIT_ENABLED`): it locates the
owner's *autonomous* tournament, advances it one step via
`tournament_spawn.advance_autonomous_tournament` (which settles + releases the
field on completion), and turns the round's structural beats into `LobbyEvent`s
the existing ticker emit block ships as `world_event`s.

Two design points the handoff calls out:

- **Discriminating autonomous from human.** A `spawn_autonomous_tournament` row
  has no live `game_id` and is not pre-registered in memory; the robust
  discriminator is its FIELD — an autonomous field has no `human:<owner>` seat
  (the human builder always seats one). `is_autonomous` checks exactly that, so
  a player-gated human tournament is never auto-advanced here.
- **Structural-only beats.** `beats_to_world_events` keeps the field-collapse
  milestones + the bubble + the winner and drops per-hand knockouts / table
  breaks — the "never every hand" filter, so the ticker reads as a few landmark
  lines, not a knockout firehose.

Pure where it can be (`is_autonomous`, `beats_to_world_events` take plain data
and are unit-testable without Flask); the advance step takes an injected registry
+ repos. **Caller holds `get_sandbox_lock(sandbox_id)`** across the advance — the
settle mutates the escrow — and relies on it alone (no per-tournament registry
lock here, to avoid a lock-ordering inversion with the advance route). See
`docs/plans/P3_REMAINING_HANDOFF.md` §P3.7.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from tournament.identity import resolve_display_name

logger = logging.getLogger(__name__)

# Synthetic field-seat ids minted by `build_initial_state` (`P01..PNN`) for the
# legacy `/register` route. A field made entirely of these is human-playable (the
# player drives P01 via /sit) — NOT autonomous.
_SYNTHETIC_SEAT = re.compile(r'^P\d+$')


def is_autonomous(session, owner_id: str) -> bool:
    """True iff `session` is an autonomous (AI-only) tournament the world ticker
    should advance (and the play routes should reject).

    Three creation paths, two of them human-playable:
      - **accept** (`create_human_tournament`) seats `human:<owner>` → human.
      - **/register** (legacy) builds an all-synthetic `P##` field the player
        drives via /sit → human.
      - **decline/expire** (`spawn_autonomous_tournament`) fields REAL personas
        with no human seat → autonomous.

    So: a `human:<owner>` seat OR an all-synthetic field ⇒ human-playable; a field
    of real personas with no human seat ⇒ autonomous. Reads only `session.entries`
    so it's cheap and unit-testable."""
    from flask_app.services.tournament_spawn import human_seat_id

    entries = session.entries
    if human_seat_id(owner_id) in entries:
        return False  # accept path — a real human is seated
    if entries and all(_SYNTHETIC_SEAT.match(pid or '') for pid in entries):
        return False  # legacy /register — synthetic field the human drives
    return True


def beats_to_world_events(
    beats,
    *,
    winner_name: Optional[str],
    sandbox_id: str,
    complete: bool,
    winner_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> list:
    """Translate the *structural* subset of a beat burst into `LobbyEvent`s for
    the lobby ticker, oldest-first.

    Kept: field-collapse milestones (`final_table` / `heads_up` / `down_to`), the
    bubble, and — when `complete` — the winner. Dropped: per-hand knockouts and
    table breaks (the "never every hand" filter). Each event gets a strictly
    increasing `created_at` (microsecond-staggered from `now`) so multiple beats
    in one tick keep distinct de-dup keys and emit in order.
    """
    from cash_mode import activity

    base = now or datetime.utcnow()
    events: list = []

    def _stamp() -> str:
        # Strictly-increasing timestamps so same-tick beats don't collide on the
        # frontend's `created_at|type|personality_id` de-dup key.
        return (base + timedelta(microseconds=len(events))).isoformat()

    for beat in beats:
        btype = beat.get('type')
        if btype == 'milestone':
            kind = beat.get('kind', 'down_to')
            remaining = beat.get('remaining', 0)
            events.append(
                activity.LobbyEvent(
                    type=activity.EVENT_TOURNAMENT_MILESTONE,
                    table_id="",
                    stake_label="",
                    personality_id="",
                    name="",
                    reason=kind,
                    message=activity.format_tournament_milestone_message(kind, remaining),
                    created_at=_stamp(),
                    sandbox_id=sandbox_id,
                )
            )
        elif btype == 'bubble':
            paid = beat.get('paid_places', 0)
            events.append(
                activity.LobbyEvent(
                    type=activity.EVENT_TOURNAMENT_BUBBLE,
                    table_id="",
                    stake_label="",
                    personality_id="",
                    name="",
                    reason="",
                    message=activity.format_tournament_bubble_message(paid),
                    created_at=_stamp(),
                    sandbox_id=sandbox_id,
                )
            )

    if complete and winner_name is not None:
        events.append(
            activity.LobbyEvent(
                type=activity.EVENT_TOURNAMENT_WINNER,
                table_id="",
                stake_label="",
                personality_id=winner_id or "",
                name=winner_name,
                reason="",
                message=activity.format_tournament_winner_message(winner_name),
                created_at=_stamp(),
                sandbox_id=sandbox_id,
            )
        )
    return events


def advance_owner_tournament(
    *,
    owner_id: str,
    sandbox_id: str,
    registry,
    session_repo,
    bankroll_repo,
    ledger_repo,
    personality_repo=None,
    rounds_per_tick: int = 1,
    now: Optional[datetime] = None,
) -> Optional[dict]:
    """Advance the owner's active *autonomous* tournament one tick's worth of
    rounds, settling on completion, and return the structural beats to surface.

    Returns `{tournament_id, complete, settled, rounds, events}` — `events` are
    the `LobbyEvent`s the caller records into the activity buffer (the ticker's
    emit block then ships them as `world_event`s) — or None when there's nothing
    autonomous to advance: no active tournament, the active one is human-driven
    (player-gated — never advanced here), or it's already complete.

    Caller holds `get_sandbox_lock(sandbox_id)` (the settle inside mutates the
    escrow). Best-effort by contract of the ticker: the caller swallows failures.
    """
    from flask_app.services import tournament_spawn
    from tournament.beats import build_beats, level_up_beat
    from tournament.session import paid_places_for

    tid = registry.find_active_for_owner(owner_id)
    if tid is None:
        return None
    rec = registry.get(tid)
    if rec is None:
        return None
    session = rec.get('session')
    if session is None or not is_autonomous(session, owner_id):
        # No session, or human-driven (the live game bridge advances that one).
        return None
    if session.is_complete():
        # Already settled on the completing tick; nothing left to do.
        return None

    remaining_before = session.field.active_count
    level_before = session.current_level().level

    result = tournament_spawn.advance_autonomous_tournament(
        tournament_id=tid,
        session=session,
        entries=dict(session.entries),
        sandbox_id=sandbox_id,
        bankroll_repo=bankroll_repo,
        ledger_repo=ledger_repo,
        session_repo=session_repo,
        rounds_per_tick=rounds_per_tick,
    )

    # Persist the advanced (or completed) state FIRST — before deriving beats — so
    # a hiccup in beat-building can never leave a stale `session_json` (the durable
    # field would lag the in-memory one, dropping the climactic winner/final-table
    # beats on a resumed read). The settle inside `advance_*` already stamped the
    # durable row's status + payout guard; this save only rewrites
    # session_json/status (not the economy columns), so it can't wipe the payout.
    registry.persist(tid)

    reports = result['reports']
    beats = build_beats(
        reports,
        paid_places=paid_places_for(session.field.field_size),
        table_size=session.config.table_size,
        human_id=session.human_id,
        remaining_before=remaining_before,
    )
    level_after = session.current_level()
    if level_after.level > level_before and reports:
        beats.append(level_up_beat(level_after, round_index=reports[-1].round_index))

    complete = session.is_complete()
    winner_name = None
    winner_id = None
    if complete:
        wid = session.winner()
        if wid:
            # Resolve the winner's persona name through the canonical resolver —
            # `session.entries[wid]` is the bot ARCHETYPE, not a display name, so
            # rendering it left "calling_station" as the Main Event champion.
            winner_id = wid
            winner_name = resolve_display_name(
                wid,
                is_human=(wid == session.human_id),
                personality_repo=personality_repo,
            )

    events = beats_to_world_events(
        beats,
        winner_name=winner_name,
        winner_id=winner_id,
        sandbox_id=sandbox_id,
        complete=complete,
        now=now,
    )
    return {
        'tournament_id': tid,
        'complete': complete,
        'settled': result['settled'],
        'rounds': result['rounds'],
        'events': events,
    }


def reconcile_stuck_payouts(
    *,
    session_repo,
    ledger_repo,
    bankroll_repo,
    registry,
    resolve_sandbox,
    get_lock,
    older_than_iso: Optional[str] = None,
) -> list:
    """Sweep every tournament wedged at `payout_status='in_progress'` (a crash
    mid-distribute) and resume each via `tournament_economy_service`'s
    ledger-authoritative reconcile. The retry mechanism the payout's
    "leave in_progress for a reconcile pass" comment always assumed but never had.

    Injected `resolve_sandbox(owner_id) -> sandbox_id` and `get_lock(sandbox_id)
    -> contextmanager` keep it testable without Flask. Each row is reconciled
    under ITS OWN sandbox lock (the watchdog runs outside any sandbox lock, so it
    can correctly serialize against live payouts per sandbox). Best-effort per row
    — one bad tournament never aborts the sweep. Returns a list of
    `{tournament_id, owner_id, reconciled, escrow_balance}` for logging/admin.

    The `(human_owner_id, real_persona_ids)` pair is re-derived to MATCH what the
    original payout used: a human-entered Main Event (its field carries the
    `human:<owner>` seat) credits only the human for real and sweeps AI shares to
    the bank (mirroring the live route's `_maybe_payout`); an autonomous one
    credits every real persona. `older_than_iso` is a grace window so a payout
    in-flight on a request thread isn't reconciled out from under it."""
    from core.economy.ledger import tournament as tournament_account
    from flask_app.services import tournament_economy_service as econ
    from flask_app.services.tournament_spawn import human_seat_id

    results: list = []
    try:
        rows = session_repo.list_stuck_payouts(older_than_iso=older_than_iso)
    except Exception:  # noqa: BLE001
        logger.exception("reconcile sweep: failed to list stuck payouts")
        return results

    for row in rows:
        tid = row['tournament_id']
        owner = row.get('owner_id') or ''
        try:
            sandbox_id = resolve_sandbox(owner)
            rec = registry.get(tid)
            if rec is None or rec.get('session') is None:
                logger.warning("reconcile sweep: could not rehydrate %s; skipping", tid)
                continue
            session = rec['session']
            is_human = human_seat_id(owner) in session.entries
            human_owner_id = owner if is_human else None
            real_persona_ids = frozenset() if is_human else frozenset(session.entries.keys())
            with get_lock(sandbox_id):
                ok = econ.reconcile_stuck_payout(
                    tournament_id=tid,
                    session=session,
                    human_owner_id=human_owner_id,
                    sandbox_id=sandbox_id,
                    bankroll_repo=bankroll_repo,
                    ledger_repo=ledger_repo,
                    session_repo=session_repo,
                    real_persona_ids=real_persona_ids,
                )
                balance = ledger_repo.balance_of(tournament_account(tid), sandbox_id=sandbox_id)
            results.append({
                'tournament_id': tid, 'owner_id': owner,
                'reconciled': bool(ok), 'escrow_balance': balance,
            })
        except Exception:  # noqa: BLE001 — one bad row must not abort the sweep
            logger.exception("reconcile sweep: failed for tournament %s", tid)
    return results
