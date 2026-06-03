"""Gated stab-defense (OVERBET_BALANCING.md §5i/§5j).

The dual of the river-bluff gate. The capped-checking-range probe showed the bot
folds ~41% to half-pot stabs (vs 22% to raises) — after it checks, its range is
weaker, so a frequent stabber profits (~-1.2 bb/100). This layer widens the bot's
defense facing a postflop bet — shifting a fraction of `fold` mass to `call` — but
ONLY vs a detected frequent stabber (so it never costs vs the fish, who don't stab;
symmetric to the river-bluff gate firing only vs over-folders). Default OFF
(`intensity=0.0`) → byte-identical.

Honest risk: a prior "call wider vs a maniac" layer measured INERT (clamped near
GTO, wrong spots). This targets a different spot (facing a bet after checking, where
the bot demonstrably over-folds 41%), so it has room — but it must be MEASURED vs
the adaptive stabber, not assumed.
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
from .strategy_profile import StrategyProfile

LAYER = 'stab_defense'
DEFAULT_MIN_STAB = 0.50  # opponent stab-frequency at/above which the gate fires


def apply_stab_defense(
    strategy: StrategyProfile,
    *,
    action_context: str,
    street: Optional[str],
    stab_read: Optional[float],
    intensity: float = 0.0,
    min_stab: float = DEFAULT_MIN_STAB,
    prior_layer_fired: bool = False,
    disable_rules=None,
) -> Tuple[StrategyProfile, InterventionTrace]:
    """Facing a postflop bet vs a detected stabber, shift `intensity` of the
    `fold` mass to `call` (bluff-catch the stab wider). Gated on `stab_read`
    (opponent stab frequency); None / below `min_stab` → no-op (value-only
    defense, safe vs the fish). Returns ``(new_strategy, trace)``."""
    order = layer_order_for(LAYER)

    if prior_layer_fired:
        return strategy, make_no_op_trace(
            LAYER, 'default', order, reason_code='prior_override_active'
        )

    gate_ok = (
        intensity > 0.0
        and action_context in ('facing_bet', 'facing_raise')
        and (street or '').upper() in ('FLOP', 'TURN', 'RIVER')
        and stab_read is not None
        and stab_read >= min_stab
    )
    if not gate_ok:
        return strategy, make_no_op_trace(LAYER, 'default', order, reason_code='gates_not_met')

    if is_rule_disabled(disable_rules, LAYER, 'defend'):
        return strategy, make_disabled_trace(LAYER, 'defend', order)

    probs = dict(strategy.action_probabilities)
    fold_mass = probs.get('fold', 0.0)
    if fold_mass <= 0.0 or 'call' not in probs and 'call' not in strategy.action_probabilities:
        # Nothing to shift, or no call action available.
        if fold_mass <= 0.0:
            return strategy, make_no_op_trace(LAYER, 'defend', order, reason_code='no_fold_mass')
    move = fold_mass * intensity
    new = dict(probs)
    remaining = fold_mass - move
    if remaining > 1e-9:
        new['fold'] = remaining
    else:
        new.pop('fold', None)
    new['call'] = new.get('call', 0.0) + move
    new_strategy = StrategyProfile(action_probabilities=new)

    return new_strategy, InterventionTrace(
        layer=LAYER,
        rule_id='defend',
        layer_order=order,
        fired=True,
        operation=InterventionOperation.OVERRIDE.value,
        effect='shift_fold_to_call_vs_stabber',
        effect_size=l1_distance(strategy.action_probabilities, new_strategy.action_probabilities),
        action_changed=(
            primary_action(strategy.action_probabilities)
            != primary_action(new_strategy.action_probabilities)
        ),
        primary_action_before=primary_action(strategy.action_probabilities),
        primary_action_after=primary_action(new_strategy.action_probabilities),
        replaced_prior_action=True,
        reason_code='stab_defense',
        rationale=(
            f'stab defense: action_context={action_context} street={street} '
            f'stab_read={stab_read:.2f} intensity={intensity:.2f}'
        ),
        inputs={
            'action_context': action_context,
            'street': street,
            'stab_read': round(stab_read, 4),
            'intensity': round(intensity, 4),
        },
        input_strategy_summary=summarize_strategy(strategy.action_probabilities),
        output_strategy_summary=summarize_strategy(new_strategy.action_probabilities),
    )
