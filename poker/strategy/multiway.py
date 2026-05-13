"""
Multiway pot heuristic adjustments.

Scales aggressive action frequencies down and passive frequencies up
when more than 2 players are in the pot, with position-aware multipliers.
"""

from .personality_modifier import categorize_action
from .strategy_profile import StrategyProfile


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
) -> StrategyProfile:
    """Apply multiway pot heuristics to a strategy profile.

    For heads-up (2 or fewer players), returns strategy unchanged.
    For 3+ players, scales down aggressive actions and scales up checks,
    then renormalizes.

    Args:
        strategy: Base strategy to adjust.
        num_players: Number of players in the pot.
        position: 'IP' (in position) or 'OOP' (out of position).

    Returns:
        Adjusted StrategyProfile with renormalized probabilities.
    """
    if num_players <= 2:
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
