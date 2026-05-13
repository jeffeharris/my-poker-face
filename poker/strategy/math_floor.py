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

from .strategy_profile import StrategyProfile

logger = logging.getLogger(__name__)


# Thresholds taken from the spec's prompt-injection rules in
# poker/prompts/CLAUDE.md so that hybrid and tiered paths agree on what
# counts as "pot-odds-obvious".
SHORT_STACK_BB = 3.0           # Below 3 BB: push/fold required
TINY_POT_ODDS_RATIO = 0.05     # cost / (cost + pot) <= 5%, i.e. ~20:1 odds — matches the pot_committed prompt-injection rule
TINY_CALL_BB_THRESHOLD = 5.0   # AND cost_to_call < 5 BB in absolute terms (don't fire on routine deep-stack pot-odds spots)


def apply_pot_odds_floor(
    strategy: StrategyProfile,
    cost_to_call: int,
    pot_total: int,
    player_stack: int,
    player_bet: int,
    big_blind: int,
    legal_actions: List[str],
) -> Tuple[StrategyProfile, Optional[str]]:
    """Override strategy when immediate arithmetic mandates a call/jam.

    Three trigger conditions, in order of priority:

    1. **Short stack push/fold** (`stack_bb < 3`): below this depth, fold
       equity is zero — any defensible hand should be all-in. Folding
       just means the blinds eliminate you anyway. Target action: `all_in`
       if legal, else `call`.

    2. **Pot committed** (`player_bet > player_stack`): you've already
       invested more chips than you have left. Folding forfeits a large
       sunk-cost claim on the pot to save very little. Target: `call`.

    3. **Tiny pot odds** (`cost / (cost + pot) <= 0.15`): need ≤15%
       equity to break even. Any non-zero-equity hand is +EV to call.
       Target: `call`.

    Returns the (possibly overridden) strategy plus a string label of which
    rule fired (or None if no override applied). The label is for logging
    and tests; the strategy is what gets sampled downstream.
    """
    # Short-circuit: nothing to do when we're not facing a call, or call
    # isn't even on the table (e.g. all-in already locked, action closed).
    if cost_to_call <= 0 or 'call' not in legal_actions:
        return strategy, None

    stack_bb = player_stack / big_blind if big_blind > 0 else float('inf')
    pot_odds_ratio = (
        cost_to_call / (cost_to_call + pot_total)
        if (cost_to_call + pot_total) > 0 else 1.0
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
        return strategy, None

    # Pick the target action. Short-stack prefers all_in if legal so we
    # actually realize the fold-equity-zero push; otherwise call.
    if rule == 'short_stack' and 'all_in' in legal_actions:
        target = 'all_in'
    else:
        target = 'call'

    # Deterministic override on the target. We intentionally do NOT mix in
    # residual mass for other engine-level actions (e.g. 'raise') because
    # the postflop resolver expects abstract sized actions (bet_67, raise_150),
    # and leaking an unsized 'raise' here breaks downstream. When the floor
    # fires, math wins — no residual personality.
    return StrategyProfile(action_probabilities={target: 1.0}), rule
