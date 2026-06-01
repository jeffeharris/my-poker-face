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


def spawn_autonomous_tournament(
    *,
    owner_id: str,
    sandbox_id: str,
    personality_repo,
    bankroll_repo,
    ledger_repo,
    session_repo,
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
    entries = select_persona_field(
        personality_repo=personality_repo,
        owner_id=owner_id,
        field_size=field_size,
        archetypes=archetypes,
        rng_seed=rng_seed,
        human_id=None,
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

    # Fund from the bank pool off ONE economy snapshot (no human buy-in).
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

    return {
        'tournament_id': tournament_id,
        'session': session,
        'entries': entries,
        'plan': plan,
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
    Idempotent (the payout_status guard). Caller holds the sandbox lock."""
    return econ.apply_payout_on_complete(
        tournament_id=tournament_id,
        session=session,
        human_owner_id=None,
        sandbox_id=sandbox_id,
        bankroll_repo=bankroll_repo,
        ledger_repo=ledger_repo,
        session_repo=session_repo,
        real_persona_ids=frozenset(entries.keys()),
    )
