"""
Tiered bot strategy module.

Provides solver-derived baselines, personality distortion, and action mapping
for the TieredBotController architecture.
"""

from .nodes import PreflopNode, PostflopNode
from .strategy_profile import StrategyProfile
from .strategy_table import StrategyTable, load_strategy_table

__all__ = [
    'PreflopNode',
    'PostflopNode',
    'StrategyProfile',
    'StrategyTable',
    'load_strategy_table',
]

# Modules below are imported when available (incremental build).
try:
    from .preflop_classifier import build_preflop_node, get_6max_position, classify_preflop_scenario
    __all__ += ['build_preflop_node', 'get_6max_position', 'classify_preflop_scenario']
except ImportError:
    pass

try:
    from .personality_modifier import modify_strategy
    __all__ += ['modify_strategy']
except ImportError:
    pass

try:
    from .deviation_profiles import DeviationProfile, select_deviation_profile, DEVIATION_PROFILES
    __all__ += ['DeviationProfile', 'select_deviation_profile', 'DEVIATION_PROFILES']
except ImportError:
    pass

try:
    from .action_mapper import resolve_preflop_sizing
    __all__ += ['resolve_preflop_sizing']
except ImportError:
    pass
