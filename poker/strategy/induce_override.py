"""Induce override: smooth-call instead of raise vs detected multi-street barrelers.

Phase B Item 2: switches the gate to read `barrel_frequency` directly
(Phase B Item 1 ships the stat). Replaces Phase A's AF_pf × cbet_attempt
proxy. Also replaces the fixed 1.00 call redistribution with a
confidence-scaled mix in [0.70, 0.90].

See:
- docs/plans/INDUCE_OVERRIDE_PHASE_A.md — original design, shipped
- docs/plans/INDUCE_OVERRIDE_PHASE_B.md — Item 1 + Item 2 specs

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
    AggregatedOpponentStats,
    DecisionContext,
    GATING_FLOOR,
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
RATE_RAMP_MIN = 0.60   # below this the gate doesn't fire
RATE_RAMP_MAX = 0.85   # at/above this the rate axis saturates
OPPS_RAMP_MIN = 5.0    # aligned with MIN_BARREL_OPPORTUNITIES
OPPS_RAMP_MAX = 50.0   # at/above this the sample axis saturates
CALL_PROB_MIN = 0.70   # minimum trap intensity when gate fires
CALL_PROB_MAX = 0.90   # maximum trap intensity (preserves unexploitability)

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
    'nuts':        (frozenset({'actual_nuts'}),                    1),
    'strong_made': (frozenset({'actual_nuts', 'near_nuts'}),       0),
}
ELIGIBLE_HAND_STRENGTHS = frozenset(HAND_CLASS_GATES.keys())

# Streets where induce can fire. River is excluded — no streets left
# to extract on.
ELIGIBLE_STREETS = frozenset({'flop', 'turn'})


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
        stats.barrel_frequency, RATE_RAMP_MIN, RATE_RAMP_MAX,
    )
    sample_confidence = _ramp(
        float(stats.barrel_opportunities), OPPS_RAMP_MIN, OPPS_RAMP_MAX,
    )
    intensity = rate_intensity * sample_confidence
    return CALL_PROB_MIN + intensity * (CALL_PROB_MAX - CALL_PROB_MIN)


def _raise_actions(available_actions) -> List[str]:
    """All raise-like action keys in the current strategy. Mirrors the
    helper in value_override.py."""
    return [
        a for a in available_actions
        if a == 'jam' or a.startswith(('bet_', 'raise_'))
    ]


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

    # Phase B Item 3: per-hand-class gating. Each eligible class has
    # its own nut_status whitelist and danger-flag cap (see
    # HAND_CLASS_GATES). strong_made trades wider hand-class coverage
    # for stricter texture + nut-status requirements.
    class_gate = HAND_CLASS_GATES.get(hand_strength)
    if class_gate is None:
        return False, f'hand_class_{hand_strength}'
    allowed_nut_statuses, max_danger_flags = class_gate
    if nut_status not in allowed_nut_statuses:
        return False, f'nut_status_{nut_status}'
    if danger_flag_count > max_danger_flags:
        return False, 'board_too_dangerous'

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
            layer='induce_override', rule_id='default',
            layer_order=layer_order_for('induce_override'),
        )

    available = strategy.action_probabilities
    has_call = 'call' in available
    has_fold = 'fold' in available

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
            layer='induce_override', rule_id='default',
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
