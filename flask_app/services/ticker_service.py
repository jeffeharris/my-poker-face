"""Realtime background world ticker for cash mode.

A single shared background task advances the unseated-table world for
every *active* sandbox (see `presence.py`) on a fixed cadence —
independent of whether the player is in the lobby or seated at a table.
This replaces the old read-driven model where the world only moved when
`GET /api/cash/lobby` was polled.

Design (see `docs/plans/CASH_MODE_REALTIME_TICKER.md`):

- **One thread, not one-per-session.** The GIL serializes the pure-Python
  sim anyway, and a single writer avoids SQLite write-lock contention.
- **Time-budgeted.** Each cycle spends at most `CYCLE_BUDGET_MS` across
  all active sandboxes, round-robining so none is starved. Under load the
  world slows gracefully instead of starving foreground request handling.
- **Cooperative yield** between sandboxes (`socketio.sleep(0)`).
- **Per-user pace** maps to the `hand_sim_prob` (and cadence) passed to
  `refresh_unseated_tables`.
- After each sandbox tick, pushes `lobby_tick` + new `world_event`s to the
  per-user lobby room so the client refreshes / shows signals without
  polling.

Seeding (`ensure_lobby_seeded`) stays a lobby-GET responsibility — the
ticker only advances tables that already exist, so a user who opened a
socket but never loaded the lobby costs nothing here.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Cadence + budget. BASE_TICK is the wall-clock spacing between cycles;
# CYCLE_BUDGET caps how long one cycle spends running sims so the ticker
# can never monopolize the single worker's core.
BASE_TICK_SECONDS = 2.0
CYCLE_BUDGET_MS = 250.0
# Max new ticker events scanned/pushed per sandbox per tick (cosmetic).
WORLD_EVENT_LIMIT = 20
# PRH-14: hard cap on how many distinct sandboxes one cycle advances. The
# CYCLE_BUDGET already bounds wall-clock per cycle, but background work +
# narration spend scale with the number of active sandboxes; this caps that
# fan-out explicitly so presence (keep-the-lobby-polled) can't drive unbounded
# concurrent ticking. Applied AFTER the round-robin rotation, so the capped
# window slides across cycles and no sandbox is permanently starved.
MAX_ACTIVE_SANDBOXES_PER_CYCLE = int(os.environ.get('WORLD_TICKER_MAX_SANDBOXES', '50'))

# pace -> (hand_sim_prob, run_every_n_cycles). `run_every` lets the
# quietest pace tick less often without a separate timer. With a 2s base
# tick, the per-table mean interval between hands is
# (run_every * BASE_TICK) / prob:
#   subtle   -> (3*2)/0.15 ≈ 40s   (ambient backdrop; world barely drifts)
#   lively   -> (1*2)/0.40 = 5s    (busy but followable; default)
#   bustling -> (1*2)/0.90 ≈ 2.2s  (Vegas-floor churn)
# Per table — the lobby's aggregate feed moves ~Ntables faster.
_PACE_PARAMS: Dict[str, Tuple[float, int]] = {
    "subtle": (0.15, 3),
    "lively": (0.40, 1),
    "bustling": (0.90, 1),
}
_DEFAULT_PACE = "lively"


def is_enabled() -> bool:
    """Whether the realtime ticker should run (default on).

    When off (`WORLD_TICKER_ENABLED=false`), `GET /api/cash/lobby` keeps
    its legacy read-driven `refresh_unseated_tables` call — the safety
    fallback so the world still moves without the ticker.
    """
    return os.environ.get("WORLD_TICKER_ENABLED", "true").lower() != "false"


_started = False
_start_lock = threading.RLock()
_stop = threading.Event()
# owner_id -> created_at of the newest world_event we've already pushed,
# so we only emit events generated since the last tick (no backlog spam).
_last_marker: Dict[str, str] = {}
_cycle = 0
_rr_offset = 0

# Net-worth snapshot cadence. The ticker records a holdings snapshot per
# active sandbox at most this often (wall-clock), driving the admin
# "Player Holdings" net-worth-over-time chart. Far slower than the base
# tick — net worth drifts on the order of minutes, and the table is just
# for admin analytics, so a fine cadence would only bloat the table.
SNAPSHOT_INTERVAL_SECONDS = 600.0
# sandbox_id -> monotonic time of its last recorded snapshot.
_last_snapshot_at: Dict[str, float] = {}

# Prestige recompute cadence. Reputation drifts even more slowly than net
# worth (it aggregates a relationship graph that barely moves tick-to-tick),
# and recomputing scans the human's inbound edges + completed sessions, so a
# fine cadence would only add load. 5 minutes per active sandbox is plenty
# to keep the lobby scoreboard fresh.
PRESTIGE_INTERVAL_SECONDS = 300.0
# sandbox_id -> monotonic time of its last prestige recompute.
_last_prestige_at: Dict[str, float] = {}

# Stale-session watchdog (T2.3). A cash session whose `games` row hasn't
# been touched within STALE_SESSION_TTL_SECONDS — and which isn't in
# memory (so nobody's actively playing it) — is an abandoned orphan: it
# wedges the sit guard until cleaned. The watchdog runs the same sweep
# the boot hook does, on a slow wall-clock cadence, so orphans created
# between reboots self-clear instead of lingering. Far slower than the
# base tick because abandonment is measured in minutes.
# 4h, not 30m (Codex review #1): a session is only reaped — settled at
# chips=0 — once it's gone genuinely cold, so a player who steps away
# doesn't get their table stack burned. Mirrors cash_mode.lobby's
# DEFAULT_STALE_TTL_SECONDS.
STALE_SESSION_TTL_SECONDS = 14400.0
WATCHDOG_INTERVAL_SECONDS = 300.0
# monotonic time of the last watchdog pass (None until the first run).
_last_watchdog_at: Optional[float] = None

# Payout-reconcile watchdog (tournament circuit). A tournament whose prize payout
# crashed mid-distribute is left at `payout_status='in_progress'` with partial
# credits and no retry path — `apply_payout_on_complete`'s guard actively BLOCKS
# re-entry, so without this it stays wedged forever (escrow non-zero, finishers
# unpaid). This janitor resumes each stuck payout from the ledger (pay only the
# unpaid remainder — never a double credit). Flag-gated with the rest of the
# circuit. Grace window so a payout in-flight on a request thread isn't stolen.
PAYOUT_RECONCILE_INTERVAL_SECONDS = 300.0
PAYOUT_RECONCILE_GRACE_SECONDS = 120.0
# monotonic time of the last reconcile pass (None until the first run).
_last_payout_reconcile_at: Optional[float] = None


def start_world_ticker(socketio) -> None:
    """Start the shared ticker once. Idempotent across create_app() calls."""
    global _started
    if not is_enabled():
        logger.info("[TICKER] world ticker disabled via WORLD_TICKER_ENABLED")
        return
    with _start_lock:
        if _started:
            return
        _started = True
        _stop.clear()
        socketio.start_background_task(_run, socketio)
        logger.info(
            "[TICKER] world ticker started (tick=%.1fs budget=%.0fms)",
            BASE_TICK_SECONDS,
            CYCLE_BUDGET_MS,
        )


def stop_world_ticker() -> None:
    """Signal the ticker loop to exit. For tests / graceful shutdown."""
    global _started
    with _start_lock:
        _stop.set()
        _started = False


def _run(socketio) -> None:
    """The ticker loop. Runs until `stop_world_ticker()` is called."""
    global _cycle, _rr_offset
    while not _stop.is_set():
        socketio.sleep(BASE_TICK_SECONDS)
        if _stop.is_set():
            break
        _cycle += 1
        try:
            _run_cycle(socketio)
        except Exception:
            # One bad cycle must never kill the loop.
            logger.exception("[TICKER] cycle failed")
        try:
            _maybe_run_stale_session_watchdog()
        except Exception:
            # The watchdog is a janitor; a failure must never kill the loop.
            logger.exception("[TICKER] stale-session watchdog failed")
        try:
            _maybe_run_payout_reconcile_watchdog()
        except Exception:
            # Same janitor discipline — never kill the loop on a reconcile hiccup.
            logger.exception("[TICKER] payout-reconcile watchdog failed")


def _maybe_run_stale_session_watchdog(now_monotonic: Optional[float] = None) -> int:
    """Sweep abandoned cash sessions, rate-limited (T2.3).

    Runs at most once per `WATCHDOG_INTERVAL_SECONDS`. Reuses the boot
    sweep (`_boot_sweep_stale_cash_rows`) but passes the set of cash
    games currently in memory as `skip_game_ids`: a live in-memory copy
    would just re-save a deleted row (the resurrection race), and the
    player may still be at the table. So only truly-cold, past-TTL rows
    get reaped. Runs regardless of whether any sandbox is active —
    orphans persist even when nobody's online.

    Returns the number of rows swept (0 when rate-limited or disabled).
    Best-effort: any failure is logged and swallowed by the caller.
    """
    global _last_watchdog_at
    now = now_monotonic if now_monotonic is not None else time.monotonic()
    if _last_watchdog_at is not None and (now - _last_watchdog_at) < WATCHDOG_INTERVAL_SECONDS:
        return 0
    # Stamp BEFORE the work so a persistently-failing sweep backs off to
    # the normal cadence instead of retrying every tick.
    _last_watchdog_at = now

    from datetime import datetime

    from cash_mode.lobby import _boot_sweep_stale_cash_rows
    from flask_app import extensions
    from flask_app.services import game_state_service

    cash_session_repo = getattr(extensions, "cash_session_repo", None)
    game_repo = getattr(extensions, "game_repo", None)
    if cash_session_repo is None or game_repo is None:
        return 0

    in_memory_cash_ids = {
        gid for gid, gdata in list(game_state_service.games.items()) if gdata.get("cash_mode")
    }

    return _boot_sweep_stale_cash_rows(
        game_repo=game_repo,
        cash_session_repo=cash_session_repo,
        stake_repo=getattr(extensions, "stake_repo", None),
        chip_ledger_repo=getattr(extensions, "chip_ledger_repo", None),
        bankroll_repo=getattr(extensions, "bankroll_repo", None),
        cash_table_repo=getattr(extensions, "cash_table_repo", None),
        entity_presence_repo=getattr(extensions, "entity_presence_repo", None),
        stale_ttl_seconds=int(STALE_SESSION_TTL_SECONDS),
        now=datetime.utcnow(),
        skip_game_ids=in_memory_cash_ids,
        source="watchdog",
        # The skip-set is a cheap first pass; the authoritative guard
        # against the resurrection race (Codex #2) is the per-game lock +
        # in-memory re-check the sweep does when given game_state_service.
        game_state_service=game_state_service,
    )


def _maybe_run_payout_reconcile_watchdog(now_monotonic: Optional[float] = None) -> int:
    """Resume tournament payouts wedged at `payout_status='in_progress'`, rate-
    limited. Flag-gated with the circuit (`TOURNAMENT_CIRCUIT_ENABLED`); inert
    when off or when nothing is stuck. Runs at most once per
    `PAYOUT_RECONCILE_INTERVAL_SECONDS`, regardless of active sandboxes (a stuck
    payout persists even when nobody's online). Returns the number of tournaments
    reconciled to `complete`. Best-effort: failures are logged and swallowed."""
    global _last_payout_reconcile_at
    from cash_mode import economy_flags

    if not economy_flags.TOURNAMENT_CIRCUIT_ENABLED:
        return 0
    now = now_monotonic if now_monotonic is not None else time.monotonic()
    if (
        _last_payout_reconcile_at is not None
        and (now - _last_payout_reconcile_at) < PAYOUT_RECONCILE_INTERVAL_SECONDS
    ):
        return 0
    # Stamp BEFORE the work so a persistently-failing sweep backs off to cadence.
    _last_payout_reconcile_at = now

    from datetime import datetime, timedelta

    from flask_app import extensions
    from flask_app.services import game_state_service, tournament_registry, tournament_ticker
    from flask_app.services.sandbox_resolver import resolve_default_sandbox_for

    session_repo = getattr(extensions, "tournament_session_repo", None)
    ledger_repo = getattr(extensions, "chip_ledger_repo", None)
    bankroll_repo = getattr(extensions, "bankroll_repo", None)
    sandbox_repo = getattr(extensions, "sandbox_repo", None)
    if session_repo is None or ledger_repo is None or bankroll_repo is None:
        return 0

    grace_iso = (datetime.utcnow() - timedelta(seconds=PAYOUT_RECONCILE_GRACE_SECONDS)).isoformat()
    results = tournament_ticker.reconcile_stuck_payouts(
        session_repo=session_repo,
        ledger_repo=ledger_repo,
        bankroll_repo=bankroll_repo,
        registry=tournament_registry,
        resolve_sandbox=lambda owner: resolve_default_sandbox_for(owner, sandbox_repo=sandbox_repo),
        get_lock=game_state_service.get_sandbox_lock,
        older_than_iso=grace_iso,
    )
    n = sum(1 for r in results if r.get('reconciled'))
    if results:
        logger.info("[TICKER] payout-reconcile: %d/%d stuck tournaments resolved", n, len(results))
    return n


def _run_cycle(socketio) -> None:
    """Advance every active sandbox once, within the time budget."""
    global _rr_offset
    from flask_app.services import presence

    sessions = presence.active_sessions()
    if not sessions:
        # Prune markers for owners no longer active so the dict can't grow
        # unbounded over a long uptime.
        _last_marker.clear()
        return

    # Rotate the work list so a budget cutoff doesn't always starve the
    # same tail sandboxes across cycles.
    if sessions:
        _rr_offset = (_rr_offset + 1) % len(sessions)
        sessions = sessions[_rr_offset:] + sessions[:_rr_offset]

    # PRH-14: cap how many sandboxes one cycle advances. The rotation above
    # already slid the window, so this slice is fair across cycles — it just
    # bounds the per-cycle fan-out (work + narration spend) when an unusual
    # number of sandboxes are active at once.
    if MAX_ACTIVE_SANDBOXES_PER_CYCLE > 0 and len(sessions) > MAX_ACTIVE_SANDBOXES_PER_CYCLE:
        sessions = sessions[:MAX_ACTIVE_SANDBOXES_PER_CYCLE]

    active_owners = {s.owner_id for s in sessions}
    for stale in [o for o in _last_marker if o not in active_owners]:
        _last_marker.pop(stale, None)

    cycle_start = time.monotonic()
    for session in sessions:
        if (time.monotonic() - cycle_start) * 1000.0 > CYCLE_BUDGET_MS:
            break  # defer the rest to the next cycle
        try:
            _tick_sandbox(socketio, session.owner_id, session.sandbox_id)
        except Exception:
            logger.exception("[TICKER] tick failed for owner=%s", session.owner_id)
        socketio.sleep(0)  # cooperative yield between sandboxes


def _resolve_pace(owner_id: str) -> Tuple[float, int]:
    """Look up the owner's pace params, defaulting on any failure."""
    from flask_app import extensions

    repo = getattr(extensions, "user_prefs_repo", None)
    pace = _DEFAULT_PACE
    if repo is not None:
        try:
            pace = repo.get_world_pace(owner_id)
        except Exception as e:
            # Display-only fallback, but log it so a persistent pace-lookup
            # failure (e.g. a broken user_prefs read) isn't silently invisible.
            logger.warning("world-pace lookup failed for %r; using default: %s", owner_id, e)
            pace = _DEFAULT_PACE
    return _PACE_PARAMS.get(pace, _PACE_PARAMS[_DEFAULT_PACE])


def _record_vacated(invite_repo, invite: dict, results: dict) -> None:
    """Fold this tick's freshly-vacated reservations (each result's `.called_up`)
    into the invite's `vacated_pids` — the observable gather progress. Best-effort
    and idempotent: only the reserved pids that actually left are recorded, only
    written when the set grew, never breaks the tick.

    Observability ONLY — NOT a spawn gate. The autonomous run always spawns at
    `expires_at` (via `expire_due`), never early "when gathered", so an
    incomplete `vacated_pids` never changes spawn timing. It's fed from the
    unseated-tables refresh here; reservations vacated off the human's OWN live
    table (game_handler's hand-boundary refresh, outside this lock) leave cash
    correctly but aren't folded in — deliberately, to avoid a cross-path invite
    write race on a non-load-bearing field. Whereabouts derives bound/seated from
    LIVE seat status, not from this."""
    try:
        reserved = set(invite.get('reserved_pids') or [])
        vacated = set(invite.get('vacated_pids') or [])
        for result in results.values():
            vacated.update(getattr(result, 'called_up', None) or [])
        vacated &= reserved  # only reserved personas count toward the gather
        if vacated != set(invite.get('vacated_pids') or []):
            invite_repo.set_vacated_pids(invite['invite_id'], sorted(vacated))
    except Exception:  # noqa: BLE001 — gather bookkeeping is best-effort
        logger.exception("gather: recording vacated_pids failed for %s", invite.get('invite_id'))


def _tick_sandbox(socketio, owner_id: str, sandbox_id: str) -> None:
    """Run one world-advancing refresh for a sandbox + push the deltas."""
    from cash_mode import economy_flags
    from cash_mode.activity import recent_events, serialize_event
    from cash_mode.lobby import refresh_unseated_tables
    from flask_app import extensions
    from flask_app.handlers.game_handler import live_cash_seated_pids
    from flask_app.services import game_state_service, presence

    hand_sim_prob, run_every = _resolve_pace(owner_id)
    if run_every > 1 and (_cycle % run_every) != 0:
        return  # quiet pace: skip this cycle

    # Baseline the event marker on first sight so we don't replay the
    # ring-buffer backlog the moment a user becomes active.
    if owner_id not in _last_marker:
        existing = recent_events(limit=1, sandbox_id=sandbox_id)
        _last_marker[owner_id] = existing[0].created_at if existing else ""

    # Hold the per-sandbox seat lock around the read-modify-write of the
    # sandbox's tables so the ticker's live-fill serializes with the route-side
    # seat claims (human sit / sponsor-sit), which take the same lock. Without
    # it, a human sit interleaving across refresh's load→save DB-yield gap is
    # last-write-wins → a stranded already-debited AI buy-in or a double-seat /
    # seated_and_idle split-brain. See game_state_service.get_sandbox_lock.
    with game_state_service.get_sandbox_lock(sandbox_id):
        # Cash→tournament draw (flag-gated): the reserved field of the owner's
        # open Main Event is gathered off cash this tick — passed as
        # called_up_pids so seated reservations leave + aren't re-seated. The
        # actual leavers come back on each result's `.called_up`; record them
        # as vacated_pids so the field's gather progress is observable.
        from flask_app.services import tournament_invites as invites

        invite_repo = getattr(extensions, "tournament_invite_repo", None)
        gather_invite = invites.open_invite_for_gather(invite_repo, owner_id)
        called_up = set(gather_invite['reserved_pids'] or []) if gather_invite else set()

        results = refresh_unseated_tables(
            cash_table_repo=extensions.cash_table_repo,
            personality_repo=extensions.personality_repo,
            bankroll_repo=extensions.bankroll_repo,
            user_id=owner_id,
            sandbox_id=sandbox_id,
            hand_sim_prob=hand_sim_prob,
            chip_ledger_repo=extensions.chip_ledger_repo,
            relationship_repo=extensions.relationship_repo,
            stake_repo=extensions.stake_repo,
            vice_repo=extensions.vice_state_repo,
            side_hustle_repo=extensions.side_hustle_state_repo,
            prestige_snapshots_repo=extensions.prestige_snapshots_repo,
            live_seated_pids=live_cash_seated_pids(sandbox_id),
            human_headroom=economy_flags.LIVE_FILL_HUMAN_HEADROOM,
            # Keep personas who are in a tournament out of cash seats.
            tournament_repo=extensions.tournament_session_repo,
            called_up_pids=called_up or None,
        )

        if gather_invite and called_up:
            _record_vacated(invite_repo, gather_invite, results)

    _maybe_record_holdings_snapshot(sandbox_id)
    # Recompute the human's reputation scoreboard. Placed before the
    # event-emit block below so a quadrant-shift beat it records into the
    # activity buffer rides out on this same tick.
    _maybe_recompute_prestige(owner_id, sandbox_id)

    # Circuit Main Event hook (flag-gated, default OFF): offer/expire invites on
    # the tick and advance the owner's autonomous tournament one step, recording
    # structural beats into the activity buffer so the emit block below ships
    # them. Placed before the emit so this tick's beats ride out immediately.
    _maybe_tick_tournament(owner_id, sandbox_id)

    room = presence.lobby_room_name(owner_id)
    # Push new ticker events (newest-first from the buffer; emit oldest
    # first so the client appends in chronological order).
    prev_marker = _last_marker.get(owner_id, "")
    fresh = [
        e
        for e in recent_events(limit=WORLD_EVENT_LIMIT, sandbox_id=sandbox_id)
        if e.created_at > prev_marker
    ]
    if fresh:
        _last_marker[owner_id] = fresh[0].created_at
        for event in reversed(fresh):
            socketio.emit("world_event", serialize_event(event), to=room)

    # Lightweight nudge so a mounted lobby refetches the snapshot.
    socketio.emit("lobby_tick", {"sandbox_id": sandbox_id, "ts": time.time()}, to=room)


def _maybe_record_holdings_snapshot(sandbox_id: str) -> None:
    """Record a net-worth snapshot for this sandbox, rate-limited.

    Captures at most once per `SNAPSHOT_INTERVAL_SECONDS` per sandbox so
    the admin "Player Holdings" chart has real net-worth-over-time points.
    Best-effort: any failure is logged and swallowed — snapshotting must
    never delay or break the world tick.
    """
    now = time.monotonic()
    last = _last_snapshot_at.get(sandbox_id)
    if last is not None and (now - last) < SNAPSHOT_INTERVAL_SECONDS:
        return
    try:
        from flask_app import extensions
        from flask_app.services.holdings_view import record_holdings_snapshot

        repo = getattr(extensions, "holdings_snapshots_repo", None)
        if repo is None:
            return
        # Stamp the attempt BEFORE recording: a persistently failing snapshot
        # (DB lock, schema mismatch) must back off to the normal cadence, not
        # retry the full N+1 computation on every 2s tick.
        _last_snapshot_at[sandbox_id] = now
        record_holdings_snapshot(
            snapshots_repo=repo,
            bankroll_repo=extensions.bankroll_repo,
            personality_repo=extensions.personality_repo,
            user_repo=extensions.user_repo,
            stake_repo=extensions.stake_repo,
            cash_table_repo=extensions.cash_table_repo,
            db_path=extensions.persistence_db_path,
            sandbox_id=sandbox_id,
        )
    except Exception:
        logger.exception("[TICKER] holdings snapshot failed for sandbox=%s", sandbox_id)


def _maybe_v2_overlay(owner_id, sandbox_id, v1_score, now):
    """Field-relative Renown-v2 overlay for the human, or None.

    Returns None when the flag is off, the field repo is unavailable, the
    human isn't in the scored field, or anything throws — every None path makes
    the caller persist the v1-only row, so the world tick never breaks.

    When it returns a dict, the renown AXIS is v2 (uncapped, field-relative) and
    the regard axis stays v1's (orthogonal, unchanged shape) — so the quadrant's
    warm/hostile split reuses the v1 regard the dial already shows, and only the
    high/low-renown test becomes field-relative (`renown_v2 >= high_cut`). The
    v2 renown ratchets via its own peak, mirroring v1's ratchet-then-classify.
    """
    try:
        from cash_mode import economy_flags

        if not getattr(economy_flags, "RENOWN_V2_ENABLED", False):
            return None
        from cash_mode.prestige import (
            PROD_VOLUME_DENOMINATOR,
            WeightsV2,
            quadrant_label_relative,
            regard_of_v2,
            score_renown_field,
        )
        from flask_app import extensions

        field_repo = getattr(extensions, "renown_field_repo", None)
        prestige_repo = getattr(extensions, "prestige_snapshots_repo", None)
        if field_repo is None or prestige_repo is None:
            return None

        field = field_repo.build_inputs(sandbox_id, owner_id)
        if owner_id not in field:
            return None
        weights = WeightsV2(volume_denominator=PROD_VOLUME_DENOMINATOR)
        scored = score_renown_field(field, weights)
        h = scored.get(owner_id)
        if h is None:
            return None

        # Ratchet the v2 renown on its own scale (independent of v1's ratchet),
        # then classify the (ratcheted) renown against the current field cut.
        v2_peak = prestige_repo.load_renown_v2_peak(sandbox_id, owner_id)
        renown_v2 = max(v2_peak, h.renown_total)
        quadrant = quadrant_label_relative(renown_v2, v1_score.regard, h.high_cut)
        out = {
            "quadrant": quadrant,
            "renown_v2": round(renown_v2, 4),
            "victim_percentile": round(h.victim_percentile, 4),
            "high_cut": round(h.high_cut, 4),
            "components": {k: round(v, 4) for k, v in h.components.items()},
            "field_size": len(field),
        }

        # Per-AI fan-out (behind its own flag): the field was scored over EVERY
        # entity above — persist the AI rows the overlay would otherwise discard.
        # Each AI ratchets on its OWN v2 peak (one batched GROUP-BY read) and
        # uses its symmetric v2 regard (regard_of_v2) for the warm/hostile split.
        # The caller batch-writes these via record_ai_many; building them here
        # reuses the single field build + score (no extra compute).
        if getattr(economy_flags, "RENOWN_V2_PERSIST_AI", False):
            ai_peaks = prestige_repo.load_renown_v2_peaks(sandbox_id, "ai")
            field_size = len(field)
            ai_rows = []
            for eid, fr in scored.items():
                if eid == owner_id:
                    continue
                ai_regard = regard_of_v2(field[eid])
                ai_rv2 = max(ai_peaks.get(eid, 0.0), fr.renown_total)
                ai_rows.append(
                    {
                        "owner_id": eid,
                        "renown_v2": round(ai_rv2, 4),
                        "regard": round(ai_regard, 4),
                        "quadrant": quadrant_label_relative(ai_rv2, ai_regard, fr.high_cut),
                        "victim_percentile": round(fr.victim_percentile, 4),
                        "high_cut": round(fr.high_cut, 4),
                        "components": {k: round(v, 4) for k, v in fr.components.items()},
                        "field_size": field_size,
                    }
                )
            out["ai_rows"] = ai_rows

        return out
    except Exception:
        logger.warning("[TICKER] renown-v2 overlay failed for owner=%s", owner_id)
        return None


def _maybe_recompute_prestige(owner_id: str, sandbox_id: str) -> None:
    """Recompute + persist the human's prestige for this sandbox, rate-limited.

    Captures at most once per `PRESTIGE_INTERVAL_SECONDS` per sandbox. Renown
    ratchets (we pass the persisted peak into the compute, which takes the
    max), regard swings. When the quadrant changes from the previous capture,
    records a one-line "the room sees you differently" beat into the activity
    ring buffer so the existing emit path surfaces it on the lobby ticker.

    Best-effort: any failure is logged and swallowed — prestige must never
    delay or break the world tick.
    """
    now_mono = time.monotonic()
    last = _last_prestige_at.get(sandbox_id)
    if last is not None and (now_mono - last) < PRESTIGE_INTERVAL_SECONDS:
        return
    try:
        from dataclasses import replace
        from datetime import datetime, timedelta

        from cash_mode import activity
        from cash_mode.prestige import compute_prestige, iso_utc
        from flask_app import extensions
        from poker.repositories.prestige_snapshots_repository import (
            DEFAULT_RETENTION_DAYS,
        )

        repo = getattr(extensions, "prestige_snapshots_repo", None)
        if repo is None:
            return
        # Stamp the attempt BEFORE the work: a persistently failing recompute
        # (DB lock, schema mismatch) must back off to the normal cadence, not
        # retry the full aggregate on every tick.
        _last_prestige_at[sandbox_id] = now_mono

        prev = repo.load_latest(sandbox_id, owner_id)
        peak = repo.load_renown_peak(sandbox_id, owner_id)
        now = datetime.utcnow()
        score = compute_prestige(
            owner_id=owner_id,
            sandbox_id=sandbox_id,
            now=now,
            relationship_repo=extensions.relationship_repo,
            cash_session_repo=extensions.cash_session_repo,
            renown_peak=peak,
        )

        # v2 overlay (behind RENOWN_V2_ENABLED): score the human against the
        # whole field for the uncapped, field-relative renown + quadrant. When
        # it succeeds, the CONSUMED quadrant column becomes the v2 relative
        # quadrant (so all 4 hooks + the lobby follow with no hook change) and
        # the v2 columns are persisted alongside the v1 baseline. Best-effort:
        # any failure falls back to the v1-only row, never breaking the tick.
        v2 = _maybe_v2_overlay(owner_id, sandbox_id, score, now)
        if v2 is not None:
            score = replace(score, quadrant=v2["quadrant"])
            repo.record(
                captured_at=score.computed_at,
                sandbox_id=sandbox_id,
                owner_id=owner_id,
                score=score,
                formula_version="v2",
                renown_v2=v2["renown_v2"],
                victim_percentile=v2["victim_percentile"],
                high_cut=v2["high_cut"],
                renown_v2_components=v2["components"],
                field_size=v2["field_size"],
            )
            # Per-AI fan-out (RENOWN_V2_PERSIST_AI). Best-effort in its OWN guard
            # AFTER the human row: a fan-out failure must never lose the human's
            # capture or break the tick. One batched insert for the whole field.
            ai_rows = v2.get("ai_rows")
            if ai_rows:
                try:
                    repo.record_ai_many(
                        sandbox_id=sandbox_id,
                        captured_at=score.computed_at,
                        rows=ai_rows,
                    )
                except Exception as exc:
                    logger.warning(
                        "[TICKER] renown-v2 AI fan-out persist failed " "(sandbox=%s, %d rows): %s",
                        sandbox_id,
                        len(ai_rows),
                        exc,
                    )
        else:
            repo.record(
                captured_at=score.computed_at,
                sandbox_id=sandbox_id,
                owner_id=owner_id,
                score=score,
            )
        try:
            cutoff = iso_utc(now - timedelta(days=DEFAULT_RETENTION_DAYS))
            repo.prune(cutoff)
        except Exception as exc:
            logger.warning("[TICKER] prestige prune failed: %s", exc)

        # Quadrant flip → ticker beat (only when we have a prior capture to
        # compare against, so the first-ever capture is silent).
        if prev is not None and prev.get("quadrant") != score.quadrant:
            activity.record_event(
                activity.LobbyEvent(
                    type=activity.EVENT_REPUTATION_SHIFT,
                    table_id="",
                    stake_label="",
                    personality_id="",
                    name="",
                    reason=score.quadrant,
                    message=activity.format_reputation_shift_message(score.quadrant),
                    created_at=score.computed_at,
                    sandbox_id=sandbox_id,
                )
            )
    except Exception:
        logger.exception("[TICKER] prestige recompute failed for owner=%s", owner_id)


def _maybe_tick_tournament(owner_id: str, sandbox_id: str) -> None:
    """Circuit Main Event world-tick hook (P3.7), flag-gated (default OFF).

    Behind `economy_flags.TOURNAMENT_CIRCUIT_ENABLED`, per active sandbox:
      (a) sweep expired invites to autonomous play + let the chairman offer a new
          Main Event (FLUSH + cooldown) — so offers/expiries fire on the tick,
          not only on lobby load; then
      (b) advance the owner's *autonomous* tournament one round, recording its
          structural beats into the activity buffer so `_tick_sandbox`'s emit
          block ships them as `world_event`s (no separate emit needed).

    Holds the per-sandbox lock around the work (the settle mutates the escrow),
    matching `GET /api/tournament/invite`'s pattern. Best-effort: any failure is
    logged and swallowed — the tournament hook must never break the cash world
    tick. Inert when off, and a quick no-op when the sandbox has no tournament.
    """
    from cash_mode import economy_flags

    if not economy_flags.TOURNAMENT_CIRCUIT_ENABLED:
        return
    try:
        from cash_mode import activity
        from flask_app import extensions
        from flask_app.services import (
            game_state_service,
            tournament_invites as invites,
            tournament_registry,
            tournament_ticker,
        )

        invite_repo = getattr(extensions, "tournament_invite_repo", None)
        session_repo = getattr(extensions, "tournament_session_repo", None)
        ledger_repo = getattr(extensions, "chip_ledger_repo", None)
        bankroll_repo = getattr(extensions, "bankroll_repo", None)
        personality_repo = getattr(extensions, "personality_repo", None)
        cash_table_repo = getattr(extensions, "cash_table_repo", None)
        prestige_repo = getattr(extensions, "prestige_snapshots_repo", None)
        if invite_repo is None or session_repo is None or ledger_repo is None:
            return  # persistence not wired (e.g. unit context) — nothing to do
        # The cash→tournament draw context (tournaments-as-a-draw); flag-gated
        # downstream, so this is inert unless TOURNAMENT_DRAW_ENABLED.
        draw_ctx = invites.draw_context(
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            prestige_repo=prestige_repo,
            cash_table_repo=cash_table_repo,
            ledger_repo=ledger_repo,
        )

        events: list = []
        with game_state_service.get_sandbox_lock(sandbox_id):
            try:
                invites.expire_due(
                    invite_repo=invite_repo,
                    personality_repo=personality_repo,
                    bankroll_repo=bankroll_repo,
                    ledger_repo=ledger_repo,
                    session_repo=session_repo,
                    cash_table_repo=cash_table_repo,
                    sandbox_id=sandbox_id,  # only sweep this sandbox (the lock we hold)
                )
                invites.maybe_offer_main_event(
                    invite_repo=invite_repo,
                    session_repo=session_repo,
                    ledger_repo=ledger_repo,
                    owner_id=owner_id,
                    sandbox_id=sandbox_id,
                    draw_ctx=draw_ctx,
                )
            except Exception:  # noqa: BLE001 — surfacing is best-effort
                logger.exception("[TICKER] invite sweep failed for owner=%s", owner_id)

            result = tournament_ticker.advance_owner_tournament(
                owner_id=owner_id,
                sandbox_id=sandbox_id,
                registry=tournament_registry,
                session_repo=session_repo,
                bankroll_repo=bankroll_repo,
                ledger_repo=ledger_repo,
                personality_repo=personality_repo,
                prestige_repo=getattr(extensions, "prestige_snapshots_repo", None),
            )
            if result:
                events = result['events']
        # Record outside the sandbox lock — the activity buffer has its own lock.
        for event in events:
            activity.record_event(event)
    except Exception:  # noqa: BLE001 — the hook must never kill the cash tick
        logger.exception("[TICKER] tournament tick failed for owner=%s", owner_id)
