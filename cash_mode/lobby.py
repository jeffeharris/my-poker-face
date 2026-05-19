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

import random
from typing import Callable, Tuple

from cash_mode.bankroll import (
    AIBankrollState,
    project_bankroll,
)
from cash_mode.fake_sim import (
    DEFAULT_FAKE_HAND_PROB,
    FakeHandResult,
    roll_fake_hand,
)
from cash_mode.movement import (
    DEFAULT_LIVE_FILL_PROB,
    RosterRefreshResult,
    refresh_table_roster,
)
from cash_mode.stakes import (
    STAKES_LADDER,
    STAKES_ORDER,
    table_buy_in_window,
)
from cash_mode.tables import (
    BASELINE_AI_SEATS,
    CashTableState,
    IdlePoolEntry,
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

    # Randomly distribute the 4 AI seats across the 6 positions so the
    # player picks a seat with positional meaning (3-handed UTG vs.
    # button) rather than always taking position 5 because that's where
    # the deterministic empty seat is. `seed_rng` is local — pure-ish
    # boot pass; tests can override by patching random.Random.
    seed_rng = random.Random()

    for stake_label in STAKES_ORDER:
        table_id = _table_id_for_stake(stake_label)
        existing = by_id.get(table_id)
        if existing is not None:
            # Already seeded; preserve.
            out_tables.append(existing)
            continue

        # Pick which 4 positions hold AI seats (the remaining 2 stay
        # open and become the player's choices). Distinct random sample
        # so no duplicates.
        ai_positions = sorted(
            seed_rng.sample(range(TABLE_SEAT_COUNT), BASELINE_AI_SEATS)
        )

        # Build a fresh row. Fill the chosen positions with AI seats.
        seats = [open_slot() for _ in range(TABLE_SEAT_COUNT)]
        _, min_buy_in, max_buy_in = table_buy_in_window(stake_label)
        position_iter = iter(ai_positions)
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

            seat_position = next(position_iter)
            seats[seat_position] = ai_slot(pid, ai_buy_in)
            seated_globally.add(pid)
            filled += 1
            logger.info(
                "[CASH][LOBBY] seed %s: seated %r at seat %d chips=%d",
                stake_label, pid, seat_position, ai_buy_in,
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


def _global_seated_set(tables: List[CashTableState]) -> Set[str]:
    """Return personality_ids currently in any table's AI slot."""
    out: Set[str] = set()
    for t in tables:
        for slot in t.seats:
            if slot["kind"] == "ai":
                out.add(slot["personality_id"])
    return out


def refresh_unseated_tables(
    *,
    cash_table_repo,
    personality_repo,
    bankroll_repo,
    rng: Optional[random.Random] = None,
    now: Optional[datetime] = None,
    user_id: Optional[str] = None,
    live_fill_prob: float = DEFAULT_LIVE_FILL_PROB,
    fake_hand_prob: float = DEFAULT_FAKE_HAND_PROB,
) -> Dict[str, RosterRefreshResult]:
    """Run a movement+live-fill refresh on every table without a human.

    Called from `GET /api/cash/lobby` (lazy cadence — see handoff
    §"Cadence"). For each table whose seats don't include a `"human"`
    slot, evaluates AI movement and rolls live-fill probability on
    open seats. Persists table + idle-pool changes through the repos.

    Tables with a human seated are skipped here: the hand-boundary
    refresh hook in commit 7 covers those. Two separate cadences keep
    the rolls cheap and avoid running movement twice per hand.

    Returns `{table_id: RosterRefreshResult}` for the refreshed tables
    so callers can log/inspect. Empty dict means nothing was refreshed.
    """
    if rng is None:
        rng = random.Random()
    if now is None:
        now = datetime.utcnow()

    tables = cash_table_repo.list_all_tables()
    idle_pool = cash_table_repo.list_idle()
    seated_globally = _global_seated_set(tables)
    eligible = personality_repo.list_eligible_for_cash_mode(user_id=user_id)

    def _bankroll_lookup(pid: str) -> Optional[int]:
        return bankroll_repo.load_ai_bankroll_current(pid, now=now)

    def _buy_in_lookup(pid: str) -> int:
        # Map back to a table buy-in: needs the stake_label of the
        # destination table. We close over the current iteration's
        # `table.stake_label` via the outer scope.
        return _current_table_buy_in[pid]

    out: Dict[str, RosterRefreshResult] = {}
    for table in tables:
        if table.human_seat_index() is not None:
            # Active session table; the hand-boundary hook handles it.
            continue

        big_blind, table_min_buy_in, table_max_buy_in = table_buy_in_window(table.stake_label)
        try:
            stake_idx = STAKES_ORDER.index(table.stake_label)
        except ValueError:
            continue
        next_tier_min_buy_in: Optional[int] = None
        if stake_idx + 1 < len(STAKES_ORDER):
            _, nxt_min, _ = table_buy_in_window(STAKES_ORDER[stake_idx + 1])
            next_tier_min_buy_in = nxt_min

        # Build a per-table buy-in lookup that honors per-personality
        # `buy_in_multiplier`. Computed once per table; passed into the
        # pure helper.
        _current_table_buy_in: Dict[str, int] = {}

        def _buy_in_for(pid: str) -> int:
            if pid in _current_table_buy_in:
                return _current_table_buy_in[pid]
            knobs = bankroll_repo.load_personality_knobs(pid)
            threshold = round(table_min_buy_in * knobs.buy_in_multiplier)
            value = min(threshold, table_max_buy_in)
            _current_table_buy_in[pid] = value
            return value

        # Fake-sim: probability-gated roll BEFORE movement so a
        # won-big result can cascade into a stake_up in the same tick.
        # See cash_mode/fake_sim.py for the conservation + capping
        # rules. Chips actually mutate; ratification happens via the
        # existing leave-time AI cash-out (Path A) when a player
        # eventually sits at this table.
        fake_result: Optional[FakeHandResult] = None
        if rng.random() < fake_hand_prob:
            fake_result = roll_fake_hand(
                table.seats, big_blind=big_blind, rng=rng,
            )
            if fake_result.delta > 0:
                table.seats = fake_result.new_seats

        result = refresh_table_roster(
            table,
            idle_pool=idle_pool,
            eligible_candidates=eligible,
            seated_globally=seated_globally,
            bankroll_lookup=_bankroll_lookup,
            buy_in_lookup=_buy_in_for,
            rng=rng,
            now=now,
            stake_idx=stake_idx,
            table_min_buy_in=table_min_buy_in,
            table_max_buy_in=table_max_buy_in,
            next_tier_min_buy_in=next_tier_min_buy_in,
            live_fill_prob=live_fill_prob,
        )

        # Persist the table (always — last_activity_at bumps) and idle
        # pool changes.
        cash_table_repo.save_table(result.new_table, now=now)
        for change in result.idle_changes:
            if change.kind == "add" and change.entry is not None:
                cash_table_repo.save_idle(change.entry)
            elif change.kind == "remove":
                cash_table_repo.delete_idle(change.personality_id)

        # Emit lobby activity events from the refresh result.
        # `decisions` covers AIs that were on the table at the start
        # of the refresh; non-`stay` decisions correspond to leaves.
        # `freshly_seated_personality_ids` covers joins from idle pool
        # or live-fill from the eligible pool.
        _emit_activity_events(
            table=result.new_table,
            previous_table=table,
            decisions=result.decisions,
            freshly_seated_personality_ids=result.freshly_seated_personality_ids,
            personality_repo=personality_repo,
            now=now,
        )

        # Fake-sim big-win/loss events. Only emitted when the roll
        # crossed the big-event threshold (see fake_sim.py defaults).
        # Small chip drifts mutate state quietly without spamming
        # the ticker.
        if fake_result is not None and fake_result.big_event:
            _emit_fake_sim_events(
                table=result.new_table,
                fake_result=fake_result,
                personality_repo=personality_repo,
                now=now,
            )

        # Refresh idle_pool snapshot so the next iteration sees the
        # updated state (we may have added or removed entries).
        idle_pool = cash_table_repo.list_idle()

        out[table.table_id] = result

    return out


def _emit_activity_events(
    *,
    table,
    previous_table,
    decisions,
    freshly_seated_personality_ids,
    personality_repo,
    now: datetime,
) -> None:
    """Push lobby activity events to the in-memory ring buffer.

    Pulls display names from the personality repo. Wrapped in a
    broad except — the ticker is a UX nicety, not a correctness
    surface; if it fails for one event the lobby refresh shouldn't
    abort. Same defensive style as `try/except` around the relationship
    hint lookup in the lobby route.
    """
    from cash_mode.activity import (
        EVENT_JOIN,
        EVENT_LEAVE,
        LobbyEvent,
        format_join_message,
        format_leave_message,
        record_event,
    )

    def _name_for(pid: str) -> Optional[str]:
        try:
            personality = personality_repo.load_personality_by_id(pid)
        except Exception:
            return None
        if not personality:
            return None
        return personality.get("name") or pid

    stake = table.stake_label
    ts = now.isoformat()

    for pid, decision in decisions.items():
        if decision == "stay":
            continue
        name = _name_for(pid)
        if not name:
            continue
        try:
            record_event(LobbyEvent(
                type=EVENT_LEAVE,
                table_id=table.table_id,
                stake_label=stake,
                personality_id=pid,
                name=name,
                reason=decision,
                message=format_leave_message(name, stake, decision),
                created_at=ts,
            ))
        except Exception:
            # Buffer is best-effort. Don't let it break the refresh.
            pass

    for pid in freshly_seated_personality_ids:
        name = _name_for(pid)
        if not name:
            continue
        try:
            record_event(LobbyEvent(
                type=EVENT_JOIN,
                table_id=table.table_id,
                stake_label=stake,
                personality_id=pid,
                name=name,
                reason="",
                message=format_join_message(name, stake),
                created_at=ts,
            ))
        except Exception:
            pass


def _emit_fake_sim_events(
    *,
    table,
    fake_result,
    personality_repo,
    now: datetime,
) -> None:
    """Push paired big_win + big_loss events for a fake-sim roll.

    Both sides are recorded so future filtering (per-personality
    feeds, "show me losses only") works without re-deriving the
    pair. The lobby ticker today shows the most recent N events,
    so the user sees both rows next to each other ("Napoleon won
    $X off Bezos" / "Bezos dropped $X to Napoleon"). If that reads
    as duplicate noise in playtest, we can suppress the loss-side
    at the route layer.
    """
    from cash_mode.activity import (
        EVENT_BIG_LOSS,
        EVENT_BIG_WIN,
        LobbyEvent,
        format_big_loss_message,
        format_big_win_message,
        record_event,
    )

    winner_pid = fake_result.winner_pid
    loser_pid = fake_result.loser_pid
    if not winner_pid or not loser_pid:
        return

    def _name_for(pid: str) -> Optional[str]:
        try:
            personality = personality_repo.load_personality_by_id(pid)
        except Exception:
            return None
        if not personality:
            return None
        return personality.get("name") or pid

    winner_name = _name_for(winner_pid)
    loser_name = _name_for(loser_pid)
    if not winner_name or not loser_name:
        return

    stake = table.stake_label
    ts = now.isoformat()
    delta = int(fake_result.delta)

    try:
        record_event(LobbyEvent(
            type=EVENT_BIG_WIN,
            table_id=table.table_id,
            stake_label=stake,
            personality_id=winner_pid,
            name=winner_name,
            reason=loser_pid,  # opponent id for frontend grouping
            message=format_big_win_message(winner_name, loser_name, stake, delta),
            created_at=ts,
        ))
        record_event(LobbyEvent(
            type=EVENT_BIG_LOSS,
            table_id=table.table_id,
            stake_label=stake,
            personality_id=loser_pid,
            name=loser_name,
            reason=winner_pid,
            message=format_big_loss_message(loser_name, winner_name, stake, delta),
            created_at=ts,
        ))
    except Exception:
        # Buffer is best-effort.
        pass


def kill_all_cash_sessions(
    *,
    game_state_service,
    game_repo,
    cash_table_repo=None,
    bankroll_repo=None,
) -> int:
    """Boot reconcile: drop stale in-memory cash games; reset orphan seats.

    Cash sessions are now expected to *survive* a reboot — `progress_game`
    auto-saves cash rows on every step, the cold-load path in
    `/api/game-state/<id>` rehydrates them with cash-mode flags + AI
    controllers, and the player reconnects to their frozen table just
    like a tournament. This function used to wipe every `cash-*` row at
    boot (a v1.5-deploy hygiene step from before resume worked); that
    purge has been removed.

    What this still does:

      1. Drop every in-memory `cash_mode=True` game from
         `game_state_service`. A fresh process has no in-memory games,
         so this is effectively a no-op at startup, but it stays here
         so callers (e.g. tests) can use it to force-clear runtime
         state.

      2. Reconcile orphan `"human"` seats on persistent `cash_tables`.
         A seat is orphan when its `personality_id` (the owner) has no
         surviving `cash-*` row — typically because some other process
         deleted it. For each orphan: refund the seat's chips to the
         owner's bankroll and revert the slot to `open_slot()`. Without
         this, the lobby would render the player as still seated at a
         vanished table. Skipped when `cash_table_repo` and
         `bankroll_repo` are not provided (older test harnesses).

    Returns the count of in-memory cash sessions dropped (item 1) so
    the boot logger can report it. Orphan-seat resets are logged at
    INFO level individually.
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

    # Reconcile orphan human seats. A seat is orphan when its owner
    # has no surviving `cash-*` row — the lobby would otherwise render
    # the player as still seated at a vanished table.
    if cash_table_repo is not None and bankroll_repo is not None:
        from dataclasses import replace as _dc_replace
        try:
            rows = game_repo.list_games(owner_id=None, limit=10000, offset=0)
        except Exception as e:
            logger.warning("[CASH][LOBBY] list_games failed during reconcile: %s", e)
            rows = []
        owners_with_cash_row: Set[str] = {
            (row.owner_id or "")
            for row in rows
            if row.game_id.startswith("cash-") and row.owner_id
        }

        try:
            tables = cash_table_repo.list_all_tables()
        except Exception as e:
            logger.warning("[CASH][LOBBY] list_all_tables failed during reconcile: %s", e)
            tables = []

        for table in tables:
            for idx, slot in enumerate(table.seats):
                if slot.get("kind") != "human":
                    continue
                owner_id = slot.get("personality_id")
                if owner_id and owner_id in owners_with_cash_row:
                    # Seat backed by a real cash row — leave it intact
                    # so the player can resume on reconnect.
                    continue
                refund_chips = int(slot.get("chips", 0))
                try:
                    if owner_id and refund_chips > 0:
                        br = bankroll_repo.load_player_bankroll(owner_id)
                        if br is not None:
                            bankroll_repo.save_player_bankroll(
                                _dc_replace(br, chips=br.chips + refund_chips)
                            )
                    cash_table_repo.save_table(
                        table.with_seat(idx, open_slot())
                    )
                    logger.info(
                        "[CASH][LOBBY] kill_all_cash_sessions: reset orphan human seat "
                        "table=%r seat=%d owner=%r refunded=%d",
                        table.table_id, idx, owner_id, refund_chips,
                    )
                except Exception as e:
                    logger.warning(
                        "[CASH][LOBBY] failed to reset human seat "
                        "table=%r seat=%d: %s",
                        table.table_id, idx, e,
                    )

    if dropped:
        logger.info(
            "[CASH][LOBBY] kill_all_cash_sessions: dropped %d cash session(s) total",
            dropped,
        )
    return dropped
