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
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from cash_mode.staker_history import StakerHistoryStats, candidate_weight
from cash_mode.staker_profile import StakerProfile
from cash_mode.stakes_ladder import STAKES_ORDER
from cash_mode.tables import (
    CashTableState,
    IdlePoolEntry,
    ai_slot,
    ai_slot_fish,
    open_slot,
)

logger = logging.getLogger(__name__)


# --- Movement decision ---

# Pressure-driven movement (spec: docs/plans/CASH_MODE_MOVEMENT_PRESSURE_DESIGN.md).
# Each AI's per-hand leave probability is `pressure / (pressure + LEAVE_K)`
# where pressure accumulates from four signals weighted below.
W_STAKE_UP = 0.5  # stack ≥ max_buy_in → eager to book the win
W_SHORT = 0.6  # stack < min_buy_in → tilt walk or rebuy
W_DETACHED = 0.3  # hands spent in 'detached' zone (folding too much)
W_TENURE = 0.2  # tired (low energy)
LEAVE_K = 2.0  # curve shape: at pressure=1.0, leave prob ≈ 0.33

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

# Fish are pool-funded chip donors: they stay and reload from their
# bankroll instead of leaving, so the whole stake feeds the table — UNLESS
# they tilt. At/above this emotional intensity a fish may storm off with
# its remaining chips (chance scales with intensity). Makes table manners
# an economic lever: keep the fish content and it keeps donating;
# needle / cooler / berate it and it leaves. Tunable.
FISH_TILT_LEAVE_THRESHOLD = 0.5

# Predator retention is gated on energy: a grinder farms a fish table
# until it's worn down to this floor, then it's released to cycle out
# (its winnings redistribute as a fresh predator takes the seat). Without
# the floor a winning predator — never short, with stake_up/bored_move
# suppressed — would hoard the table forever (sim showed one stack run to
# ~94k). Approximates "casinos drain energy faster, cycling people
# through" without threading table type into the psychology system.
#
# Tuned low — release only when genuinely worn down. At 0.35 it cycled
# predators so eagerly the high tables under-staffed and farmed less
# (sim: $200 fell to 1 grinder, fish stopped bleeding). 0.2 keeps
# predators farming while still rotating the most-tenured eventually.
CASINO_PREDATOR_FATIGUE_FLOOR = 0.2

# Minimum cooldown seconds between an AI leaving a table and being
# eligible to refill the same table. The pressure-derived variable
# extension on top of this is computed at leave time.
MIN_COOLDOWN_SECONDS = 10

# Idle recovery (spec: docs/plans/CASH_MODE_MOVEMENT_PRESSURE_DESIGN.md).
# The leave side is energy-driven (tired AIs walk to the idle pool), but
# energy never recovered while idle and re-seating ignored it — so AIs
# returned just as drained. These close that loop: while idle, energy
# springs back toward baseline on wall-clock time (same projection-on-read
# pattern as bankroll regen), and re-seating gates on the recovered value.
IDLE_ENERGY_RECOVERY_PER_HOUR = 0.5  # +energy/hour idle, toward baseline
# Re-seat gate is RELATIVE to each AI's own baseline, not an absolute energy
# value — low-baseline personas (e.g. baseline 0.32) recover only up to their
# baseline, so an absolute floor would strand them in the idle pool forever.
# An idle AI must recharge to this fraction of its baseline before returning.
RESEAT_RECOVERY_FLOOR = 0.85

# Phase 4.5 Commit 1 — per-staker garnishment on AI take_stake.
# Mirrors `cash_mode.sponsor_offers.GARNISHMENT_RATE_CAP` so AI-side
# and human-side garnishment surfaces stay calibrated together. The
# absolute cap keeps stacked tier-bump + garnishment shifts from
# producing degenerate 100%-cut deals on heavily-leveraged borrowers.
GARNISHMENT_RATE_CAP = 0.20
GARNISHMENT_ABSOLUTE_CAP = 0.55


# Movement decision string literals. Strings rather than an enum because
# they cross the pure-helper boundary and surface to logs / admin views
# as-is. `rebuy` is new — the AI tops up at the same seat instead of
# leaving.
MovementDecision = (
    str  # 'stay' | 'stake_up' | 'take_break' | 'forced_leave' | 'bored_move' | 'rebuy'
)


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
    energy: float = 0.5  # 0 = exhausted, 1 = fresh
    zone: str = ""  # 'detached' triggers detached pressure
    hands_in_detached_zone: int = 0  # consecutive hands in detached zone
    emotional_intensity: float = 0.0  # 0..1 — biases rebuy bucket toward min when high


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
    detached_raw = (ctx.hands_in_detached_zone / 8.0) if ctx.zone == "detached" else 0.0
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


def project_idle_energy(
    stored_energy: float,
    baseline_energy: float,
    idle_seconds: float,
    recovery_per_hour: float = IDLE_ENERGY_RECOVERY_PER_HOUR,
) -> float:
    """Project an idle AI's energy forward through wall-clock rest.

    Springs `stored_energy` toward `baseline_energy` at `recovery_per_hour`,
    clamped so rest never pushes energy *past* baseline (a worn-down AI
    recovers to its natural level, not beyond). Pure — mirrors the
    projection-on-read used for bankroll regen.
    """
    if idle_seconds <= 0 or stored_energy >= baseline_energy:
        return min(stored_energy, max(stored_energy, baseline_energy))
    recovered = stored_energy + recovery_per_hour * (idle_seconds / 3600.0)
    return min(baseline_energy, recovered)


def reseat_readiness(recovery: float) -> float:
    """Per-tick probability an idle, recovered AI returns to a seat.

    `recovery` is the fraction recharged toward the AI's baseline (1.0 = back
    to baseline). 0 below `RESEAT_RECOVERY_FLOOR` ("still resting"), then ramps
    linearly to 1.0 at full recovery — so the more rested an AI is, the likelier
    it leaves the idle pool this tick. Augments (not replaces) the live-fill roll.
    """
    if recovery < RESEAT_RECOVERY_FLOOR:
        return 0.0
    span = 1.0 - RESEAT_RECOVERY_FLOOR
    if span <= 0:
        return 1.0
    return max(0.0, min(1.0, (recovery - RESEAT_RECOVERY_FLOOR) / span))


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


def _coerce_fish_movement(
    decision: MovementDecision,
    ctx: MovementContext,
    rng: random.Random,
) -> MovementDecision:
    """Apply fish-specific movement rules to a raw movement decision.

    A fish is a pool-funded chip donor meant to feed the table until its
    stake is gone, not a session-booking grinder. So:

    - **Content fish** (`emotional_intensity < FISH_TILT_LEAVE_THRESHOLD`):
      never wander tables (`bored_move`) or move up stakes (`stake_up`),
      and instead of leaving short (`take_break`) or busting the seat
      (`forced_leave`) they **reload from the bankroll** (`rebuy`) — until
      the bankroll is too thin to fund a buy-in, at which point they bust
      for real. Net effect: the entire pool-funded stake is lost at the
      table rather than recycled back to the pool on a cash-out.

    - **Upset fish** (intensity at/above the threshold): table manners
      failed. A fish already busting just goes; otherwise it may storm off
      *with its remaining chips*, the chance rising with how tilted it is.
      Keep the fish content and this branch never fires.
    """
    if ctx.emotional_intensity >= FISH_TILT_LEAVE_THRESHOLD:
        if decision == "forced_leave":
            return "forced_leave"
        return "take_break" if rng.random() < ctx.emotional_intensity else "stay"

    if decision in ("stake_up", "bored_move"):
        return "stay"
    if decision in ("take_break", "forced_leave"):
        can_reload = ctx.projected_bankroll >= ctx.min_buy_in
        return "rebuy" if can_reload else decision
    return decision


def _coerce_predator_retention(
    decision: MovementDecision,
    table_has_fish: bool,
    energy: float,
) -> MovementDecision:
    """Keep a grinder from *drifting* off a fish table out of boredom —
    but never block the `stake_up` graduation exit.

    A table holding a seated fish is a feeding ground, and a predator that
    wanders away from it (`bored_move`) after a pot or two leaves money on
    the felt. Suppress that one discretionary drift so predators stay and
    farm. This is what staffs a high-stakes casino: without it, rich AIs
    win a pot off the fish and immediately drift away (the $200 table sat
    half-empty in sim).

    `stake_up` is deliberately NOT suppressed — it's the healthy release.
    A predator that has farmed enough to buy into the next stake should
    book the win and move up; that rising stack pressure is exactly what
    stops a single grinder from hoarding the table forever. (When this
    exit *was* gated, winning predators never tired below the energy floor
    and one stack ran past 500k chips at a $50 seat — the bug this guard
    now avoids.) `take_break`/`forced_leave` also always stand, so
    losing/short predators rotate regardless.

    Boredom suppression is gated on energy: once a predator is worn down
    past `CASINO_PREDATOR_FATIGUE_FLOOR` even its `bored_move` stands, so a
    tired grinder cycles out too.
    """
    if not table_has_fish:
        return decision
    if energy < CASINO_PREDATOR_FATIGUE_FLOOR:
        return decision  # worn down — let it cycle out
    if decision == "bored_move":
        return "stay"
    return decision


def resolve_dominant_signal(
    ctx: MovementContext,
    decision: MovementDecision,
) -> str:
    """Recover the dominant pressure signal that drove `decision`.

    Deterministic, rng-free — recomputes pressures and picks the max,
    with `forced_leave` short-circuited to the synthetic `"bust"`
    signal (the busted-stack path skips the pressure roll entirely).
    Returns `""` for `stay` / `rebuy` callers who don't need it.

    Used by leave-narrative generation to give the LLM a "why" hint
    that matches the same signal the decision logic acted on.
    """
    if decision == "forced_leave":
        return "bust"
    if decision in ("stay", "rebuy"):
        return ""
    pressures = compute_leave_pressure(ctx)
    if not pressures or sum(pressures.values()) <= 0:
        return ""
    return max(pressures, key=pressures.get)


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
        "min": REBUY_BASE_WEIGHTS["min"]
        + 30.0 * (1.0 - ctx.energy)
        + 20.0 * ctx.emotional_intensity,
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
    pressure_factor = 0.4 * (1.0 - ctx.energy) + 0.3 * ctx.emotional_intensity + 0.3 * bankroll_drag
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
            key
            for key, (left_at, cd) in _recent_leaves.items()
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
class StakeCreationChange:
    """Phase 4: an AI accepted a stake from another AI to avoid bust.

    Returned from `refresh_table_roster` when `evaluate_ai_movement`
    would have returned `forced_leave` but a willing staker existed
    with capacity. The caller must:
      - Debit the staker's bankroll by `principal`.
      - Create a `Stake` row (`status=active`, both kinds `personality`,
        `format=pure`, `cut` from the staker's `staker_profile.rate_anchor`).
      - The seat is already updated in `new_table` to `principal` chips.

    The borrower's pre-bust chips (`chips_at_bust`) returned to their
    bankroll via the normal `from_seat` `BankrollChange` that fires
    alongside this — same shape as a regular leave. The seat then
    refills to `principal` from the staker.

    This dataclass is parallel to `RebuyChange`: both describe seat
    refills with named provenance, but `rebuy` debits the same AI's
    bankroll and `take_stake` debits a different AI's bankroll +
    spawns a stake row.
    """

    borrower_id: str
    staker_id: str
    seat_index: int
    principal: int
    stake_label: str
    cut: float


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

    `leave_signals` carries the dominant pressure signal per non-stay
    AI so the lobby's activity emitter can build narrative hints
    without re-running the pressure compute. `''` for stays.
    """

    new_table: CashTableState
    idle_changes: List[IdlePoolChange] = field(default_factory=list)
    freshly_seated_personality_ids: List[str] = field(default_factory=list)
    bankroll_changes: List[BankrollChange] = field(default_factory=list)
    decisions: Dict[str, MovementDecision] = field(default_factory=dict)
    rebuy_changes: List[RebuyChange] = field(default_factory=list)
    leave_signals: Dict[str, str] = field(default_factory=dict)
    # Phase 4: AI-borrow stake creations. Each entry's seat in
    # `new_table` is already refilled to `principal`; the caller
    # persists the stake row + debits the staker's bankroll.
    stake_creations: List[StakeCreationChange] = field(default_factory=list)


def find_ai_staker_for(
    *,
    borrower_id: str,
    principal: int,
    candidate_pids: List[str],
    staker_profile_lookup: Callable[[str], StakerProfile],
    bankroll_lookup: Callable[[str], Optional[int]],
    relationship_lookup: Callable[[str, str], Optional[Tuple[float, float, float]]],
    rng: random.Random,
    history_lookup: Optional[Callable[[str], Dict[str, StakerHistoryStats]]] = None,
    starting_bankroll_lookup: Optional[Callable[[str], Optional[int]]] = None,
) -> Optional[Tuple[str, StakerProfile]]:
    """Pick an AI staker willing and able to fund `principal` for borrower.

    Phase 4 Commit 2. Walks `candidate_pids` (table-local in v1;
    Commit 4 broadens to the global pool), filters by staker_profile
    willingness, bankroll capacity, and relationship gates. When
    multiple candidates qualify, selection is either uniform random
    (legacy behavior, when `history_lookup` is None) or weighted by
    each candidate's staking incentives (when `history_lookup` is
    provided — see `cash_mode/staker_history.py`).

    Filters:
      - `profile.willing` must be True (stoic lenders refuse outright).
      - `bankroll >= principal / max_loan_pct_of_bankroll` so the
        principal is within the candidate's stake-size policy. Phrased
        as a ratio rather than a hard floor so deeper-bankrolled
        personalities scale up naturally.
      - Relationship axes (candidate's view of borrower):
        `respect >= profile.respect_floor`, `heat <= profile.heat_ceiling`.

    Borrower's own id is excluded defensively even though `refresh_table_roster`
    shouldn't pass it in `candidate_pids`.

    Weighted selection (opt-in via `history_lookup`):
      - `history_lookup(staker_id)` returns per-borrower outcome stats
        the lobby caches once per refresh.
      - `starting_bankroll_lookup(staker_id)` powers the
        wealth-overflow score; if omitted, that contribution is
        skipped and only belief + warmth shape the weights.
      - Composite weight = base + excess_pressure + skill_belief +
        relationship_warmth, floored at MIN_WEIGHT.

    Returns (staker_id, staker_profile) on success, None when no
    candidate clears every gate. Caller treats None as "fall back to
    `forced_leave`".
    """
    # Capture rel + bankroll alongside each qualified candidate so the
    # weighted-selection pass below can reuse them without re-querying
    # the lookup callbacks.
    qualified: List[Tuple[str, StakerProfile, int, Optional[Tuple[float, float, float]]]] = []
    for cand_id in candidate_pids:
        if cand_id == borrower_id:
            continue
        try:
            profile = staker_profile_lookup(cand_id)
        except Exception as exc:
            logger.debug(
                "find_ai_staker_for: staker_profile lookup failed for %r: %s",
                cand_id,
                exc,
            )
            continue
        if not profile.willing:
            continue
        bankroll = bankroll_lookup(cand_id) or 0
        if profile.max_loan_pct_of_bankroll <= 0:
            continue
        # Capacity check: principal must fit within the lender's
        # per-loan ceiling (max_loan_pct × bankroll).
        if bankroll * profile.max_loan_pct_of_bankroll < principal:
            continue
        # Relationship gates. None → neutral defaults (0.5/0.5/0.0).
        rel = relationship_lookup(cand_id, borrower_id)
        if rel is None:
            likability, respect, heat = 0.5, 0.5, 0.0
        else:
            likability, respect, heat = rel
        if respect < profile.respect_floor:
            continue
        if heat > profile.heat_ceiling:
            continue
        qualified.append((cand_id, profile, bankroll, rel))

    if not qualified:
        return None

    if history_lookup is None:
        # Legacy uniform-random path — preserved for callers that
        # haven't wired the incentive scoring (and as a fallback when
        # the lobby is invoked without stake_repo).
        cand_id, profile, _, _ = rng.choice(qualified)
        return cand_id, profile

    weights: List[float] = []
    for cand_id, _profile, bankroll, rel in qualified:
        try:
            history = history_lookup(cand_id)
        except Exception as exc:
            logger.debug(
                "find_ai_staker_for: history lookup failed for %r: %s",
                cand_id,
                exc,
            )
            history = {}
        stats = history.get(borrower_id) if history else None
        starting = None
        if starting_bankroll_lookup is not None:
            try:
                starting = starting_bankroll_lookup(cand_id)
            except Exception as exc:
                logger.debug(
                    "find_ai_staker_for: starting_bankroll lookup failed for %r: %s",
                    cand_id,
                    exc,
                )
                starting = None
        weights.append(
            candidate_weight(
                bankroll=bankroll,
                starting_bankroll=starting,
                history_stats=stats,
                relationship_axes=rel,
            )
        )
    cand_id, profile, _, _ = rng.choices(qualified, weights=weights, k=1)[0]
    return cand_id, profile


def garnished_stake_cut(
    *,
    rate_anchor: float,
    outstanding_carry: int,
    principal: int,
) -> float:
    """Garnishment-adjusted cut for an AI-borrower stake creation.

    Phase 4.5 Commit 1 — mirrors the human-borrower garnishment in
    `cash_mode.sponsor_offers.compute_personality_offers`. When the
    candidate staker already holds an unpaid carry from this borrower,
    bump the new stake's cut by `outstanding_carry / principal`, capped
    at `GARNISHMENT_RATE_CAP` (+20pp), then clamp the final value to
    `GARNISHMENT_ABSOLUTE_CAP` (0.55) so stacked bumps can't push past
    the human-surface ceiling.

    Returns `rate_anchor` unchanged when no carry exists (or principal
    is zero) — the no-prior-history case.
    """
    if outstanding_carry <= 0 or principal <= 0:
        return rate_anchor
    garnish = min(outstanding_carry / principal, GARNISHMENT_RATE_CAP)
    return min(rate_anchor + garnish, GARNISHMENT_ABSOLUTE_CAP)


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
    # Idle-recovery gate: recovery fraction toward baseline for an idle AI
    # (caller computes projected-energy ÷ baseline from emotional_state_json +
    # time-since-left; 1.0 = fully rested). When provided, idle candidates
    # below RESEAT_RECOVERY_FLOOR are still resting and won't be picked; above
    # it, return chance ramps with recovery. Omit → no gate (pre-idle behavior).
    energy_lookup: Optional[Callable[[str], float]] = None,
    # Phase 4: optional callbacks to intercept `forced_leave` with a
    # `take_stake` decision when a peer AI is willing and able to
    # stake the busting borrower. Omit (default None) → never tries
    # to convert, preserving pre-Phase-4 behavior.
    borrower_profile_lookup: Optional[Callable[[str], Any]] = None,
    staker_profile_lookup: Optional[Callable[[str], StakerProfile]] = None,
    relationship_lookup: Optional[
        Callable[[str, str], Optional[Tuple[float, float, float]]]
    ] = None,
    stake_label: Optional[str] = None,
    # Phase 4 Commit 4: cross-table staker candidates. Empty by
    # default → only AIs at this table can stake (Commit 2's
    # table-local behavior). Pass a broader list (idle + other-table
    # AIs filtered to adjacent stakes) for the wider pool.
    cross_table_staker_pids: Optional[List[str]] = None,
    # Phase 4.5 Commit 1: when provided, returns the outstanding carry
    # the borrower owes to a specific staker (across all open carry
    # rows). Used to garnish the cut on a fresh take_stake — same model
    # as the human-borrower garnishment in compute_personality_offers,
    # just enforced at movement time for AI borrowers. None → no
    # garnishment (pre-Phase-4.5 callers).
    carry_lookup: Optional[Callable[[str, str], int]] = None,
    # Staker-incentives plan: per-refresh history + starting-bankroll
    # caches the lobby plumbs through so the matcher can do weighted
    # selection. Either being None → uniform-random fallback inside
    # find_ai_staker_for. See cash_mode/staker_history.py.
    history_lookup: Optional[Callable[[str], Dict[str, StakerHistoryStats]]] = None,
    starting_bankroll_lookup: Optional[Callable[[str], Optional[int]]] = None,
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
    leave_signals: Dict[str, str] = {}
    freshly_vacated: Set[int] = set()
    stake_creations: List[StakeCreationChange] = []
    # Snapshot the AIs currently at this table before any movement
    # applies, so a busting AI's `take_stake` candidates are the OTHER
    # AIs seated alongside them at the start of the tick (not their
    # post-leave neighbors).
    pre_movement_ai_pids = [
        s.get("personality_id")
        for s in new_seats
        if s.get("kind") == "ai" and s.get("personality_id")
    ]

    # Is there a fish to farm at this table? Drives predator retention:
    # grinders stay to feast instead of booking the win and leaving.
    # Fish are casino-only, so this is non-trivial only at casinos.
    table_has_fish = any(s.get("kind") == "ai" and s.get("archetype") == "fish" for s in new_seats)

    # Step 1: process AI seats.
    for i, slot in enumerate(new_seats):
        if slot["kind"] != "ai":
            continue
        pid = slot["personality_id"]
        ai_chips = int(slot.get("chips", 0))
        # Fish are casino-bound. With a real (pool-funded) bankroll they
        # run normal movement — short-stack → re-buy from that bankroll,
        # or go home when it's dry — but their tier-drift decisions are
        # suppressed below so they never wander to another stake or table.
        is_fish = slot.get("archetype") == "fish"
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
        # Fish are casino-bound chip donors: they reload from their
        # pool-funded bankroll and stay until the whole stake is gone,
        # unless they tilt and storm off. See `_coerce_fish_movement`.
        if is_fish:
            decision = _coerce_fish_movement(decision, ctx, rng)
        else:
            # Predator retention: grinders stay to farm a seated fish
            # rather than booking the win and leaving (the pull that
            # staffs high-stakes casinos), until they tire and cycle out.
            # See `_coerce_predator_retention`.
            decision = _coerce_predator_retention(decision, table_has_fish, ctx.energy)
        # Stamp the dominant pressure signal so the lobby's activity
        # emitter can build narrative hints for leave_narrative without
        # rerunning the pressure compute (or worse, re-querying psych).
        if decision != "stay":
            leave_signals[pid] = resolve_dominant_signal(ctx, decision)
        # Phase 4: intercept `forced_leave` with `take_stake` when a
        # peer AI is willing to fund the busting borrower. Only fires
        # when ALL the required callbacks are wired (lobby supplies
        # them post-Phase-4; pre-Phase-4 callers omit them and behavior
        # is unchanged).
        pending_stake: Optional[Tuple[str, StakerProfile]] = None
        if (
            decision == "forced_leave"
            and borrower_profile_lookup is not None
            and staker_profile_lookup is not None
            and relationship_lookup is not None
            and stake_label is not None
        ):
            try:
                borrower_profile = borrower_profile_lookup(pid)
            except Exception as exc:
                logger.debug(
                    "take_stake: borrower profile lookup failed for %r: %s",
                    pid,
                    exc,
                )
                borrower_profile = None
            if borrower_profile is not None and getattr(
                borrower_profile,
                "willing",
                False,
            ):
                # Candidate pool: AIs at this table PLUS any cross-table
                # candidates the lobby supplied. Dedup defensively in
                # case an AI appears in both lists.
                local_candidates = [c for c in pre_movement_ai_pids if c != pid]
                if cross_table_staker_pids:
                    seen = set(local_candidates)
                    seen.add(pid)
                    for extra in cross_table_staker_pids:
                        if extra not in seen:
                            local_candidates.append(extra)
                            seen.add(extra)
                pending_stake = find_ai_staker_for(
                    borrower_id=pid,
                    principal=table_min_buy_in,
                    candidate_pids=local_candidates,
                    staker_profile_lookup=staker_profile_lookup,
                    bankroll_lookup=lambda c: bankroll_lookup(c),
                    relationship_lookup=relationship_lookup,
                    rng=rng,
                    history_lookup=history_lookup,
                    starting_bankroll_lookup=starting_bankroll_lookup,
                )
                if pending_stake is not None:
                    decision = "take_stake"
        decisions[pid] = decision

        if decision == "take_stake":
            staker_id, staker_profile = pending_stake
            # Refill the seat to principal. The borrower's pre-bust
            # chips return to bankroll via the same `from_seat` path
            # a regular leave would emit, keeping chip-conservation
            # consistent with sessions that begin from an empty seat
            # newly funded by a staker.
            seat_chips = int(slot.get("chips", 0))
            if seat_chips > 0:
                bankroll_changes.append(
                    BankrollChange(
                        direction="from_seat",
                        personality_id=pid,
                        amount=seat_chips,
                    )
                )
            new_seats[i] = ai_slot(pid, table_min_buy_in)
            # Phase 4.5 Commit 1: per-staker garnishment. When the
            # picked staker already holds an unpaid carry from this
            # borrower, bump the new stake's cut. Mirrors the human
            # garnishment in compute_personality_offers. Defaults to
            # rate_anchor when no carry_lookup is provided (pre-4.5
            # callers / tests).
            cut = staker_profile.rate_anchor
            if carry_lookup is not None:
                try:
                    outstanding = int(carry_lookup(staker_id, pid))
                except Exception as exc:
                    logger.debug(
                        "take_stake: carry_lookup failed staker=%r borrower=%r: %s",
                        staker_id,
                        pid,
                        exc,
                    )
                    outstanding = 0
                cut = garnished_stake_cut(
                    rate_anchor=cut,
                    outstanding_carry=outstanding,
                    principal=table_min_buy_in,
                )
            stake_creations.append(
                StakeCreationChange(
                    borrower_id=pid,
                    staker_id=staker_id,
                    seat_index=i,
                    principal=table_min_buy_in,
                    stake_label=stake_label,
                    cut=cut,
                )
            )
            # `seated_globally` stays unchanged — pid still occupies
            # the seat, just freshly funded by the staker.
            continue

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
            # Never rebuy more than the bankroll can fund. `_apply_rebuys`
            # clamps the bankroll debit to >=0 but credits the seat the full
            # amount, so an over-reach would mint chips (audit drift). Clamp
            # to the projected bankroll; if there's nothing left to reload
            # with, skip the rebuy and leave the short stack as-is.
            rebuy_amount = min(rebuy_amount, max(0, ctx.projected_bankroll))
            if rebuy_amount <= 0:
                continue
            new_chips = ai_chips + rebuy_amount
            # Preserve the `archetype='fish'` stamp across a rebuy. The
            # stamp is what gates `_coerce_fish_movement` (no tier-drift /
            # no wander) and predator-retention's `table_has_fish`, and
            # what the teardown/reclaim chip-return paths key on. Rewriting
            # with a bare `ai_slot` would silently de-stamp a fish on its
            # first reload — at a casino the zombie-reclaim re-seats a fresh
            # fish, but a whale at a lobby table (reclaim is casino-gated)
            # would quietly turn into a wandering grinder holding a deep
            # pool-funded stack. A fish reloads until its bankroll is dry,
            # so this fires often; keep it a fish the whole way down.
            new_seats[i] = ai_slot_fish(pid, new_chips) if is_fish else ai_slot(pid, new_chips)
            rebuy_changes.append(
                RebuyChange(
                    personality_id=pid,
                    seat_index=i,
                    amount=rebuy_amount,
                    new_seat_chips=new_chips,
                )
            )
            bankroll_changes.append(
                BankrollChange(
                    direction="to_seat",
                    personality_id=pid,
                    amount=rebuy_amount,
                )
            )
            continue
        # Vacate; record idle pool addition + bankroll credit.
        # The seat's chips return to the AI's bankroll (subject to
        # cap-clamp on the credit side — handled by the caller via
        # `credit_ai_cash_out`, which records the cap_clamp ledger
        # entry for any overflow).
        seat_chips = int(slot.get("chips", 0))
        if seat_chips > 0:
            bankroll_changes.append(
                BankrollChange(
                    direction="from_seat",
                    personality_id=pid,
                    amount=seat_chips,
                )
            )
        new_seats[i] = open_slot()
        freshly_vacated.add(i)
        seated_globally.discard(pid)
        target_stake = None
        if decision == "stake_up" and stake_idx + 1 < len(STAKES_ORDER):
            target_stake = STAKES_ORDER[stake_idx + 1]
        idle_changes.append(
            IdlePoolChange(
                kind="add",
                personality_id=pid,
                entry=IdlePoolEntry(
                    personality_id=pid,
                    left_at=now,
                    reason=_movement_decision_to_idle_reason(decision),
                    target_stake=target_stake,
                ),
            )
        )
        # Record per-table cooldown so this AI doesn't immediately
        # refill the SAME seat on the next live-fill roll. They remain
        # eligible for any other table.
        cooldown_seconds = compute_leave_cooldown_seconds(ctx, rng)
        record_leave_cooldown(table.table_id, pid, cooldown_seconds, now)

    # Step 2: live-fill open seats.
    freshly_seated: List[str] = []
    open_indices = [
        i
        for i, s in enumerate(new_seats)
        if s["kind"] == "open" and not (defer_freshly_vacated_live_fill and i in freshly_vacated)
    ]

    # Idle pool candidates (oldest first), filtered to those NOT
    # globally seated and whose `target_stake` allows this table, and
    # who aren't on per-table leave cooldown ("just left, no immediate
    # rejoin at this table").
    #
    # Target-stake stickiness: an idle entry with `target_stake=$X`
    # normally only accepts seats at $X. But if the AI's bankroll has
    # since dropped below the min buy-in at $X (e.g. they lost chips
    # before their target seat opened up), they'd sit stranded in idle
    # forever. Relax the gate in that case — let any affordable table
    # pick them up. The per-seat bankroll check below still enforces
    # affordability at THIS table.
    from cash_mode.stakes_ladder import table_buy_in_window as _bi_window

    def _can_still_afford_target(entry: IdlePoolEntry) -> bool:
        if entry.target_stake is None:
            return True
        try:
            _, target_min, _ = _bi_window(entry.target_stake)
        except Exception:
            return True  # Unknown tier — don't gate.
        projected = bankroll_lookup(entry.personality_id) or 0
        return projected >= target_min

    def _idle_candidate_filter(entry: IdlePoolEntry) -> bool:
        if entry.personality_id in seated_globally:
            return False
        if (
            entry.target_stake is not None
            and entry.target_stake != table.stake_label
            and _can_still_afford_target(entry)
        ):
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
            # Idle-recovery gate: still-tired AIs keep resting; more-recovered
            # ones are likelier to return this tick. `energy_lookup` returns the
            # recovery fraction toward baseline (1.0 = fully rested). No-op when
            # energy_lookup omitted.
            if energy_lookup is not None:
                recovery = energy_lookup(entry.personality_id)
                if recovery < RESEAT_RECOVERY_FLOOR or rng.random() >= reseat_readiness(recovery):
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
        bankroll_changes.append(
            BankrollChange(
                direction="to_seat",
                personality_id=pid,
                amount=buy_in,
            )
        )
        # Clear any idle-pool row for the AI we just seated. For the
        # `idle` source this is the matching removal. For the `eligible`
        # source it self-heals a stale row: a `stake_up_queued` AI whose
        # target tier doesn't match this table is rejected by
        # `_idle_candidate_filter` but is still in `eligible_candidates`
        # (which is filtered only by off-grid, not idle membership), so
        # it can be seated here below its target — leaving a live idle
        # row = the `seated_and_idle` split-brain. The caller's
        # delete_idle is a harmless no-op when no row exists.
        idle_changes.append(
            IdlePoolChange(
                kind="remove",
                personality_id=pid,
            )
        )

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
        # v111: carry forward the table's identity fields. Without these
        # the subsequent `save_table` would write defaults (name=None,
        # table_type='lobby') and clobber whatever the lobby config or
        # private-table creator had set.
        name=table.name,
        table_type=table.table_type,
        # v113: preserve the closing-state countdown across refreshes.
        # Without this carry-forward, every refresh_table_roster save
        # would NULL the column and undo the smooth-shutdown lifecycle
        # the casino provisioner just wrote.
        closing_hand_countdown=table.closing_hand_countdown,
    )
    return RosterRefreshResult(
        new_table=new_table,
        idle_changes=idle_changes,
        freshly_seated_personality_ids=freshly_seated,
        bankroll_changes=bankroll_changes,
        decisions=decisions,
        rebuy_changes=rebuy_changes,
        leave_signals=leave_signals,
        stake_creations=stake_creations,
    )
