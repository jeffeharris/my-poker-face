"""
Short-stack heuristic: depth-aware action shaping.

Step B of the original Phase 6 plan
(see docs/plans/PHASE_6_OPPONENT_EXPLOITATION.md § Step B).

The strategy table is calibrated for ~100 BB deep play. As effective
stack shortens, medium-sized raises become structurally bad: you commit
a meaningful fraction of your stack but leave yourself awkwardly
positioned for the next street. Optimal play below ~20 BB shifts
toward "raise (jam) or fold" — preserve the option of folding or
fully commit. Mid-sized raises (raise_3bb at 12bb stack, bet_67 at 8bb
effective) are EV-negative because they invite re-raises you can't
correctly respond to.

This module is the LIGHT version of that fix. It doesn't replace the
strategy table with Nash push/fold charts (that's deferred work). It
just suppresses medium-raise probability mass at short depth and
redistributes it to `jam` (or fold if jam isn't legal).

## Pipeline placement

```
exploitation → value_override → short_stack → math_floor → sample
```

Sits AFTER value_override (which handles strong hands vs aggressors,
independent of depth) and BEFORE math_floor (which is the final safety
net for pot-committed / short-stack-forced spots).

## Depth buckets

| effective_stack_bb | medium-raise suppression |
|---|---|
| > 20 BB        | 0% (no change — deep enough for normal play)     |
| 10-20 BB       | linear ramp (50% at 15 BB; 80% at 12 BB)         |
| ≤ 10 BB        | 100% (all non-jam raises collapsed)              |

No threshold at 5 BB by design — between 3-10 BB the behavior is
uniform ("all raises become jams"), and math floor catches the truly
desperate regime (`stack_bb < 3` → forced all-in/call).

## What counts as "medium raise"

Any abstract action in `strategy.action_probabilities` that starts with
'raise_' or 'bet_' AND isn't 'jam'. The strategy table emits these
labels; we suppress them in favor of either 'jam' (preferred when legal)
or 'fold' (fallback).
"""

from typing import List, Tuple

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


# Depth thresholds — adjusting these is the main calibration knob if
# Step B's effect proves too weak / strong.
DEPTH_DEEP_BB = 20.0        # >= this: no suppression
DEPTH_SHORT_BB = 10.0       # <= this: full suppression


def medium_raise_suppression_factor(effective_stack_bb: float) -> float:
    """Linear ramp from 0% suppression at 20 BB to 100% at 10 BB.

    Returns a value in [0.0, 1.0]. Used to scale how much of the medium
    raise probability mass gets redistributed to jam/fold.
    """
    if effective_stack_bb >= DEPTH_DEEP_BB:
        return 0.0
    if effective_stack_bb <= DEPTH_SHORT_BB:
        return 1.0
    # Linear interpolation between 20 BB (factor=0) and 10 BB (factor=1)
    return (DEPTH_DEEP_BB - effective_stack_bb) / (DEPTH_DEEP_BB - DEPTH_SHORT_BB)


def _is_medium_raise(action: str) -> bool:
    """A medium raise is any non-jam raise/bet action label."""
    if action == 'jam':
        return False
    return action.startswith(('raise_', 'bet_'))


def apply_short_stack_heuristics(
    strategy: StrategyProfile,
    effective_stack_bb: float,
    legal_actions: List[str],
    disable_rules=None,
) -> Tuple[StrategyProfile, InterventionTrace]:
    """Suppress medium-raise mass when effective stack is short.

    Args:
        strategy: Output of upstream pipeline steps (table + personality
            + exploitation + value_override).
        effective_stack_bb: Smaller of hero's stack and the largest
            active opponent's stack, expressed in big blinds.
        legal_actions: Engine-level legal action labels at this decision
            (e.g. ['fold', 'call', 'raise', 'all_in']). Used to decide
            whether suppressed mass can flow to 'jam' (requires
            'all_in') or must fall back to 'fold'.

    Returns:
        `(StrategyProfile, InterventionTrace)`. The strategy is the
        input redistributed when short-stack conditions apply; the
        trace records `operation='clamp'` (per Codex r3 disambiguation:
        we BOUND the medium-raise mass, the action is still in the
        distribution though possibly at zero, rather than VETOing it).
        Returns the input unchanged with a no-op trace at deep stacks
        (>20 BB) or when no medium raise actions are present.
    """
    # Phase 7.6 Step 5: ablation hook. Skip if rule is disabled.
    if is_rule_disabled(disable_rules, 'short_stack', 'default'):
        return strategy, make_disabled_trace(
            layer='short_stack', rule_id='default',
            layer_order=layer_order_for('short_stack'),
        )

    factor = medium_raise_suppression_factor(effective_stack_bb)
    if factor == 0.0:
        return strategy, make_no_op_trace(
            layer='short_stack', rule_id='default',
            layer_order=layer_order_for('short_stack'),
            reason_code='stack_deep',
        )

    medium_raises = [
        a for a in strategy.action_probabilities
        if _is_medium_raise(a)
        and strategy.action_probabilities[a] > 0.0
    ]
    if not medium_raises:
        return strategy, make_no_op_trace(
            layer='short_stack', rule_id='default',
            layer_order=layer_order_for('short_stack'),
            reason_code='no_medium_raises_in_strategy',
        )

    can_jam = 'all_in' in legal_actions or 'jam' in strategy.action_probabilities
    sink_action = 'jam' if can_jam else 'fold'
    # If neither 'jam' nor 'fold' is acceptable, give up (no-op rather
    # than corrupt the distribution).
    if sink_action == 'fold' and 'fold' not in legal_actions:
        return strategy, make_no_op_trace(
            layer='short_stack', rule_id='default',
            layer_order=layer_order_for('short_stack'),
            reason_code='no_legal_sink_action',
        )

    # Build the redistributed distribution.
    new_dist = dict(strategy.action_probabilities)
    redistributed = 0.0
    for action in medium_raises:
        original = new_dist[action]
        keep = original * (1.0 - factor)
        new_dist[action] = keep
        redistributed += original - keep

    new_dist[sink_action] = new_dist.get(sink_action, 0.0) + redistributed
    modified = StrategyProfile(action_probabilities=new_dist)

    effect_size = l1_distance(strategy.action_probabilities, new_dist)
    primary_before = primary_action(strategy.action_probabilities)
    primary_after = primary_action(new_dist)

    return modified, InterventionTrace(
        layer='short_stack',
        rule_id='default',
        layer_order=layer_order_for('short_stack'),
        fired=True,
        operation=InterventionOperation.CLAMP.value,
        effect='distribution_clamped',
        effect_size=round(effect_size, 4),
        action_changed=(primary_before != primary_after),
        primary_action_before=primary_before,
        primary_action_after=primary_after,
        replaced_prior_action=False,
        preserved_prior_intent=True,
        reason_code=f'depth_clamp_sink_{sink_action}',
        rationale=(
            f"Short-stack medium-raise suppression at {effective_stack_bb:.1f} BB; "
            f"factor={factor:.2f}, redistributed {redistributed:.3f} to {sink_action}"
        ),
        confidence=round(factor, 4),
        inputs={
            'effective_stack_bb': round(effective_stack_bb, 2),
            'suppression_factor': round(factor, 4),
            'sink_action': sink_action,
        },
        input_strategy_summary=summarize_strategy(strategy.action_probabilities),
        output_strategy_summary=summarize_strategy(new_dist),
        extra={
            'medium_raises_suppressed': sorted(medium_raises),
            'redistributed_mass': round(redistributed, 4),
        },
    )
