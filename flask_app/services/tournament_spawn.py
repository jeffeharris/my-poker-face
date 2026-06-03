"""Spawn an autonomous, real-persona Main Event (P3 foundation).

The redistribution heartbeat: create an AI-only tournament whose field is the
sandbox's real personas, fund its prize pool from the bank pool via the
EconomyChairman (an overlay when the bank is flush), and — at completion —
distribute that pool into the personas' real bankrolls. Net effect: a flush
bank drains reserves into the AI field, which then cycles those chips back
through the cash tables. No human required (P2 autonomy).

This is the engine-side seam the P3 scheduler/lobby (the surfacing layer) will
call; it is written with injected repos so it runs head-lessly and is fully
testable without Flask. Registering it into the in-memory `tournament_registry`
+ the world-tick advance + the lobby card are the *surfacing* steps (later);
this module only makes the chips correct for a tournament run to completion.

See `docs/plans/TOURNAMENT_CIRCUIT_SURFACING.md` §3 (event-in-the-sandbox model)
and `TOURNAMENT_ECONOMY_ON_STATE_MODEL.md` (escrow/split contract).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from flask_app.services import tournament_economy_service as econ
from flask_app.services.tournament_field import select_persona_field
from tournament.config import DEFAULT_FIELD_ARCHETYPES, TournamentConfig
from tournament.director import FakeHandResolver

logger = logging.getLogger(__name__)

MIN_FIELD = 2  # a tournament needs at least two entrants


def _new_id() -> str:
    # Reuse the registry's id scheme without importing global state at module load.
    from flask_app.services import tournament_registry

    return tournament_registry.new_tournament_id()


class DraftScanError(Exception):
    """A seat / active-participant exclusion scan failed. Raised so the spawn
    ABORTS rather than fielding from an incomplete exclusion set — under-exclusion
    is the dangerous direction (it would draft a currently-seated persona into a
    tournament, the double-presence / ghost-seat bug). Fail closed, never open."""


def _seated_cash_pids(cash_table_repo, sandbox_id: str) -> set:
    """Personality ids currently in an AI cash seat in this sandbox. `None` repo
    (not wired — e.g. unit tests) legitimately means "no cash seats to exclude";
    a SCAN that throws is a different thing — it raises `DraftScanError` so the
    caller fails closed rather than under-excluding."""
    if cash_table_repo is None:
        return set()
    try:
        return {
            slot.get('personality_id')
            for tbl in cash_table_repo.list_all_tables(sandbox_id=sandbox_id)
            for slot in tbl.seats
            if slot.get('kind') == 'ai' and slot.get('personality_id')
        }
    except Exception as e:  # noqa: BLE001
        raise DraftScanError(f"seated-pid scan failed for sandbox {sandbox_id}") from e


def draft_exclusions(*, cash_table_repo, session_repo, owner_id: str, sandbox_id: str) -> set:
    """Personas we must NOT draft into a new tournament field: those currently
    seated at a cash table, plus those already in an active tournament. Keeps a
    persona in exactly one place — the double-presence / ghost-seat guard.

    Fails CLOSED: a scan error raises `DraftScanError` (the spawners catch it and
    abort) rather than returning a partial set that could let a busy persona be
    drafted."""
    excl = _seated_cash_pids(cash_table_repo, sandbox_id)
    if session_repo is not None:
        try:
            excl |= session_repo.active_participant_pids(owner_id)
        except Exception as e:  # noqa: BLE001
            raise DraftScanError(f"active-participant scan failed for owner {owner_id}") from e
    return excl


def spawn_autonomous_tournament(
    *,
    owner_id: str,
    sandbox_id: str,
    personality_repo,
    bankroll_repo,
    ledger_repo,
    session_repo,
    cash_table_repo=None,
    field_size: int = 9,
    table_size: int = 3,
    starting_stack: int = 10_000,
    seed: int = 0,
    rng_seed: int = 0,
    archetypes: tuple[str, ...] = DEFAULT_FIELD_ARCHETYPES,
) -> Optional[dict]:
    """Build + fund an autonomous real-persona tournament. Returns a dict
    `{tournament_id, session, entries, plan}` or None if the sandbox can't field
    at least `MIN_FIELD` distinct personas.

    Caller holds `get_sandbox_lock(sandbox_id)` (the economy snapshot → decide →
    apply-transfers must be atomic). The returned `session` is funny-money only;
    the real chips live in the `tournament:<id>` escrow the funding stamps.

    No real human entrant: `human_id` is a nominal field seat (the first
    persona) so the session is well-formed, but payout is driven with
    `human_owner_id=None` + `real_persona_ids = entries.keys()`, so every
    finisher is credited as a real `ai:<pid>` bankroll, not a player.
    """
    try:
        exclude = draft_exclusions(
            cash_table_repo=cash_table_repo,
            session_repo=session_repo,
            owner_id=owner_id,
            sandbox_id=sandbox_id,
        )
    except DraftScanError:
        logger.exception(
            "[TOURNAMENT] autonomous spawn aborted for owner=%s: exclusion scan failed "
            "(fail-closed — won't risk drafting a seated persona)",
            owner_id,
        )
        return None
    entries = select_persona_field(
        personality_repo=personality_repo,
        owner_id=owner_id,
        field_size=field_size,
        archetypes=archetypes,
        rng_seed=rng_seed,
        human_id=None,
        exclude=exclude,
    )
    if len(entries) < MIN_FIELD:
        logger.info(
            "[TOURNAMENT] autonomous spawn skipped for owner=%s: only %d eligible "
            "personas (need >= %d)",
            owner_id,
            len(entries),
            MIN_FIELD,
        )
        return None

    from tournament.session import TournamentSession

    config = TournamentConfig(
        field_size=len(entries),
        table_size=table_size,
        starting_stack=starting_stack,
        seed=seed,
    )
    resolver = FakeHandResolver()
    nominal_human = next(iter(entries))  # a field seat; NOT a real human
    session = TournamentSession(
        config, ai_resolver=resolver, human_id=nominal_human, entries=entries
    )

    tournament_id = _new_id()
    created_at = datetime.utcnow().isoformat()
    if session_repo is not None:
        session_repo.save(
            tournament_id=tournament_id,
            owner_id=owner_id,
            status='active',
            resolver_kind='fake',
            session_json=json.dumps(session.to_dict()),
            created_at=created_at,
        )

    # Fund from the bank pool off ONE economy snapshot (no human buy-in). If
    # funding fails, delete the just-written `active` row — otherwise it's an
    # unfunded orphan that never settles (payout_status NULL) and permanently
    # trips the one-active-per-owner guard (re-spawned on every lobby load).
    try:
        plan = econ.plan_funding(
            ledger_repo=ledger_repo,
            sandbox_id=sandbox_id,
            field_size=len(entries),
            buy_in=0,
            human_in=False,
        )
        econ.apply_buy_in(
            tournament_id=tournament_id,
            owner_id=owner_id,
            sandbox_id=sandbox_id,
            plan=plan,
            bankroll_repo=bankroll_repo,
            ledger_repo=ledger_repo,
            session_repo=session_repo,
        )
    except Exception:
        if session_repo is not None:
            try:
                session_repo.delete(tournament_id)
            except Exception:  # noqa: BLE001 — best-effort cleanup
                logger.exception("failed to roll back orphan spawn row %s", tournament_id)
        raise

    return {
        'tournament_id': tournament_id,
        'session': session,
        'entries': entries,
        'plan': plan,
    }


def human_seat_id(owner_id: str) -> str:
    """Stable field-seat id for the human entrant (distinct from the synthetic
    `P01` ids and from persona ids). The session's `human_id`; payout maps it
    back to the real player bankroll."""
    return f"human:{owner_id}"


def create_human_tournament(
    *,
    owner_id: str,
    sandbox_id: str,
    personality_repo,
    bankroll_repo,
    ledger_repo,
    session_repo,
    cash_table_repo=None,
    buy_in: int,
    field_size: int = 9,
    table_size: int = 3,
    starting_stack: int = 10_000,
    seed: int = 0,
    rng_seed: int = 0,
    archetypes: tuple[str, ...] = DEFAULT_FIELD_ARCHETYPES,
    register: bool = True,
) -> Optional[dict]:
    """Build a tournament the human plays IN — a real-persona field with the
    human in seat 0 — and charge their buy-in to the escrow.

    Returns `{tournament_id, session, entries, plan, human_id}` or None if the
    sandbox can't field at least `MIN_FIELD` seats. Raises
    `tournament_economy_service.InsufficientFundsError` if the human can't cover
    the buy-in (no chips move first). Caller holds `get_sandbox_lock`.

    The other tables run on the fake resolver (no LLM); the human's own table is
    driven live once they `/sit`. Registers the in-memory record (so `/sit`
    works) when `register` is True; the durable `tournaments` row is written via
    the injected `session_repo` regardless.
    """
    human_id = human_seat_id(owner_id)
    try:
        exclude = draft_exclusions(
            cash_table_repo=cash_table_repo,
            session_repo=session_repo,
            owner_id=owner_id,
            sandbox_id=sandbox_id,
        )
    except DraftScanError:
        logger.exception(
            "[TOURNAMENT] human tournament aborted for owner=%s: exclusion scan failed "
            "(fail-closed — won't risk drafting a seated persona)",
            owner_id,
        )
        return None
    entries = select_persona_field(
        personality_repo=personality_repo,
        owner_id=owner_id,
        field_size=field_size,
        archetypes=archetypes,
        rng_seed=rng_seed,
        human_id=human_id,
        exclude=exclude,
    )
    if len(entries) < MIN_FIELD:
        logger.info(
            "[TOURNAMENT] human tournament skipped for owner=%s: only %d seats",
            owner_id,
            len(entries),
        )
        return None

    from tournament.session import TournamentSession

    config = TournamentConfig(
        field_size=len(entries),
        table_size=table_size,
        starting_stack=starting_stack,
        seed=seed,
    )
    resolver = FakeHandResolver()
    session = TournamentSession(config, ai_resolver=resolver, human_id=human_id, entries=entries)

    tournament_id = _new_id()
    created_at = datetime.utcnow().isoformat()
    if session_repo is not None:
        session_repo.save(
            tournament_id=tournament_id,
            owner_id=owner_id,
            status='active',
            resolver_kind='fake',
            session_json=json.dumps(session.to_dict()),
            created_at=created_at,
        )

    # Fund: the human pays the buy-in; overlay added if the bank is flush.
    # plan_funding + apply_buy_in share ONE economy snapshot under the lock.
    plan = econ.plan_funding(
        ledger_repo=ledger_repo,
        sandbox_id=sandbox_id,
        field_size=len(entries),
        buy_in=buy_in,
        human_in=True,
    )
    try:
        econ.apply_buy_in(  # raises InsufficientFundsError if the human can't cover it
            tournament_id=tournament_id,
            owner_id=owner_id,
            sandbox_id=sandbox_id,
            plan=plan,
            bankroll_repo=bankroll_repo,
            ledger_repo=ledger_repo,
            session_repo=session_repo,
        )
    except Exception:
        # Affordability is checked before any chips move, so a raise here means
        # nothing was debited — but the durable `tournaments` row was already
        # written above. Delete it so a failed accept leaves NO orphan active
        # tournament (which would otherwise block re-offer via offer()'s
        # find_active guard and double up if the player retries). Then re-raise.
        if session_repo is not None:
            try:
                session_repo.delete(tournament_id)
            except Exception:  # noqa: BLE001 — cleanup is best-effort
                logger.exception(
                    "failed to clean up tournament row %s after buy-in failure", tournament_id
                )
        raise

    if register:
        try:
            from flask_app.services import tournament_registry

            tournament_registry.put(
                tournament_id,
                {
                    'session': session,
                    'owner_id': owner_id,
                    'created_at': created_at,
                    'resolver': resolver,
                    'resolver_kind': 'fake',
                    'game_id': None,
                },
            )
        except Exception:  # noqa: BLE001 — durable row already written; registry is the hot cache
            logger.exception("registry put failed for %s", tournament_id)

    return {
        'tournament_id': tournament_id,
        'session': session,
        'entries': entries,
        'plan': plan,
        'human_id': human_id,
    }


def advance_autonomous_tournament(
    *,
    tournament_id: str,
    session,
    entries: dict,
    sandbox_id: str,
    bankroll_repo,
    ledger_repo,
    session_repo,
    rounds_per_tick: int = 1,
) -> dict:
    """Advance an autonomous tournament a bounded number of rounds (one world
    tick's worth) and settle it the moment it completes.

    This is the per-tick step the world ticker calls so a declined / un-accepted
    Main Event plays out at world pace, like the cash tables, rather than
    resolving in one burst. Returns `{rounds, complete, settled, reports}` —
    `reports` are the `RoundReport`s for this tick (the caller turns them into
    ticker beats; persistence of the session is the caller's job, via the
    registry). Idempotent at the tail: once complete, further calls just ensure
    the settle ran (the payout_status guard makes that a no-op).

    Caller holds `get_sandbox_lock(sandbox_id)`."""
    reports = []
    for _ in range(max(1, rounds_per_tick)):
        report = session.advance_round()
        if report is None:
            break
        reports.append(report)

    settled = False
    if session.is_complete():
        settled = settle_autonomous_tournament(
            tournament_id=tournament_id,
            session=session,
            entries=entries,
            sandbox_id=sandbox_id,
            bankroll_repo=bankroll_repo,
            ledger_repo=ledger_repo,
            session_repo=session_repo,
        )

    return {
        'rounds': len(reports),
        'complete': session.is_complete(),
        'settled': settled,
        'reports': reports,
    }


def settle_autonomous_tournament(
    *,
    tournament_id: str,
    session,
    entries: dict,
    sandbox_id: str,
    bankroll_repo,
    ledger_repo,
    session_repo,
) -> bool:
    """Distribute a completed autonomous tournament's pool to its real personas.

    Thin wrapper over `apply_payout_on_complete` that supplies
    `human_owner_id=None` (no human) and `real_persona_ids = entries.keys()`, so
    every in-the-money finisher is credited to its real `ai:<pid>` bankroll.
    Idempotent (the payout_status guard). Caller holds the sandbox lock.

    Marks the tournament row 'complete' — releasing its entrants from the
    double-presence exclusion back to cash seating — ONLY once the payout reached
    a terminal state (`complete` or `skipped`). A payout that THREW leaves
    `payout_status='in_progress'`; we deliberately leave `status='active'` so the
    stranded escrow stays visible to the payout-reconcile watchdog (which credits
    the unpaid finishers and then releases the field). Marking complete on a
    failed payout would hide the strand forever — the bug this guards."""
    ran = econ.apply_payout_on_complete(
        tournament_id=tournament_id,
        session=session,
        human_owner_id=None,
        sandbox_id=sandbox_id,
        bankroll_repo=bankroll_repo,
        ledger_repo=ledger_repo,
        session_repo=session_repo,
        real_persona_ids=frozenset(entries.keys()),
    )
    if session_repo is not None and session.is_complete():
        try:
            # Release the field only if the payout settled (complete/skipped) — NOT
            # if it's wedged 'in_progress' (let the reconcile watchdog finish it).
            payout_status = (session_repo.load(tournament_id) or {}).get('payout_status')
            if payout_status in ('complete', 'skipped'):
                session_repo.set_status(tournament_id, 'complete')
            else:
                logger.warning(
                    "[TOURNAMENT] %s complete but payout_status=%s — leaving status "
                    "'active' for the reconcile watchdog (escrow stranded)",
                    tournament_id,
                    payout_status,
                )
        except Exception:  # noqa: BLE001 — release is best-effort; never break settle
            logger.exception("set_status complete failed for %s", tournament_id)
    return ran
