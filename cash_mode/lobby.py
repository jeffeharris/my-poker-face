"""Lobby seeding and boot-time cleanup.

Two top-level entry points called from app startup:

  - `ensure_lobby_seeded(cash_table_repo, personality_repo,
      bankroll_repo, now, owner_id_for_eligibility)` — idempotent.
    Creates 5 lobby tables (one per stake) if missing, fills each
    with 4 baseline AI personalities, leaves 2 seats `"open"`.

  - `kill_all_cash_sessions(game_state_service, game_repo)` —
    one-shot boot cleanup. Deletes every in-flight cash game from
    memory and every persisted `cash-*` row. Subsumes the older
    `cleanup_orphan_cash_games`.

Both are pure-ish: they take repository instances as parameters
rather than importing from `flask_app.extensions`, so the test
harness can pass tempdb-backed repos.

Spec: `docs/plans/CASH_MODE_LOBBY_HANDOFF.md` §"Lobby maintenance" (a)
and §"Locked decisions" (3 — kill_all_cash_sessions).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from cash_mode.bankroll import (
    AIBankrollState,
    project_bankroll,
)
from cash_mode.stakes import (
    STAKES_LADDER,
    STAKES_ORDER,
    table_buy_in_window,
)
from cash_mode.tables import (
    BASELINE_AI_SEATS,
    CashTableState,
    TABLE_SEAT_COUNT,
    ai_slot,
    open_slot,
)

logger = logging.getLogger(__name__)


def _table_id_for_stake(stake_label: str) -> str:
    """Return the stable table_id for a stake's primary v1.5 lobby table.

    `cash-table-2-001` style — the dollar sign in stake_label isn't
    URL-safe, so we slugify to the bare numeric.
    """
    if stake_label.startswith("$"):
        slug = stake_label[1:]
    else:
        slug = stake_label
    return f"cash-table-{slug}-001"


def ensure_lobby_seeded(
    *,
    cash_table_repo,
    personality_repo,
    bankroll_repo,
    now: Optional[datetime] = None,
    user_id: Optional[str] = None,
) -> List[CashTableState]:
    """Idempotent boot-time lobby seed.

    For each stake in `STAKES_ORDER`:
      1. If a `cash_tables` row exists for the canonical table_id,
         leave it alone.
      2. Otherwise create a new `CashTableState`, fill 4 AI seats with
         eligible personalities (affordable, not already seated at
         another freshly seeded table), leave 2 seats open.

    Each personality lands on at most one table across the whole lobby
    seed (global uniqueness invariant). Personalities are pulled from
    `list_eligible_for_cash_mode`, then filtered down to those whose
    projected bankroll covers the table's AI buy-in
    (`min_buy_in × buy_in_multiplier`).

    Returns the final list of `CashTableState`s in the lobby (newly
    seeded + previously existing rows). Used by tests + the boot hook
    to verify a successful seed.

    `now` defaults to `datetime.utcnow()`. Explicit `now` is useful in
    tests to pin the projection clock.

    AI bankrolls are NOT debited at seed time — chips for the AI seats
    are *placeholder* values that represent the AI's intended table
    stack. The real debit happens at sit-down (`_build_cash_game`
    debits each seated AI's persistent bankroll for `ai_buy_in`).
    That way, re-seeding (idempotent boot pass) doesn't double-spend
    AI bankrolls.
    """
    if now is None:
        now = datetime.utcnow()

    existing_tables = cash_table_repo.list_all_tables()
    by_id: Dict[str, CashTableState] = {t.table_id: t for t in existing_tables}
    # Global "already seated" set, used both for incremental seeding and
    # for preserving uniqueness across tables that already exist.
    seated_globally: Set[str] = set()
    for t in existing_tables:
        for slot in t.seats:
            if slot["kind"] == "ai":
                seated_globally.add(slot["personality_id"])

    eligible = personality_repo.list_eligible_for_cash_mode(user_id=user_id)

    out_tables: List[CashTableState] = []

    for stake_label in STAKES_ORDER:
        table_id = _table_id_for_stake(stake_label)
        existing = by_id.get(table_id)
        if existing is not None:
            # Already seeded; preserve.
            out_tables.append(existing)
            continue

        # Build a fresh row. Fill 4 AI seats.
        seats = [open_slot() for _ in range(TABLE_SEAT_COUNT)]
        _, min_buy_in, max_buy_in = table_buy_in_window(stake_label)
        filled = 0
        for cand in eligible:
            if filled >= BASELINE_AI_SEATS:
                break
            pid = cand.get("personality_id")
            name = cand.get("name")
            if not pid or pid in seated_globally:
                continue

            knobs = bankroll_repo.load_personality_knobs(pid)
            ai_threshold = round(min_buy_in * knobs.buy_in_multiplier)
            ai_buy_in = min(ai_threshold, max_buy_in)

            stored = bankroll_repo.load_ai_bankroll(pid)
            if stored is None:
                # No bankroll row yet — use the personality's cap as a
                # generous starting projection. Sit-down will write the
                # row at debit time.
                projected = knobs.bankroll_cap
            else:
                projected = project_bankroll(
                    stored, knobs.bankroll_cap, knobs.bankroll_rate, now,
                )
            if projected < ai_threshold:
                continue

            seats[filled] = ai_slot(pid, ai_buy_in)
            seated_globally.add(pid)
            filled += 1
            logger.info(
                "[CASH][LOBBY] seed %s: seated %r at chips=%d",
                stake_label, pid, ai_buy_in,
            )

        new_state = CashTableState(
            table_id=table_id,
            stake_label=stake_label,
            seats=seats,
            created_at=now,
            last_activity_at=now,
        )
        cash_table_repo.save_table(new_state, now=now)
        out_tables.append(new_state)
        logger.info(
            "[CASH][LOBBY] seed %s: created table %r with %d AI seats",
            stake_label, table_id, filled,
        )

    return out_tables


def kill_all_cash_sessions(
    *,
    game_state_service,
    game_repo,
) -> int:
    """One-shot boot cleanup: drop every in-flight cash session.

    Subsumes `cleanup_orphan_cash_games`. Two purges in one pass:

      1. Every in-memory game with `cash_mode=True` → delete from
         `game_state_service`. The state machine, controllers, memory
         manager, and pressure stats go with it.

      2. Every persisted `cash-*` row (regardless of owner) → delete
         from `game_repo`. Cash games shouldn't even be persisted but
         `progress_game`'s auto-save can write them; we don't want
         them lingering across deploys.

    Per handoff §"Locked decisions" (3): the deploy that lands the
    lobby has zero production users to preserve, so killing every
    in-flight session is the safe and simple option.

    Returns the count of cash sessions dropped (memory + DB combined)
    so the boot logger can report it.
    """
    dropped = 0

    # In-memory.
    in_memory_to_delete = []
    for gid, gdata in list(game_state_service.games.items()):
        if gdata.get("cash_mode"):
            in_memory_to_delete.append(gid)
    for gid in in_memory_to_delete:
        game_state_service.delete_game(gid)
        dropped += 1
        logger.info("[CASH][LOBBY] kill_all_cash_sessions: dropped in-memory %r", gid)

    # Persisted.
    try:
        rows = game_repo.list_games(owner_id=None, limit=10000, offset=0)
    except Exception as e:
        logger.warning("[CASH][LOBBY] list_games failed during cleanup: %s", e)
        rows = []

    persisted_to_delete = [row.game_id for row in rows if row.game_id.startswith("cash-")]
    for gid in persisted_to_delete:
        try:
            game_repo.delete_game(gid)
            dropped += 1
            logger.info("[CASH][LOBBY] kill_all_cash_sessions: dropped persisted %r", gid)
        except Exception as e:
            logger.warning("[CASH][LOBBY] delete_game(%r) failed: %s", gid, e)

    if dropped:
        logger.info(
            "[CASH][LOBBY] kill_all_cash_sessions: dropped %d cash session(s) total",
            dropped,
        )
    return dropped
