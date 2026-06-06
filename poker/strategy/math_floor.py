"""Math floor for tiered bot decisions.

Strategy tables encode solver frequencies for abstract buckets (hand class,
texture, position, SPR-bucket, etc.) but don't capture the immediate
cost-to-call vs pot-size vs stack arithmetic of any single decision. In
pot-committed, short-stack, or trivial-pot-odds spots, raw table sampling
produces clearly -EV folds.

This module is the dedicated math floor: a post-distortion, pre-sample
override that detects those three classic leak conditions (mirrored from
the prompt-injection rules the hybrid path already uses, see
poker/prompts/CLAUDE.md) and replaces the strategy with a math-driven
distribution that calls or jams.

Architectural invariant #3 in TIERED_BOT_ARCHITECTURE.md says personality
distortion never overrides solver support. This floor sits *outside* the
personality system: it overrides when arithmetic demands it, regardless
of archetype.
"""

import logging
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

logger = logging.getLogger(__name__)


# Thresholds taken from the spec's prompt-injection rules in
# poker/prompts/CLAUDE.md so that hybrid and tiered paths agree on what
# counts as "pot-odds-obvious".
SHORT_STACK_BB = 3.0  # Below 3 BB: push/fold required
TINY_POT_ODDS_RATIO = 0.05  # cost / (cost + pot) <= 5%, i.e. ~20:1 odds — matches the pot_committed prompt-injection rule
TINY_CALL_BB_THRESHOLD = 5.0  # AND cost_to_call < 5 BB in absolute terms (don't fire on routine deep-stack pot-odds spots)


def apply_pot_odds_floor(
    strategy: StrategyProfile,
    cost_to_call: int,
    pot_total: int,
    player_stack: int,
    player_bet: int,
    big_blind: int,
    legal_actions: List[str],
    disable_rules=None,
) -> Tuple[StrategyProfile, InterventionTrace]:
    """Override strategy when immediate arithmetic mandates a call/jam.

    Three trigger conditions, in order of priority:

    1. **Short stack push/fold** (`stack_bb < 3`): below this depth, fold
       equity is zero — any defensible hand should be all-in. Folding
       just means the blinds eliminate you anyway. Target action: `all_in`
       if legal, else `call`.

    2. **Pot committed** (`player_bet > player_stack`): you've already
       invested more chips than you have left. Folding forfeits a large
       sunk-cost claim on the pot to save very little. Target: `call`.

    3. **Tiny pot odds** (`cost / (cost + pot) <= 0.05`, the
       `TINY_POT_ODDS_RATIO` constant): need ≤5% equity to break even
       (and `cost_to_call < TINY_CALL_BB_THRESHOLD` so it doesn't fire on
       routine deep-stack spots). Any non-zero-equity hand is +EV to call.
       Target: `call`.

    Phase 7.6 (Step 4): returns `(strategy, trace)`. The legacy second
    return value (rule name) is now encoded in `trace.reason_code`
    (one of `short_stack`, `pot_committed`, `tiny_pot_odds`, or a no-op
    code). Operation is `VETO` per Codex r3 clamp-vs-veto
    disambiguation: math_floor REMOVES non-target actions from
    consideration entirely (the resulting distribution puts 100% mass
    on a single action), not just clamps their mass.
    """
    layer_order = layer_order_for('math_floor')

    # Phase 7.6 Step 5: ablation hook. When disabled, the rule emits a
    # `disabled_by_ablation` no-op trace and the strategy passes through
    # unchanged — used by Mode 4 ablation studies.
    if is_rule_disabled(disable_rules, 'math_floor', 'default'):
        return strategy, make_disabled_trace(
            layer='math_floor',
            rule_id='default',
            layer_order=layer_order,
        )

    # Short-circuit: nothing to do when we're not facing a call, or call
    # isn't even on the table (e.g. all-in already locked, action closed).
    if cost_to_call <= 0:
        return strategy, make_no_op_trace(
            layer='math_floor',
            rule_id='default',
            layer_order=layer_order,
            reason_code='no_call_facing',
        )
    if 'call' not in legal_actions:
        return strategy, make_no_op_trace(
            layer='math_floor',
            rule_id='default',
            layer_order=layer_order,
            reason_code='call_not_legal',
        )

    stack_bb = player_stack / big_blind if big_blind > 0 else float('inf')
    pot_odds_ratio = (
        cost_to_call / (cost_to_call + pot_total) if (cost_to_call + pot_total) > 0 else 1.0
    )

    cost_bb = cost_to_call / big_blind if big_blind > 0 else float('inf')

    rule: Optional[str] = None
    if stack_bb < SHORT_STACK_BB:
        rule = 'short_stack'
    elif player_bet > player_stack and cost_to_call > 0:
        rule = 'pot_committed'
    elif pot_odds_ratio <= TINY_POT_ODDS_RATIO and cost_bb < TINY_CALL_BB_THRESHOLD:
        rule = 'tiny_pot_odds'

    if rule is None:
        return strategy, make_no_op_trace(
            layer='math_floor',
            rule_id='default',
            layer_order=layer_order,
            reason_code='no_rule_triggered',
        )

    # Pick the target action. Short-stack prefers a shove if the engine
    # has 'all_in' legal so we actually realize the fold-equity-zero
    # push; otherwise call. We emit the abstract action label 'jam' here
    # — the action_mapper resolves 'jam' to ('all_in', stack). Emitting
    # raw 'all_in' would crash both resolve_preflop_sizing and
    # resolve_postflop_sizing (they only know 'jam' in the abstract
    # vocabulary), which the tournament runner has been silently swallowing
    # via fold-on-error.
    if rule == 'short_stack' and 'all_in' in legal_actions:
        target = 'jam'
    else:
        target = 'call'

    # Deterministic override on the target. We intentionally do NOT mix in
    # residual mass for other engine-level actions (e.g. 'raise') because
    # the postflop resolver expects abstract sized actions (bet_67, raise_150),
    # and leaking an unsized 'raise' here breaks downstream. When the floor
    # fires, math wins — no residual personality.
    new_probs = {target: 1.0}
    modified = StrategyProfile(action_probabilities=new_probs)

    primary_before = primary_action(strategy.action_probabilities)
    primary_after = target
    effect_size = l1_distance(strategy.action_probabilities, new_probs)

    trace = InterventionTrace(
        layer='math_floor',
        rule_id='default',
        layer_order=layer_order,
        fired=True,
        operation=InterventionOperation.VETO.value,
        effect='distribution_replaced',
        effect_size=round(effect_size, 4),
        action_changed=(primary_before != primary_after),
        primary_action_before=primary_before,
        primary_action_after=primary_after,
        replaced_prior_action=(primary_before != primary_after),
        preserved_prior_intent=False,
        reason_code=rule,
        rationale=(
            f"Math floor fired '{rule}': "
            f"stack_bb={stack_bb:.1f}, pot_odds_ratio={pot_odds_ratio:.3f}, "
            f"cost_bb={cost_bb:.1f}, target={target}"
        ),
        confidence=1.0,
        inputs={
            'rule': rule,
            'target': target,
            'stack_bb': round(stack_bb, 2),
            'pot_odds_ratio': round(pot_odds_ratio, 4),
            'cost_bb': round(cost_bb, 2),
        },
        input_strategy_summary=summarize_strategy(strategy.action_probabilities),
        output_strategy_summary=summarize_strategy(new_probs),
    )
    return modified, trace
