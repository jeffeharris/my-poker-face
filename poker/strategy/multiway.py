"""
Multiway pot heuristic adjustments.

Scales aggressive action frequencies down and passive frequencies up
when more than 2 players are in the pot, with position-aware multipliers.

VALUE EXEMPTION (STRUCTURAL_PASSIVITY_PLAN.md §13): the suppression is correct
for *bluffs* (don't bluff into a field) but wrong for *value* — you value-bet
the nuts into more callers, not fewer. The per-signature leak finder traced the
bot's postflop passivity to multiway scaling value hands down (nuts' bet 0.75
HU → 0.32 in a 6-way pot = checks the nuts 68%). So value classes
(`nuts`, `strong_made`) are exempt from suppression. This is the root-cause fix
that the (now-retired) `value_bet_floor` override was correcting after the fact;
it recovered +7 bb/100 vs a human clone, +12.6 vs the 5-rule mix, +26.8 vs
GTO-Lite (all 3000×3, all seeds positive).
"""

from typing import Optional

from .personality_modifier import categorize_action
from .strategy_profile import StrategyProfile

# Hand classes (simplify_hand_class output) that value-bet regardless of field
# size — exempt from multiway aggression suppression.
VALUE_CLASSES = frozenset({'nuts', 'strong_made'})


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _bluff_mult(num_players: int, position: str) -> float:
    """Multiplier for aggressive actions (bet_*, raise_*, jam)."""
    n = num_players
    if position == 'IP':
        return _clamp(0.5 + (n - 3) * -0.1, 0.1, 1.0)
    # OOP
    return _clamp(0.3 + (n - 3) * -0.1, 0.1, 1.0)


def _check_mult(position: str) -> float:
    """Multiplier for check actions."""
    return 1.3 if position == 'IP' else 1.5


def apply_multiway_adjustment(
    strategy: StrategyProfile,
    num_players: int,
    position: str,
    hand_class: Optional[str] = None,
) -> StrategyProfile:
    """Apply multiway pot heuristics to a strategy profile.

    For heads-up (2 or fewer players), returns strategy unchanged.
    For 3+ players, scales down aggressive actions and scales up checks,
    then renormalizes — EXCEPT for value classes, which are exempt (you
    value-bet the nuts into a field, not away from it).

    Args:
        strategy: Base strategy to adjust.
        num_players: Number of players in the pot.
        position: 'IP' (in position) or 'OOP' (out of position).
        hand_class: simplify_hand_class output. When in VALUE_CLASSES
            (nuts/strong_made), suppression is skipped (the §13 value
            exemption). None = legacy behavior (suppress everything).

    Returns:
        Adjusted StrategyProfile with renormalized probabilities.
    """
    if num_players <= 2:
        return strategy

    # Value exemption: don't suppress value hands' aggression in multiway.
    if hand_class in VALUE_CLASSES:
        return strategy

    bluff_m = _bluff_mult(num_players, position)
    check_m = _check_mult(position)

    adjusted = {}
    for action, prob in strategy.action_probabilities.items():
        cat = categorize_action(action)
        if cat == 'aggressive':
            adjusted[action] = prob * bluff_m
        elif action == 'check':
            adjusted[action] = prob * check_m
        else:
            # fold, call: unchanged
            adjusted[action] = prob

    # Renormalize
    total = sum(adjusted.values())
    if total > 0:
        adjusted = {a: p / total for a, p in adjusted.items()}

    return StrategyProfile(action_probabilities=adjusted)
