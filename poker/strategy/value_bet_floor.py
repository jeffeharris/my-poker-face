"""Value-bet floor (docs/plans/STRUCTURAL_PASSIVITY_PLAN.md §12).

The per-signature leak finder showed the postflop chart **under-bets value in
unopened spots**: it checks the nuts ~42% on the turn, strong made hands
~60-62% on the turn/river, and ~70% as the flop c-bettor. Against a call-happy
opponent (the realistic eval, Jeff_clone WtSD 0.59) that leaves value
uncollected. The gap is in the chart's own frequencies, not the pipeline —
realized aggression already tracks the chart closely.

This layer is the **betting mirror of `defense_floor`**: when the action is
checked to hero (`unopened`) and hero holds a clear value class, pump the total
bet mass up to a floor, drawing from check. It is hand-class gated (NOT
line-gated), so it catches the broad population the multi-street barrel layer
(H1) misses — H1 only fires when hero was the prior-street aggressor, but the
chart under-bets value even when it wasn't.

Intended as a measurement scaffold: A/B its EV behind `enable_value_bet_floor`;
if it proves out, bake the frequencies into `postflop_strategies.json` and
retire the layer (push the knowledge into the situation policy rather than
growing the override pile).

## Pipeline placement
After `multistreet_context`, before `defense_floor`. Unopened-only, so it never
conflicts with defense_floor / math_floor (which act facing a bet). It defers
when an upstream override already replaced the distribution (prior_layer_fired)
and feeds prior_layer_fired downstream, mirroring defense_floor.
"""

from typing import Optional, Tuple

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
from .multistreet_context import _pump_bet
from .strategy_profile import StrategyProfile

LAYER = 'value_bet_floor'

# Bet-frequency floors for clear value classes when checked to (unopened).
# Calibrated above the chart's observed under-betting (nuts ~0.58, strong ~0.38
# realized on the turn) toward standard value frequencies. A/B-tunable.
VALUE_BET_FLOOR_TARGET = {
    'nuts': 0.85,
    'strong_made': 0.70,
}


def apply_value_bet_floor(
    strategy: StrategyProfile,
    *,
    hand_class: str,
    action_context: str,
    prior_layer_fired: bool = False,
    disable_rules=None,
) -> Tuple[StrategyProfile, InterventionTrace]:
    """Pump bet mass to a value floor in unopened spots with a value hand.

    Args:
        strategy: distribution out of the multistreet layer.
        hand_class: simplify_hand_class output.
        action_context: node.facing_action ('unopened'/'facing_bet'/...).
        prior_layer_fired: True iff an upstream override already replaced the
            distribution this decision — defer (mirror defense_floor).
        disable_rules: ablation set; (LAYER, 'default').

    Returns `(new_strategy, trace)`; `new_strategy is strategy` on no-op.
    """
    order = layer_order_for(LAYER)

    if is_rule_disabled(disable_rules, LAYER, 'default'):
        return strategy, make_disabled_trace(LAYER, 'default', order)

    if prior_layer_fired:
        return strategy, make_no_op_trace(
            LAYER, 'default', order, reason_code='prior_override_active')

    if action_context != 'unopened':
        return strategy, make_no_op_trace(
            LAYER, 'default', order, reason_code='not_unopened')

    if hand_class not in VALUE_BET_FLOOR_TARGET:
        return strategy, make_no_op_trace(
            LAYER, 'default', order, reason_code='hand_class_not_value')

    target = VALUE_BET_FLOOR_TARGET[hand_class]
    new = _pump_bet(strategy, target)
    if new is strategy:
        return strategy, make_no_op_trace(
            LAYER, 'default', order,
            reason_code='no_bet_action_or_above_target')

    return new, InterventionTrace(
        layer=LAYER,
        rule_id='default',
        layer_order=order,
        fired=True,
        operation=InterventionOperation.OVERRIDE.value,
        effect='pump_bet',
        effect_size=l1_distance(
            strategy.action_probabilities, new.action_probabilities),
        action_changed=(
            primary_action(strategy.action_probabilities)
            != primary_action(new.action_probabilities)),
        primary_action_before=primary_action(strategy.action_probabilities),
        primary_action_after=primary_action(new.action_probabilities),
        replaced_prior_action=True,
        reason_code=f'value_bet_floor_{hand_class}',
        rationale=(
            f'value-bet floor: unopened {hand_class} pumped to {target:.2f} bet'),
        inputs={'hand_class': hand_class, 'target': round(target, 4)},
        input_strategy_summary=summarize_strategy(strategy.action_probabilities),
        output_strategy_summary=summarize_strategy(new.action_probabilities),
    )
