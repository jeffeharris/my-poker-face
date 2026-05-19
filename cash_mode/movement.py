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
import threading
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from cash_mode.stakes_ladder import STAKES_ORDER
from cash_mode.tables import (
    CashTableState,
    IdlePoolEntry,
    ai_slot,
    open_slot,
)

logger = logging.getLogger(__name__)


# --- Movement decision ---

# Pressure-driven movement (spec: docs/plans/CASH_MODE_MOVEMENT_PRESSURE_DESIGN.md).
# Each AI's per-hand leave probability is `pressure / (pressure + LEAVE_K)`
# where pressure accumulates from four signals weighted below.
W_STAKE_UP = 0.5    # stack ≥ max_buy_in → eager to book the win
W_SHORT = 0.6       # stack < min_buy_in → tilt walk or rebuy
W_DETACHED = 0.3    # hands spent in 'detached' zone (folding too much)
W_TENURE = 0.2      # tired (low energy)
LEAVE_K = 2.0       # curve shape: at pressure=1.0, leave prob ≈ 0.33

# Hard floor for `forced_leave` — busted AIs gone regardless of pressure.
# Anchored to the table's min buy-in (not the AI's current buy-in) so the
# threshold is table-relative and doesn't drift if the AI bought in below max.
FORCED_LEAVE_RATIO = 0.3

# Per-hand fill probability per open seat. Replaces the per-poll roll;
# now ticks once per real or sim hand. With 2 opens this averages ~10
# hands between fills, which feels like a live cash room rhythm.
DEFAULT_LIVE_FILL_PROB = 0.05

# Rebuy bucket weights (base, before bias). Picked by weighted_random
# after a short-stack leave-vs-rebuy roll lands on 'rebuy'.
REBUY_BASE_WEIGHTS = {"min": 40.0, "mid": 40.0, "max": 20.0}

# Minimum cooldown seconds between an AI leaving a table and being
# eligible to refill the same table. The pressure-derived variable
# extension on top of this is computed at leave time.
MIN_COOLDOWN_SECONDS = 10


# Movement decision string literals. Strings rather than an enum because
# they cross the pure-helper boundary and surface to logs / admin views
# as-is. `rebuy` is new — the AI tops up at the same seat instead of
# leaving.
MovementDecision = str  # 'stay' | 'stake_up' | 'take_break' | 'forced_leave' | 'bored_move' | 'rebuy'


@dataclass(frozen=True)
class MovementContext:
    """Per-AI snapshot used to compute movement pressure.

    Built once per hand boundary per seated AI. Psychology fields default
    to neutral so callers without live psych access (early tests, simple
    paths) still get coherent behavior — the AI just won't show
    detached/tenure pressure.
    """

    ai_chips: int
    min_buy_in: int
    max_buy_in: int
    projected_bankroll: int
    stake_idx: int
    next_tier_min_buy_in: Optional[int]
    # Psychology-derived (live controller). Defaults make a "neutral" AI.
    energy: float = 0.5                   # 0 = exhausted, 1 = fresh
    zone: str = ""                        # 'detached' triggers detached pressure
    hands_in_detached_zone: int = 0       # consecutive hands in detached zone
    emotional_intensity: float = 0.0      # 0..1 — biases rebuy bucket toward min when high


def compute_leave_pressure(ctx: MovementContext) -> Dict[str, float]:
    """Return the four weighted pressure components.

    Keyed by signal name so callers can introspect (logging, tests).
    Total pressure is `sum(values)`. Leave probability via
    `pressure / (pressure + LEAVE_K)`.
    """
    min_bi = max(1, ctx.min_buy_in)
    max_bi = max(1, ctx.max_buy_in)
    stake_up_raw = max(0.0, ctx.ai_chips / max_bi - 1.0)
    short_raw = max(0.0, 1.0 - ctx.ai_chips / min_bi)
    detached_raw = (
        (ctx.hands_in_detached_zone / 8.0)
        if ctx.zone == "detached" else 0.0
    )
    # Tenure only kicks in once energy drops below 0.5. At energy=0.5
    # the AI is "neutral" and contributes 0 to leave pressure; at
    # energy=0 ("exhausted") tenure_raw=1.0. Without this gate, a fresh
    # default-0.5 AI generated ~5% leaves/hand just from tenure.
    tenure_raw = max(0.0, (0.5 - ctx.energy) * 2.0)
    return {
        "stake_up": W_STAKE_UP * stake_up_raw,
        "short": W_SHORT * short_raw,
        "detached": W_DETACHED * detached_raw,
        "tenure": W_TENURE * tenure_raw,
    }


def evaluate_ai_movement(
    ctx: MovementContext,
    rng: random.Random,
) -> MovementDecision:
    """Decide what an AI does at their next hand boundary.

    Pure: rng is the only side-effect, and the caller owns it.

    Decision flow:
      1. `forced_leave` if stack ≤ `FORCED_LEAVE_RATIO × min_buy_in`
         (hard floor, no pressure roll).
      2. Compute leave pressure from stack position, detached zone,
         and energy. Roll `pressure / (pressure + LEAVE_K)` for leave.
      3. If staying: return `stay`.
      4. If leaving: pick the dominant pressure source to decide
         direction:
         - `short` → leave-vs-rebuy split (see `decide_leave_or_rebuy`).
           Returns `rebuy` or `take_break`.
         - `stake_up` → `stake_up` if a higher tier is affordable,
           else `take_break`.
         - `detached` or `tenure` → `bored_move`.
    """
    if ctx.min_buy_in <= 0:
        return "stay"

    if ctx.ai_chips <= int(FORCED_LEAVE_RATIO * ctx.min_buy_in):
        return "forced_leave"

    pressures = compute_leave_pressure(ctx)
    total = sum(pressures.values())
    if total <= 0:
        return "stay"

    leave_prob = total / (total + LEAVE_K)
    if rng.random() >= leave_prob:
        return "stay"

    dominant = max(pressures, key=pressures.get)
    if dominant == "short":
        return "rebuy" if decide_leave_or_rebuy(ctx, rng) == "rebuy" else "take_break"
    if dominant == "stake_up":
        can_stake_up = (
            ctx.next_tier_min_buy_in is not None
            and ctx.projected_bankroll >= ctx.next_tier_min_buy_in
        )
        return "stake_up" if can_stake_up else "take_break"
    return "bored_move"


def decide_leave_or_rebuy(
    ctx: MovementContext,
    rng: random.Random,
) -> str:
    """Weighted split between 'leave' and 'rebuy' for short-stack AIs.

    Flush/engaged → rebuy. Tired/broke → leave. Weights:
      - leave: base 1 + bonus from low energy + bonus from low bankroll
      - rebuy: base 1 + bonus from high bankroll
    """
    min_bi = max(1, ctx.min_buy_in)
    low_bankroll_signal = max(0.0, 1.0 - ctx.projected_bankroll / min_bi)
    high_bankroll_signal = min(1.0, ctx.projected_bankroll / (min_bi * 3))
    leave_w = 1.0 + 1.5 * (1.0 - ctx.energy) + 1.5 * low_bankroll_signal
    rebuy_w = 1.0 + 1.5 * high_bankroll_signal
    return "rebuy" if rng.random() < rebuy_w / (leave_w + rebuy_w) else "leave"


def pick_rebuy_amount(
    ctx: MovementContext,
    rng: random.Random,
) -> int:
    """Pick a rebuy amount via weighted bucket (min / mid / max).

    Biases:
      - High bankroll → shifts toward `max` bucket.
      - Low energy or high tilt intensity → shifts toward `min` bucket.
    """
    max_bi = max(1, ctx.max_buy_in)
    bankroll_factor = min(1.0, ctx.projected_bankroll / (max_bi * 5))
    weights = {
        "min": REBUY_BASE_WEIGHTS["min"] + 30.0 * (1.0 - ctx.energy) + 20.0 * ctx.emotional_intensity,
        "mid": REBUY_BASE_WEIGHTS["mid"],
        "max": REBUY_BASE_WEIGHTS["max"] + 40.0 * bankroll_factor,
    }
    total = sum(weights.values())
    roll = rng.random() * total
    cumulative = 0.0
    for bucket, w in weights.items():
        cumulative += w
        if roll < cumulative:
            choice = bucket
            break
    else:  # pragma: no cover — total>0 guaranteed by base weights
        choice = "mid"
    mid_amount = (ctx.min_buy_in + ctx.max_buy_in) // 2
    return {"min": ctx.min_buy_in, "mid": mid_amount, "max": ctx.max_buy_in}[choice]


def compute_leave_cooldown_seconds(
    ctx: MovementContext,
    rng: random.Random,
) -> int:
    """Pressure-derived cooldown before this AI may refill the same table.

    Formula: `MIN_COOLDOWN_SECONDS + round(0..8 * pressure_factor) × 5`.
    pressure_factor is high when the AI left frustrated (low energy,
    high tilt, depleted bankroll). Returns seconds of wall-clock
    cooldown; the 5× multiplier maps "hands" to "approximate seconds
    at ~5s per sim hand."
    """
    min_bi = max(1, ctx.min_buy_in)
    bankroll_drag = max(0.0, 1.0 - ctx.projected_bankroll / (min_bi * 3))
    pressure_factor = (
        0.4 * (1.0 - ctx.energy)
        + 0.3 * ctx.emotional_intensity
        + 0.3 * bankroll_drag
    )
    pressure_factor = max(0.0, min(1.0, pressure_factor))
    extra_hands = round(rng.random() * 8 * pressure_factor)
    return MIN_COOLDOWN_SECONDS + extra_hands * 5


# --- Recent-leave cooldown (process-local, no persistence) ---

# Keyed by (table_id, personality_id). Tracks when an AI left and how
# long they're locked out of the SAME table. Process-local — a restart
# wipes the table, which is fine: the worst case is one stale immediate
# refill after a restart.
_recent_leaves_lock = threading.Lock()
_recent_leaves: Dict[Tuple[str, str], Tuple[datetime, int]] = {}


def record_leave_cooldown(
    table_id: str,
    personality_id: str,
    cooldown_seconds: int,
    now: datetime,
) -> None:
    """Mark `personality_id` as on cooldown for `table_id`.

    Opportunistically sweeps stale entries (cooldown elapsed) so the
    registry doesn't grow unbounded when AIs leave tables that no
    candidate later checks via `is_in_cooldown`.
    """
    with _recent_leaves_lock:
        _recent_leaves[(table_id, personality_id)] = (now, int(cooldown_seconds))
        # Sweep elapsed entries. Cooldowns top out at ~50s today, so
        # the registry stays small (proportional to active cycling).
        stale = [
            key for key, (left_at, cd) in _recent_leaves.items()
            if (now - left_at).total_seconds() >= cd
        ]
        for key in stale:
            del _recent_leaves[key]


def is_in_cooldown(
    table_id: str,
    personality_id: str,
    now: datetime,
) -> bool:
    """Return True if this AI left this table within the cooldown window."""
    with _recent_leaves_lock:
        entry = _recent_leaves.get((table_id, personality_id))
        if entry is None:
            return False
        left_at, cooldown_seconds = entry
        if (now - left_at).total_seconds() >= cooldown_seconds:
            # Cooldown elapsed — drop the record so the dict doesn't grow.
            del _recent_leaves[(table_id, personality_id)]
            return False
        return True


def clear_cooldowns() -> None:
    """Wipe the cooldown registry. Test helper."""
    with _recent_leaves_lock:
        _recent_leaves.clear()


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
class BankrollChange:
    """Describe a chip transfer between an AI's bankroll and a cash
    table seat. Captured during refresh_table_roster (which is pure)
    so the caller can persist the transfer in the right order.

    `direction` is `'to_seat'` (chips leaving the AI's bankroll for a
    newly-filled seat — pure transfer, no ledger entry) or
    `'from_seat'` (chips returning from a vacated seat — goes through
    `credit_ai_cash_out` so regen commits and cap-clamp overflow
    fires a ledger entry).

    Without these explicit transfers, the chip-ledger audit double-
    counts seat chips against AI bankrolls and reports a growing drift
    (~675 chips per lobby tick under full sim, per the v0 ledger
    diagnostic).
    """

    direction: str  # 'to_seat' | 'from_seat'
    personality_id: str
    amount: int


@dataclass
class RebuyChange:
    """An AI added chips to their seat instead of leaving.

    Returned from `refresh_table_roster` when the pressure-driven
    decision lands on `rebuy`. The caller (seated game handler) is
    responsible for debiting the AI's bankroll and updating the seat
    chip count + the live `Player.stack`.
    """

    personality_id: str
    seat_index: int
    amount: int
    new_seat_chips: int


@dataclass
class RosterRefreshResult:
    """Bundle the outputs of a refresh pass.

    `new_table` is the updated CashTableState (always returned, even
    when no movement happened — the timestamp will still bump on save).
    `idle_changes` lists the moves the caller must persist to
    `cash_idle_pool`. `freshly_seated_personality_ids` is the set of
    AIs newly added to the table; the caller can use it to update the
    global "seated_globally" set if it tracks one.

    `bankroll_changes` is the seat-side chip transfers the caller must
    apply to keep the audit's `actual_outstanding` invariant. See
    `BankrollChange` for the directions.

    `decisions` is keyed by personality_id for the AIs that were on
    the table at the start of the refresh, with their MovementDecision.
    Useful for tests and logs.
    """

    new_table: CashTableState
    idle_changes: List[IdlePoolChange] = field(default_factory=list)
    freshly_seated_personality_ids: List[str] = field(default_factory=list)
    bankroll_changes: List[BankrollChange] = field(default_factory=list)
    decisions: Dict[str, MovementDecision] = field(default_factory=dict)
    rebuy_changes: List[RebuyChange] = field(default_factory=list)


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
    defer_freshly_vacated_live_fill: bool = True,
    psych_lookup: Optional[Callable[[str], Dict[str, Any]]] = None,
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
    bankroll_changes: List[BankrollChange] = []
    rebuy_changes: List[RebuyChange] = []
    decisions: Dict[str, MovementDecision] = {}
    freshly_vacated: Set[int] = set()

    # Step 1: process AI seats.
    for i, slot in enumerate(new_seats):
        if slot["kind"] != "ai":
            continue
        pid = slot["personality_id"]
        ai_chips = int(slot.get("chips", 0))
        # buy_in_lookup gives this AI's table-specific buy-in (honors
        # per-personality buy-in multipliers). table_min_buy_in /
        # table_max_buy_in are absolute and feed pressure thresholds.
        projected = bankroll_lookup(pid) or 0
        psych = psych_lookup(pid) if psych_lookup else {}
        ctx = MovementContext(
            ai_chips=ai_chips,
            min_buy_in=table_min_buy_in,
            max_buy_in=table_max_buy_in,
            projected_bankroll=projected,
            stake_idx=stake_idx,
            next_tier_min_buy_in=next_tier_min_buy_in,
            energy=float(psych.get("energy", 0.5)),
            zone=str(psych.get("zone", "")),
            hands_in_detached_zone=int(psych.get("hands_in_detached_zone", 0)),
            emotional_intensity=float(psych.get("emotional_intensity", 0.0)),
        )
        decision = evaluate_ai_movement(ctx, rng)
        decisions[pid] = decision
        if decision == "stay":
            continue
        if decision == "rebuy":
            # Top up at the same seat. The persisted seat shows the new
            # chip count immediately. Bankroll debit channel:
            #   - Unseated path (lobby.py) consumes the `to_seat`
            #     BankrollChange via its existing bankroll loop.
            #   - Seated path (game_handler.py:_apply_rebuys) consumes
            #     the RebuyChange directly to debit the AI's bankroll
            #     AND update the live `Player.stack`. It does NOT walk
            #     `bankroll_changes` for this — adding such a loop later
            #     would cause a double-debit, so keep these two channels
            #     in sync if you refactor.
            rebuy_amount = pick_rebuy_amount(ctx, rng)
            new_chips = ai_chips + rebuy_amount
            new_seats[i] = ai_slot(pid, new_chips)
            rebuy_changes.append(RebuyChange(
                personality_id=pid,
                seat_index=i,
                amount=rebuy_amount,
                new_seat_chips=new_chips,
            ))
            bankroll_changes.append(BankrollChange(
                direction="to_seat",
                personality_id=pid,
                amount=rebuy_amount,
            ))
            continue
        # Vacate; record idle pool addition + bankroll credit.
        # The seat's chips return to the AI's bankroll (subject to
        # cap-clamp on the credit side — handled by the caller via
        # `credit_ai_cash_out`, which records the cap_clamp ledger
        # entry for any overflow).
        seat_chips = int(slot.get("chips", 0))
        if seat_chips > 0:
            bankroll_changes.append(BankrollChange(
                direction="from_seat",
                personality_id=pid,
                amount=seat_chips,
            ))
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
        # Record per-table cooldown so this AI doesn't immediately
        # refill the SAME seat on the next live-fill roll. They remain
        # eligible for any other table.
        cooldown_seconds = compute_leave_cooldown_seconds(ctx, rng)
        record_leave_cooldown(table.table_id, pid, cooldown_seconds, now)

    # Step 2: live-fill open seats.
    freshly_seated: List[str] = []
    open_indices = [
        i for i, s in enumerate(new_seats)
        if s["kind"] == "open"
        and not (defer_freshly_vacated_live_fill and i in freshly_vacated)
    ]

    # Idle pool candidates (oldest first), filtered to those NOT
    # globally seated and whose `target_stake` allows this table, and
    # who aren't on per-table leave cooldown ("just left, no immediate
    # rejoin at this table").
    def _idle_candidate_filter(entry: IdlePoolEntry) -> bool:
        if entry.personality_id in seated_globally:
            return False
        if entry.target_stake is not None and entry.target_stake != table.stake_label:
            return False
        if is_in_cooldown(table.table_id, entry.personality_id, now):
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
                if is_in_cooldown(table.table_id, pid, now):
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
        # Pure transfer: AI's bankroll funds the new seat's chips.
        # Without this, live-fill creates chips from nowhere (the leak
        # the chip-ledger audit was catching at ~675 chips/tick).
        bankroll_changes.append(BankrollChange(
            direction="to_seat",
            personality_id=pid,
            amount=buy_in,
        ))
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
        # Preserve the dealer-button position across the refresh.
        # `get_dealer_index` self-heals when the seat at this index is
        # no longer occupied (movement removed the AI), so we don't
        # need to second-guess that here — passing the prior value
        # through is the right default.
        dealer_idx=table.dealer_idx,
    )
    return RosterRefreshResult(
        new_table=new_table,
        idle_changes=idle_changes,
        freshly_seated_personality_ids=freshly_seated,
        bankroll_changes=bankroll_changes,
        decisions=decisions,
        rebuy_changes=rebuy_changes,
    )
