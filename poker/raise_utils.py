"""Shared raise-level helpers.

Lives in its own module so callers like ``poker.bounded_options`` can
consume the helpers without pulling in the full ``poker.controllers``
import surface (which previously required an absolute lazy import to
break the cycle).
"""

# Raise level to action name mapping for preflop
RAISE_LEVEL_ACTIONS = {
    0: 'open_raise',
    1: '3bet',
    2: '4bet',
}


def _classify_raise_action(raise_count: int) -> str:
    """Classify a raise/all-in action based on raise count."""
    return RAISE_LEVEL_ACTIONS.get(raise_count, '4bet+')
