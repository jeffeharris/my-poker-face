"""
Tunable zone parameter system and event sensitivity configuration.

Parameters can be overridden via:
  1. Environment variable ZONE_PARAMS_FILE pointing to a JSON config
  2. Default file at experiments/configs/zone_parameters.json
  3. Runtime override via set_zone_params()
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


# === TUNABLE PARAMETER SYSTEM ===

_zone_params_cache: Optional[Dict[str, Any]] = None
_zone_params_overrides: Dict[str, float] = {}


def _load_zone_params() -> Dict[str, Any]:
    """Load zone parameters from config file."""
    global _zone_params_cache
    if _zone_params_cache is not None:
        return _zone_params_cache

    # Default values (hardcoded fallback)
    defaults = {
        'penalty_thresholds': {
            'PENALTY_TILTED_THRESHOLD': 0.35,
            'PENALTY_OVERCONFIDENT_THRESHOLD': 0.90,
            'PENALTY_TIMID_THRESHOLD': 0.10,
            'PENALTY_SHAKEN_CONF_THRESHOLD': 0.35,
            'PENALTY_SHAKEN_COMP_THRESHOLD': 0.35,
            'PENALTY_OVERHEATED_CONF_THRESHOLD': 0.65,
            'PENALTY_OVERHEATED_COMP_THRESHOLD': 0.35,
            'PENALTY_DETACHED_CONF_THRESHOLD': 0.35,
            'PENALTY_DETACHED_COMP_THRESHOLD': 0.65,
        },
        'zone_radii': {
            'ZONE_POKER_FACE_RADIUS': 0.16,
            'ZONE_GUARDED_RADIUS': 0.15,
            'ZONE_COMMANDING_RADIUS': 0.14,
            'ZONE_AGGRO_RADIUS': 0.12,
        },
        'recovery': {
            'RECOVERY_BELOW_BASELINE_FLOOR': 0.60,
            'RECOVERY_BELOW_BASELINE_RANGE': 0.40,
            'RECOVERY_ABOVE_BASELINE': 0.80,
        },
        'gravity': {
            'GRAVITY_STRENGTH': 0.01,
        },
    }

    # Try to load from file
    config_path = os.environ.get('ZONE_PARAMS_FILE')
    if not config_path:
        # Look for default config relative to this file
        poker_dir = Path(__file__).parent
        project_root = poker_dir.parent
        config_path = project_root / 'experiments' / 'configs' / 'zone_parameters.json'

    try:
        if Path(config_path).exists():
            with open(config_path, 'r') as f:
                loaded = json.load(f)
                # Merge loaded values into defaults
                for category in ['penalty_thresholds', 'zone_radii', 'recovery', 'gravity']:
                    if category in loaded:
                        defaults[category].update(loaded[category])
                logger.debug(f"Loaded zone parameters from {config_path}")
    except Exception as e:
        logger.warning(f"Failed to load zone parameters from {config_path}: {e}")

    _zone_params_cache = defaults
    return defaults


def get_zone_param(name: str) -> float:
    """
    Get a zone parameter value.

    Checks in order:
    1. Runtime overrides (set_zone_params)
    2. Loaded config file
    3. Hardcoded defaults
    """
    # Check runtime overrides first
    if name in _zone_params_overrides:
        return _zone_params_overrides[name]

    params = _load_zone_params()

    # Search all categories for the parameter
    for category in ['penalty_thresholds', 'zone_radii', 'recovery', 'gravity']:
        if name in params.get(category, {}):
            return params[category][name]

    raise KeyError(f"Unknown zone parameter: {name}")


def set_zone_params(overrides: Dict[str, float]) -> None:
    """
    Set runtime parameter overrides.

    Use this in experiments to test different parameter values
    without modifying config files.

    Example:
        set_zone_params({'RECOVERY_BELOW_BASELINE_FLOOR': 0.70})
    """
    global _zone_params_overrides
    _zone_params_overrides.update(overrides)
    logger.info(f"Zone parameter overrides set: {overrides}")


def clear_zone_params() -> None:
    """Clear all runtime parameter overrides."""
    global _zone_params_overrides, _zone_params_cache
    _zone_params_overrides = {}
    _zone_params_cache = None
    logger.info("Zone parameter overrides cleared")


def get_all_zone_params() -> Dict[str, float]:
    """Get all zone parameters as a flat dict (for reporting)."""
    params = _load_zone_params()
    result = {}
    for category in ['penalty_thresholds', 'zone_radii', 'recovery', 'gravity']:
        result.update(params.get(category, {}))
    # Apply overrides
    result.update(_zone_params_overrides)
    return result


# === EVENT SENSITIVITY SYSTEM ===

# Severity-based sensitivity floors
SEVERITY_MINOR = 0.20
SEVERITY_NORMAL = 0.30
SEVERITY_MAJOR = 0.40

# Asymmetric recovery constants (now loaded from config via get_zone_param())
# These module-level constants are kept for backwards compatibility
# but actual usage should call get_zone_param() for runtime overrides
RECOVERY_BELOW_BASELINE_FLOOR = 0.6  # Use get_zone_param('RECOVERY_BELOW_BASELINE_FLOOR')
RECOVERY_BELOW_BASELINE_RANGE = 0.4  # Use get_zone_param('RECOVERY_BELOW_BASELINE_RANGE')
RECOVERY_ABOVE_BASELINE = 0.8  # Use get_zone_param('RECOVERY_ABOVE_BASELINE')

# Event severity categorization
EVENT_SEVERITY = {
    # Minor events (floor=0.20) - routine, small stakes
    'win': 'minor',
    'fold_under_pressure': 'minor',
    'cooler': 'minor',

    # Normal events (floor=0.30) - standard gameplay, default
    'big_win': 'normal',
    'big_loss': 'normal',
    'bluff_called': 'normal',
    'successful_bluff': 'normal',
    'short_stack': 'normal',
    'winning_streak': 'normal',
    'losing_streak': 'normal',

    # Major events (floor=0.40) - high impact, dramatic moments
    'bad_beat': 'major',
    'got_sucked_out': 'major',
    'double_up': 'major',
    'crippled': 'major',
    'eliminated_opponent': 'major',
    'suckout': 'major',
    'nemesis_win': 'major',
    'nemesis_loss': 'major',
}


def _get_severity_floor(event_name: str) -> float:
    """
    Get sensitivity floor based on event severity.

    Args:
        event_name: Name of the pressure event

    Returns:
        Sensitivity floor (0.20 for minor, 0.30 for normal, 0.40 for major)
    """
    severity = EVENT_SEVERITY.get(event_name, 'normal')
    floors = {
        'minor': SEVERITY_MINOR,
        'normal': SEVERITY_NORMAL,
        'major': SEVERITY_MAJOR,
    }
    return floors[severity]


def _calculate_sensitivity(anchor: float, floor: float) -> float:
    """
    Calculate sensitivity multiplier with severity-based floor.

    Formula: sensitivity = floor + (1 - floor) * anchor

    This ensures minimum sensitivity based on event severity while
    still allowing personality to scale the impact.

    Args:
        anchor: Personality anchor value (0-1)
        floor: Minimum sensitivity floor based on event severity

    Returns:
        Sensitivity multiplier (floor to 1.0)
    """
    return floor + (1.0 - floor) * anchor
