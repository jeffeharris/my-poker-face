"""Pure-function lobby movement helpers — the "feel alive" core.

Two functions form the load-bearing piece of the Lobby v1.5 economy:

  - `evaluate_ai_movement(ai_chips, buy_in, projected_bankroll,
      stake_idx, rng)` → one of `stay`/`stake_up`/`take_break`/
      `forced_leave`/`bored_move`. Pure: same inputs always produce
      the same output (modulo rng).

  - `refresh_table_roster(table, idle_pool, eligible_personalities,
      seated_globally, bankroll_lookup, stake_lookup, rng, now,
      live_fill_prob, table_min_buy_in, table_max_buy_in,
      stake_label, ...)` → `(new_table, idle_changes,
      fresh_seated_personality_ids)`. Applies movement decisions to
      each AI seat and rolls live-fill probability on each open seat.

Both helpers are deliberately stripped of repository/Flask
dependencies — callers (cash routes, hand-boundary hook) plumb the
data and persist results themselves. This is the same convention
Path A's `credit_ai_cash_out` follows: pure-math helpers in
`cash_mode/`, route stitching in `flask_app/`.

Spec: `docs/plans/CASH_MODE_LOBBY_HANDOFF.md` §"Lobby maintenance".
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Set, Tuple

from cash_mode.stakes import STAKES_ORDER
from cash_mode.tables import (
    CashTableState,
    IdlePoolEntry,
    ai_slot,
    open_slot,
)

logger = logging.getLogger(__name__)


# --- Movement decision ---

# Default probabilistic constants. Tunable per call via the kwargs on
# `evaluate_ai_movement` so playtest can sweep without touching this
# module. Defaults match handoff §"Lobby maintenance".
DEFAULT_STAKE_UP_PROB = 0.30
DEFAULT_TAKE_BREAK_PROB = 0.10
DEFAULT_BORED_MOVE_PROB = 0.015
DEFAULT_LIVE_FILL_PROB = 0.15

# Thresholds for "won big" / "lost big" classification (as multiples
# of the AI's buy-in for this table).
BIG_WIN_RATIO = 2.0
BIG_LOSS_RATIO = 0.3


# Movement decision string literals (mirrors handoff §"Lobby maintenance"
# decision tree). Strings rather than an enum because they cross the
# pure-helper boundary and surface to logs / admin views as-is.
MovementDecision = str  # 'stay' | 'stake_up' | 'take_break' | 'forced_leave' | 'bored_move'


def evaluate_ai_movement(
    *,
    ai_chips: int,
    buy_in: int,
    projected_bankroll: int,
    stake_idx: int,
    next_tier_min_buy_in: Optional[int],
    rng: random.Random,
    stake_up_prob: float = DEFAULT_STAKE_UP_PROB,
    take_break_prob: float = DEFAULT_TAKE_BREAK_PROB,
    bored_move_prob: float = DEFAULT_BORED_MOVE_PROB,
) -> MovementDecision:
    """Decide what an AI does at their next hand boundary.

    Pure: rng is the only side-effect, and the caller owns it.

    Decision tree (handoff §"Lobby maintenance" b):

      1. `ai_chips <= BIG_LOSS_RATIO × buy_in` → `forced_leave`
         (busted or near-bust; needs to recover bankroll off-table).

      2. `ai_chips >= BIG_WIN_RATIO × buy_in`:
         - If a higher stake exists AND `projected_bankroll` affords
           its min buy-in: `stake_up_prob` chance to `stake_up`.
         - Otherwise: `take_break_prob` chance to `take_break`.
         - Otherwise: `stay`.

      3. Otherwise: `bored_move_prob` chance to `bored_move`
         (small base-rate cycling); else `stay`.

    `next_tier_min_buy_in` is None when this AI is already at the top
    of the stakes ladder — short-circuits the stake-up branch.
    """
    if buy_in <= 0:
        # Defensive: avoid div-by-zero / nonsense if a caller passes
        # a zero buy-in. Treat as "stay" — better to leave the AI
        # alone than misclassify them.
        return "stay"

    if ai_chips <= int(BIG_LOSS_RATIO * buy_in):
        return "forced_leave"

    if ai_chips >= int(BIG_WIN_RATIO * buy_in):
        can_stake_up = (
            next_tier_min_buy_in is not None
            and projected_bankroll >= next_tier_min_buy_in
        )
        if can_stake_up and rng.random() < stake_up_prob:
            return "stake_up"
        if rng.random() < take_break_prob:
            return "take_break"
        return "stay"

    if rng.random() < bored_move_prob:
        return "bored_move"
    return "stay"


# --- Roster refresh ---


@dataclass
class IdlePoolChange:
    """Describe one AI's movement into or out of the idle pool.

    Returned from `refresh_table_roster` so the calling route can
    persist the change without re-deriving the intent. `kind` is
    `'add'` (AI moved table → idle) or `'remove'` (AI moved idle →
    table). For `'add'`, `entry` is the IdlePoolEntry to write; for
    `'remove'`, `entry` is None and the personality_id alone is enough.
    """

    kind: str  # 'add' | 'remove'
    personality_id: str
    entry: Optional[IdlePoolEntry] = None


@dataclass
class RosterRefreshResult:
    """Bundle the outputs of a refresh pass.

    `new_table` is the updated CashTableState (always returned, even
    when no movement happened — the timestamp will still bump on save).
    `idle_changes` lists the moves the caller must persist to
    `cash_idle_pool`. `freshly_seated_personality_ids` is the set of
    AIs newly added to the table; the caller can use it to update the
    global "seated_globally" set if it tracks one.

    `decisions` is keyed by personality_id for the AIs that were on
    the table at the start of the refresh, with their MovementDecision.
    Useful for tests and logs.
    """

    new_table: CashTableState
    idle_changes: List[IdlePoolChange] = field(default_factory=list)
    freshly_seated_personality_ids: List[str] = field(default_factory=list)
    decisions: Dict[str, MovementDecision] = field(default_factory=dict)


def _movement_decision_to_idle_reason(decision: MovementDecision) -> str:
    """Map a movement decision to the corresponding idle-pool reason.

    Most decisions map verbatim (the strings line up). `'stake_up'`
    becomes `'stake_up_queued'` — the idle row carries the *queued*
    intent rather than the in-flight movement verb.
    """
    if decision == "stake_up":
        return "stake_up_queued"
    return decision


def refresh_table_roster(
    table: CashTableState,
    *,
    idle_pool: List[IdlePoolEntry],
    eligible_candidates: List[Dict[str, str]],
    seated_globally: Set[str],
    bankroll_lookup: Callable[[str], Optional[int]],
    buy_in_lookup: Callable[[str], int],
    rng: random.Random,
    now: datetime,
    stake_idx: int,
    table_min_buy_in: int,
    table_max_buy_in: int,
    next_tier_min_buy_in: Optional[int] = None,
    live_fill_prob: float = DEFAULT_LIVE_FILL_PROB,
    stake_up_prob: float = DEFAULT_STAKE_UP_PROB,
    take_break_prob: float = DEFAULT_TAKE_BREAK_PROB,
    bored_move_prob: float = DEFAULT_BORED_MOVE_PROB,
    defer_freshly_vacated_live_fill: bool = False,
) -> RosterRefreshResult:
    """Apply movement decisions to a table's AI seats, then live-fill opens.

    Pure-ish — the only side effect is `rng.random()` consumption. All
    repository access is plumbed through the lookup callables so the
    helper can run with a tempdb or a fake.

    Algorithm:

      1. For each AI seat: call `evaluate_ai_movement`. If the
         decision isn't `stay`, vacate the seat (kind → `"open"`) and
         add an `IdlePoolChange(kind='add', ...)` carrying the
         appropriate idle-pool reason.

      2. For each `"open"` seat (after step 1): roll
         `live_fill_prob`. If it triggers, try to seat an eligible AI
         (idle pool first, then `eligible_candidates`). Update
         `seated_globally` so the next seat's pick doesn't double-place.

    Notes on invariants:

      - The human seat (kind=='human') is never touched. The player's
        session controls that slot; the refresh hook is for AI-only
        movement.

      - `seated_globally` is the set of personality_ids currently
        occupying any cash_tables row's AI slot. Callers pass the
        global view (across all tables) so we don't seat the same AI
        twice. The set is mutated in-place — caller's responsibility
        if they want to keep an unmodified copy.

      - `idle_pool` is read-only here; the caller persists changes
        via `idle_changes`. We never silently drop or re-add idle rows
        — every move is reported.

    Live-fill candidate selection (handoff §"Roster refresh"):

      - First, scan `idle_pool` oldest-first; pick the first AI whose
        bankroll (via `bankroll_lookup`) affords this table's
        `buy_in_lookup` and whose `target_stake` either matches this
        table's stake or is None. On success, emit
        `IdlePoolChange(kind='remove', ...)`.

      - If no idle candidate qualifies, scan `eligible_candidates`
        (never-seated AIs in this lobby cycle) and pick the first
        affordable one.

      - If neither pool yields, the open seat stays open. That's fine
        — the next refresh tick rolls again.

    When `defer_freshly_vacated_live_fill=True`, seats vacated in step 1
    are skipped during step 2's live-fill pass. They stay open this
    tick and become candidates on the next refresh — the "feels less
    robotic" naturalism: a chair sits empty for at least one tick
    before someone new sits down.
    """
    new_seats = list(table.seats)
    idle_changes: List[IdlePoolChange] = []
    decisions: Dict[str, MovementDecision] = {}
    freshly_vacated: Set[int] = set()

    # Step 1: process AI seats.
    for i, slot in enumerate(new_seats):
        if slot["kind"] != "ai":
            continue
        pid = slot["personality_id"]
        ai_chips = int(slot.get("chips", 0))
        buy_in = buy_in_lookup(pid)
        projected = bankroll_lookup(pid) or 0
        decision = evaluate_ai_movement(
            ai_chips=ai_chips,
            buy_in=buy_in,
            projected_bankroll=projected,
            stake_idx=stake_idx,
            next_tier_min_buy_in=next_tier_min_buy_in,
            rng=rng,
            stake_up_prob=stake_up_prob,
            take_break_prob=take_break_prob,
            bored_move_prob=bored_move_prob,
        )
        decisions[pid] = decision
        if decision == "stay":
            continue
        # Vacate; record idle pool addition.
        new_seats[i] = open_slot()
        freshly_vacated.add(i)
        seated_globally.discard(pid)
        target_stake = None
        if decision == "stake_up" and stake_idx + 1 < len(STAKES_ORDER):
            target_stake = STAKES_ORDER[stake_idx + 1]
        idle_changes.append(IdlePoolChange(
            kind="add",
            personality_id=pid,
            entry=IdlePoolEntry(
                personality_id=pid,
                left_at=now,
                reason=_movement_decision_to_idle_reason(decision),
                target_stake=target_stake,
            ),
        ))

    # Step 2: live-fill open seats.
    freshly_seated: List[str] = []
    open_indices = [
        i for i, s in enumerate(new_seats)
        if s["kind"] == "open"
        and not (defer_freshly_vacated_live_fill and i in freshly_vacated)
    ]

    # Idle pool candidates (oldest first), filtered to those NOT
    # globally seated and whose `target_stake` allows this table.
    def _idle_candidate_filter(entry: IdlePoolEntry) -> bool:
        if entry.personality_id in seated_globally:
            return False
        if entry.target_stake is not None and entry.target_stake != table.stake_label:
            return False
        return True

    idle_candidates = [e for e in idle_pool if _idle_candidate_filter(e)]

    # Track candidates we already pulled from each pool so the next
    # open seat doesn't double-pick.
    used_idle: Set[str] = set()
    used_eligible: Set[str] = set()

    for seat_idx_local in open_indices:
        if rng.random() >= live_fill_prob:
            continue

        # Try idle pool.
        chosen = None
        for entry in idle_candidates:
            if entry.personality_id in used_idle:
                continue
            if entry.personality_id in seated_globally:
                continue
            buy_in = buy_in_lookup(entry.personality_id)
            projected = bankroll_lookup(entry.personality_id) or 0
            if projected < buy_in:
                continue
            chosen = ("idle", entry.personality_id, buy_in)
            used_idle.add(entry.personality_id)
            break

        # Try eligible-never-seated pool.
        if chosen is None:
            for cand in eligible_candidates:
                pid = cand.get("personality_id")
                if not pid:
                    continue
                if pid in seated_globally:
                    continue
                if pid in used_eligible:
                    continue
                buy_in = buy_in_lookup(pid)
                projected = bankroll_lookup(pid) or 0
                if projected < buy_in:
                    continue
                chosen = ("eligible", pid, buy_in)
                used_eligible.add(pid)
                break

        if chosen is None:
            continue

        source, pid, buy_in = chosen
        new_seats[seat_idx_local] = ai_slot(pid, buy_in)
        seated_globally.add(pid)
        freshly_seated.append(pid)
        if source == "idle":
            idle_changes.append(IdlePoolChange(
                kind="remove",
                personality_id=pid,
            ))

    new_table = CashTableState(
        table_id=table.table_id,
        stake_label=table.stake_label,
        seats=new_seats,
        created_at=table.created_at,
        last_activity_at=now,
    )
    return RosterRefreshResult(
        new_table=new_table,
        idle_changes=idle_changes,
        freshly_seated_personality_ids=freshly_seated,
        decisions=decisions,
    )
