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
from cash_mode.staker_history import StakerHistoryStats
from cash_mode.stakes import BORROWER_KIND_PERSONALITY
from cash_mode.stakes_ladder import (
    STAKES_LADDER,
    STAKES_ORDER,
    table_buy_in_window,
)
from cash_mode.staking_tier import TIER_HOUSE_ONLY, resolve_tier
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

    # Phase 4 Commit 4: build the cross-table staker candidate pool
    # ONCE per refresh, indexed by stake_label of the target table.
    # Each stake's pool includes (a) AIs seated at OTHER tables whose
    # `stake_comfort_zone` is the target stake or an adjacent stake,
    # plus (b) idle / eligible-but-never-seated AIs filtered the same
    # way. Computed lazily inside the per-table loop because adjacency
    # depends on the target stake_label.
    def _adjacent_stakes(label: str) -> List[str]:
        if label not in STAKES_ORDER:
            return []
        idx = STAKES_ORDER.index(label)
        return [
            STAKES_ORDER[i]
            for i in (idx - 1, idx, idx + 1)
            if 0 <= i < len(STAKES_ORDER)
        ]

    # Cache `stake_comfort_zone` per pid once per refresh. The knobs
    # are static for the duration of a lobby refresh, so loading them
    # per (pid, table) combo would do up to 5× the necessary work
    # across the per-table loop. Populated lazily on first access.
    _comfort_zone_cache: Dict[str, Optional[str]] = {}

    def _comfort_zone(pid: str) -> Optional[str]:
        if pid not in _comfort_zone_cache:
            try:
                knobs = bankroll_repo.load_personality_knobs(pid)
                _comfort_zone_cache[pid] = knobs.stake_comfort_zone
            except Exception:
                _comfort_zone_cache[pid] = None
        return _comfort_zone_cache[pid]

    def _cross_table_pool_for(target_stake_label: str, current_table_id: str) -> List[str]:
        if not _take_stake_enabled:
            return []
        adj = set(_adjacent_stakes(target_stake_label))
        if not adj:
            return []
        candidates: List[str] = []
        seen: set = set()
        # AIs at other tables.
        for other_table in tables:
            if other_table.table_id == current_table_id:
                continue
            for slot in other_table.seats:
                if slot.get("kind") != "ai":
                    continue
                pid = slot.get("personality_id")
                if not pid or pid in seen:
                    continue
                if _comfort_zone(pid) in adj:
                    candidates.append(pid)
                    seen.add(pid)
        # Idle pool AIs.
        for idle_entry in idle_pool:
            pid = idle_entry.personality_id
            if pid in seen:
                continue
            if _comfort_zone(pid) in adj:
                candidates.append(pid)
                seen.add(pid)
        # Eligible-never-seated personalities (already filtered to
        # cash-eligible upstream; we further filter by adjacency).
        for cand in eligible:
            pid = cand.get("personality_id")
            if not pid or pid in seen:
                continue
            if _comfort_zone(pid) in adj:
                candidates.append(pid)
                seen.add(pid)
        return candidates

    # In-memory set of pids that already received a `take_stake` this
    # lobby refresh. Stake rows aren't written until AFTER the burst
    # loop completes, so `load_active_for_borrower` returns stale
    # (None) within the burst. Without this guard, an AI that busts
    # twice across a multi-hand burst would get two stakes created in
    # `agg_stake_creations`, violating the one-active-stake invariant
    # and orphaning the earlier stake row.
    _burst_stake_creation_pids: set = set()

    def _borrower_profile_lookup(pid: str):
        # An AI already on an active stake can't take a new one — the
        # `one-active-stake-per-borrower` invariant would otherwise break
        # (orphaned active rows accumulate in the stakes table). Surface
        # this as "unwilling" to the take_stake interception so the AI
        # falls back to forced_leave + session-end settlement instead.
        profile = bankroll_repo.load_borrower_profile(pid)
        if not profile.willing:
            return profile
        from cash_mode.lender_profile import BorrowerProfile
        # Burst-local guard: was this pid already given a stake earlier
        # in the current refresh? (DB check below sees stale state.)
        if pid in _burst_stake_creation_pids:
            return BorrowerProfile(willing=False)
        if stake_repo is not None:
            existing = stake_repo.load_active_for_borrower(
                pid, "personality",
            )
            if existing is not None:
                return BorrowerProfile(willing=False)
        return profile

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

    # Staker-incentives plan: per-refresh history cache so the
    # weighted-selection path in find_ai_staker_for does at most one
    # `aggregate_history_for_staker` query per candidate per refresh,
    # regardless of how many busts/burst-hands surface that candidate.
    # Lifetime is one refresh — disposed when this function returns.
    _history_cache: Dict[str, Dict[str, StakerHistoryStats]] = {}

    def _history_for(staker_id: str):
        if stake_repo is None:
            return {}
        if staker_id not in _history_cache:
            try:
                _history_cache[staker_id] = (
                    stake_repo.aggregate_history_for_staker(staker_id)
                )
            except Exception as exc:
                logger.debug(
                    "[CASH][LOBBY] history aggregation failed staker=%r: %s",
                    staker_id, exc,
                )
                _history_cache[staker_id] = {}
        return _history_cache[staker_id]

    # Parallel cache for starting_bankroll values used by the excess-
    # pressure score. Static for the refresh duration since knobs come
    # from `personalities.config_json` which doesn't mutate mid-refresh.
    _starting_bankroll_cache: Dict[str, Optional[int]] = {}

    def _starting_bankroll_for(pid: str) -> Optional[int]:
        if pid not in _starting_bankroll_cache:
            try:
                knobs = bankroll_repo.load_personality_knobs(pid)
                _starting_bankroll_cache[pid] = knobs.starting_bankroll
            except Exception:
                _starting_bankroll_cache[pid] = None
        return _starting_bankroll_cache[pid]

    def _carry_lookup(staker_id: str, borrower_id: str) -> int:
        """Phase 4.5 Commit 1 — total outstanding carry borrower → staker.

        Returns 0 when the borrower has no carries with this staker, or
        when no stake_repo is wired (early tests / standalone callers).
        Used by `refresh_table_roster`'s `take_stake` branch to garnish
        the new stake's cut against the candidate's prior unpaid debt.
        """
        if stake_repo is None:
            return 0
        try:
            carries = stake_repo.list_carries_for_staker(staker_id)
        except Exception as exc:
            logger.debug(
                "[CASH][LOBBY] carry_lookup failed staker=%r borrower=%r: %s",
                staker_id, borrower_id, exc,
            )
            return 0
        return sum(
            int(c.carry_amount) for c in carries
            if c.borrower_id == borrower_id
        )

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

        # Phase 4.5 Commit 2 — tier-gated take_stake. Wrap the borrower
        # lookup with a per-table tier check so an AI whose carry-load
        # has pushed them to `house_only` at this stake can't intercept
        # `forced_leave` with a peer-staked seat refill. Without this
        # gate, an over-leveraged AI keeps qualifying for peer stakes
        # purely because lender filters don't read the borrower's tier
        # — the runaway-debt failure mode. House-staker fallback is
        # NOT applied here (movement-time is the wrong layer for
        # sponsor-flow selection); over-tier AIs just `forced_leave`
        # normally and may try again at a lower-stake table later.
        _target_stake_label = table.stake_label

        def _borrower_lookup_for_table(
            pid: str, _stake=_target_stake_label,
        ):
            profile = _borrower_profile_lookup(pid)
            if not profile.willing:
                return profile
            if stake_repo is None:
                return profile
            try:
                tier = resolve_tier(
                    borrower_id=pid,
                    borrower_kind=BORROWER_KIND_PERSONALITY,
                    current_stake_label=_stake,
                    stake_repo=stake_repo,
                )
            except Exception as exc:
                logger.debug(
                    "[CASH][LOBBY] tier resolution failed pid=%r: %s",
                    pid, exc,
                )
                return profile
            if tier == TIER_HOUSE_ONLY:
                from cash_mode.lender_profile import BorrowerProfile
                return BorrowerProfile(willing=False)
            return profile

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
                    _borrower_lookup_for_table if _take_stake_enabled else None
                ),
                lender_profile_lookup=(
                    _lender_profile_lookup if _take_stake_enabled else None
                ),
                relationship_lookup=(
                    _relationship_lookup if _take_stake_enabled else None
                ),
                stake_label=table.stake_label,
                # Phase 4 Commit 4: cross-table candidate pool. The
                # per-table loop sees the pre-loop snapshot of other
                # tables / idle pool, which is good enough — a staker
                # picked here might have also moved this tick at their
                # own table, but the bankroll lookup re-checks capacity
                # so a now-broke AI wouldn't qualify even if they
                # appeared in this list.
                cross_table_staker_pids=_cross_table_pool_for(
                    table.stake_label, table.table_id,
                ),
                # Phase 4.5 Commit 1: per-staker garnishment for AI
                # borrowers. Only meaningful when stake_repo is wired
                # (else the lookup returns 0 and the cut stays at
                # rate_anchor as before).
                carry_lookup=(
                    _carry_lookup if _take_stake_enabled else None
                ),
                # Staker-incentives plan: weighted candidate selection
                # in find_ai_staker_for. Wired only when stake_repo is
                # available; otherwise the matcher falls back to its
                # legacy uniform-random pick.
                history_lookup=(
                    _history_for if _take_stake_enabled else None
                ),
                starting_bankroll_lookup=(
                    _starting_bankroll_for if _take_stake_enabled else None
                ),
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
            # Update the burst-local set so subsequent hands within
            # this same burst can't double-stake the same borrower.
            for sc in per_hand.stake_creations:
                _burst_stake_creation_pids.add(sc.borrower_id)

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

        # Phase 4 Commit 3: settle AI-borrower stakes BEFORE the normal
        # from_seat credit fires. When an AI with an active stake row
        # leaves, the chips on their seat need to be split per the
        # stake's `cut` between staker and borrower — not credited
        # whole to the borrower. We compute the settlement flows here
        # and record which `from_seat` BankrollChange index was
        # consumed by the settlement so the from_seat loop below
        # skips ONLY that entry (not all from_seat entries for the
        # same pid — a take_stake earlier in the burst emits its
        # own from_seat for the bust chips, which must still credit).
        settled_from_seat_indices: set = set()
        if stake_repo is not None:
            from cash_mode.stake_chip_flow import (
                DIRECTION_BORROWER_SEAT_TO_BORROWER_BANKROLL,
                DIRECTION_BORROWER_SEAT_TO_STAKER_BANKROLL,
                build_stake_settlement_flows,
            )
            from cash_mode.stake_settlement import settle_stake_on_leave
            from cash_mode.stakes import (
                BORROWER_KIND_PERSONALITY,
                STAKE_STATUS_CARRY,
                STAKER_KIND_HUMAN,
            )

            # Find the LAST from_seat per pid — that's the session-end
            # leave amount (any earlier from_seat for the same pid is
            # the take_stake bust-chips return, which must still
            # credit the bankroll normally).
            last_from_seat_index: Dict[str, int] = {}
            for i, bc in enumerate(result.bankroll_changes):
                if bc.direction == "from_seat":
                    last_from_seat_index[bc.personality_id] = i

            for pid, idx in last_from_seat_index.items():
                chips_at_leave = result.bankroll_changes[idx].amount
                # Was this AI a stake borrower? Look up active stake.
                active_stake = stake_repo.load_active_for_borrower(
                    pid, BORROWER_KIND_PERSONALITY,
                )
                if active_stake is None:
                    continue
                settlement = settle_stake_on_leave(
                    active_stake.stake_id, chips_at_leave,
                    stake_repo=stake_repo,
                    chip_ledger_repo=chip_ledger_repo,
                    ledger_context={
                        "site": "ai_session_end",
                        "table_id": result.new_table.table_id,
                    },
                    sandbox_id=sandbox_id,
                    now=now,
                )
                if settlement is None:
                    continue
                # Apply the settlement flows. For AI-staker / AI-borrower
                # personality stakes the flows are pure bankroll→bankroll
                # transfers — no ledger entry, mirror the route's leave
                # path.
                flows = build_stake_settlement_flows(settlement)
                for flow in flows:
                    if flow.direction == DIRECTION_BORROWER_SEAT_TO_STAKER_BANKROLL:
                        # Phase 5 Commit 3 — human-staker branch. When
                        # the staker is the player, credit their
                        # player_bankroll_state row instead of
                        # credit_ai_cash_out (which would mis-route the
                        # chips into an AI bankroll). Read-modify-write
                        # so concurrent leaves don't lose the credit.
                        if flow.staker_kind == STAKER_KIND_HUMAN:
                            from cash_mode.bankroll import PlayerBankrollState
                            existing = bankroll_repo.load_player_bankroll(
                                flow.staker_id,
                            )
                            if existing is not None:
                                bankroll_repo.save_player_bankroll(
                                    PlayerBankrollState(
                                        player_id=existing.player_id,
                                        chips=existing.chips + flow.amount,
                                        starting_bankroll=existing.starting_bankroll,
                                    ),
                                )
                            else:
                                logger.warning(
                                    "[CASH][LOBBY] human staker bankroll "
                                    "missing for credit staker=%r stake=%r",
                                    flow.staker_id, active_stake.stake_id,
                                )
                        else:
                            from cash_mode.bankroll import credit_ai_cash_out
                            credit_ai_cash_out(
                                bankroll_repo, flow.staker_id, flow.amount,
                                sandbox_id=sandbox_id,
                                now=now,
                                chip_ledger_repo=chip_ledger_repo,
                                ledger_context={
                                    "stake_id": active_stake.stake_id,
                                    "site": "ai_stake_settle_staker",
                                },
                            )
                    elif flow.direction == DIRECTION_BORROWER_SEAT_TO_BORROWER_BANKROLL:
                        from cash_mode.bankroll import credit_ai_cash_out
                        credit_ai_cash_out(
                            bankroll_repo, flow.borrower_id, flow.amount,
                            sandbox_id=sandbox_id,
                            now=now,
                            chip_ledger_repo=chip_ledger_repo,
                            ledger_context={
                                "stake_id": active_stake.stake_id,
                                "site": "ai_stake_settle_borrower",
                            },
                        )
                # Fire repaid/defaulted event. Carry rolls forward
                # silently on the relationship axes — only the
                # explicit STAKE_DEFAULTED action would fire the
                # sharper hit; natural carry is just a status='carry'
                # row. Phase 4 Commit 5: when status is carry, emit
                # an EVENT_AI_DEFAULT to the lobby ticker so the
                # player sees the moment-of-default drama even though
                # no axis-shift event fires.
                if (
                    relationship_repo is not None
                    and settlement.new_status != STAKE_STATUS_CARRY
                    and settlement.staker_id is not None
                    and settlement.forgiven_amount == 0
                ):
                    try:
                        from poker.memory import OpponentModelManager
                        from poker.memory.relationship_events import (
                            RelationshipEvent,
                        )
                        mgr = OpponentModelManager(
                            relationship_repo=relationship_repo,
                        )
                        mgr.record_event(
                            actor_id=settlement.staker_id,
                            target_id=settlement.borrower_id,
                            event=RelationshipEvent.STAKE_REPAID,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[CASH][LOBBY] STAKE_REPAID event failed "
                            "stake=%r: %s", active_stake.stake_id, exc,
                        )
                if (
                    settlement.new_status == STAKE_STATUS_CARRY
                    and settlement.carry_amount >= AI_STAKE_TICKER_THRESHOLD
                    and settlement.staker_id is not None
                ):
                    try:
                        from cash_mode.activity import (
                            EVENT_AI_DEFAULT,
                            LobbyEvent,
                            format_ai_default_message,
                            record_event,
                        )
                        staker_name = _ticker_name_for(
                            settlement.staker_id, personality_repo,
                        )
                        borrower_name = _ticker_name_for(
                            settlement.borrower_id, personality_repo,
                        )
                        if staker_name and borrower_name:
                            record_event(LobbyEvent(
                                type=EVENT_AI_DEFAULT,
                                table_id=result.new_table.table_id,
                                stake_label=active_stake.stake_tier,
                                personality_id=settlement.borrower_id,
                                name=borrower_name,
                                reason=settlement.staker_id,
                                message=format_ai_default_message(
                                    borrower_name, staker_name,
                                    active_stake.stake_tier,
                                    settlement.carry_amount,
                                ),
                                created_at=now.isoformat(),
                                sandbox_id=sandbox_id,
                            ))
                    except Exception as exc:
                        logger.warning(
                            "[CASH][LOBBY] EVENT_AI_DEFAULT emit failed: %s",
                            exc,
                        )
                settled_from_seat_indices.add(idx)

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
        for i, bc in enumerate(result.bankroll_changes):
            if bc.direction == "to_seat":
                debit_bankroll_for_seat(
                    bankroll_repo, bc.personality_id, bc.amount,
                    sandbox_id=sandbox_id,
                )
            elif bc.direction == "from_seat":
                # Skip ONLY the specific from_seat entry the
                # settlement consumed; earlier from_seat entries for
                # the same pid (take_stake bust chips) still credit.
                if i in settled_from_seat_indices:
                    continue
                credit_ai_cash_out(
                    bankroll_repo,
                    bc.personality_id,
                    bc.amount,
                    sandbox_id=sandbox_id,
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
        #   - Emit EVENT_AI_STAKE on the lobby ticker (Commit 5).
        from cash_mode.activity import AI_STAKE_TICKER_THRESHOLD

        def _ticker_name_for(pid: str, personality_repo) -> Optional[str]:
            try:
                personality = personality_repo.load_personality_by_id(pid)
            except Exception:
                return None
            if not personality:
                return None
            return personality.get("name") or pid

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
                # Phase 4 Commit 5: emit ticker event for stakes above
                # the threshold so the player sees the AI economy
                # moving. Smaller stakes (at $2/$10) fire silently —
                # state mutates, but the ticker stays focused on
                # higher-stakes drama.
                if sc.principal >= AI_STAKE_TICKER_THRESHOLD:
                    try:
                        from cash_mode.activity import (
                            EVENT_AI_STAKE,
                            LobbyEvent,
                            format_ai_stake_message,
                            record_event,
                        )
                        staker_name = _ticker_name_for(
                            sc.staker_id, personality_repo,
                        )
                        borrower_name = _ticker_name_for(
                            sc.borrower_id, personality_repo,
                        )
                        if staker_name and borrower_name:
                            record_event(LobbyEvent(
                                type=EVENT_AI_STAKE,
                                table_id=result.new_table.table_id,
                                stake_label=sc.stake_label,
                                personality_id=sc.staker_id,
                                name=staker_name,
                                reason=sc.borrower_id,
                                message=format_ai_stake_message(
                                    staker_name, borrower_name,
                                    sc.stake_label, sc.principal,
                                ),
                                created_at=now.isoformat(),
                                sandbox_id=sandbox_id,
                            ))
                    except Exception as exc:
                        logger.warning(
                            "[CASH][LOBBY] EVENT_AI_STAKE emit failed: %s",
                            exc,
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

    # Phase 4.5 Commits 3-5: AI-initiated carry resolution. Runs once
    # per lobby refresh, after every table has been processed. Iterates
    # AIs with outstanding carries (single bulk query) and rolls
    # voluntary payoff / forgiveness ask / explicit default per the
    # handoff spec. Best-effort — failures here don't affect the
    # table-refresh side effects above.
    if stake_repo is not None:
        try:
            from cash_mode.ai_carry_resolution import resolve_ai_carries

            def _energy_lookup(pid: str) -> float:
                """Resolve an AI's energy for the explicit-default
                pressure formula. Cache lookup first (active sessions);
                fall back to persisted emotional_state_json for idle
                AIs; neutral 0.5 default for never-played AIs.

                Best-effort — any failure returns 0.5 so the pressure
                math still proceeds with neutral energy. The carry
                resolution surface tolerates this gracefully (other
                pressure signals dominate when energy is unknown).
                """
                from cash_mode.full_sim import _get_default_controller_cache
                cache = _get_default_controller_cache()
                ctrl = cache.get(pid)
                if ctrl is not None:
                    psych = getattr(ctrl, 'psychology', None)
                    if psych is not None:
                        try:
                            return float(getattr(psych, 'energy', 0.5))
                        except Exception:
                            return 0.5
                try:
                    blob = bankroll_repo.load_emotional_state_json(
                        pid, sandbox_id=sandbox_id,
                    )
                except Exception:
                    return 0.5
                if not blob:
                    return 0.5
                try:
                    import json as _json
                    state_dict = _json.loads(blob)
                    return float(state_dict.get('energy', 0.5))
                except Exception:
                    return 0.5

            batch = resolve_ai_carries(
                bankroll_repo=bankroll_repo,
                stake_repo=stake_repo,
                relationship_repo=relationship_repo,
                chip_ledger_repo=chip_ledger_repo,
                sandbox_id=sandbox_id,
                energy_lookup=_energy_lookup,
                rng=rng,
                now=now,
            )
            _emit_carry_resolution_events(
                batch=batch,
                personality_repo=personality_repo,
                stake_repo=stake_repo,
                now=now,
                sandbox_id=sandbox_id,
            )
        except Exception as exc:
            logger.warning(
                "[CASH][LOBBY] AI carry resolution failed: %s", exc,
            )

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


def _emit_carry_resolution_events(
    *,
    batch,
    personality_repo,
    stake_repo,
    now: datetime,
    sandbox_id: Optional[str] = None,
) -> None:
    """Translate a CarryResolutionBatch into LobbyEvents.

    Phase 4.5 Commits 3-5 — surfaces payoff / forgiveness / default
    outcomes to the lobby ticker. Threshold-gated by
    `AI_CARRY_TICKER_THRESHOLD` (mirror of `AI_STAKE_TICKER_THRESHOLD`)
    so small-stake resolutions stay invisible. Refused forgiveness
    asks are intentionally silent — the axis shift is enough drama;
    every refusal in the ticker would be noise.

    Best-effort: ring-buffer failures don't propagate.
    """
    if not batch or not batch.results:
        return

    from cash_mode.activity import (
        AI_CARRY_TICKER_THRESHOLD,
        EVENT_AI_DEFAULT,
        EVENT_AI_FORGIVEN,
        EVENT_AI_PAYOFF,
        LobbyEvent,
        format_ai_explicit_default_message,
        format_ai_forgiven_message,
        format_ai_payoff_message,
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

    ts = now.isoformat()

    for result in batch.results:
        if result.amount < AI_CARRY_TICKER_THRESHOLD:
            continue
        borrower_name = _name_for(result.borrower_id)
        staker_name = _name_for(result.staker_id)
        if not borrower_name or not staker_name:
            continue

        if result.kind == 'payoff':
            event_type = EVENT_AI_PAYOFF
            message = format_ai_payoff_message(
                borrower_name, staker_name, result.stake_tier, result.amount,
            )
            actor_pid = result.borrower_id
            counterparty_pid = result.staker_id
            actor_name = borrower_name
        elif result.kind == 'forgiven':
            event_type = EVENT_AI_FORGIVEN
            message = format_ai_forgiven_message(
                staker_name, borrower_name, result.stake_tier, result.amount,
            )
            # The staker is the actor in a grant (they chose to forgive),
            # so the event indexes by staker_id. Mirrors how `ai_stake`
            # uses the staker as the actor and the borrower as `reason`.
            actor_pid = result.staker_id
            counterparty_pid = result.borrower_id
            actor_name = staker_name
        elif result.kind == 'default':
            event_type = EVENT_AI_DEFAULT
            message = format_ai_explicit_default_message(
                borrower_name, staker_name, result.stake_tier, result.amount,
            )
            actor_pid = result.borrower_id
            counterparty_pid = result.staker_id
            actor_name = borrower_name
        else:
            # forgiveness_refused — silent on the ticker by design.
            continue

        try:
            record_event(LobbyEvent(
                type=event_type,
                table_id="",  # carry resolutions aren't table-scoped
                stake_label=result.stake_tier,
                personality_id=actor_pid,
                name=actor_name,
                reason=counterparty_pid,
                message=message,
                created_at=ts,
                sandbox_id=sandbox_id,
            ))
        except Exception as exc:
            logger.warning(
                "[CASH][LOBBY] carry resolution event emit failed (%s): %s",
                result.kind, exc,
            )


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
