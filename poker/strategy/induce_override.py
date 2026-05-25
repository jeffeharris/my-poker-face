"""Induce override: smooth-call instead of raise vs detected multi-street barrelers.

Phase B Item 2: switches the gate to read `barrel_frequency` directly
(Phase B Item 1 ships the stat). Replaces Phase A's AF_pf × cbet_attempt
proxy. Also replaces the fixed 1.00 call redistribution with a
confidence-scaled mix in [0.70, 0.90].

Phase B Item 4: adds an open-spot IP branch. When hero is IP, free to
act (no facing bet) on flop/turn with a strong hand AND villain has a
detected check-then-barrel tendency (`flop_check_then_barrel_rate`),
hero checks back instead of cbet-ing — to induce villain's turn barrel.

See:
- docs/plans/INDUCE_OVERRIDE_PHASE_A.md — original design, shipped
- docs/plans/INDUCE_OVERRIDE_PHASE_B.md — Items 1-4 specs

## What this does

When hero has the nuts on a dry flop/turn IP and faces a bet from an
opponent whose AggregatedOpponentStats flag them as a multi-street
barreler (barrel_frequency × confidence), this layer redistributes
the strategy distribution toward `call` — capturing the bluff sequence
across multiple streets instead of ending it with a raise.

The call probability scales with two axes:
- Signal magnitude: `barrel_frequency` ramps 0.60 → 0.85
- Sample confidence: `barrel_opportunities` ramps 10 → 50

Their product (∈ [0, 1]) maps to call probability ∈ [0.70, 0.90].
- At minimum gate (both at threshold): 0.70 call / 0.30 raise
- At maximum gate (barrel_freq ≥ 0.85, opportunities ≥ 50): 0.90 call / 0.10 raise

The 0.70 lower bound prevents the rule from degrading toward
value_override's 0.50 at low confidence — if the gate fires at all,
we're at least mildly trapping. The 0.90 upper bound preserves the
unexploitability tax against future adaptive opponents.

## Architectural placement

Sits IMMEDIATELY BEFORE `_apply_value_override` in the postflop
pipeline. When induce fires, value_override defers via its own
`prior_layer_fired` check.

## Gate (all conditions required)

- Facing a bet (`'fold' in strategy.action_probabilities`)
- Hero in position (`node.position == 'IP'`)
- Street is flop or turn (no value in trapping on the river)
- Hand is `actual_nuts` (`hand_strength == 'nuts'` AND `nut_status == 'actual_nuts'`)
- Dry board (`len(node.danger_flags) <= 1`)
- Effective stack ≥ 40 BB (need room for turn + river barrels)
- Barrel signal: `barrel_frequency >= 0.60` AND `barrel_opportunities >= 10`
- Not a station (`not _is_passive_with_jams` AND `not _is_hyper_passive`)
- Not facing all-in (no future streets to extract on)
- Heads-up only (`active_opponent_count == 1`)
- Psychology gate: `adaptation_bias * tilt_factor > GATING_FLOOR`
"""

from typing import List, Tuple

from .exploitation import (
    GATING_FLOOR,
    AggregatedOpponentStats,
    DecisionContext,
    _is_hyper_passive,
    _is_passive_with_jams,
)
from .intervention_trace import (
    InterventionOperation,
    InterventionTrace,
    is_rule_disabled,
    l1_distance,
    layer_order_for,
    make_disabled_trace,
    make_no_op_trace,
    primary_action,
    summarize_strategy,
)
from .strategy_profile import StrategyProfile

# ── Gate tunables (Phase B Item 2) ─────────────────────────────────

# Barrel signal thresholds. Gate fires when both barrel_frequency
# meets the minimum and barrel_opportunities is sufficient. Below
# either threshold, induce stays off.
#
# MIN_BARREL_OPPORTUNITIES was tuned from 10→5 in the Phase B Item 2
# validation: the original threshold required ~30-50 hands of warmup
# vs Maniac before firing, which combined with the narrow gate gave
# only ~5 fires per 1000-hand arm. Dropping to 5 cuts warmup roughly
# in half. The tradeoff (lower sample confidence on the signal) is
# absorbed by the confidence-scaled mix: at opps=5 the sample
# confidence is 0 → call_prob lands at the CALL_PROB_MIN floor (0.70),
# so a small-sample fire is still meaningfully trapping but not
# maxed out.
MIN_BARREL_FREQUENCY = 0.60
MIN_BARREL_OPPORTUNITIES = 5

# Sample-floor on observed hands. Even with barrel data populated,
# require a minimum activity baseline to avoid cold-start spikes.
MIN_HANDS_OBSERVED = 10

# Confidence-scaled mixing parameters (Phase B Item 2).
# rate_intensity ramps barrel_frequency between RATE_MIN and RATE_MAX
# to [0, 1]. sample_confidence ramps barrel_opportunities between
# OPPS_MIN and OPPS_MAX to [0, 1]. Their product (∈ [0, 1]) maps to
# call probability in [CALL_MIN, CALL_MAX].
RATE_RAMP_MIN = 0.60  # below this the gate doesn't fire
RATE_RAMP_MAX = 0.85  # at/above this the rate axis saturates
OPPS_RAMP_MIN = 5.0  # aligned with MIN_BARREL_OPPORTUNITIES
OPPS_RAMP_MAX = 50.0  # at/above this the sample axis saturates
CALL_PROB_MIN = 0.70  # minimum trap intensity when gate fires
CALL_PROB_MAX = 0.90  # maximum trap intensity (preserves unexploitability)

# Stack-depth floor in BB. Below 40 BB, the SPR after a flop call is
# too low to extract meaningful turn/river barrels.
MIN_EFFECTIVE_STACK_BB = 40.0

# Phase B Item 3: hand-class gating. Each eligible hand class has its
# own (allowed nut_status set, max danger flag count) tuple. The
# stricter texture/nut requirements for strong_made compensate for
# the increased turn-card risk vs nuts:
#   - nuts        : tolerates ≤1 danger flag, requires actual_nuts
#   - strong_made : requires fully dry board (0 danger flags) AND a
#                    near-nut or actual-nut classification (excludes
#                    `non_nut_strong` and `bluff_catcher`).
#
# Hand classes not in this map block the gate with reason_code
# 'hand_class_<class>'. nut_status not in the allowed set blocks with
# 'nut_status_<status>'. Danger flag overage blocks with
# 'board_too_dangerous'.
HAND_CLASS_GATES: dict = {
    'nuts': (frozenset({'actual_nuts'}), 1),
    'strong_made': (frozenset({'actual_nuts', 'near_nuts'}), 0),
}
ELIGIBLE_HAND_STRENGTHS = frozenset(HAND_CLASS_GATES.keys())

# Streets where induce can fire. River is excluded — no streets left
# to extract on.
ELIGIBLE_STREETS = frozenset({'flop', 'turn'})

# ── Open-spot tunables (Phase B Item 4) ────────────────────────────
#
# The open-spot branch reads `flop_check_then_barrel_rate` (the OOP
# check-then-barrel pattern) instead of `barrel_frequency` (the
# facing-bet barrel pattern). Thresholds start a touch lower than the
# facing-bet branch's MIN_BARREL_FREQUENCY (0.60): TrapBaitBot-style
# opponents barrel ~80% after check-through, so the stat converges
# well above the gate; the lower threshold (0.55) widens the firing
# surface to include moderately-trappy real opponents. Sample floor
# matches the facing-bet branch — 5 opportunities is the minimum
# warmup at which sample_confidence ramps off zero.
#
# Redistribution is flat per spec (Phase B doc §"Item 4 — Design"):
# check=0.70, raise=0.30 split evenly across raise actions. The
# spec does not call for confidence-scaling here — the open-spot
# exploit is more about correctly identifying the spot than about
# trap intensity.
MIN_FLOP_CHECK_THEN_BARREL_FREQUENCY = 0.55
MIN_FLOP_CHECK_THEN_BARREL_OPPORTUNITIES = 5
OPEN_SPOT_CHECK_PROBABILITY = 0.70

# ── OOP tunables (Phase B Item 5) ──────────────────────────────────
#
# Item 5 fills the OOP slots that Items 2 and 4 explicitly left out:
#   - Facing-bet OOP with strong hand → check-raise (Decision B)
#   - Open-spot OOP with strong hand → trap-check (Decision A)
#
# The two branches are independent gate evaluations — Decision B
# implicitly requires hero to have checked the flop (because hero is
# OOP and now facing a bet on the same street), so no cross-decision
# state is needed.
#
# Both branches gate on `cbet_attempt_rate` — the PFR's tendency to
# cbet flop when given a clean opportunity. Threshold 0.70 chosen
# because the stat has dense samples (ticks every time the player is
# PFR on flop) and 0.70 cleanly separates frequent cbetters (Maniac,
# CaseBot in cbet-heavy spots) from selective ones (TAG ~0.45-0.50,
# nit ~0.30). The check-raise branch additionally gates on
# `barrel_frequency` to ensure villain will continue putting money in
# on later streets if hero just calls — softer threshold (0.50) than
# Item 2's 0.60 because the flop check-raise extracts directly.
#
# Redistribution is flat, matching Item 4. Item 2's confidence ramp
# didn't show measurable EV benefit in the matrix, so we don't pay
# its complexity cost here either.
MIN_CBET_ATTEMPT_RATE = 0.70
MIN_POSTFLOP_SEEN_AS_PFR = 5
MIN_OOP_CHECK_RAISE_BARREL_FREQUENCY = 0.50
OOP_TRAP_CHECK_PROBABILITY = 0.80
OOP_CHECK_RAISE_PROBABILITY = 0.80


def _check_hand_class_gate(
    hand_strength: str,
    nut_status: str,
    danger_flag_count: int,
) -> Tuple[bool, str]:
    """Phase B Item 3: per-hand-class gating helper.

    Used by both `should_apply_induce_override` (facing-bet branch) and
    `should_apply_open_spot_induce` (open-spot branch). Each eligible
    hand class has its own nut_status whitelist and danger-flag cap
    (see `HAND_CLASS_GATES`). strong_made trades wider hand-class
    coverage for stricter texture + nut-status requirements.

    Returns `(passed, reason_code)` — same convention as the gate
    functions. On pass returns `(True, 'hand_class_pass')`.
    """
    class_gate = HAND_CLASS_GATES.get(hand_strength)
    if class_gate is None:
        return False, f'hand_class_{hand_strength}'
    allowed_nut_statuses, max_danger_flags = class_gate
    if nut_status not in allowed_nut_statuses:
        return False, f'nut_status_{nut_status}'
    if danger_flag_count > max_danger_flags:
        return False, 'board_too_dangerous'
    return True, 'hand_class_pass'


def _ramp(value: float, start: float, end: float) -> float:
    """Linear ramp from `start` to `end`, clamped to [0, 1].

    Mirrors the pattern in `exploitation._ramp` (private helper used
    by compute_pattern_intensity). Returns 0 at or below `start`,
    1 at or above `end`, linear in between.
    """
    if value <= start:
        return 0.0
    if value >= end:
        return 1.0
    return (value - start) / (end - start)


def compute_call_probability(stats: AggregatedOpponentStats) -> float:
    """Confidence-scaled call probability ∈ [CALL_PROB_MIN, CALL_PROB_MAX].

    Two-axis ramp:
      - Signal magnitude (barrel_frequency 0.60 → 0.85)
      - Sample confidence (barrel_opportunities 10 → 50)

    Multiplied, then linearly mapped to the call-probability range.
    Caller has already verified barrel_frequency >= MIN_BARREL_FREQUENCY
    and barrel_opportunities >= MIN_BARREL_OPPORTUNITIES (i.e. ramp
    inputs are at or above their MIN). At those thresholds intensity=0
    and call_prob=CALL_PROB_MIN.
    """
    rate_intensity = _ramp(
        stats.barrel_frequency,
        RATE_RAMP_MIN,
        RATE_RAMP_MAX,
    )
    sample_confidence = _ramp(
        float(stats.barrel_opportunities),
        OPPS_RAMP_MIN,
        OPPS_RAMP_MAX,
    )
    intensity = rate_intensity * sample_confidence
    return CALL_PROB_MIN + intensity * (CALL_PROB_MAX - CALL_PROB_MIN)


def _raise_actions(available_actions) -> List[str]:
    """All raise-like action keys in the current strategy. Mirrors the
    helper in value_override.py."""
    return [a for a in available_actions if a == 'jam' or a.startswith(('bet_', 'raise_'))]


def should_apply_induce_override(
    *,
    stats: AggregatedOpponentStats,
    hand_strength: str,
    nut_status: str,
    street: str,
    position: str,
    danger_flag_count: int,
    effective_stack_bb: float,
    active_opponent_count: int,
    decision_context: DecisionContext,
    has_call: bool,
    has_fold: bool,
    adaptation_bias: float,
    tilt_factor: float = 1.0,
) -> Tuple[bool, str]:
    """Evaluate the Phase B Item 2 gate. Returns (should_fire, reason_code).

    The reason_code surfaces in the no-op trace so attribution analysis
    can see which gate component blocked. When `should_fire` is True,
    reason_code is `'gate_pass'`.

    Gate checks are ordered cheap → expensive so the early exits are
    fast.
    """
    # Cheap structural checks first.
    if not has_call:
        return False, 'no_call_action'
    if not has_fold:
        # 'fold' in strategy = facing a bet. No fold = not facing a bet.
        return False, 'not_facing_bet'
    if decision_context.facing_all_in:
        # No future streets to extract on once stacks are committed.
        return False, 'facing_all_in'
    if street not in ELIGIBLE_STREETS:
        return False, f'wrong_street_{street}'
    if position != 'IP':
        return False, 'oop_not_supported_phase_a'
    if active_opponent_count != 1:
        return False, 'multiway_not_supported_phase_a'
    if effective_stack_bb < MIN_EFFECTIVE_STACK_BB:
        return False, 'below_stack_floor'

    passed, reason = _check_hand_class_gate(
        hand_strength,
        nut_status,
        danger_flag_count,
    )
    if not passed:
        return False, reason

    # Psychology gate (same shape as value_override / exploitation).
    if adaptation_bias * tilt_factor <= GATING_FLOOR:
        return False, 'psychology_suppressed'

    # Sample-floor + signal-floor on barrel stats.
    if stats.hands_observed < MIN_HANDS_OBSERVED:
        return False, 'cold_start_hands'
    if stats.barrel_opportunities < MIN_BARREL_OPPORTUNITIES:
        return False, 'cold_start_barrel_sample'
    if stats.barrel_frequency < MIN_BARREL_FREQUENCY:
        return False, 'barrel_frequency_below_threshold'

    # Station exclusions — both detectors return False when the stats
    # don't match the pattern, so this is also cheap.
    if _is_passive_with_jams(stats):
        return False, 'opponent_is_jam_station'
    if _is_hyper_passive(stats):
        return False, 'opponent_is_hyper_passive'

    return True, 'gate_pass'


def should_apply_open_spot_induce(
    *,
    stats: AggregatedOpponentStats,
    hand_strength: str,
    nut_status: str,
    street: str,
    position: str,
    danger_flag_count: int,
    effective_stack_bb: float,
    active_opponent_count: int,
    decision_context: DecisionContext,
    has_check: bool,
    has_fold: bool,
    adaptation_bias: float,
    tilt_factor: float = 1.0,
) -> Tuple[bool, str]:
    """Evaluate the Phase B Item 4 open-spot gate.

    Mirrors `should_apply_induce_override` but for the open-spot
    (no-bet) case: hero is IP, free to act, with strong hand, vs a
    villain who shows the check-then-barrel tendency. Returns
    `(should_fire, reason_code)`.
    """
    if not has_check:
        return False, 'no_check_action'
    if has_fold:
        # Facing a bet — handled by the facing-bet branch.
        return False, 'facing_bet_use_facing_branch'
    if decision_context.facing_all_in:
        return False, 'facing_all_in'
    if street not in ELIGIBLE_STREETS:
        return False, f'wrong_street_{street}'
    if position != 'IP':
        return False, 'oop_not_supported_open_spot'
    if active_opponent_count != 1:
        return False, 'multiway_not_supported_open_spot'
    if effective_stack_bb < MIN_EFFECTIVE_STACK_BB:
        return False, 'below_stack_floor'

    passed, reason = _check_hand_class_gate(
        hand_strength,
        nut_status,
        danger_flag_count,
    )
    if not passed:
        return False, reason

    if adaptation_bias * tilt_factor <= GATING_FLOOR:
        return False, 'psychology_suppressed'

    if stats.hands_observed < MIN_HANDS_OBSERVED:
        return False, 'cold_start_hands'
    if stats.flop_check_barrel_opportunities < MIN_FLOP_CHECK_THEN_BARREL_OPPORTUNITIES:
        return False, 'cold_start_flop_check_barrel_sample'
    if stats.flop_check_then_barrel_rate < MIN_FLOP_CHECK_THEN_BARREL_FREQUENCY:
        return False, 'flop_check_barrel_rate_below_threshold'

    if _is_passive_with_jams(stats):
        return False, 'opponent_is_jam_station'
    if _is_hyper_passive(stats):
        return False, 'opponent_is_hyper_passive'

    return True, 'gate_pass'


def compute_open_spot_induce_strategy(
    strategy: StrategyProfile,
) -> StrategyProfile:
    """Redistribute strategy to check / raise per the Item 4 spec.

    Flat split: `OPEN_SPOT_CHECK_PROBABILITY` to `check`, remainder
    evenly across raise-like action keys. Other action keys (fold,
    call quanta, non-raise bets) get zero mass — the open-spot branch
    picks between checking back (trap) and betting (unexploitability).

    If the strategy has no raise actions (pathological for an open
    spot), the full mass goes to check.
    """
    available = list(strategy.action_probabilities.keys())
    raises = _raise_actions(available)

    if not raises:
        return StrategyProfile(action_probabilities={'check': 1.0})

    raise_share = (1.0 - OPEN_SPOT_CHECK_PROBABILITY) / len(raises)
    new_probs = {'check': OPEN_SPOT_CHECK_PROBABILITY}
    for action in raises:
        new_probs[action] = raise_share
    return StrategyProfile(action_probabilities=new_probs)


def should_apply_oop_trap_check(
    *,
    stats: AggregatedOpponentStats,
    hand_strength: str,
    nut_status: str,
    street: str,
    position: str,
    danger_flag_count: int,
    effective_stack_bb: float,
    active_opponent_count: int,
    decision_context: DecisionContext,
    has_check: bool,
    has_fold: bool,
    adaptation_bias: float,
    tilt_factor: float = 1.0,
) -> Tuple[bool, str]:
    """Evaluate the Phase B Item 5 OOP trap-check gate.

    Decision A of the OOP check-raise tech: hero OOP, free to act on
    flop, with strong hand, vs a frequent-cbetter. Force a check (suppress
    bet-for-value) so villain has the chance to cbet and we can
    check-raise. Returns `(should_fire, reason_code)`.
    """
    if not has_check:
        return False, 'no_check_action'
    if has_fold:
        return False, 'facing_bet_use_facing_branch'
    if decision_context.facing_all_in:
        return False, 'facing_all_in'
    if street not in ELIGIBLE_STREETS:
        return False, f'wrong_street_{street}'
    if position != 'OOP':
        return False, 'ip_not_supported_oop_branch'
    if active_opponent_count != 1:
        return False, 'multiway_not_supported_oop_branch'
    if effective_stack_bb < MIN_EFFECTIVE_STACK_BB:
        return False, 'below_stack_floor'

    passed, reason = _check_hand_class_gate(
        hand_strength,
        nut_status,
        danger_flag_count,
    )
    if not passed:
        return False, reason

    if adaptation_bias * tilt_factor <= GATING_FLOOR:
        return False, 'psychology_suppressed'

    if stats.hands_observed < MIN_HANDS_OBSERVED:
        return False, 'cold_start_hands'
    if stats.postflop_seen_as_pfr_count < MIN_POSTFLOP_SEEN_AS_PFR:
        return False, 'cold_start_cbet_sample'
    if stats.cbet_attempt_rate < MIN_CBET_ATTEMPT_RATE:
        return False, 'cbet_attempt_rate_below_threshold'

    if _is_passive_with_jams(stats):
        return False, 'opponent_is_jam_station'
    if _is_hyper_passive(stats):
        return False, 'opponent_is_hyper_passive'

    return True, 'gate_pass'


def compute_oop_trap_check_strategy(
    strategy: StrategyProfile,
) -> StrategyProfile:
    """Redistribute toward check (trap) per Item 5 spec.

    Flat split: `OOP_TRAP_CHECK_PROBABILITY` to `check`, remainder
    evenly across raise-like action keys to keep the line unpolarized.
    Other action keys (fold, call quanta, non-raise bets) get zero
    mass — the trap-check branch picks between checking (trap) and
    betting (balance).

    If the strategy has no raise actions, the full mass goes to check.
    """
    available = list(strategy.action_probabilities.keys())
    raises = _raise_actions(available)

    if not raises:
        return StrategyProfile(action_probabilities={'check': 1.0})

    raise_share = (1.0 - OOP_TRAP_CHECK_PROBABILITY) / len(raises)
    new_probs = {'check': OOP_TRAP_CHECK_PROBABILITY}
    for action in raises:
        new_probs[action] = raise_share
    return StrategyProfile(action_probabilities=new_probs)


def should_apply_oop_check_raise(
    *,
    stats: AggregatedOpponentStats,
    hand_strength: str,
    nut_status: str,
    street: str,
    position: str,
    danger_flag_count: int,
    effective_stack_bb: float,
    active_opponent_count: int,
    decision_context: DecisionContext,
    has_call: bool,
    has_fold: bool,
    adaptation_bias: float,
    tilt_factor: float = 1.0,
) -> Tuple[bool, str]:
    """Evaluate the Phase B Item 5 OOP check-raise gate.

    Decision B of the OOP check-raise tech: hero OOP, facing cbet on
    flop, with strong hand, vs a frequent-cbetter who also barrels.
    Force a raise (the check-raise) instead of smooth-calling. Hero
    being OOP and now facing a bet implies they checked first (HU
    convention: OOP acts first, can only bet/check; donk-leads excluded
    from scope), so no cross-decision state is needed.

    Gated on both `cbet_attempt_rate` (villain cbets often → trap fires
    often) and `barrel_frequency` (villain continues to put money in
    if hero calls → smooth-call has lower EV than raise). Returns
    `(should_fire, reason_code)`.
    """
    if not has_call:
        return False, 'no_call_action'
    if not has_fold:
        return False, 'not_facing_bet'
    if decision_context.facing_all_in:
        return False, 'facing_all_in'
    if street not in ELIGIBLE_STREETS:
        return False, f'wrong_street_{street}'
    if position != 'OOP':
        return False, 'ip_not_supported_oop_branch'
    if active_opponent_count != 1:
        return False, 'multiway_not_supported_oop_branch'
    if effective_stack_bb < MIN_EFFECTIVE_STACK_BB:
        return False, 'below_stack_floor'

    passed, reason = _check_hand_class_gate(
        hand_strength,
        nut_status,
        danger_flag_count,
    )
    if not passed:
        return False, reason

    if adaptation_bias * tilt_factor <= GATING_FLOOR:
        return False, 'psychology_suppressed'

    if stats.hands_observed < MIN_HANDS_OBSERVED:
        return False, 'cold_start_hands'
    if stats.postflop_seen_as_pfr_count < MIN_POSTFLOP_SEEN_AS_PFR:
        return False, 'cold_start_cbet_sample'
    if stats.cbet_attempt_rate < MIN_CBET_ATTEMPT_RATE:
        return False, 'cbet_attempt_rate_below_threshold'
    # The barrel_frequency stat surfaces neutral prior 0.5 when no
    # samples — combined with the postflop_seen_as_pfr_count gate above,
    # at MIN_POSTFLOP_SEEN_AS_PFR samples we expect some barrel sample
    # too, but if the barrel-frequency comes in below MIN we still want
    # to skip (villain cbets but doesn't continue — call has better EV
    # than raise).
    if stats.barrel_frequency < MIN_OOP_CHECK_RAISE_BARREL_FREQUENCY:
        return False, 'barrel_frequency_below_threshold'

    if _is_passive_with_jams(stats):
        return False, 'opponent_is_jam_station'
    if _is_hyper_passive(stats):
        return False, 'opponent_is_hyper_passive'

    return True, 'gate_pass'


def compute_oop_check_raise_strategy(
    strategy: StrategyProfile,
) -> StrategyProfile:
    """Redistribute toward raise (check-raise) per Item 5 spec.

    Flat split: `OOP_CHECK_RAISE_PROBABILITY` to raises, remainder to
    call. Fold gets zero mass — we hold a strong hand. Other action
    keys (check, non-raise bets) get zero mass — we're facing a bet
    and the choice is between raise (trap) and call (balance).

    If the strategy has no raise actions (pathological for a facing-bet
    spot), the full mass goes to call.
    """
    available = list(strategy.action_probabilities.keys())
    raises = _raise_actions(available)

    if not raises:
        return StrategyProfile(action_probabilities={'call': 1.0})

    raise_share = OOP_CHECK_RAISE_PROBABILITY / len(raises)
    new_probs = {'call': 1.0 - OOP_CHECK_RAISE_PROBABILITY}
    for action in raises:
        new_probs[action] = raise_share
    return StrategyProfile(action_probabilities=new_probs)


def compute_induce_override_strategy(
    strategy: StrategyProfile,
    call_probability: float,
) -> StrategyProfile:
    """Redistribute strategy to `call_probability` call / remainder raise.

    The remainder (1 - call_probability) is split evenly across all
    raise-like action keys in the input strategy. Other action keys
    ('fold', 'check', non-raise quanta) get zero probability since
    induce specifically picks between call (trap) and raise (unexploitability).

    If the strategy has no raise actions (pathological for a facing-bet
    spot), the full mass goes to call.
    """
    available = list(strategy.action_probabilities.keys())
    raises = _raise_actions(available)

    if not raises:
        # No raise option — give everything to call. Pathological since
        # induce only fires when facing a bet, but the safety net
        # mirrors value_override's logic.
        return StrategyProfile(action_probabilities={'call': 1.0})

    raise_share = (1.0 - call_probability) / len(raises)
    new_probs = {'call': call_probability}
    for action in raises:
        new_probs[action] = raise_share
    return StrategyProfile(action_probabilities=new_probs)


def apply_induce_override(
    strategy: StrategyProfile,
    *,
    stats: AggregatedOpponentStats,
    hand_strength: str,
    nut_status: str,
    street: str,
    position: str,
    danger_flag_count: int,
    effective_stack_bb: float,
    active_opponent_count: int,
    decision_context: DecisionContext,
    adaptation_bias: float,
    tilt_factor: float = 1.0,
    disable_rules=None,
) -> Tuple[StrategyProfile, InterventionTrace]:
    """Apply the induce override.

    Returns `(new_strategy, trace)`. When the rule doesn't fire,
    `new_strategy is strategy` and the trace's `fired` is False with
    a `reason_code` indicating which gate component blocked.
    """
    if is_rule_disabled(disable_rules, 'induce_override', 'default'):
        return strategy, make_disabled_trace(
            layer='induce_override',
            rule_id='default',
            layer_order=layer_order_for('induce_override'),
        )

    available = strategy.action_probabilities
    has_call = 'call' in available
    has_fold = 'fold' in available
    has_check = 'check' in available

    # 2x2 dispatch on (spot type) × (position):
    #
    #                   IP                              OOP
    #               ┌─────────────────────────────┬─────────────────────────────┐
    #   facing-bet  │ Item 2: smooth call         │ Item 5b: check-raise        │
    #               ├─────────────────────────────┼─────────────────────────────┤
    #   open-spot   │ Item 4: check back          │ Item 5a: trap check         │
    #               └─────────────────────────────┴─────────────────────────────┘
    #
    # has_fold = facing a bet (fold offered). has_check + not has_fold =
    # open spot (free to act). The two action-set conditions are mutually
    # exclusive in standard poker. Within each row, position decides
    # which branch handles the case.
    if has_fold:
        if position == 'IP':
            return _apply_facing_bet_induce(
                strategy,
                stats=stats,
                hand_strength=hand_strength,
                nut_status=nut_status,
                street=street,
                position=position,
                danger_flag_count=danger_flag_count,
                effective_stack_bb=effective_stack_bb,
                active_opponent_count=active_opponent_count,
                decision_context=decision_context,
                has_call=has_call,
                has_fold=has_fold,
                adaptation_bias=adaptation_bias,
                tilt_factor=tilt_factor,
            )
        if position == 'OOP':
            return _apply_oop_check_raise(
                strategy,
                stats=stats,
                hand_strength=hand_strength,
                nut_status=nut_status,
                street=street,
                position=position,
                danger_flag_count=danger_flag_count,
                effective_stack_bb=effective_stack_bb,
                active_opponent_count=active_opponent_count,
                decision_context=decision_context,
                has_call=has_call,
                has_fold=has_fold,
                adaptation_bias=adaptation_bias,
                tilt_factor=tilt_factor,
            )

    if has_check:
        if position == 'IP':
            return _apply_open_spot_induce(
                strategy,
                stats=stats,
                hand_strength=hand_strength,
                nut_status=nut_status,
                street=street,
                position=position,
                danger_flag_count=danger_flag_count,
                effective_stack_bb=effective_stack_bb,
                active_opponent_count=active_opponent_count,
                decision_context=decision_context,
                has_check=has_check,
                has_fold=has_fold,
                adaptation_bias=adaptation_bias,
                tilt_factor=tilt_factor,
            )
        if position == 'OOP':
            return _apply_oop_trap_check(
                strategy,
                stats=stats,
                hand_strength=hand_strength,
                nut_status=nut_status,
                street=street,
                position=position,
                danger_flag_count=danger_flag_count,
                effective_stack_bb=effective_stack_bb,
                active_opponent_count=active_opponent_count,
                decision_context=decision_context,
                has_check=has_check,
                has_fold=has_fold,
                adaptation_bias=adaptation_bias,
                tilt_factor=tilt_factor,
            )

    # Neither facing-bet nor open-spot, OR position not in {IP, OOP} —
    # no decision to redistribute.
    return strategy, make_no_op_trace(
        layer='induce_override',
        rule_id='default',
        layer_order=layer_order_for('induce_override'),
        reason_code='no_actionable_spot',
    )


def _apply_facing_bet_induce(
    strategy: StrategyProfile,
    *,
    stats: AggregatedOpponentStats,
    hand_strength: str,
    nut_status: str,
    street: str,
    position: str,
    danger_flag_count: int,
    effective_stack_bb: float,
    active_opponent_count: int,
    decision_context: DecisionContext,
    has_call: bool,
    has_fold: bool,
    adaptation_bias: float,
    tilt_factor: float,
) -> Tuple[StrategyProfile, InterventionTrace]:
    """Phase B Item 2 facing-bet branch (extracted from apply_induce_override)."""
    should_fire, reason_code = should_apply_induce_override(
        stats=stats,
        hand_strength=hand_strength,
        nut_status=nut_status,
        street=street,
        position=position,
        danger_flag_count=danger_flag_count,
        effective_stack_bb=effective_stack_bb,
        active_opponent_count=active_opponent_count,
        decision_context=decision_context,
        has_call=has_call,
        has_fold=has_fold,
        adaptation_bias=adaptation_bias,
        tilt_factor=tilt_factor,
    )

    if not should_fire:
        return strategy, make_no_op_trace(
            layer='induce_override',
            rule_id='default',
            layer_order=layer_order_for('induce_override'),
            reason_code=reason_code,
        )

    call_probability = compute_call_probability(stats)
    new_strategy = compute_induce_override_strategy(strategy, call_probability)

    summary_before = summarize_strategy(strategy.action_probabilities)
    summary_after = summarize_strategy(new_strategy.action_probabilities)
    primary_before = primary_action(strategy.action_probabilities)
    primary_after = primary_action(new_strategy.action_probabilities)
    effect_size = l1_distance(
        strategy.action_probabilities,
        new_strategy.action_probabilities,
    )

    trace = InterventionTrace(
        layer='induce_override',
        rule_id='default',
        layer_order=layer_order_for('induce_override'),
        fired=True,
        operation=InterventionOperation.OVERRIDE.value,
        effect='smooth_call',
        effect_size=effect_size,
        action_changed=(primary_before != primary_after),
        primary_action_before=primary_before,
        primary_action_after=primary_after,
        reason_code=f'induced_{street}_facing_bet',
        rationale=(
            f'induce override: nuts IP on {street}, '
            f'barrel_freq={stats.barrel_frequency:.2f}, '
            f'barrel_opps={stats.barrel_opportunities}, '
            f'call_prob={call_probability:.2f}, '
            f'stack={effective_stack_bb:.1f} BB → smooth-call to induce barrel'
        ),
        inputs={
            'hand_strength': hand_strength,
            'nut_status': nut_status,
            'street': street,
            'position': position,
            'danger_flag_count': danger_flag_count,
            'effective_stack_bb': round(effective_stack_bb, 2),
            'active_opponent_count': active_opponent_count,
            'barrel_frequency': round(stats.barrel_frequency, 4),
            'barrel_opportunities': stats.barrel_opportunities,
            'third_barrel_frequency': round(stats.third_barrel_frequency, 4),
            'third_barrel_opportunities': stats.third_barrel_opportunities,
            'call_probability': round(call_probability, 4),
            'hands_observed': stats.hands_observed,
        },
        input_strategy_summary=summary_before,
        output_strategy_summary=summary_after,
    )

    return new_strategy, trace


def _apply_open_spot_induce(
    strategy: StrategyProfile,
    *,
    stats: AggregatedOpponentStats,
    hand_strength: str,
    nut_status: str,
    street: str,
    position: str,
    danger_flag_count: int,
    effective_stack_bb: float,
    active_opponent_count: int,
    decision_context: DecisionContext,
    has_check: bool,
    has_fold: bool,
    adaptation_bias: float,
    tilt_factor: float,
) -> Tuple[StrategyProfile, InterventionTrace]:
    """Phase B Item 4 open-spot branch: check back IP to induce villain
    barrel after a check-through flop."""
    should_fire, reason_code = should_apply_open_spot_induce(
        stats=stats,
        hand_strength=hand_strength,
        nut_status=nut_status,
        street=street,
        position=position,
        danger_flag_count=danger_flag_count,
        effective_stack_bb=effective_stack_bb,
        active_opponent_count=active_opponent_count,
        decision_context=decision_context,
        has_check=has_check,
        has_fold=has_fold,
        adaptation_bias=adaptation_bias,
        tilt_factor=tilt_factor,
    )

    if not should_fire:
        return strategy, make_no_op_trace(
            layer='induce_override',
            rule_id='default',
            layer_order=layer_order_for('induce_override'),
            reason_code=reason_code,
        )

    new_strategy = compute_open_spot_induce_strategy(strategy)

    summary_before = summarize_strategy(strategy.action_probabilities)
    summary_after = summarize_strategy(new_strategy.action_probabilities)
    primary_before = primary_action(strategy.action_probabilities)
    primary_after = primary_action(new_strategy.action_probabilities)
    effect_size = l1_distance(
        strategy.action_probabilities,
        new_strategy.action_probabilities,
    )

    trace = InterventionTrace(
        layer='induce_override',
        rule_id='default',
        layer_order=layer_order_for('induce_override'),
        fired=True,
        operation=InterventionOperation.OVERRIDE.value,
        effect='check_back',
        effect_size=effect_size,
        action_changed=(primary_before != primary_after),
        primary_action_before=primary_before,
        primary_action_after=primary_after,
        reason_code=f'induced_{street}_open_spot',
        rationale=(
            f'induce override: {hand_strength} IP on {street} open spot, '
            f'fcb_rate={stats.flop_check_then_barrel_rate:.2f}, '
            f'fcb_opps={stats.flop_check_barrel_opportunities}, '
            f'stack={effective_stack_bb:.1f} BB → check back to induce barrel'
        ),
        inputs={
            'hand_strength': hand_strength,
            'nut_status': nut_status,
            'street': street,
            'position': position,
            'danger_flag_count': danger_flag_count,
            'effective_stack_bb': round(effective_stack_bb, 2),
            'active_opponent_count': active_opponent_count,
            'flop_check_then_barrel_rate': round(
                stats.flop_check_then_barrel_rate,
                4,
            ),
            'flop_check_barrel_opportunities': stats.flop_check_barrel_opportunities,
            'check_probability': OPEN_SPOT_CHECK_PROBABILITY,
            'hands_observed': stats.hands_observed,
        },
        input_strategy_summary=summary_before,
        output_strategy_summary=summary_after,
    )

    return new_strategy, trace


def _apply_oop_trap_check(
    strategy: StrategyProfile,
    *,
    stats: AggregatedOpponentStats,
    hand_strength: str,
    nut_status: str,
    street: str,
    position: str,
    danger_flag_count: int,
    effective_stack_bb: float,
    active_opponent_count: int,
    decision_context: DecisionContext,
    has_check: bool,
    has_fold: bool,
    adaptation_bias: float,
    tilt_factor: float,
) -> Tuple[StrategyProfile, InterventionTrace]:
    """Phase B Item 5 OOP trap-check branch.

    Decision A of the OOP check-raise tech: with a strong hand OOP free
    to act on the flop vs a frequent cbetter, force a check to set up
    the check-raise.
    """
    should_fire, reason_code = should_apply_oop_trap_check(
        stats=stats,
        hand_strength=hand_strength,
        nut_status=nut_status,
        street=street,
        position=position,
        danger_flag_count=danger_flag_count,
        effective_stack_bb=effective_stack_bb,
        active_opponent_count=active_opponent_count,
        decision_context=decision_context,
        has_check=has_check,
        has_fold=has_fold,
        adaptation_bias=adaptation_bias,
        tilt_factor=tilt_factor,
    )

    if not should_fire:
        return strategy, make_no_op_trace(
            layer='induce_override',
            rule_id='default',
            layer_order=layer_order_for('induce_override'),
            reason_code=reason_code,
        )

    new_strategy = compute_oop_trap_check_strategy(strategy)

    summary_before = summarize_strategy(strategy.action_probabilities)
    summary_after = summarize_strategy(new_strategy.action_probabilities)
    primary_before = primary_action(strategy.action_probabilities)
    primary_after = primary_action(new_strategy.action_probabilities)
    effect_size = l1_distance(
        strategy.action_probabilities,
        new_strategy.action_probabilities,
    )

    trace = InterventionTrace(
        layer='induce_override',
        rule_id='default',
        layer_order=layer_order_for('induce_override'),
        fired=True,
        operation=InterventionOperation.OVERRIDE.value,
        effect='trap_check',
        effect_size=effect_size,
        action_changed=(primary_before != primary_after),
        primary_action_before=primary_before,
        primary_action_after=primary_after,
        reason_code=f'induced_{street}_oop_trap_check',
        rationale=(
            f'induce override: {hand_strength} OOP on {street} open spot, '
            f'cbet_attempt_rate={stats.cbet_attempt_rate:.2f}, '
            f'pfr_seen={stats.postflop_seen_as_pfr_count}, '
            f'stack={effective_stack_bb:.1f} BB → check to set check-raise trap'
        ),
        inputs={
            'hand_strength': hand_strength,
            'nut_status': nut_status,
            'street': street,
            'position': position,
            'danger_flag_count': danger_flag_count,
            'effective_stack_bb': round(effective_stack_bb, 2),
            'active_opponent_count': active_opponent_count,
            'cbet_attempt_rate': round(stats.cbet_attempt_rate, 4),
            'postflop_seen_as_pfr_count': stats.postflop_seen_as_pfr_count,
            'check_probability': OOP_TRAP_CHECK_PROBABILITY,
            'hands_observed': stats.hands_observed,
        },
        input_strategy_summary=summary_before,
        output_strategy_summary=summary_after,
    )

    return new_strategy, trace


def _apply_oop_check_raise(
    strategy: StrategyProfile,
    *,
    stats: AggregatedOpponentStats,
    hand_strength: str,
    nut_status: str,
    street: str,
    position: str,
    danger_flag_count: int,
    effective_stack_bb: float,
    active_opponent_count: int,
    decision_context: DecisionContext,
    has_call: bool,
    has_fold: bool,
    adaptation_bias: float,
    tilt_factor: float,
) -> Tuple[StrategyProfile, InterventionTrace]:
    """Phase B Item 5 OOP check-raise branch.

    Decision B of the OOP check-raise tech: with a strong hand OOP
    facing villain's cbet on the flop, force a raise (the check-raise)
    instead of smooth-calling.
    """
    should_fire, reason_code = should_apply_oop_check_raise(
        stats=stats,
        hand_strength=hand_strength,
        nut_status=nut_status,
        street=street,
        position=position,
        danger_flag_count=danger_flag_count,
        effective_stack_bb=effective_stack_bb,
        active_opponent_count=active_opponent_count,
        decision_context=decision_context,
        has_call=has_call,
        has_fold=has_fold,
        adaptation_bias=adaptation_bias,
        tilt_factor=tilt_factor,
    )

    if not should_fire:
        return strategy, make_no_op_trace(
            layer='induce_override',
            rule_id='default',
            layer_order=layer_order_for('induce_override'),
            reason_code=reason_code,
        )

    new_strategy = compute_oop_check_raise_strategy(strategy)

    summary_before = summarize_strategy(strategy.action_probabilities)
    summary_after = summarize_strategy(new_strategy.action_probabilities)
    primary_before = primary_action(strategy.action_probabilities)
    primary_after = primary_action(new_strategy.action_probabilities)
    effect_size = l1_distance(
        strategy.action_probabilities,
        new_strategy.action_probabilities,
    )

    trace = InterventionTrace(
        layer='induce_override',
        rule_id='default',
        layer_order=layer_order_for('induce_override'),
        fired=True,
        operation=InterventionOperation.OVERRIDE.value,
        effect='check_raise',
        effect_size=effect_size,
        action_changed=(primary_before != primary_after),
        primary_action_before=primary_before,
        primary_action_after=primary_after,
        reason_code=f'induced_{street}_oop_check_raise',
        rationale=(
            f'induce override: {hand_strength} OOP on {street} facing cbet, '
            f'cbet_attempt_rate={stats.cbet_attempt_rate:.2f}, '
            f'barrel_freq={stats.barrel_frequency:.2f}, '
            f'pfr_seen={stats.postflop_seen_as_pfr_count}, '
            f'stack={effective_stack_bb:.1f} BB → check-raise to complete trap'
        ),
        inputs={
            'hand_strength': hand_strength,
            'nut_status': nut_status,
            'street': street,
            'position': position,
            'danger_flag_count': danger_flag_count,
            'effective_stack_bb': round(effective_stack_bb, 2),
            'active_opponent_count': active_opponent_count,
            'cbet_attempt_rate': round(stats.cbet_attempt_rate, 4),
            'postflop_seen_as_pfr_count': stats.postflop_seen_as_pfr_count,
            'barrel_frequency': round(stats.barrel_frequency, 4),
            'barrel_opportunities': stats.barrel_opportunities,
            'raise_probability': OOP_CHECK_RAISE_PROBABILITY,
            'hands_observed': stats.hands_observed,
        },
        input_strategy_summary=summary_before,
        output_strategy_summary=summary_after,
    )

    return new_strategy, trace
