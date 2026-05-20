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
from cash_mode.full_sim import (
    DEFAULT_HAND_SIM_PROB,
    HandSimResult,
    hand_burst_count,
    play_one_hand,
)
from cash_mode.movement import (
    DEFAULT_LIVE_FILL_PROB,
    RosterRefreshResult,
    refresh_table_roster,
)
from cash_mode.stakes_ladder import (
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


def _next_occupied_seat(
    seats: List[Dict[str, Any]], start_after: int,
) -> Optional[int]:
    """Find the next non-`open` seat clockwise from `start_after` (exclusive).

    Returns `None` when no seat is occupied. `start_after = -1` finds the
    first occupied seat starting at index 0.
    """
    n = len(seats)
    for offset in range(1, n + 1):
        idx = (start_after + offset) % n
        if seats[idx].get("kind") != "open":
            return idx
    return None


def get_dealer_index(table: CashTableState) -> Optional[int]:
    """Current dealer seat index for `table`, or `None` if all seats open.

    Reads `table.dealer_idx` (schema v96+) and self-heals when that
    points to a now-open seat (an AI left between refreshes — the
    button rolls forward to the next occupied seat). The in-memory
    mutation is a soft-correction for the read; the persistent value
    isn't rewritten here, so the heal stays read-only until the next
    refresh tick reseats the button via the engine path.
    """
    seats = table.seats
    idx = table.dealer_idx
    if 0 <= idx < len(seats) and seats[idx].get("kind") != "open":
        return idx
    return _next_occupied_seat(seats, start_after=-1)


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


def ensure_ai_bankrolls_seeded(
    *,
    personality_repo,
    bankroll_repo,
    sandbox_id: str,
    now: Optional[datetime] = None,
    user_id: Optional[str] = None,
    chip_ledger_repo=None,
) -> Dict[str, str]:
    """Idempotent bankroll seed for every cash-eligible personality.

    For each personality returned by `list_eligible_for_cash_mode`:
      - No `ai_bankroll_state` row → write
        `chips=knobs.starting_bankroll, last_regen_tick=now`.
      - Row exists with `last_regen_tick IS NULL` → repair to the same
        seeded state. This is the placeholder pattern that
        `save_emotional_state_json` leaves behind when the controller
        flushes psychology before the AI has ever been credited a
        starting bankroll. Without the repair, those rows sit at
        `chips=0` forever with no regen clock.
      - Row exists with `last_regen_tick` set → leave alone (live state).

    Returns `{personality_id: action}` where action is one of
    `"created"`, `"repaired"`, `"skipped"`. Useful for boot logs.

    Why this exists: the live-fill path in `refresh_table_roster`
    treats "no row" as "0 chips" (via `load_ai_bankroll_current`
    returning None and movement.py's `or 0`). The seed path uses
    `knobs.starting_bankroll` as a fallback. Without this helper, only the
    handful of personalities seeded into table seats at boot ever got
    rows — every personality added later was permanently locked out
    of live-fill. Calling this alongside `ensure_lobby_seeded` keeps
    every eligible personality usable.
    """
    if now is None:
        now = datetime.utcnow()

    eligible = personality_repo.list_eligible_for_cash_mode(user_id=user_id)
    actions: Dict[str, str] = {}
    for cand in eligible:
        pid = cand.get("personality_id")
        if not pid:
            continue
        knobs = bankroll_repo.load_personality_knobs(pid)
        stored = bankroll_repo.load_ai_bankroll(pid, sandbox_id=sandbox_id)
        needs_write = stored is None or stored.last_regen_tick is None
        if not needs_write:
            actions[pid] = "skipped"
            continue
        new_state = AIBankrollState(
            personality_id=pid,
            chips=knobs.starting_bankroll,
            last_regen_tick=now,
        )
        bankroll_repo.save_ai_bankroll(
            new_state,
            sandbox_id=sandbox_id,
            chip_ledger_repo=chip_ledger_repo,
        )
        if stored is None:
            actions[pid] = "created"
        else:
            actions[pid] = "repaired"
            # save_ai_bankroll only emits `ai_seed` on first-write —
            # the repair case (placeholder row at chips=0) writes a
            # non-zero chip count without auditing the mint. Emit
            # manually so the chip ledger stays balanced. The mint is
            # documenting chips that should have been seeded at
            # placeholder-row creation time (when
            # `save_emotional_state_json` inserted chips=0).
            if chip_ledger_repo is not None and new_state.chips > stored.chips:
                from core.economy import ledger as chip_ledger
                chip_ledger.record_ai_seed(
                    chip_ledger_repo,
                    personality_id=pid,
                    amount=new_state.chips - stored.chips,
                    context={
                        'site': 'ensure_ai_bankrolls_seeded',
                        'sandbox_id': sandbox_id,
                        'reason': 'placeholder_repair',
                    },
                    sandbox_id=sandbox_id,
                )
    n_created = sum(1 for a in actions.values() if a == "created")
    n_repaired = sum(1 for a in actions.values() if a == "repaired")
    if n_created or n_repaired:
        logger.info(
            "[CASH][LOBBY] bankroll seed: %d created, %d repaired, %d skipped",
            n_created, n_repaired, len(actions) - n_created - n_repaired,
        )
    return actions


def ensure_lobby_seeded(
    *,
    cash_table_repo,
    personality_repo,
    bankroll_repo,
    now: Optional[datetime] = None,
    user_id: Optional[str] = None,
    sandbox_id: Optional[str] = None,
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

    AI bankrolls ARE debited at seed time — `debit_bankroll_for_seat`
    pulls `ai_buy_in` chips from each AI's bankroll into the seat,
    keeping the chip-ledger audit's `actual_outstanding` invariant
    intact (chips move from `ai_bankrolls_stored` to
    `cash_table_seats_ai`, total unchanged, no ledger entry needed
    because it's a pure transfer between two non-bank pools).

    Idempotent boot pass: this function only debits when seating a
    NEW AI into a freshly-created table. Existing tables are
    preserved unchanged, so re-running ensure_lobby_seeded on a
    second boot doesn't double-debit. Tables created in this pass
    are recorded in `out_tables` so the boot hook can log them.

    Symmetric credit: when an AI leaves a seat (movement decision
    via `refresh_table_roster`), the corresponding `BankrollChange`
    of direction `from_seat` returns those chips to the bankroll
    via `credit_ai_cash_out`, which handles cap-clamp + ledger
    entries for any overflow.
    """
    if now is None:
        now = datetime.utcnow()

    existing_tables = cash_table_repo.list_all_tables(sandbox_id=sandbox_id)
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

            stored = bankroll_repo.load_ai_bankroll(pid, sandbox_id=sandbox_id)
            if stored is None:
                # No bankroll row yet — use the personality's cap as a
                # generous starting projection. Sit-down will write the
                # row at debit time.
                projected = knobs.starting_bankroll
            else:
                projected = project_bankroll(
                    stored, knobs.starting_bankroll, knobs.bankroll_rate, now,
                )
            if projected < ai_threshold:
                continue

            seat_position = next(position_iter)
            seats[seat_position] = ai_slot(pid, ai_buy_in)
            seated_globally.add(pid)
            filled += 1
            # Debit the AI's bankroll to fund their initial seat
            # chips. Without this debit the chip-ledger audit
            # double-counts (the comment above this loop explained the
            # original placeholder semantics). Pure transfer, no
            # ledger entry — `ai_bankrolls_stored` and
            # `cash_table_seats_ai` move in opposite directions by
            # `ai_buy_in`, preserving `actual_outstanding`.
            from cash_mode.bankroll import debit_bankroll_for_seat
            debit_bankroll_for_seat(
                bankroll_repo,
                pid,
                ai_buy_in,
                sandbox_id=sandbox_id,
            )
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
        cash_table_repo.save_table(new_state, sandbox_id=sandbox_id, now=now)
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
    sandbox_id: Optional[str] = None,
    live_fill_prob: float = DEFAULT_LIVE_FILL_PROB,
    hand_sim_prob: float = DEFAULT_HAND_SIM_PROB,
    chip_ledger_repo=None,
    # Phase 4: stake_repo + relationship_repo are required for the
    # take_stake interception. When either is None, take_stake never
    # fires and forced_leave behaves as it did pre-Phase-4 (preserves
    # behavior for the limited number of test callers that don't pass
    # them — tests for take_stake plumbing pass both).
    relationship_repo=None,
    stake_repo=None,
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

    tables = cash_table_repo.list_all_tables(sandbox_id=sandbox_id)
    idle_pool = cash_table_repo.list_idle(sandbox_id=sandbox_id)
    seated_globally = _global_seated_set(tables)
    eligible = personality_repo.list_eligible_for_cash_mode(user_id=user_id)

    def _bankroll_lookup(pid: str) -> Optional[int]:
        current = bankroll_repo.load_ai_bankroll_current(
            pid, sandbox_id=sandbox_id, now=now,
        )
        if current is not None:
            return current
        # No row yet — mirror the seed-path fallback so a personality
        # added between boot and the first lobby refresh isn't locked
        # out of live-fill. `ensure_ai_bankrolls_seeded` should have
        # written the row already; this is the defensive shim.
        return bankroll_repo.load_personality_knobs(pid).starting_bankroll

    # Phase 4 take_stake callbacks. Wired only when both stake_repo
    # and relationship_repo are provided. The borrower/lender profile
    # lookups always work — the gates inside find_ai_staker_for treat
    # missing config as defaults.
    _take_stake_enabled = (
        relationship_repo is not None and stake_repo is not None
    )

    def _borrower_profile_lookup(pid: str):
        return bankroll_repo.load_borrower_profile(pid)

    def _lender_profile_lookup(pid: str):
        return bankroll_repo.load_lender_profile(pid)

    def _relationship_lookup(observer_id: str, opponent_id: str):
        if relationship_repo is None:
            return None
        rel = relationship_repo.load_relationship_state(
            observer_id=observer_id, opponent_id=opponent_id, now=now,
        )
        if rel is None:
            return None
        return (rel.likability, rel.respect, rel.heat)

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

        # Full sim with catch-up burst (Commits 3-5): if the table
        # was last refreshed recently, fire at most one probability-
        # gated hand (existing behavior). If the lobby was unwatched
        # for longer than the burst threshold, simulate "the world
        # advanced while you were away" by running multiple hands
        # before this refresh tick returns. Cap is enforced per
        # `hand_burst_count` to keep the lobby response under the
        # 500 ms budget Phase 0 measured.
        gap_seconds = 0.0
        if table.last_activity_at is not None:
            gap_seconds = max(0.0, (now - table.last_activity_at).total_seconds())
        burst_n = hand_burst_count(
            gap_seconds=gap_seconds,
            base_prob=hand_sim_prob,
            rng=rng,
        )

        # Per-hand movement + fill: each sim hand drives one movement
        # evaluation and one live-fill roll per open seat. Replaces the
        # prior "one refresh after the whole burst" cadence, which made
        # fills tied to poll frequency rather than table activity.
        # Aggregate the per-hand results so persistence + event emission
        # below sees the full burst's worth of changes.
        from cash_mode.full_sim import _get_default_controller_cache
        controller_cache = _get_default_controller_cache()

        def _psych_lookup_sim(pid: str) -> Dict[str, Any]:
            ctrl = controller_cache.get(pid)
            if ctrl is None:
                return {}
            psych = getattr(ctrl, 'psychology', None)
            if psych is None:
                return {}
            try:
                zone = getattr(psych, 'primary_zone', 'neutral')
            except Exception:
                zone = 'neutral'
            try:
                intensity = min(1.0, float(psych.zone_effects.total_penalty_strength))
            except Exception:
                intensity = 0.0
            return {
                'energy': float(getattr(psych, 'energy', 0.5)),
                'zone': zone,
                'hands_in_detached_zone': int(getattr(ctrl, '_detached_hands', 0)),
                'emotional_intensity': intensity,
            }

        # Snapshot pre-burst table for _emit_activity_events below.
        # `table` gets reassigned each iteration to the post-hand result,
        # so without this snapshot the diff-aware event helper would see
        # the wrong "previous" state.
        previous_table_snapshot = table

        sim_results: List[HandSimResult] = []
        agg_decisions: Dict[str, str] = {}
        agg_idle_changes = []
        agg_bankroll_changes = []
        agg_freshly_seated: List[str] = []
        agg_rebuy_changes = []
        agg_stake_creations = []
        for _ in range(burst_n):
            # Rotate the dealer button to the next occupied seat for
            # this hand. Matters for seat-choice UX — when a player
            # opens the lobby, the visible button position tells them
            # what positional spots (UTG / CO / BTN / blinds) the open
            # seats correspond to. Without this, the in-engine dealer
            # would be seat 0 for every burst hand and the position
            # signal would be noise.
            current_dealer = get_dealer_index(table)
            next_dealer = _next_occupied_seat(
                table.seats,
                start_after=current_dealer if current_dealer is not None else -1,
            )

            r = play_one_hand(
                table.seats,
                big_blind=big_blind,
                rng=rng,
                sandbox_id=sandbox_id,
                name_for=_name_for_personality(personality_repo),
                starting_dealer_seat_idx=next_dealer,
                bankroll_repo=bankroll_repo,
                chip_ledger_repo=chip_ledger_repo,
                table_id=getattr(table, 'table_id', None),
            )
            if r.delta > 0:
                table.seats = r.new_seats
            # Persist the dealer position on the table state. The
            # subsequent `cash_table_repo.save_table` writes it to the
            # `cash_tables.dealer_idx` column (schema v96), so the
            # rotation survives backend restart. On a no-op hand
            # (table dropped below 2 AIs mid-burst), `dealer_seat_idx`
            # is None — leave the prior value in place so we don't
            # corrupt the position with a "no hand happened" marker.
            if r.dealer_seat_idx is not None:
                table.dealer_idx = r.dealer_seat_idx
            sim_results.append(r)

            # Advance detached counters for AI seats now that the hand
            # has resolved (their psychology reflects this hand's events).
            for slot in table.seats:
                if slot.get('kind') != 'ai':
                    continue
                pid = slot.get('personality_id')
                ctrl = controller_cache.get(pid) if pid else None
                if ctrl is None:
                    continue
                psych = getattr(ctrl, 'psychology', None)
                if psych is None:
                    continue
                try:
                    zone = getattr(psych, 'primary_zone', 'neutral')
                except Exception:
                    zone = 'neutral'
                prior = getattr(ctrl, '_detached_hands', 0)
                ctrl._detached_hands = (prior + 1) if zone == 'detached' else 0

            # Per-hand movement + fill. Each iteration sees the latest
            # seat state (chips updated by play_one_hand) and rolls
            # against the same pressure model used at seated tables.
            per_hand = refresh_table_roster(
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
                defer_freshly_vacated_live_fill=True,
                psych_lookup=_psych_lookup_sim,
                # Phase 4: intercept forced_leave with take_stake when
                # peer AIs are willing to fund the busting borrower.
                # Wired only when callers pass relationship_repo and
                # stake_repo — None inputs short-circuit the interception
                # back to plain forced_leave inside refresh_table_roster.
                borrower_profile_lookup=(
                    _borrower_profile_lookup if _take_stake_enabled else None
                ),
                lender_profile_lookup=(
                    _lender_profile_lookup if _take_stake_enabled else None
                ),
                relationship_lookup=(
                    _relationship_lookup if _take_stake_enabled else None
                ),
                stake_label=table.stake_label,
            )
            # Carry the post-hand table forward to the next iteration.
            table = per_hand.new_table
            # Refresh idle_pool snapshot from in-memory aggregates so the
            # next iteration's idle-candidate filter sees the latest.
            for ch in per_hand.idle_changes:
                if ch.kind == 'add' and ch.entry is not None:
                    idle_pool = [e for e in idle_pool if e.personality_id != ch.personality_id]
                    idle_pool.append(ch.entry)
                elif ch.kind == 'remove':
                    idle_pool = [e for e in idle_pool if e.personality_id != ch.personality_id]
            agg_decisions.update(per_hand.decisions)
            agg_idle_changes.extend(per_hand.idle_changes)
            agg_bankroll_changes.extend(per_hand.bankroll_changes)
            agg_freshly_seated.extend(per_hand.freshly_seated_personality_ids)
            agg_rebuy_changes.extend(per_hand.rebuy_changes)
            agg_stake_creations.extend(per_hand.stake_creations)

        # Synthesize a result object that the existing post-loop
        # persistence + event-emission code can consume unchanged.
        result = RosterRefreshResult(
            new_table=table,
            idle_changes=agg_idle_changes,
            freshly_seated_personality_ids=agg_freshly_seated,
            bankroll_changes=agg_bankroll_changes,
            decisions=agg_decisions,
            rebuy_changes=agg_rebuy_changes,
            stake_creations=agg_stake_creations,
        )

        # Persist the table (always — last_activity_at bumps) and idle
        # pool changes. The dealer button was advanced in real engine-
        # order inside the burst loop above (one rotation per sim hand,
        # synchronized with `play_one_hand`'s starting dealer), so we
        # don't need a separate `advance_dealer` step here.
        cash_table_repo.save_table(result.new_table, sandbox_id=sandbox_id, now=now)

        for change in result.idle_changes:
            if change.kind == "add" and change.entry is not None:
                cash_table_repo.save_idle(change.entry, sandbox_id=sandbox_id)
            elif change.kind == "remove":
                cash_table_repo.delete_idle(change.personality_id, sandbox_id=sandbox_id)

        # Apply bankroll ↔ seat transfers (closes the v1.5 lobby-seed
        # leak: live-fill used to mint chips on new seats without
        # deducting from the AI's bankroll). `to_seat` is a pure
        # transfer (no ledger entry); `from_seat` goes through
        # `credit_ai_cash_out` so regen commits and cap-clamp overflow
        # fires a ledger entry.
        from cash_mode.bankroll import (
            credit_ai_cash_out,
            debit_bankroll_for_seat,
        )
        for bc in result.bankroll_changes:
            if bc.direction == "to_seat":
                debit_bankroll_for_seat(
                    bankroll_repo, bc.personality_id, bc.amount,
                    sandbox_id=sandbox_id,
                )
            elif bc.direction == "from_seat":
                credit_ai_cash_out(
                    bankroll_repo,
                    bc.personality_id,
                    bc.amount,
                    now=now,
                    chip_ledger_repo=chip_ledger_repo,
                    ledger_context={
                        "site": "refresh_table_roster_vacate",
                        "table_id": result.new_table.table_id,
                    },
                )

        # Phase 4: apply AI-borrow stake creations. The seat refill
        # was already baked into result.new_table by refresh_table_roster
        # (the borrower's chips moved from chips_at_bust → principal,
        # and the from_seat above credited the borrower's bankroll
        # with chips_at_bust). What remains:
        #   - Debit the staker's bankroll by principal.
        #   - Persist a Stake row (status=active, both kinds personality).
        #   - Fire STAKE_OFFERED so the staker's relationship axes
        #     toward the borrower reflect the new tie.
        if result.stake_creations:
            from cash_mode.stakes import (
                BORROWER_KIND_PERSONALITY,
                STAKE_FORMAT_PURE,
                STAKE_STATUS_ACTIVE,
                STAKER_KIND_PERSONALITY,
                Stake,
            )
            import uuid

            for sc in result.stake_creations:
                debit_bankroll_for_seat(
                    bankroll_repo, sc.staker_id, sc.principal,
                    sandbox_id=sandbox_id,
                )
                stake = Stake(
                    stake_id=f"ai_stake_{uuid.uuid4().hex[:12]}",
                    session_id=f"ai_session_{sc.borrower_id}_{int(now.timestamp())}",
                    staker_id=sc.staker_id,
                    staker_kind=STAKER_KIND_PERSONALITY,
                    borrower_id=sc.borrower_id,
                    borrower_kind=BORROWER_KIND_PERSONALITY,
                    format=STAKE_FORMAT_PURE,
                    principal=sc.principal,
                    match_amount=0,
                    origination_fee=0,
                    cut=sc.cut,
                    status=STAKE_STATUS_ACTIVE,
                    carry_amount=0,
                    stake_tier=sc.stake_label,
                    created_at=now,
                )
                if stake_repo is not None:
                    stake_repo.create_stake(stake)
                if relationship_repo is not None:
                    try:
                        from poker.memory import OpponentModelManager
                        from poker.memory.relationship_events import (
                            RelationshipEvent,
                        )
                        mgr = OpponentModelManager(
                            relationship_repo=relationship_repo,
                        )
                        mgr.record_event(
                            actor_id=sc.staker_id,
                            target_id=sc.borrower_id,
                            event=RelationshipEvent.STAKE_OFFERED,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[CASH][LOBBY] STAKE_OFFERED event failed "
                            "staker=%r borrower=%r: %s",
                            sc.staker_id, sc.borrower_id, exc,
                        )

        # Emit lobby activity events from the refresh result.
        # `decisions` covers AIs that were on the table at the start
        # of the refresh; non-`stay` decisions correspond to leaves.
        # `freshly_seated_personality_ids` covers joins from idle pool
        # or live-fill from the eligible pool.
        _emit_activity_events(
            table=result.new_table,
            previous_table=previous_table_snapshot,
            decisions=result.decisions,
            freshly_seated_personality_ids=result.freshly_seated_personality_ids,
            personality_repo=personality_repo,
            now=now,
            sandbox_id=sandbox_id,
        )

        # Burst-aware event emission: pick at most one headline per
        # event type across the whole burst, then add a summary event
        # when hands were compressed. The per-type-per-burst cap is
        # the resolution recorded in the design doc's Q6.
        _emit_burst_events(
            table=result.new_table,
            sim_results=sim_results,
            personality_repo=personality_repo,
            now=now,
            sandbox_id=sandbox_id,
        )

        # Refresh idle_pool snapshot so the next iteration sees the
        # updated state (we may have added or removed entries).
        idle_pool = cash_table_repo.list_idle(sandbox_id=sandbox_id)

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
    sandbox_id: Optional[str] = None,
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
                sandbox_id=sandbox_id,
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
                sandbox_id=sandbox_id,
            ))
        except Exception:
            pass


def _name_for_personality(personality_repo) -> Callable[[str], str]:
    """Return a `pid -> display_name` resolver backed by the repo.

    Used by `play_one_hand` so the engine builds controllers with the
    right personality name (the TieredBotController looks up its
    config by name). Falls back to the personality_id on any miss so
    the engine still runs — controllers without a config get the
    default psychology, which is fine for sim purposes.
    """

    def _resolve(pid: str) -> str:
        try:
            personality = personality_repo.load_personality_by_id(pid)
        except Exception:
            return pid
        if not personality:
            return pid
        return personality.get("name") or pid

    return _resolve


def _emit_sim_events(
    *,
    table,
    sim_result,
    personality_repo,
    now: datetime,
    sandbox_id: Optional[str] = None,
) -> None:
    """Push paired big_win + big_loss events for a sim hand.

    Both sides are recorded so future filtering (per-personality
    feeds, "show me losses only") works without re-deriving the
    pair. The lobby ticker today shows the most recent N events,
    so the user sees both rows next to each other ("Napoleon won
    $X off Bezos" / "Bezos dropped $X to Napoleon"). Same shape
    as the predecessor fake-sim emission so the event contract on
    the wire is unchanged across the swap.
    """
    from cash_mode.activity import (
        EVENT_BIG_LOSS,
        EVENT_BIG_WIN,
        LobbyEvent,
        format_big_loss_message,
        format_big_win_message,
        record_event,
    )

    winner_pid = sim_result.winner_pid
    loser_pid = sim_result.loser_pid
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
    delta = int(sim_result.delta)

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
            sandbox_id=sandbox_id,
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
            sandbox_id=sandbox_id,
        ))
    except Exception:
        # Buffer is best-effort.
        pass


def _emit_burst_events(
    *,
    table,
    sim_results: List[HandSimResult],
    personality_repo,
    now: datetime,
    sandbox_id: Optional[str] = None,
) -> None:
    """Emit at most one event per type across a catch-up burst.

    The lobby ticker shows a small window; bursting 25 hands could
    flood it with 25 big_win events from one table and bury every
    other movement signal. Design Q6 (doc 2026-05-19) picked the
    per-burst per-table cap: at most one big_win/big_loss, one
    all_in, and one bust per refresh per table, plus an aggregate
    summary event when hands were compressed.

    Selection: the headline big_win across the burst is the hand
    with the largest delta. The headline all_in / bust are the first
    such events in the burst (chronological order — the user-facing
    framing reads "X shoved" once, not "X shoved 4 times").
    """
    if not sim_results:
        return

    # Pick the biggest big_event hand for the headline win/loss
    # emission. None when no hand in the burst crossed threshold.
    headline_big: Optional[HandSimResult] = None
    for r in sim_results:
        if not r.big_event:
            continue
        if headline_big is None or r.delta > headline_big.delta:
            headline_big = r

    if headline_big is not None:
        _emit_sim_events(
            table=table,
            sim_result=headline_big,
            personality_repo=personality_repo,
            now=now,
            sandbox_id=sandbox_id,
        )

    # Aggregate hand-level events across the burst, dedup'd by type.
    # `_emit_hand_events` already caps to one per type per call; we
    # just pass it the union of every burst hand's events.
    from cash_mode.full_sim import HandSimResult as _HSR

    aggregated_events = []
    for r in sim_results:
        aggregated_events.extend(r.hand_events)
    if aggregated_events:
        synthetic = _HSR(
            new_seats=sim_results[-1].new_seats,
            hand_events=aggregated_events,
        )
        _emit_hand_events(
            table=table,
            sim_result=synthetic,
            personality_repo=personality_repo,
            now=now,
            sandbox_id=sandbox_id,
        )

    # Summary event when more than one hand fired. Drops a single
    # "...and N more hands" line so the user knows the world ticked.
    if len(sim_results) > 1:
        _emit_burst_summary(
            table=table,
            sim_results=sim_results,
            personality_repo=personality_repo,
            now=now,
            sandbox_id=sandbox_id,
        )


def _emit_burst_summary(
    *,
    table,
    sim_results: List[HandSimResult],
    personality_repo,
    now: datetime,
    sandbox_id: Optional[str] = None,
) -> None:
    """Emit a single summary event for a multi-hand burst.

    "Top leader" is the personality with the largest cumulative net
    delta across the burst. When the burst was chip-neutral for
    everyone (rare — would need every hand to be near-zero), the
    summary degenerates to a plain hand-count phrase.
    """
    from cash_mode.activity import (
        EVENT_BURST_SUMMARY,
        LobbyEvent,
        format_burst_summary_message,
        record_event,
    )

    net_by_pid: Dict[str, int] = {}
    for r in sim_results:
        if not r.winner_pid or not r.loser_pid:
            continue
        net_by_pid[r.winner_pid] = net_by_pid.get(r.winner_pid, 0) + r.delta
        net_by_pid[r.loser_pid] = net_by_pid.get(r.loser_pid, 0) - r.delta

    top_pid: Optional[str] = None
    top_delta = 0
    for pid, net in net_by_pid.items():
        if abs(net) > abs(top_delta):
            top_pid = pid
            top_delta = net

    top_name: Optional[str] = None
    if top_pid:
        try:
            personality = personality_repo.load_personality_by_id(top_pid)
            if personality:
                top_name = personality.get("name") or top_pid
        except Exception:
            top_name = top_pid

    try:
        record_event(LobbyEvent(
            type=EVENT_BURST_SUMMARY,
            table_id=table.table_id,
            stake_label=table.stake_label,
            personality_id=top_pid or "",
            name=top_name or "",
            reason="",
            message=format_burst_summary_message(
                stake_label=table.stake_label,
                hands=len(sim_results),
                top_name=top_name,
                top_net_delta=top_delta,
            ),
            created_at=now.isoformat(),
            sandbox_id=sandbox_id,
        ))
    except Exception:
        pass


def _emit_hand_events(
    *,
    table,
    sim_result,
    personality_repo,
    now: datetime,
    sandbox_id: Optional[str] = None,
) -> None:
    """Translate `HandSimResult.hand_events` into `LobbyEvent`s.

    Per the design doc's resolved Q6, hand-level events use a
    per-burst per-table cap so a catch-up burst (Commit 5) of 25
    hands can't blow past 25 events for one table. v1 here emits
    AT MOST one event per type per table per refresh — `seen_types`
    enforces the cap. Commit 5 will replace this single-call cap
    with a burst-aware cap that operates across the whole hand
    sequence; today the per-tick cap is already in effect because
    only one hand fires per refresh.
    """
    from cash_mode.activity import (
        EVENT_ALL_IN,
        EVENT_BUST,
        LobbyEvent,
        format_all_in_message,
        format_bust_message,
        record_event,
    )
    from cash_mode.full_sim import HAND_EVENT_ALL_IN, HAND_EVENT_BUST

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
    seen_types: Set[str] = set()

    for evt in sim_result.hand_events:
        if evt.type in seen_types:
            continue
        name = _name_for(evt.personality_id)
        if not name:
            continue

        if evt.type == HAND_EVENT_ALL_IN:
            opponent_name = (
                _name_for(evt.opponent_pid) if evt.opponent_pid else None
            )
            try:
                record_event(LobbyEvent(
                    type=EVENT_ALL_IN,
                    table_id=table.table_id,
                    stake_label=stake,
                    personality_id=evt.personality_id,
                    name=name,
                    reason=evt.opponent_pid or "",
                    message=format_all_in_message(name, stake, opponent_name),
                    created_at=ts,
                    sandbox_id=sandbox_id,
                ))
                seen_types.add(evt.type)
            except Exception:
                pass

        elif evt.type == HAND_EVENT_BUST:
            try:
                record_event(LobbyEvent(
                    type=EVENT_BUST,
                    table_id=table.table_id,
                    stake_label=stake,
                    personality_id=evt.personality_id,
                    name=name,
                    reason=evt.opponent_pid or "",
                    message=format_bust_message(name, stake),
                    created_at=ts,
                    sandbox_id=sandbox_id,
                ))
                seen_types.add(evt.type)
            except Exception:
                pass


def kill_all_cash_sessions(
    *,
    game_state_service,
    game_repo,
    cash_table_repo=None,
    bankroll_repo=None,
    sandbox_id: Optional[str] = None,
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
    if (
        cash_table_repo is not None
        and bankroll_repo is not None
        and sandbox_id is not None
    ):
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
            tables = cash_table_repo.list_all_tables(sandbox_id=sandbox_id)
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
                        table.with_seat(idx, open_slot()),
                        sandbox_id=sandbox_id,
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
