"""Induce override: smooth-call instead of raise vs detected multi-street barrelers.

Phase A of the induce_override initiative. See
docs/plans/INDUCE_OVERRIDE_PHASE_A.md for full design + testable hypothesis.

## What this does

When hero has the nuts on a dry flop/turn IP and faces a bet from an
opponent whose AggregatedOpponentStats flag them as a multi-street
barreler (high AF_postflop + high cbet_attempt_rate with sufficient
sample), this layer replaces the strategy distribution with **100%
call** — capturing the bluff sequence across multiple streets instead
of ending it with a raise.

The 100% call is Phase A's validation-mode redistribution. None of
Phase A's matchup villains adapt to a smooth-call line; the 0.85/0.15
unexploitability mix is Phase B work once adaptive opponents are in
the matrix.

## Architectural placement

Sits IMMEDIATELY BEFORE `_apply_value_override` in the postflop
pipeline. When induce fires, value_override defers via its own
`prior_layer_fired` check. The two are mutually exclusive by gate
construction: value_override fires on hyper_aggressive opponents with
nuts/strong_made/strong; induce fires on a narrower subset
(barreler-proxy opponent + nuts only + dry board + IP + ≥40 BB +
flop/turn). When both gates match the same decision, induce wins.

## Gate (all conditions required)

- Facing a bet (`'fold' in strategy.action_probabilities`)
- Hero in position (`node.position == 'IP'`)
- Street is flop or turn (no value in trapping on the river)
- Hand is `actual_nuts` (`hand_strength == 'nuts'` AND `nut_status == 'actual_nuts'`)
- Dry board (`len(node.danger_flags) <= 1`)
- Effective stack ≥ 40 BB (need room for turn + river barrels)
- Barreler proxy: `AF_postflop >= 3.0` AND `cbet_attempt_rate >= 0.70`
- Sample floor: `hands_observed >= 10` AND `postflop_seen_as_pfr_count >= 10`
- Not a station (`not _is_passive_with_jams` AND `not _is_hyper_passive`)
- Not facing all-in (no future streets to extract on)
- Heads-up only (`active_opponent_count == 1`)
- Psychology gate: `adaptation_bias * tilt_factor > GATING_FLOOR`
"""

from typing import Tuple

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

# ── Gate tunables (Phase A) ────────────────────────────────────────

# Barreler proxy thresholds. Below these, induce stays off.
MIN_AGGRESSION_FACTOR_POSTFLOP = 3.0
MIN_CBET_ATTEMPT_RATE = 0.70

# Sample-floor thresholds. Prevents firing on cold-start samples.
MIN_HANDS_OBSERVED = 10
MIN_POSTFLOP_SEEN_AS_PFR = 10

# Stack-depth floor in BB. Below 40 BB, the SPR after a flop call is
# too low to extract meaningful turn/river barrels.
MIN_EFFECTIVE_STACK_BB = 40.0

# Maximum board-danger flags (paired_board, four_flush_board, etc.)
# allowed for the rule to fire. 0 = strict dry; 1 = one mild flag.
MAX_DANGER_FLAGS = 1

# Hand-strength + nut-status gate. Phase A is `actual_nuts` only.
ELIGIBLE_HAND_STRENGTH = 'nuts'
ELIGIBLE_NUT_STATUS = 'actual_nuts'

# Streets where induce can fire. River is excluded — no streets left
# to extract on.
ELIGIBLE_STREETS = frozenset({'flop', 'turn'})


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
    """Evaluate the Phase A gate. Returns (should_fire, reason_code).

    The reason_code surfaces in the no-op trace so attribution analysis
    can see which gate component blocked. When `should_fire` is True,
    reason_code is `'gate_pass'`.

    Gate checks are ordered cheap → expensive so the early exits are
    fast — pure-Python dataclass attribute reads first, then the
    station-detector calls (which iterate through tendencies fields).
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
    if hand_strength != ELIGIBLE_HAND_STRENGTH:
        return False, f'hand_class_{hand_strength}'
    if nut_status != ELIGIBLE_NUT_STATUS:
        return False, f'nut_status_{nut_status}'
    if danger_flag_count > MAX_DANGER_FLAGS:
        return False, 'board_too_dangerous'

    # Psychology gate (same shape as value_override / exploitation).
    if adaptation_bias * tilt_factor <= GATING_FLOOR:
        return False, 'psychology_suppressed'

    # Sample-floor checks before barreler proxy — both are cheap field
    # reads but the sample floor is more often the blocker in fresh
    # opponent models.
    if stats.hands_observed < MIN_HANDS_OBSERVED:
        return False, 'cold_start_hands'
    if stats.postflop_seen_as_pfr_count < MIN_POSTFLOP_SEEN_AS_PFR:
        return False, 'cold_start_cbet_sample'

    # Barreler proxy.
    if stats.aggression_factor_postflop < MIN_AGGRESSION_FACTOR_POSTFLOP:
        return False, 'af_postflop_below_threshold'
    if stats.cbet_attempt_rate < MIN_CBET_ATTEMPT_RATE:
        return False, 'cbet_rate_below_threshold'

    # Station exclusions — both detectors return False when the stats
    # don't match the pattern, so this is also cheap.
    if _is_passive_with_jams(stats):
        return False, 'opponent_is_jam_station'
    if _is_hyper_passive(stats):
        return False, 'opponent_is_hyper_passive'

    return True, 'gate_pass'


def compute_induce_override_strategy(
    strategy: StrategyProfile,
) -> StrategyProfile:
    """Replace the strategy distribution with 100% call.

    Phase A's validation-mode redistribution. The full 1.00 maximizes
    measurable signal during ablation testing against static villains.
    Phase B switches to 0.85 call / 0.15 raise (or confidence-scaled)
    once adaptive opponents enter the matrix.

    Does not invent a 'call' action — caller already gated on it being
    present in the distribution.
    """
    return StrategyProfile(action_probabilities={'call': 1.0})


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

    new_strategy = compute_induce_override_strategy(strategy)

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
            f'AF_pf={stats.aggression_factor_postflop:.2f}, '
            f'cbet_rate={stats.cbet_attempt_rate:.2f}, '
            f'pfr_seen={stats.postflop_seen_as_pfr_count}, '
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
            'aggression_factor_postflop': round(
                stats.aggression_factor_postflop, 4,
            ),
            'cbet_attempt_rate': round(stats.cbet_attempt_rate, 4),
            'postflop_seen_as_pfr_count': stats.postflop_seen_as_pfr_count,
            'hands_observed': stats.hands_observed,
        },
        input_strategy_summary=summary_before,
        output_strategy_summary=summary_after,
    )

    return new_strategy, trace
