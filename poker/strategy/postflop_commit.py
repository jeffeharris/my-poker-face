"""Postflop commit heuristic: get stacks in with value at low SPR.

Companion to the SPR fallback in `strategy_table.lookup_postflop_with_fallback`.
That fallback recovers a real (high-SPR) strategy for short-stack spots that
would otherwise hit the passive conservative default. This layer then pushes
that strategy toward *commitment* when the stack-to-pot ratio is genuinely
low — because at SPR < 2 a strong made hand wants the money in, not a small
bet it might have to fold to a raise or a check-back it never gets paid on.

The diagnosed leak (`SOLVER_CHART_SCOPE.md`): at 25bb the bot checked the
nuts ~89% unopened and raised facing a bet ~1% (AggFactor 0.06 vs 0.27 at
100bb). The SPR fallback fixes most of the "no strategy at all" problem; this
layer fixes the residual "bets too small / flats instead of committing" at low
SPR.

## What it does

For a **value** hand (`nuts` / `strong_made`) at **low** SPR, redistribute a
class-specific fraction of the *non-jam* probability mass — the passive action
(`check` unopened / `call` facing a bet) **and** the small bets/raises — into
`jam`. At SPR < 2 a bet already commits a large fraction of the stack, so
folding small sizes into a jam loses little sizing nuance and (vs a calling
station) extracts maximally while guaranteeing the money goes in.

Marginal/weak hands and draws are untouched (committing those at low SPR is
how you stack off behind). Pure-fold spots are untouched.

## Pipeline placement

```
... → personality → exploitation → ... → short_stack → postflop_commit → math_floor
```

Runs late (just before math_floor) as a structural commitment floor on value
hands — personality/exploitation cannot un-commit the nuts at SPR < 2. Only
fires postflop (gates on `spr_bucket`, which is None/absent preflop).
"""

from typing import List, Optional, Tuple

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


COMMIT_SPR_BUCKET = 'low'
VALUE_CLASSES = frozenset({'nuts', 'strong_made'})

# Fraction of *non-jam* mass funneled into `jam`, per value class. The nuts
# commit hard; strong_made keeps more small-bet/flat sizing (it can be behind
# and wants the option to pot-control on the rare bad runout). Calibration
# surface — tune against measure_passivity.
_COMMIT_FRACTION = {
    'nuts': 0.85,
    'strong_made': 0.55,
}


def _passive_action(facing_action: str) -> str:
    """The passive label to drain at this node."""
    return 'check' if facing_action == 'unopened' else 'call'


def _is_movable_aggressive(action: str) -> bool:
    """Non-jam bet/raise mass that low SPR should upsize into a jam."""
    if action == 'jam':
        return False
    return action.startswith(('bet_', 'raise_'))


def apply_postflop_commit(
    strategy: StrategyProfile,
    spr_bucket: Optional[str],
    hand_class: Optional[str],
    facing_action: str,
    legal_actions: List[str],
    disable_rules=None,
) -> Tuple[StrategyProfile, InterventionTrace]:
    """Funnel value-hand mass into `jam` at low SPR. See module docstring.

    Returns `(StrategyProfile, InterventionTrace)`. No-op (unchanged strategy)
    when SPR isn't low, the hand isn't a value class, jamming isn't legal, or
    there's no non-jam mass to move.
    """
    order = layer_order_for('postflop_commit')
    if is_rule_disabled(disable_rules, 'postflop_commit', 'default'):
        return strategy, make_disabled_trace(
            layer='postflop_commit', rule_id='default', layer_order=order,
        )

    if spr_bucket != COMMIT_SPR_BUCKET:
        return strategy, make_no_op_trace(
            layer='postflop_commit', rule_id='default', layer_order=order,
            reason_code='spr_not_low',
        )
    if hand_class not in VALUE_CLASSES:
        return strategy, make_no_op_trace(
            layer='postflop_commit', rule_id='default', layer_order=order,
            reason_code='not_value_class',
        )
    # Commit means jam; if we can't jam there's nothing to do.
    if 'all_in' not in legal_actions and 'jam' not in strategy.action_probabilities:
        return strategy, make_no_op_trace(
            layer='postflop_commit', rule_id='default', layer_order=order,
            reason_code='no_jam_available',
        )

    fraction = _COMMIT_FRACTION[hand_class]
    passive = _passive_action(facing_action)
    movable = [
        a for a, p in strategy.action_probabilities.items()
        if p > 0.0 and (a == passive or _is_movable_aggressive(a))
    ]
    if not movable:
        return strategy, make_no_op_trace(
            layer='postflop_commit', rule_id='default', layer_order=order,
            reason_code='no_movable_mass',
        )

    new_dist = dict(strategy.action_probabilities)
    redistributed = 0.0
    for action in movable:
        original = new_dist[action]
        keep = original * (1.0 - fraction)
        new_dist[action] = keep
        redistributed += original - keep
    new_dist['jam'] = new_dist.get('jam', 0.0) + redistributed
    modified = StrategyProfile(action_probabilities=new_dist)

    effect_size = l1_distance(strategy.action_probabilities, new_dist)
    primary_before = primary_action(strategy.action_probabilities)
    primary_after = primary_action(new_dist)

    return modified, InterventionTrace(
        layer='postflop_commit',
        rule_id='default',
        layer_order=order,
        fired=True,
        operation=InterventionOperation.CLAMP.value,
        effect='value_committed_to_jam',
        effect_size=round(effect_size, 4),
        action_changed=(primary_before != primary_after),
        primary_action_before=primary_before,
        primary_action_after=primary_after,
        replaced_prior_action=False,
        preserved_prior_intent=True,
        reason_code=f'low_spr_commit_{hand_class}',
        rationale=(
            f"Low-SPR {hand_class} commit: funneled {redistributed:.3f} of "
            f"non-jam mass into jam (fraction={fraction:.2f})"
        ),
        confidence=round(fraction, 4),
        inputs={
            'spr_bucket': spr_bucket,
            'hand_class': hand_class,
            'facing_action': facing_action,
            'commit_fraction': fraction,
        },
        input_strategy_summary=summarize_strategy(strategy.action_probabilities),
        output_strategy_summary=summarize_strategy(new_dist),
        extra={
            'movable_actions': sorted(movable),
            'redistributed_mass': round(redistributed, 4),
        },
    )
