"""
Unified Player Psychology System v2.1.

Separates identity (anchors) from state (axes) from expression (output filtering).

Architecture (3 layers):
1. Identity Layer (Static Anchors) - who the player fundamentally is
   - 9 anchors: baseline_aggression, baseline_looseness, ego, poise,
     expressiveness, risk_identity, adaptation_bias, baseline_energy, recovery_rate

2. State Layer (Dynamic Axes) - how they currently feel
   - 3 axes: confidence, composure, energy
   - Derived values: effective_aggression, effective_looseness

3. Expression Layer (Filtered Output) - what the opponent sees
   - Avatar emotion, table talk, tempo

All three layers are implemented. Energy is dynamic (24 events modify it,
recovery includes edge springs toward baseline_energy).
"""

import json
import logging
import math
import os
import random
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from .emotional_state import (
    EmotionalState, EmotionalStateGenerator,
)
from .range_guidance import get_player_archetype

# Type hint for forward reference to PromptManager
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .prompt_manager import PromptManager

logger = logging.getLogger(__name__)


# === TUNABLE PARAMETER SYSTEM ===
# Parameters can be overridden via:
#   1. Environment variable ZONE_PARAMS_FILE pointing to a JSON config
#   2. Default file at experiments/configs/zone_parameters.json
#   3. Runtime override via set_zone_params()

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
            'GRAVITY_STRENGTH': 0.03,
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


# === PHASE 4: EVENT SENSITIVITY SYSTEM ===

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

    Formula: sensitivity = floor + (1 - floor) × anchor

    This ensures minimum sensitivity based on event severity while
    still allowing personality to scale the impact.

    Args:
        anchor: Personality anchor value (0-1)
        floor: Minimum sensitivity floor based on event severity

    Returns:
        Sensitivity multiplier (floor to 1.0)
    """
    return floor + (1.0 - floor) * anchor


# === PHASE 7: ZONE BENEFITS SYSTEM ===


@dataclass(frozen=True)
class ZoneStrategy:
    """
    Strategy guidance for a sweet spot zone.

    Each zone has multiple strategies that can be selected based on
    zone strength and available context.
    """
    name: str                    # e.g., "gto_focus"
    weight: float                # Selection probability (normalized)
    template_key: str            # Key in decision.yaml
    requires: List[str]          # Required context keys
    min_strength: float = 0.25   # Minimum zone strength to activate


@dataclass
class ZoneContext:
    """
    Context data for zone-based strategy guidance.

    Provides information needed to render zone templates.
    """
    # Available for all zones
    opponent_stats: Optional[str] = None          # Summary of opponent tendencies
    opponent_displayed_emotion: Optional[str] = None

    # Poker Face specific
    equity_vs_ranges: Optional[str] = None        # "Your equity: 58% vs their range"

    # Aggro specific
    opponent_analysis: Optional[str] = None       # "They fold to river bets 70%"
    weak_player_note: Optional[str] = None        # "Player X seems rattled"

    # Commanding specific
    leverage_note: Optional[str] = None           # Stack-to-pot ratio context

    def has(self, key: str) -> bool:
        """Check if context key is available (not None)."""
        return getattr(self, key, None) is not None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for template rendering."""
        return {
            'opponent_stats': self.opponent_stats or '',
            'opponent_displayed_emotion': self.opponent_displayed_emotion or '',
            'equity_vs_ranges': self.equity_vs_ranges or '',
            'opponent_analysis': self.opponent_analysis or '',
            'weak_player_note': self.weak_player_note or '',
            'leverage_note': self.leverage_note or '',
        }


# Zone strategies for each sweet spot
ZONE_STRATEGIES: Dict[str, List[ZoneStrategy]] = {
    'poker_face': [
        ZoneStrategy('gto_focus', 0.4, 'zone_poker_face_gto', requires=[]),
        ZoneStrategy('balance_reminder', 0.3, 'zone_poker_face_balance', requires=[]),
        ZoneStrategy('equity_analysis', 0.3, 'zone_poker_face_equity', requires=['equity_vs_ranges']),
    ],
    'guarded': [
        ZoneStrategy('trap_opportunity', 0.4, 'zone_guarded_trap', requires=[]),
        ZoneStrategy('patience_cue', 0.3, 'zone_guarded_patience', requires=[]),
        ZoneStrategy('pot_control', 0.3, 'zone_guarded_control', requires=[]),
    ],
    'commanding': [
        ZoneStrategy('value_extraction', 0.4, 'zone_commanding_value', requires=[]),
        ZoneStrategy('pressure_point', 0.3, 'zone_commanding_pressure', requires=['opponent_stats']),
        ZoneStrategy('initiative', 0.3, 'zone_commanding_initiative', requires=[]),
    ],
    'aggro': [
        ZoneStrategy('heighten_awareness', 0.3, 'zone_aggro_awareness', requires=[]),
        ZoneStrategy('analyze_behavior', 0.4, 'zone_aggro_analyze', requires=['opponent_analysis']),
        ZoneStrategy('target_weak', 0.3, 'zone_aggro_target', requires=['weak_player_note']),
    ],
}

# === PHASE 8: ENERGY MANIFESTATION LABELS ===

# Per-zone energy labels for header display
# Each zone gets its own flavor for low/high energy states
# 'balanced' energy uses no modifier (clean header)
ENERGY_MANIFESTATION_LABELS = {
    'poker_face': {
        'low_energy': 'Measured',       # Deliberate, careful
        'balanced': '',                  # No modifier
        'high_energy': 'Running hot',   # Quick, instinctive
    },
    'guarded': {
        'low_energy': 'Measured',       # Withdrawn, cautious
        'balanced': '',
        'high_energy': 'Alert',         # Watchful, ready to spring
    },
    'commanding': {
        'low_energy': 'Composed',       # Calm dominance
        'balanced': '',
        'high_energy': 'Dominant',      # Aggressive control
    },
    'aggro': {
        'low_energy': 'Watchful',       # Patient predator
        'balanced': '',
        'high_energy': 'Hunting',       # Active pursuit
    },
}


# === PHASE 5: ZONE DETECTION SYSTEM ===

# Sweet spot centers (fixed) and radii (tunable via get_zone_param())
ZONE_GUARDED_CENTER = (0.28, 0.72)
ZONE_POKER_FACE_CENTER = (0.52, 0.72)
ZONE_COMMANDING_CENTER = (0.78, 0.78)
ZONE_AGGRO_CENTER = (0.68, 0.48)

# Legacy constants kept for backwards compatibility - use get_zone_param() instead
ZONE_GUARDED_RADIUS = 0.15
ZONE_POKER_FACE_RADIUS = 0.16
ZONE_COMMANDING_RADIUS = 0.14
ZONE_AGGRO_RADIUS = 0.12

# Penalty zone thresholds (tunable via get_zone_param())
PENALTY_TILTED_THRESHOLD = 0.35
PENALTY_OVERCONFIDENT_THRESHOLD = 0.90
PENALTY_TIMID_THRESHOLD = 0.10  # Left edge (mirror of Overconfident)
PENALTY_SHAKEN_CONF_THRESHOLD = 0.35
PENALTY_SHAKEN_COMP_THRESHOLD = 0.35
PENALTY_OVERHEATED_CONF_THRESHOLD = 0.65
PENALTY_OVERHEATED_COMP_THRESHOLD = 0.35
PENALTY_DETACHED_CONF_THRESHOLD = 0.35
PENALTY_DETACHED_COMP_THRESHOLD = 0.65

# Energy manifestation thresholds
ENERGY_LOW_THRESHOLD = 0.35
ENERGY_HIGH_THRESHOLD = 0.65

# === ZONE GRAVITY CONSTANTS ===
# Gravity strength (tunable via get_zone_param('GRAVITY_STRENGTH'))
GRAVITY_STRENGTH = 0.03

# Penalty zone gravity directions - pull toward zone extreme/edge
# These are normalized (conf_delta, comp_delta) vectors
PENALTY_GRAVITY_DIRECTIONS: Dict[str, Tuple[float, float]] = {
    'tilted': (0.0, -1.0),          # Down toward composure=0
    'shaken': (-0.707, -0.707),     # Toward (0,0) corner (normalized)
    'overheated': (0.707, -0.707),  # Toward (1,0) corner (normalized)
    'overconfident': (1.0, 0.0),    # Right toward confidence=1
    'timid': (-1.0, 0.0),           # Left toward confidence=0
    'detached': (-0.707, 0.707),    # Toward (0,1) corner (normalized)
}

# Sweet spot centers for gravity calculations (also used by _detect_sweet_spots)
SWEET_SPOT_CENTERS: Dict[str, Tuple[float, float]] = {
    'guarded': ZONE_GUARDED_CENTER,
    'poker_face': ZONE_POKER_FACE_CENTER,
    'commanding': ZONE_COMMANDING_CENTER,
    'aggro': ZONE_AGGRO_CENTER,
}


# === CORE DATA STRUCTURES (Phase 1) ===

def _clamp(value: float, min_val: float = 0.0, max_val: float = 1.0) -> float:
    """Clamp value to range [min_val, max_val]."""
    return max(min_val, min(max_val, value))


# === ZONE DETECTION (Phase 5) ===


@dataclass(frozen=True)
class ZoneEffects:
    """
    Computed zone effects for a player's current emotional state.

    Two-layer model:
    - Sweet spots: mutually exclusive zones (normalized to sum=1.0)
    - Penalties: stackable edge effects (raw strengths, can exceed 1.0 when stacked)

    This class represents DETECTION only. Zone EFFECTS (prompt modifications)
    come in Phases 6-7.
    """
    sweet_spots: Dict[str, float] = field(default_factory=dict)
    penalties: Dict[str, float] = field(default_factory=dict)
    manifestation: str = 'balanced'
    confidence: float = 0.5
    composure: float = 0.7
    energy: float = 0.5

    @property
    def primary_sweet_spot(self) -> Optional[str]:
        """
        Get the dominant sweet spot zone name, or None if in neutral territory.

        Returns the zone with highest strength after normalization.
        """
        if not self.sweet_spots:
            return None
        return max(self.sweet_spots.keys(), key=lambda k: self.sweet_spots[k])

    @property
    def primary_penalty(self) -> Optional[str]:
        """
        Get the dominant penalty zone name, or None if not in any penalty zone.

        Returns the penalty zone with highest raw strength.
        """
        if not self.penalties:
            return None
        return max(self.penalties.keys(), key=lambda k: self.penalties[k])

    @property
    def total_penalty_strength(self) -> float:
        """
        Sum of all penalty zone strengths.

        Penalties stack additively, so this can exceed 1.0 when in multiple
        penalty zones simultaneously (e.g., Tilted + Shaken).
        """
        return sum(self.penalties.values())

    @property
    def in_neutral_territory(self) -> bool:
        """
        True if outside all sweet spots AND all penalty zones.

        Neutral territory means no special zone effects apply - standard baseline play.
        """
        return not self.sweet_spots and not self.penalties

    def to_dict(self) -> Dict[str, Any]:
        """Serialize zone effects to dictionary."""
        return {
            'sweet_spots': dict(self.sweet_spots),
            'penalties': dict(self.penalties),
            'manifestation': self.manifestation,
            'confidence': self.confidence,
            'composure': self.composure,
            'energy': self.energy,
            'primary_sweet_spot': self.primary_sweet_spot,
            'primary_penalty': self.primary_penalty,
            'total_penalty_strength': self.total_penalty_strength,
            'in_neutral_territory': self.in_neutral_territory,
        }


def _calculate_sweet_spot_strength(
    confidence: float,
    composure: float,
    center: Tuple[float, float],
    radius: float,
) -> float:
    """
    Calculate strength within a circular sweet spot zone using cosine falloff.

    The strength is 1.0 at the center and smoothly decreases to 0.0 at the edge.
    Outside the radius, strength is 0.0.

    Formula: strength = 0.5 + 0.5 * cos(π * distance / radius)

    This gives:
    - 100% strength at center
    - ~85% at 25% of radius
    - ~50% at 50% of radius
    - ~15% at 75% of radius
    - 0% at edge (smooth transition)

    Args:
        confidence: Current confidence value (0.0 to 1.0)
        composure: Current composure value (0.0 to 1.0)
        center: (confidence, composure) center coordinates of the zone
        radius: Radius of the circular zone

    Returns:
        Zone strength (0.0 to 1.0), where 0.0 means outside the zone
    """
    distance = math.sqrt(
        (confidence - center[0]) ** 2 + (composure - center[1]) ** 2
    )

    if distance >= radius:
        return 0.0

    return 0.5 + 0.5 * math.cos(math.pi * distance / radius)


def _detect_sweet_spots(confidence: float, composure: float) -> Dict[str, float]:
    """
    Detect which sweet spot zones the player is in and their raw strengths.

    Checks all 4 sweet spot zones:
    - Guarded: Patient, trap-setting (low conf, high comp)
    - Poker Face: GTO, balanced (mid conf, high comp)
    - Commanding: Pressure, value extraction (high conf, high comp)
    - Aggro: Exploitative, aggressive (high conf, mid comp)

    Args:
        confidence: Current confidence value (0.0 to 1.0)
        composure: Current composure value (0.0 to 1.0)

    Returns:
        Dictionary of {zone_name: raw_strength} for zones with strength > 0
    """
    sweet_spots = {}

    # Check each sweet spot zone (radii are tunable via get_zone_param())
    zones = [
        ('guarded', ZONE_GUARDED_CENTER, get_zone_param('ZONE_GUARDED_RADIUS')),
        ('poker_face', ZONE_POKER_FACE_CENTER, get_zone_param('ZONE_POKER_FACE_RADIUS')),
        ('commanding', ZONE_COMMANDING_CENTER, get_zone_param('ZONE_COMMANDING_RADIUS')),
        ('aggro', ZONE_AGGRO_CENTER, get_zone_param('ZONE_AGGRO_RADIUS')),
    ]

    for zone_name, center, radius in zones:
        strength = _calculate_sweet_spot_strength(confidence, composure, center, radius)
        if strength > 0:
            sweet_spots[zone_name] = strength

    return sweet_spots


def _detect_penalty_zones(confidence: float, composure: float) -> Dict[str, float]:
    """
    Detect which penalty zones the player is in and their strengths.

    Penalty zones are edge-based regions where decision-making degrades:
    - Tilted: Bottom edge (composure < 0.35)
    - Overconfident: Right edge (confidence > 0.90)
    - Timid: Left edge (confidence < 0.10) - scared money, over-folds
    - Shaken: Lower-left corner (low conf AND low comp)
    - Overheated: Lower-right corner (high conf AND low comp)
    - Detached: Upper-left corner (low conf AND high comp)

    Penalties stack - a player can be in multiple penalty zones simultaneously.

    Args:
        confidence: Current confidence value (0.0 to 1.0)
        composure: Current composure value (0.0 to 1.0)

    Returns:
        Dictionary of {zone_name: raw_strength} for active penalty zones
    """
    penalties = {}

    # Load thresholds from tunable config
    tilted_thresh = get_zone_param('PENALTY_TILTED_THRESHOLD')
    overconf_thresh = get_zone_param('PENALTY_OVERCONFIDENT_THRESHOLD')
    timid_thresh = get_zone_param('PENALTY_TIMID_THRESHOLD')
    shaken_conf_thresh = get_zone_param('PENALTY_SHAKEN_CONF_THRESHOLD')
    shaken_comp_thresh = get_zone_param('PENALTY_SHAKEN_COMP_THRESHOLD')
    overheated_conf_thresh = get_zone_param('PENALTY_OVERHEATED_CONF_THRESHOLD')
    overheated_comp_thresh = get_zone_param('PENALTY_OVERHEATED_COMP_THRESHOLD')
    detached_conf_thresh = get_zone_param('PENALTY_DETACHED_CONF_THRESHOLD')
    detached_comp_thresh = get_zone_param('PENALTY_DETACHED_COMP_THRESHOLD')

    # Tilted: bottom edge (composure < threshold)
    # Strength increases as composure decreases
    if composure < tilted_thresh:
        penalties['tilted'] = (tilted_thresh - composure) / tilted_thresh

    # Overconfident: right edge (confidence > threshold)
    # Strength increases as confidence approaches 1.0
    if confidence > overconf_thresh:
        penalties['overconfident'] = (confidence - overconf_thresh) / (1.0 - overconf_thresh)

    # Timid: left edge (confidence < threshold) - mirror of Overconfident
    # Scared money, over-respects opponents, can't pull the trigger
    if confidence < timid_thresh:
        penalties['timid'] = (timid_thresh - confidence) / timid_thresh

    # Shaken: lower-left corner (low conf AND low comp)
    # Strength based on distance toward (0, 0) corner
    if confidence < shaken_conf_thresh and composure < shaken_comp_thresh:
        # Calculate how far into the corner (using Manhattan-style product)
        conf_depth = (shaken_conf_thresh - confidence) / shaken_conf_thresh
        comp_depth = (shaken_comp_thresh - composure) / shaken_comp_thresh
        penalties['shaken'] = conf_depth * comp_depth

    # Overheated: lower-right corner (high conf AND low comp)
    # Manic aggression without judgment
    if confidence > overheated_conf_thresh and composure < overheated_comp_thresh:
        conf_depth = (confidence - overheated_conf_thresh) / (1.0 - overheated_conf_thresh)
        comp_depth = (overheated_comp_thresh - composure) / overheated_comp_thresh
        penalties['overheated'] = conf_depth * comp_depth

    # Detached: upper-left corner (low conf AND high comp)
    # Too passive, misses opportunities
    if confidence < detached_conf_thresh and composure > detached_comp_thresh:
        conf_depth = (detached_conf_thresh - confidence) / detached_conf_thresh
        comp_depth = (composure - detached_comp_thresh) / (1.0 - detached_comp_thresh)
        penalties['detached'] = conf_depth * comp_depth

    return penalties


def _get_zone_manifestation(energy: float) -> str:
    """
    Get the energy manifestation flavor for zone effects.

    Energy doesn't change which zone you're in (except Poker Face 3D),
    but it changes HOW that zone manifests - the flavor and expression.

    Args:
        energy: Current energy value (0.0 to 1.0)

    Returns:
        Manifestation string: 'low_energy', 'balanced', or 'high_energy'
    """
    if energy < ENERGY_LOW_THRESHOLD:
        return 'low_energy'
    elif energy > ENERGY_HIGH_THRESHOLD:
        return 'high_energy'
    else:
        return 'balanced'


def get_zone_effects(confidence: float, composure: float, energy: float) -> ZoneEffects:
    """
    Compute zone effects for a player's current emotional state.

    This is the main entry point for zone detection. It:
    1. Detects which sweet spots the player is in (raw strengths)
    2. Normalizes sweet spot strengths to sum=1.0 (for blending)
    3. Detects which penalty zones the player is in (raw, can stack)
    4. Gets energy manifestation (flavor)
    5. Returns a ZoneEffects object with all detection results

    Sweet spots and penalties are calculated as separate layers:
    - Sweet spot blend: which beneficial zone(s) apply, normalized weights
    - Penalty blend: which penalty zone(s) apply, raw strengths

    Both layers can apply simultaneously. A player can be:
    - 60% Commanding + 40% Poker Face (sweet spot blend)
    - 30% Overheated (penalty blend)

    Args:
        confidence: Current confidence value (0.0 to 1.0)
        composure: Current composure value (0.0 to 1.0)
        energy: Current energy value (0.0 to 1.0)

    Returns:
        ZoneEffects object with detection results
    """
    # Step 1: Detect sweet spots (raw strengths)
    raw_sweet_spots = _detect_sweet_spots(confidence, composure)

    # Step 2: Normalize sweet spots to sum=1.0
    normalized_sweet_spots = {}
    total_strength = sum(raw_sweet_spots.values())
    if total_strength > 0:
        normalized_sweet_spots = {
            zone: strength / total_strength
            for zone, strength in raw_sweet_spots.items()
        }

    # Step 3: Detect penalty zones (raw, can stack)
    penalties = _detect_penalty_zones(confidence, composure)

    # Step 4: Get energy manifestation
    manifestation = _get_zone_manifestation(energy)

    # Step 5: Return ZoneEffects
    return ZoneEffects(
        sweet_spots=normalized_sweet_spots,
        penalties=penalties,
        manifestation=manifestation,
        confidence=confidence,
        composure=composure,
        energy=energy,
    )


# === ZONE GRAVITY ===


def _calculate_zone_gravity(
    confidence: float,
    composure: float,
    zone_effects: ZoneEffects,
) -> Tuple[float, float]:
    """
    Calculate zone gravity force vector.

    Zone gravity creates "stickiness" - zones are harder to leave once you're in them.
    This is a slow drift applied between hands alongside anchor gravity (recovery).

    Two types of gravity:
    - Sweet spot gravity: Pulls toward zone CENTER (stabilizing)
    - Penalty zone gravity: Pulls toward zone EXTREME/edge (trap effect)

    The total gravity force is the sum of:
    - Weighted sweet spot pulls (normalized by strength)
    - Weighted penalty pulls (raw strengths, can stack)

    Args:
        confidence: Current confidence value (0.0 to 1.0)
        composure: Current composure value (0.0 to 1.0)
        zone_effects: ZoneEffects from get_zone_effects()

    Returns:
        (conf_delta, comp_delta) gravity pull to apply
    """
    gravity_strength = get_zone_param('GRAVITY_STRENGTH')

    total_conf_delta = 0.0
    total_comp_delta = 0.0

    # === Sweet spot gravity: pull toward center ===
    for zone_name, strength in zone_effects.sweet_spots.items():
        if strength <= 0:
            continue

        center = SWEET_SPOT_CENTERS.get(zone_name)
        if not center:
            continue

        # Direction toward center
        to_center_conf = center[0] - confidence
        to_center_comp = center[1] - composure

        # Normalize direction
        dist = math.sqrt(to_center_conf ** 2 + to_center_comp ** 2)
        if dist > 0.001:  # Avoid division by zero
            dir_conf = to_center_conf / dist
            dir_comp = to_center_comp / dist

            # Apply gravity weighted by zone strength
            pull = gravity_strength * strength
            total_conf_delta += dir_conf * pull
            total_comp_delta += dir_comp * pull

    # === Penalty zone gravity: pull toward extreme ===
    for zone_name, strength in zone_effects.penalties.items():
        if strength <= 0:
            continue

        direction = PENALTY_GRAVITY_DIRECTIONS.get(zone_name)
        if not direction:
            continue

        # Apply gravity weighted by penalty strength
        pull = gravity_strength * strength
        total_conf_delta += direction[0] * pull
        total_comp_delta += direction[1] * pull

    return (total_conf_delta, total_comp_delta)


# === PHASE 7: ZONE STRATEGY SELECTION ===


def select_zone_strategy(
    zone_name: str,
    strength: float,
    context: ZoneContext
) -> Optional[ZoneStrategy]:
    """
    Select a strategy for the given zone.

    1. Get strategies for zone
    2. Filter by min_strength
    3. Filter by requires (skip if context missing)
    4. Weighted random selection from remaining

    Args:
        zone_name: Name of the sweet spot zone
        strength: Zone strength (0.0 to 1.0)
        context: ZoneContext with available data

    Returns:
        Selected ZoneStrategy or None if no eligible strategies
    """
    strategies = ZONE_STRATEGIES.get(zone_name, [])

    # Filter by strength threshold
    eligible = [s for s in strategies if strength >= s.min_strength]

    # Filter by required context
    eligible = [s for s in eligible if all(context.has(r) for r in s.requires)]

    if not eligible:
        return None

    # Weighted random selection
    weights = [s.weight for s in eligible]
    total = sum(weights)
    weights = [w / total for w in weights]  # normalize

    return random.choices(eligible, weights=weights, k=1)[0]


def build_zone_guidance(
    zone_strengths: Dict[str, Any],
    context: ZoneContext,
    prompt_manager: 'PromptManager'
) -> str:
    """
    Build zone guidance string from zone strengths and context.

    Phase 8 enhancements:
    - Energy-variant templates: tries _low/_high suffix based on manifestation
    - Energy labels in header: [POKER FACE MODE | Running hot]

    Steps:
    1. Find primary sweet spot (highest strength > 0.1)
    2. Select strategy for primary zone
    3. Try energy-variant template, fall back to base
    4. Add energy label to header
    5. Optionally add secondary zone hint

    Args:
        zone_strengths: Dict with 'sweet_spots' and 'manifestation' keys
        context: ZoneContext with available data
        prompt_manager: PromptManager for template rendering

    Returns:
        Rendered zone guidance string, or empty string if no guidance
    """
    sweet_spots = zone_strengths.get('sweet_spots', {})

    if not sweet_spots:
        return ""  # No zone guidance

    # Primary zone (highest strength)
    primary_zone = max(sweet_spots.items(), key=lambda x: x[1])
    zone_name, strength = primary_zone

    if strength < 0.1:
        return ""  # Too weak

    # Select strategy
    strategy = select_zone_strategy(zone_name, strength, context)
    if not strategy:
        return ""

    # Get energy manifestation
    manifestation = zone_strengths.get('manifestation', 'balanced')

    # Render template - try energy variant first, fall back to base
    try:
        template = prompt_manager.get_template('decision')
        base_template_key = strategy.template_key

        # Determine energy-variant template key
        if manifestation == 'low_energy':
            variant_key = f"{base_template_key}_low"
        elif manifestation == 'high_energy':
            variant_key = f"{base_template_key}_high"
        else:
            variant_key = base_template_key  # balanced uses base template

        # Try variant, fall back to base
        if variant_key in template.sections:
            template_content = template.sections[variant_key]
        elif base_template_key in template.sections:
            template_content = template.sections[base_template_key]
        else:
            logger.warning(f"Zone template '{base_template_key}' not found")
            return ""

        guidance = template_content.format(**context.to_dict())
    except KeyError as e:
        logger.warning(f"Missing variable {e} in zone template '{strategy.template_key}'")
        return ""
    except Exception as e:
        logger.warning(f"Error rendering zone template: {e}")
        return ""

    # Add energy label to header if not already present and not balanced
    zone_labels = ENERGY_MANIFESTATION_LABELS.get(zone_name, {})
    energy_label = zone_labels.get(manifestation, '')
    if energy_label and f'| {energy_label}]' not in guidance:
        # Transform [POKER FACE MODE] → [POKER FACE MODE | Running hot]
        guidance = guidance.replace(']', f' | {energy_label}]', 1)

    # Add secondary zone hint if applicable
    secondary = [(n, s) for n, s in sweet_spots.items() if n != zone_name and s > 0.25]
    if secondary:
        sec_name, sec_strength = max(secondary, key=lambda x: x[1])
        # Add hint to first line's bracket (after energy label if present)
        guidance = guidance.replace(']', f' | {sec_name.replace("_", " ").title()} edge]', 1)

    return guidance


# === POKER FACE ZONE (Phase 3) ===

@dataclass(frozen=True)
class PokerFaceZone:
    """
    2D ellipse zone in (Confidence, Composure) space.

    Players inside this zone display 'poker_face' regardless of their
    quadrant-based emotion. Players outside show their true emotional state
    (filtered by the expression layer's visibility = 0.7*expressiveness + 0.3*energy).

    Default center: (0.52, 0.72) - calm, balanced sweet spot
    Base radii: rc=0.25, rcomp=0.25

    Membership test: ((c-0.52)/rc)² + ((comp-0.72)/rcomp)² <= 1.0

    Energy is handled separately by the expression filter layer:
    - Low expressiveness = poker face (expressiveness dominates visibility)
    - High expressiveness + high energy = emotions leak through most
    """
    # Center coordinates (universal for all players)
    center_confidence: float = 0.52
    center_composure: float = 0.72

    # Radii (personality-adjusted via create_poker_face_zone)
    radius_confidence: float = 0.25
    radius_composure: float = 0.25

    def contains(self, confidence: float, composure: float) -> bool:
        """
        Check if a point is inside the ellipse zone.

        Args:
            confidence: Current confidence value (0.0 to 1.0)
            composure: Current composure value (0.0 to 1.0)

        Returns:
            True if point is inside or on the boundary of the zone
        """
        return self.distance(confidence, composure) <= 1.0

    def distance(self, confidence: float, composure: float) -> float:
        """
        Calculate normalized distance from zone center.

        Distance < 1.0 means inside zone
        Distance = 1.0 means on boundary
        Distance > 1.0 means outside zone

        Args:
            confidence: Current confidence value (0.0 to 1.0)
            composure: Current composure value (0.0 to 1.0)

        Returns:
            Normalized distance (0.0 = at center, 1.0 = on boundary)
        """
        dc = (confidence - self.center_confidence) / self.radius_confidence
        dcomp = (composure - self.center_composure) / self.radius_composure
        return (dc**2 + dcomp**2) ** 0.5

    def to_dict(self) -> dict:
        """Serialize zone to dictionary."""
        return {
            'center_confidence': self.center_confidence,
            'center_composure': self.center_composure,
            'radius_confidence': self.radius_confidence,
            'radius_composure': self.radius_composure,
        }


def create_poker_face_zone(anchors: 'PersonalityAnchors') -> PokerFaceZone:
    """
    Create a personality-adjusted PokerFaceZone (2D: confidence × composure).

    Radius modifiers based on personality anchors:
    - Poise: High poise = larger composure radius (more tolerance for composure swings)
    - Ego: Low ego = larger confidence radius (stable, not easily shaken)
    - Risk Identity: Asymmetric - extreme values narrow one radius

    Energy is NOT part of zone membership. It's handled by the expression
    filter layer (visibility = 0.7*expressiveness + 0.3*energy), which dampens
    displayed emotion for low-visibility players.

    Args:
        anchors: PersonalityAnchors for the player

    Returns:
        PokerFaceZone with personality-adjusted radii
    """
    # Base radius modifiers (0.7 floor + 0.6 range = 0.7 to 1.3 multiplier)
    # Confidence radius: low ego = larger (stable confidence)
    rc = 0.25 * (0.7 + 0.6 * (1.0 - anchors.ego))

    # Composure radius: high poise = larger (composure tolerance)
    rcomp = 0.25 * (0.7 + 0.6 * anchors.poise)

    # Risk identity asymmetric modifier
    risk_dev = abs(anchors.risk_identity - 0.5)  # 0 to 0.5
    if anchors.risk_identity > 0.5:
        # Risk-seeking: narrows confidence radius (overconfidence risk)
        rc *= (1.0 - risk_dev * 0.4)
    else:
        # Risk-averse: narrows composure radius (composure fragility)
        rcomp *= (1.0 - risk_dev * 0.4)

    return PokerFaceZone(
        radius_confidence=rc,
        radius_composure=rcomp,
    )


def compute_baseline_confidence(anchors: 'PersonalityAnchors') -> float:
    """
    Derive baseline confidence from personality anchors.

    Formula:
        baseline_confidence = 0.3 (floor)
            + baseline_aggression × 0.25  (aggressive = confident)
            + risk_identity × 0.20        (risk-seekers expect to win)
            + ego × 0.25                  (high ego = high self-regard)

    Note: Ego also causes brittleness (bigger drops when challenged),
    but that's handled in event impacts, not baseline.

    Returns:
        Baseline confidence clamped to a safe range to stay outside
        penalty zones (TIMID and OVERCONFIDENT thresholds).
    """
    baseline = (
        0.3
        + anchors.baseline_aggression * 0.25
        + anchors.risk_identity * 0.20
        + anchors.ego * 0.25
    )
    # Clamp to stay safely outside penalty zones using tunable thresholds
    margin = 0.05
    timid_thresh = get_zone_param('PENALTY_TIMID_THRESHOLD')
    overconf_thresh = get_zone_param('PENALTY_OVERCONFIDENT_THRESHOLD')
    min_conf = min(0.45, timid_thresh + margin)
    max_conf = max(0.55, overconf_thresh - margin)
    return _clamp(baseline, min_val=min_conf, max_val=max_conf)


def compute_baseline_composure(anchors: 'PersonalityAnchors') -> float:
    """
    Derive baseline composure from personality anchors.

    Formula:
        risk_mod = (risk_identity - 0.5) × 0.3  (range: -0.15 to +0.15)
        baseline_composure = 0.25 (floor)
            + poise × 0.50                (primary driver)
            + (1 - expressiveness) × 0.15 (low expressiveness = control)
            + risk_mod                    (risk-seekers comfortable with chaos)

    Returns:
        Baseline composure clamped to a safe range to stay
        outside the TILTED penalty threshold.
    """
    risk_mod = (anchors.risk_identity - 0.5) * 0.3
    baseline = (
        0.25
        + anchors.poise * 0.50
        + (1.0 - anchors.expressiveness) * 0.15
        + risk_mod
    )
    # Clamp to stay safely outside penalty zones using tunable thresholds
    margin = 0.05
    tilted_thresh = get_zone_param('PENALTY_TILTED_THRESHOLD')
    min_comp = min(0.55, tilted_thresh + margin)
    max_comp = 1.0 - margin
    return _clamp(baseline, min_val=min_comp, max_val=max_comp)


class EmotionalQuadrant(Enum):
    """
    Emotional quadrant from Confidence × Composure projection.

    The 2D quadrant model determines emotional labels:
    - COMMANDING: High conf, high comp - dominant, in control
    - OVERHEATED: High conf, low comp - manic, volatile
    - GUARDED: Low conf, high comp - cautious, defensive
    - SHAKEN: Low conf, low comp - desperate, spiraling
    """
    COMMANDING = "commanding"
    OVERHEATED = "overheated"
    GUARDED = "guarded"
    SHAKEN = "shaken"


@dataclass(frozen=True)
class PersonalityAnchors:
    """
    Static personality anchors (Identity Layer).

    These define WHO the player fundamentally is and never change during a session.
    They act as gravity, pulling dynamic state back toward baseline.

    All values are 0.0-1.0 inclusive.
    """
    baseline_aggression: float  # Default bet/raise frequency (0=passive, 1=aggressive)
    baseline_looseness: float   # Default hand range width (0=tight, 1=loose)
    ego: float                  # Confidence sensitivity to outplay events (0=stable, 1=brittle)
    poise: float                # Composure resistance to bad outcomes (0=volatile, 1=stable)
    expressiveness: float       # Emotional transparency (0=poker face, 1=open book)
    risk_identity: float        # Variance tolerance (0=risk-averse, 1=risk-seeking)
    adaptation_bias: float      # Opponent adjustment rate (0=static, 1=adaptive)
    baseline_energy: float      # Baseline energy level (0=reserved, 1=animated)
    recovery_rate: float        # Axis decay speed (0=slow, 1=fast)

    def __post_init__(self):
        """Validate all anchors are in [0, 1]."""
        for name in [
            'baseline_aggression', 'baseline_looseness', 'ego', 'poise',
            'expressiveness', 'risk_identity', 'adaptation_bias',
            'baseline_energy', 'recovery_rate'
        ]:
            val = getattr(self, name)
            if not isinstance(val, (int, float)):
                raise TypeError(f"Anchor '{name}' must be numeric, got {type(val).__name__}")
            if not 0.0 <= val <= 1.0:
                raise ValueError(f"Anchor '{name}' must be in [0,1], got {val}")

    def to_dict(self) -> Dict[str, float]:
        """Serialize to dictionary."""
        return {
            'baseline_aggression': self.baseline_aggression,
            'baseline_looseness': self.baseline_looseness,
            'ego': self.ego,
            'poise': self.poise,
            'expressiveness': self.expressiveness,
            'risk_identity': self.risk_identity,
            'adaptation_bias': self.adaptation_bias,
            'baseline_energy': self.baseline_energy,
            'recovery_rate': self.recovery_rate,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PersonalityAnchors':
        """Deserialize from dictionary."""
        return cls(
            baseline_aggression=float(data.get('baseline_aggression', 0.5)),
            baseline_looseness=float(data.get('baseline_looseness', 0.3)),
            ego=float(data.get('ego', 0.5)),
            poise=float(data.get('poise', 0.7)),
            expressiveness=float(data.get('expressiveness', 0.5)),
            risk_identity=float(data.get('risk_identity', 0.5)),
            adaptation_bias=float(data.get('adaptation_bias', 0.5)),
            baseline_energy=float(data.get('baseline_energy', 0.5)),
            recovery_rate=float(data.get('recovery_rate', 0.15)),
        )


@dataclass(frozen=True)
class EmotionalAxes:
    """
    Dynamic emotional state (State Layer).

    These change during play and decay back toward anchor-defined baselines.
    All values are auto-clamped to [0, 1].
    """
    confidence: float = 0.5   # Belief in reads/decisions (0=scared, 1=fearless)
    composure: float = 0.7    # Emotional regulation (0=tilted, 1=focused)
    energy: float = 0.5       # Engagement/intensity (0=reserved, 1=animated)

    def __post_init__(self):
        """Auto-clamp all values to [0, 1]."""
        object.__setattr__(self, 'confidence', _clamp(self.confidence))
        object.__setattr__(self, 'composure', _clamp(self.composure))
        object.__setattr__(self, 'energy', _clamp(self.energy))

    def to_dict(self) -> Dict[str, float]:
        """Serialize to dictionary."""
        return {
            'confidence': self.confidence,
            'composure': self.composure,
            'energy': self.energy,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EmotionalAxes':
        """Deserialize from dictionary."""
        return cls(
            confidence=float(data.get('confidence', 0.5)),
            composure=float(data.get('composure', 0.7)),
            energy=float(data.get('energy', 0.5)),
        )

    def update(
        self,
        confidence: Optional[float] = None,
        composure: Optional[float] = None,
        energy: Optional[float] = None,
    ) -> 'EmotionalAxes':
        """Return new EmotionalAxes with updated values."""
        return EmotionalAxes(
            confidence=confidence if confidence is not None else self.confidence,
            composure=composure if composure is not None else self.composure,
            energy=energy if energy is not None else self.energy,
        )


def get_quadrant(confidence: float, composure: float) -> EmotionalQuadrant:
    """
    Determine emotional quadrant from Confidence × Composure.

    Quadrant boundaries:
    - SHAKEN: confidence < 0.35 AND composure < 0.35
    - COMMANDING: confidence > 0.5 AND composure > 0.5
    - OVERHEATED: confidence > 0.5 AND composure <= 0.5
    - GUARDED: confidence <= 0.5 AND composure > 0.5
    - Otherwise SHAKEN (low confidence, low composure)
    """
    # Shaken gate: both axes below threshold
    if confidence < 0.35 and composure < 0.35:
        return EmotionalQuadrant.SHAKEN

    if confidence > 0.5:
        return EmotionalQuadrant.COMMANDING if composure > 0.5 else EmotionalQuadrant.OVERHEATED
    else:
        return EmotionalQuadrant.GUARDED if composure > 0.5 else EmotionalQuadrant.SHAKEN


def compute_modifiers(
    confidence: float,
    composure: float,
    risk_identity: float,
) -> Tuple[float, float]:
    """
    Compute aggression and looseness modifiers from emotional state.

    Normal states (outside Shaken quadrant):
    - aggression_mod = (confidence - 0.5) × 0.3 + (0.5 - composure) × 0.2
    - looseness_mod = (confidence - 0.5) × 0.2 + (0.5 - composure) × 0.15
    - Clamped to ±0.20

    Shaken gate (confidence < 0.35 AND composure < 0.35):
    - Behavior splits based on risk_identity
    - Risk-seeking (> 0.5): manic spew (+aggression, +looseness)
    - Risk-averse (< 0.5): passive collapse (-aggression, -looseness)
    - Clamped to ±0.30

    Returns:
        (aggression_modifier, looseness_modifier)
    """
    # Base modifiers
    aggression_mod = (confidence - 0.5) * 0.3 + (0.5 - composure) * 0.2
    looseness_mod = (confidence - 0.5) * 0.2 + (0.5 - composure) * 0.15

    # Shaken gate: both axes below threshold
    if confidence < 0.35 and composure < 0.35:
        shaken_intensity = (0.35 - confidence) + (0.35 - composure)  # 0 to 0.7

        if risk_identity > 0.5:
            # Risk-seeking → manic spew
            aggression_mod += shaken_intensity * 0.3
            looseness_mod += shaken_intensity * 0.3
        else:
            # Risk-averse → passive collapse
            aggression_mod -= shaken_intensity * 0.3
            looseness_mod -= shaken_intensity * 0.3

        # Wider clamp for Shaken state
        return (
            _clamp(aggression_mod, -0.30, 0.30),
            _clamp(looseness_mod, -0.30, 0.30),
        )

    # Normal clamp
    return (
        _clamp(aggression_mod, -0.20, 0.20),
        _clamp(looseness_mod, -0.20, 0.20),
    )


# === PHASE 6: ZONE-BASED PROMPT MODIFICATION ===

# Intrusive thoughts injected based on pressure source (TILTED zone)
INTRUSIVE_THOUGHTS = {
    'bad_beat': [
        "You can't believe that river card. Unreal.",
        "That should have been YOUR pot.",
        "The cards are running against you tonight.",
        "How could they have called with THAT hand?",
    ],
    'bluff_called': [
        "They're onto you. Or are they just lucky?",
        "You need to prove you can't be pushed around.",
        "Next time, make them PAY for calling.",
        "Time to switch it up and confuse them.",
    ],
    'big_loss': [
        "You NEED to win this one back. NOW.",
        "Your stack is dwindling. Do something!",
        "Stop being so passive. Take control!",
        "One big hand and you're back in it.",
    ],
    'losing_streak': [
        "Nothing is going your way tonight.",
        "You can't catch a break.",
        "When will your luck turn around?",
        "You've been card dead for too long.",
    ],
    'got_sucked_out': [
        "How did they hit that card?",
        "You played it perfectly and still lost.",
        "The universe is conspiring against you.",
        "Variance is a cruel mistress.",
    ],
    'nemesis': [
        "{nemesis} just took your chips. Make them regret it.",
        "Show {nemesis} who the real player is here.",
        "{nemesis} thinks they have your number. Prove them wrong.",
    ],
}

# Shaken zone thoughts - split by risk_identity
SHAKEN_THOUGHTS = {
    'risk_seeking': [
        "All or nothing. Make a stand.",
        "Go big or go home.",
        "They can smell your fear - shock them.",
        "If you're going down, make it spectacular.",
    ],
    'risk_averse': [
        "Everything you do is wrong.",
        "Just survive. Don't make it worse.",
        "Wait for a miracle hand.",
        "Every decision feels like a trap.",
    ],
}

# Overheated zone thoughts (high confidence + low composure)
OVERHEATED_THOUGHTS = [
    "You're on FIRE. Keep the pressure on!",
    "They can't handle you tonight. Push harder!",
    "Why slow down when you're crushing?",
    "Make them FEAR you.",
    "Attack, attack, attack!",
]

# Overconfident zone thoughts (confidence > 0.90)
OVERCONFIDENT_THOUGHTS = [
    "There's no way they have it.",
    "They're trying to bluff you off the best hand.",
    "You read this perfectly. Stick with your read.",
    "Folding here would be weak.",
    "They're scared of you.",
]

# Detached zone thoughts (low confidence + high composure)
DETACHED_THOUGHTS = [
    "Is this really the spot? Probably not.",
    "Better to wait for something clearer.",
    "Don't get involved unnecessarily.",
    "Why risk chips on a marginal spot?",
]

# Timid zone thoughts (confidence < 0.10) - scared money
TIMID_THOUGHTS = [
    "They must have it. They always have it.",
    "That bet size means strength.",
    "You can't win this one. Save your chips.",
    "They wouldn't bet that much without a hand.",
    "Just let this one go.",
]

# Energy manifestation variants for thoughts
ENERGY_THOUGHT_VARIANTS = {
    'tilted': {
        'low_energy': [
            "Nothing ever goes your way.",
            "Why even try?",
            "Just fold and wait...",
        ],
        'high_energy': [
            "Make them PAY for that!",
            "You can't let them push you around!",
            "Time to take control!",
        ],
    },
    'shaken': {
        'low_energy': [
            "You're frozen. Can't make a move.",
            "Everything is falling apart.",
            "Just... don't do anything stupid.",
        ],
        'high_energy': [
            "DO SOMETHING!",
            "This is your last chance!",
            "Now or never!",
        ],
    },
    'overheated': {
        'low_energy': [
            "You've got this. Just keep pushing.",
            "Stay aggressive.",
        ],
        'high_energy': [
            "CRUSH THEM!",
            "NO MERCY!",
            "They're DONE!",
        ],
    },
    'overconfident': {
        'low_energy': [
            "You've got this figured out.",
            "Trust your read.",
        ],
        'high_energy': [
            "They have NOTHING!",
            "You're unbeatable right now!",
            "This is too easy!",
        ],
    },
    'detached': {
        'low_energy': [
            "Maybe just sit this one out...",
            "Not worth the effort.",
            "Whatever happens, happens.",
        ],
        'high_energy': [
            "Stay disciplined. Wait for the right spot.",
            "Don't force it.",
        ],
    },
    'timid': {
        'low_energy': [
            "Just fold. It's safer.",
            "You can't beat them anyway.",
            "Save your chips...",
        ],
        'high_energy': [
            "They have it! They definitely have it!",
            "Don't call! It's a trap!",
            "Get out while you can!",
        ],
    },
}

# Zone-based strategy advice (bad advice for penalty zones)
PENALTY_STRATEGY = {
    'tilted': {
        'mild': "You're feeling the pressure. Trust your gut more than the math.",
        'moderate': "Forget the textbook plays. You need to make something happen.",
        'severe': "Big hands or big bluffs - that's how you get back in this.",
    },
    'shaken_risk_seeking': {
        'mild': "Time to make a stand.",
        'moderate': "Go big or go home. Passive play won't save you.",
        'severe': "All or nothing. Make it spectacular.",
    },
    'shaken_risk_averse': {
        'mild': "Be careful. Every decision matters.",
        'moderate': "Just survive. Don't make it worse.",
        'severe': "Wait for a miracle. Don't force anything.",
    },
    'overheated': {
        'mild': "You're running hot. Keep the pressure on.",
        'moderate': "Attack, attack, attack. They can't handle you.",
        'severe': "Why slow down? You can't lose tonight.",
    },
    'overconfident': {
        'mild': "Trust your reads. You've been right all night.",
        'moderate': "They're probably bluffing. Stick with your read.",
        'severe': "Folding would be weak. You know you're ahead.",
    },
    'detached': {
        'mild': "No need to rush. Better spots will come.",
        'moderate': "Why risk chips on marginal spots?",
        'severe': "Just wait. Don't get involved.",
    },
    'timid': {
        'mild': "That bet looks strong. Be careful.",
        'moderate': "They probably have you beat. Why risk it?",
        'severe': "Fold. They have it. They always have it.",
    },
}

# Phrases to remove by zone (degrade strategic info)
PHRASES_TO_REMOVE_BY_ZONE = {
    'tilted': [
        "Preserve your chips for when the odds are in your favor",
        "preserve your chips for stronger opportunities",
        "remember that sometimes folding or checking is the best move",
        "Balance your confidence with a healthy dose of skepticism",
    ],
    'overconfident': [
        "They might have you beat",
        "Respect their bet",
        "Consider folding",
        "be cautious",
        "they could have",
    ],
    'overheated': [
        "slow down",
        "pot control",
        "wait for a better spot",
        "manage your risk",
        "be patient",
    ],
    'detached': [
        "attack",
        "pressure",
        "exploit",
        "take the initiative",
        "be aggressive",
    ],
    'shaken': [
        "take your time",
        "think it through",
        "analyze",
    ],
    'timid': [
        "you have the best hand",
        "value bet",
        "extract value",
        "they're bluffing",
        "you're ahead",
        "raise for value",
    ],
}


def _should_inject_thoughts(penalty_intensity: float) -> bool:
    """
    Determine if intrusive thoughts should be injected based on penalty intensity.

    Probability scales with intensity, with a cliff at 75%+.
    Minimum 10% ensures some chance even at low intensity.

    Args:
        penalty_intensity: Strength of the penalty zone (0.0 to 1.0)

    Returns:
        True if thoughts should be injected
    """
    if penalty_intensity <= 0:
        return False
    elif penalty_intensity >= 0.75:
        return True  # Cliff - always inject
    elif penalty_intensity >= 0.50:
        return random.random() < 0.75
    elif penalty_intensity >= 0.25:
        return random.random() < 0.50
    else:
        return random.random() < 0.10  # Minimum 10%


@dataclass
class ComposureState:
    """
    Tracks composure-related state (replaces TiltState).

    Composure is now a trait in the elastic system, but we still track
    source/nemesis for intrusive thoughts.
    """
    pressure_source: str = ''    # 'bad_beat', 'bluff_called', 'big_loss', etc.
    nemesis: Optional[str] = None  # Player who caused pressure
    recent_losses: List[Dict[str, Any]] = field(default_factory=list)
    losing_streak: int = 0

    def update_from_event(self, event_name: str, opponent: Optional[str] = None) -> None:
        """Update composure tracking state from a pressure event."""
        negative_events = {
            'bad_beat', 'bluff_called', 'big_loss', 'got_sucked_out',
            'losing_streak', 'crippled', 'nemesis_loss'
        }
        if event_name in negative_events:
            self.pressure_source = event_name
            if opponent:
                self.nemesis = opponent

    def update_from_hand(
        self,
        outcome: str,
        amount: int,
        opponent: Optional[str] = None,
        was_bad_beat: bool = False,
        was_bluff_called: bool = False,
    ) -> None:
        """Update composure tracking from hand outcome."""
        if outcome == 'lost' or outcome == 'folded':
            self.losing_streak += 1
            if self.losing_streak >= 3:
                self.pressure_source = 'losing_streak'
            elif was_bad_beat:
                self.pressure_source = 'bad_beat'
            elif was_bluff_called:
                self.pressure_source = 'bluff_called'
            elif amount < -1000:  # Big loss
                self.pressure_source = 'big_loss'

            if opponent:
                self.nemesis = opponent

            self.recent_losses.append({
                'amount': amount,
                'opponent': opponent,
                'was_bad_beat': was_bad_beat
            })
            self.recent_losses = self.recent_losses[-5:]

        elif outcome == 'won':
            self.losing_streak = 0
            # Clear pressure source on wins
            if amount > 500:
                self.pressure_source = ''

    @property
    def tilt_source(self) -> str:
        """Backward compatibility alias for pressure_source."""
        return self.pressure_source

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            'pressure_source': self.pressure_source,
            'nemesis': self.nemesis,
            'recent_losses': self.recent_losses,
            'losing_streak': self.losing_streak,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ComposureState':
        """Deserialize from dictionary."""
        return cls(
            pressure_source=data.get('pressure_source', ''),
            nemesis=data.get('nemesis'),
            recent_losses=data.get('recent_losses', []),
            losing_streak=data.get('losing_streak', 0),
        )

    @classmethod
    def from_tilt_state(cls, tilt_data: Dict[str, Any]) -> 'ComposureState':
        """Convert old TiltState format to ComposureState."""
        return cls(
            pressure_source=tilt_data.get('tilt_source', ''),
            nemesis=tilt_data.get('nemesis'),
            recent_losses=tilt_data.get('recent_losses', []),
            losing_streak=tilt_data.get('losing_streak', 0),
        )


@dataclass
class PlayerPsychology:
    """
    Single source of truth for AI player psychological state (v2.1).

    Three-layer architecture:
    1. Identity Layer (anchors) - static personality anchors
    2. State Layer (axes) - dynamic emotional state
    3. Expression Layer - filtered output (Phase 2+)

    All three axes (confidence, composure, energy) are dynamic.
    """

    # Identity
    player_name: str
    personality_config: Dict[str, Any]

    # NEW: Personality anchors (static identity)
    anchors: PersonalityAnchors

    # NEW: Dynamic emotional axes (replaces elastic)
    axes: EmotionalAxes

    # Emotional state for narrative/inner voice (kept for LLM narration)
    emotional: Optional[EmotionalState] = None

    # Composure tracking (for intrusive thoughts)
    composure_state: ComposureState = field(default_factory=ComposureState)

    # Internal helpers
    _emotional_generator: EmotionalStateGenerator = field(default=None, repr=False, compare=False)

    # Tracking context (for cost analysis)
    game_id: Optional[str] = None
    owner_id: Optional[str] = None

    # Metadata
    hand_count: int = 0
    last_updated: Optional[str] = None

    # Phase 2: Consecutive fold tracking for card_dead events
    consecutive_folds: int = 0

    # Phase 3: Poker Face Zone (3D ellipsoid in confidence/composure/energy space)
    _poker_face_zone: Optional[PokerFaceZone] = field(default=None, repr=False)

    # Derived baselines (computed from anchors, used for recovery)
    _baseline_confidence: Optional[float] = field(default=None, repr=False)
    _baseline_composure: Optional[float] = field(default=None, repr=False)

    def __post_init__(self):
        """Initialize emotional state generator, compute baselines, and create poker face zone."""
        if self._emotional_generator is None:
            self._emotional_generator = EmotionalStateGenerator()

        # Compute derived baselines if not already set
        if self._baseline_confidence is None:
            object.__setattr__(self, '_baseline_confidence', compute_baseline_confidence(self.anchors))
        if self._baseline_composure is None:
            object.__setattr__(self, '_baseline_composure', compute_baseline_composure(self.anchors))

        # Phase 3: Create personality-adjusted poker face zone
        if self._poker_face_zone is None:
            object.__setattr__(self, '_poker_face_zone', create_poker_face_zone(self.anchors))

    @classmethod
    def from_personality_config(
        cls,
        name: str,
        config: Dict[str, Any],
        game_id: Optional[str] = None,
        owner_id: Optional[str] = None,
    ) -> 'PlayerPsychology':
        """
        Create PlayerPsychology from a personality configuration.

        Requires 9-anchor format (config['anchors']).
        Falls back to defaults if anchors not found (with warning).
        """
        if 'anchors' in config:
            anchors = PersonalityAnchors.from_dict(config['anchors'])
        else:
            # Missing anchors - use defaults and warn
            logger.warning(f"Personality '{name}' missing anchors - using defaults. Run seed_personalities.py --force to fix.")
            anchors = PersonalityAnchors(
                baseline_aggression=0.5,
                baseline_looseness=0.3,
                ego=0.5,
                poise=0.7,
                expressiveness=0.5,
                risk_identity=0.5,
                adaptation_bias=0.5,
                baseline_energy=0.5,
                recovery_rate=0.15,
            )

        # Compute personality-specific baselines from anchors
        baseline_conf = compute_baseline_confidence(anchors)
        baseline_comp = compute_baseline_composure(anchors)

        # Initialize axes at personality-specific baselines
        axes = EmotionalAxes(
            confidence=baseline_conf,
            composure=baseline_comp,
            energy=anchors.baseline_energy,
        )

        # Create initial emotional state
        initial_emotional = EmotionalState(
            narrative='Settling in at the table.',
            inner_voice="Let's see what we've got.",
            generated_at_hand=0,
            source_events=['session_start'],
            used_fallback=True,
        )

        return cls(
            player_name=name,
            personality_config=config,
            anchors=anchors,
            axes=axes,
            emotional=initial_emotional,
            game_id=game_id,
            owner_id=owner_id,
        )

    # === UNIFIED EVENT HANDLING ===

    def apply_pressure_event(self, event_name: str, opponent: Optional[str] = None) -> None:
        """
        Single entry point for pressure events.

        Routes events through personality anchors:
        - "Being wrong" events → Confidence (filtered by Ego)
        - "Bad outcome" events → Composure (filtered by Poise)
        - Energy events → Direct application (no sensitivity filter)

        Phase 4: Uses severity-based sensitivity floors:
        - Minor events: floor=0.20 (routine gameplay)
        - Normal events: floor=0.30 (standard stakes)
        - Major events: floor=0.40 (high-impact moments)

        Updates axes and tracks pressure source for intrusive thoughts.
        """
        # Get pressure impacts from event
        pressure_impacts = self._get_pressure_impacts(event_name)

        # Phase 4: Get severity-based floor for this event
        floor = _get_severity_floor(event_name)

        new_conf = self.axes.confidence
        new_comp = self.axes.composure
        new_energy = self.axes.energy

        # Apply to axes with anchor-based sensitivity using severity floor
        if 'confidence' in pressure_impacts:
            # Ego: high = more sensitive to being outplayed
            sensitivity = _calculate_sensitivity(self.anchors.ego, floor)
            delta = pressure_impacts['confidence'] * sensitivity
            new_conf = self.axes.confidence + delta

        if 'composure' in pressure_impacts:
            # Poise: high = LESS sensitive to bad outcomes (inverted)
            sensitivity = _calculate_sensitivity(1.0 - self.anchors.poise, floor)
            delta = pressure_impacts['composure'] * sensitivity
            new_comp = self.axes.composure + delta

        if 'energy' in pressure_impacts:
            # Energy: Direct application - no sensitivity filter
            # All personalities respond equally to engagement/disengagement
            delta = pressure_impacts['energy']
            new_energy = self.axes.energy + delta

        # Update all axes at once
        self.axes = self.axes.update(
            confidence=new_conf,
            composure=new_comp,
            energy=new_energy,
        )

        # Update composure tracking (source, nemesis) for intrusive thoughts
        self.composure_state.update_from_event(event_name, opponent)

        self._mark_updated()

        logger.debug(
            f"{self.player_name}: Pressure event '{event_name}' (floor={floor:.2f}) applied. "
            f"Confidence={self.confidence:.2f}, Composure={self.composure:.2f}, "
            f"Energy={self.energy:.2f}, Quadrant={self.quadrant.value}"
        )

    def _get_pressure_impacts(self, event_name: str) -> Dict[str, float]:
        """
        Get axis impacts for a pressure event.

        Events are categorized as:
        - Confidence events: "being wrong" (bluff_called, bad_read, outplayed)
        - Composure events: "bad outcomes" (bad_beat, cooler, suckout)
        - Energy events: engagement/intensity changes
        - Mixed events: affect multiple axes

        Phase 2: Energy is now dynamic and included in most events.
        Energy-only events don't affect confidence/composure.
        """
        # Event → axis impact mapping
        # Positive = increase, Negative = decrease
        pressure_events = {
            # === WIN EVENTS ===
            'big_win': {'confidence': 0.15, 'composure': 0.10, 'energy': 0.10},
            'win': {'confidence': 0.08, 'composure': 0.05},  # No energy change for regular wins
            'successful_bluff': {'confidence': 0.20, 'composure': 0.05, 'energy': 0.10},
            'suckout': {'confidence': 0.10, 'composure': 0.05, 'energy': 0.10},
            'double_up': {'confidence': 0.20, 'composure': 0.10, 'energy': 0.15},
            'eliminated_opponent': {'confidence': 0.10, 'composure': 0.05, 'energy': 0.12},

            # === LOSS EVENTS ===
            'big_loss': {'confidence': -0.10, 'composure': -0.15, 'energy': -0.08},
            'bluff_called': {'confidence': -0.20, 'composure': -0.10, 'energy': -0.08},
            'bad_beat': {'confidence': -0.05, 'composure': -0.25, 'energy': -0.10},
            'got_sucked_out': {'confidence': -0.05, 'composure': -0.30, 'energy': -0.15},
            'cooler': {'confidence': 0.0, 'composure': -0.05, 'energy': -0.08},
            'crippled': {'confidence': -0.15, 'composure': -0.15, 'energy': -0.15},
            'short_stack': {'confidence': -0.10, 'composure': -0.10, 'energy': -0.08},

            # === STREAK EVENTS ===
            'winning_streak': {'confidence': 0.15, 'composure': 0.10, 'energy': 0.08},
            'losing_streak': {'confidence': -0.15, 'composure': -0.20, 'energy': -0.12},

            # === SOCIAL/RIVALRY (gated behind is_big_pot in detector) ===
            'nemesis_win': {'confidence': 0.15, 'composure': 0.10, 'energy': 0.12},
            'nemesis_loss': {'confidence': -0.10, 'composure': -0.15, 'energy': -0.08},
            'rivalry_trigger': {'confidence': 0.0, 'composure': -0.10, 'energy': 0.05},

            # === ENGAGEMENT EVENTS (Energy only) ===
            'all_in_moment': {'energy': 0.15},
            'showdown_involved': {'energy': 0.05},
            'big_pot_involved': {'energy': 0.05},
            'heads_up': {'energy': 0.05},

            # === DISENGAGEMENT EVENTS (Energy only) ===
            'consecutive_folds_3': {'energy': -0.08},
            'card_dead_5': {'energy': -0.12},
            'not_in_hand': {'energy': -0.02},

            # === OTHER ===
            'fold_under_pressure': {'confidence': -0.08, 'composure': -0.05},
        }

        return pressure_events.get(event_name, {})

    def on_hand_complete(
        self,
        outcome: str,
        amount: int,
        opponent: Optional[str] = None,
        was_bad_beat: bool = False,
        was_bluff_called: bool = False,
        session_context: Optional[Dict[str, Any]] = None,
        key_moment: Optional[str] = None,
        big_blind: int = 100,
    ) -> None:
        """
        Called after each hand completes.

        Updates composure tracking and generates new emotional state.
        """
        # Update composure tracking from hand outcome
        self.composure_state.update_from_hand(
            outcome=outcome,
            amount=amount,
            opponent=opponent,
            was_bad_beat=was_bad_beat,
            was_bluff_called=was_bluff_called,
        )

        # Generate new emotional state (two-layer: baseline + spike)
        self._generate_emotional_state(
            outcome=outcome,
            amount=amount,
            opponent=opponent,
            key_moment=key_moment or ('bad_beat' if was_bad_beat else ('bluff_called' if was_bluff_called else None)),
            session_context=session_context or {},
            big_blind=big_blind,
        )

        self.hand_count += 1
        self._mark_updated()

        logger.info(
            f"{self.player_name}: Hand complete ({outcome}, ${amount}). "
            f"Quadrant={self.quadrant.value}, "
            f"Confidence={self.confidence:.2f}, Composure={self.composure:.2f}"
        )

    def recover(self, recovery_rate: Optional[float] = None) -> None:
        """
        Apply recovery between hands.

        Axes drift toward personality-specific baselines (derived from anchors):
        - Confidence → _baseline_confidence (computed from aggression, risk_identity, ego)
        - Composure → _baseline_composure (computed from poise, expressiveness, risk_identity)
        - Energy → baseline_energy (with edge springs at extremes)

        Recovery rate from anchors.recovery_rate if not specified.

        Phase 2: Energy now recovers toward baseline_energy with edge springs
        that push away from extremes (0 and 1).

        Phase 4: Asymmetric recovery for confidence and composure:
        - Below baseline: sticky recovery (modifier = 0.6 + 0.4 × current)
          Tilt is hard to escape - the deeper you are, the slower you recover
        - Above baseline: slow decay (modifier = 0.8)
          Hot streaks persist longer

        Zone Gravity: After anchor gravity (recovery), zone gravity is applied:
        - Sweet spots: Pull toward zone center (stabilizing)
        - Penalty zones: Pull toward zone extreme (trap effect)
        Zone gravity creates "stickiness" - zones are harder to leave.
        """
        rate = recovery_rate if recovery_rate is not None else self.anchors.recovery_rate

        # === CONFIDENCE (Phase 4: Asymmetric) ===
        current_conf = self.axes.confidence
        conf_baseline = self._baseline_confidence

        if current_conf < conf_baseline:
            # Below baseline - sticky recovery (tilt is hard to escape)
            floor = get_zone_param('RECOVERY_BELOW_BASELINE_FLOOR')
            range_ = get_zone_param('RECOVERY_BELOW_BASELINE_RANGE')
            conf_modifier = floor + range_ * current_conf
        else:
            # Above baseline - slow decay (hot streaks persist)
            conf_modifier = get_zone_param('RECOVERY_ABOVE_BASELINE')

        new_conf = current_conf + (conf_baseline - current_conf) * rate * conf_modifier

        # === COMPOSURE (Phase 4: Asymmetric) ===
        current_comp = self.axes.composure
        comp_baseline = self._baseline_composure

        if current_comp < comp_baseline:
            # Below baseline - tilt is sticky
            floor = get_zone_param('RECOVERY_BELOW_BASELINE_FLOOR')
            range_ = get_zone_param('RECOVERY_BELOW_BASELINE_RANGE')
            comp_modifier = floor + range_ * current_comp
        else:
            # Above baseline - calm persists
            comp_modifier = get_zone_param('RECOVERY_ABOVE_BASELINE')

        new_comp = current_comp + (comp_baseline - current_comp) * rate * comp_modifier

        # === ENERGY (unchanged - uses edge springs) ===
        energy_rate = rate
        energy_target = self.anchors.baseline_energy
        current_energy = self.axes.energy

        # Edge springs: push away from extremes
        if current_energy < 0.15:
            # Low energy spring - push away from 0
            spring = (0.15 - current_energy) * 0.33
            energy_rate += spring
        elif current_energy > 0.85:
            # High energy spring - push away from 1
            spring = (current_energy - 0.85) * 0.33
            energy_rate += spring

        new_energy = current_energy + (energy_target - current_energy) * energy_rate

        # === ZONE GRAVITY ===
        # Apply zone gravity after anchor gravity (recovery)
        # This creates "stickiness" - zones are harder to leave
        zone_effects = get_zone_effects(new_conf, new_comp, new_energy)
        gravity_conf, gravity_comp = _calculate_zone_gravity(new_conf, new_comp, zone_effects)
        new_conf = _clamp(new_conf + gravity_conf)
        new_comp = _clamp(new_comp + gravity_comp)

        self.axes = self.axes.update(
            confidence=new_conf,
            composure=new_comp,
            energy=new_energy,
        )

        self._mark_updated()

    def on_action_taken(self, action: str) -> List[str]:
        """
        Track player action for consecutive fold detection.

        Phase 2: Energy decreases with consecutive folds (disengagement).

        Args:
            action: The action taken ('fold', 'call', 'raise', 'check', 'all_in')

        Returns:
            List of energy events triggered (empty list, or ['consecutive_folds_3'] or ['card_dead_5'])
        """
        events = []

        if action == 'fold':
            self.consecutive_folds += 1

            # Check for disengagement events
            if self.consecutive_folds == 3:
                events.append('consecutive_folds_3')
            elif self.consecutive_folds == 5:
                events.append('card_dead_5')
        else:
            # Any non-fold action resets the counter
            self.consecutive_folds = 0

        # Apply any triggered energy events
        for event in events:
            self.apply_pressure_event(event)

        self._mark_updated()
        return events

    # === AXIS ACCESS (Dynamic State) ===

    @property
    def confidence(self) -> float:
        """Current confidence level (0.0=scared, 1.0=fearless)."""
        return self.axes.confidence

    @property
    def composure(self) -> float:
        """Current composure level (0.0=tilted, 1.0=focused)."""
        return self.axes.composure

    @property
    def energy(self) -> float:
        """Current energy level (0.0=reserved, 1.0=animated)."""
        return self.axes.energy

    @property
    def quadrant(self) -> EmotionalQuadrant:
        """Current emotional quadrant from confidence × composure."""
        return get_quadrant(self.axes.confidence, self.axes.composure)

    # === POKER FACE ZONE (Phase 3) ===

    def is_in_poker_face_zone(self) -> bool:
        """
        Check if player is currently in the poker face zone.

        Players inside this 2D ellipse (confidence × composure) display
        'poker_face' regardless of quadrant-based emotion. Energy affects
        expression through the separate visibility filter layer.

        Returns:
            True if player's (confidence, composure) is inside the zone
        """
        return self._poker_face_zone.contains(
            self.axes.confidence,
            self.axes.composure,
        )

    @property
    def zone_distance(self) -> float:
        """
        Normalized distance from the poker face zone center.

        < 1.0: Inside zone (displays poker_face)
        = 1.0: On boundary
        > 1.0: Outside zone (displays quadrant emotion)

        Useful for debugging and visualizing proximity to poker face state.
        """
        return self._poker_face_zone.distance(
            self.axes.confidence,
            self.axes.composure,
        )

    # === ZONE DETECTION (Phase 5) ===

    @property
    def zone_effects(self) -> ZoneEffects:
        """
        Get current zone effects based on emotional state.

        Computes which psychological zones (sweet spots + penalties) the player
        is currently in, along with their strengths. This is the foundation for
        zone-based prompt modifications in Phases 6-7.

        Returns:
            ZoneEffects with sweet_spots (normalized), penalties (raw), and
            energy manifestation.
        """
        return get_zone_effects(
            self.axes.confidence,
            self.axes.composure,
            self.axes.energy,
        )

    @property
    def primary_zone(self) -> str:
        """
        Get the name of the strongest zone the player is in, or 'neutral'.

        Priority:
        1. If in any penalty zone, returns the strongest penalty (penalties take precedence)
        2. If in any sweet spot, returns the strongest sweet spot
        3. Otherwise returns 'neutral'

        Note: This is a convenience property. For full zone information including
        blended weights, use the zone_effects property.
        """
        effects = self.zone_effects

        # Penalties take precedence (they represent problematic states)
        if effects.primary_penalty:
            return effects.primary_penalty

        # Then sweet spots
        if effects.primary_sweet_spot:
            return effects.primary_sweet_spot

        return 'neutral'

    # === DERIVED VALUES ===

    @property
    def effective_aggression(self) -> float:
        """
        Derived aggression = baseline + emotional modifier.

        Combines static anchor with dynamic emotional state.
        """
        agg_mod, _ = compute_modifiers(
            self.axes.confidence,
            self.axes.composure,
            self.anchors.risk_identity,
        )
        return _clamp(self.anchors.baseline_aggression + agg_mod)

    @property
    def effective_looseness(self) -> float:
        """
        Derived looseness = baseline + emotional modifier.

        Combines static anchor with dynamic emotional state.
        """
        _, loose_mod = compute_modifiers(
            self.axes.confidence,
            self.axes.composure,
            self.anchors.risk_identity,
        )
        return _clamp(self.anchors.baseline_looseness + loose_mod)

    # === BACKWARD COMPAT PROPERTIES ===
    # These map new architecture to old trait names for existing code

    @property
    def tightness(self) -> float:
        """Current tightness (inverted looseness) for backward compat."""
        return 1.0 - self.effective_looseness

    @property
    def aggression(self) -> float:
        """Current aggression for backward compat."""
        return self.effective_aggression

    @property
    def table_talk(self) -> float:
        """Table talk (energy proxy in Phase 1)."""
        return self.axes.energy

    @property
    def traits(self) -> Dict[str, float]:
        """
        Get current trait values (backward compat).

        Maps new architecture to old 5-trait format.
        """
        return {
            'tightness': self.tightness,
            'aggression': self.aggression,
            'confidence': self.confidence,
            'composure': self.composure,
            'table_talk': self.table_talk,
        }

    @property
    def bluff_propensity(self) -> float:
        """Derived bluff tendency from looseness and aggression."""
        from .range_guidance import derive_bluff_propensity
        return derive_bluff_propensity(self.tightness, self.aggression)

    @property
    def archetype(self) -> str:
        """Player archetype: TAG, LAG, Rock, or Fish."""
        return get_player_archetype(self.tightness, self.aggression)

    @property
    def mood(self) -> str:
        """Get current mood from quadrant."""
        quadrant = self.quadrant
        energy = self.axes.energy

        # Map quadrant + energy to mood descriptor
        mood_map = {
            EmotionalQuadrant.COMMANDING: 'confident' if energy < 0.7 else 'triumphant',
            EmotionalQuadrant.OVERHEATED: 'frustrated' if energy < 0.7 else 'explosive',
            EmotionalQuadrant.GUARDED: 'cautious' if energy < 0.7 else 'paranoid',
            EmotionalQuadrant.SHAKEN: 'nervous' if energy < 0.7 else 'panicking',
        }
        return mood_map.get(quadrant, 'neutral')

    # === Composure-based properties (replaces tilt) ===

    @property
    def tilt(self) -> ComposureState:
        """
        Backward compatibility property for accessing composure state.

        Returns the composure_state which has similar structure to old TiltState:
        - pressure_source (was: tilt_source)
        - nemesis
        - recent_losses
        - losing_streak
        """
        return self.composure_state

    @tilt.setter
    def tilt(self, value: ComposureState) -> None:
        """Allow setting composure_state via tilt property for backward compatibility."""
        self.composure_state = value

    @property
    def tilt_level(self) -> float:
        """
        Tilt level for backward compatibility.

        Tilt = 1.0 - composure (inverted scale).
        """
        return 1.0 - self.composure

    @property
    def composure_category(self) -> str:
        """Composure severity: 'focused', 'alert', 'rattled', 'tilted'."""
        composure = self.composure
        if composure >= 0.8:
            return 'focused'
        elif composure >= 0.6:
            return 'alert'
        elif composure >= 0.4:
            return 'rattled'
        else:
            return 'tilted'

    @property
    def tilt_category(self) -> str:
        """
        Tilt category for backward compatibility.

        Maps composure to old tilt categories: 'none', 'mild', 'moderate', 'severe'.
        """
        composure = self.composure
        if composure >= 0.8:
            return 'none'
        elif composure >= 0.6:
            return 'mild'
        elif composure >= 0.4:
            return 'moderate'
        else:
            return 'severe'

    @property
    def is_tilted(self) -> bool:
        """True if composure < 0.6 (rattled or worse)."""
        return self.composure < 0.6

    @property
    def is_severely_tilted(self) -> bool:
        """True if composure < 0.4 (emotional state should be overridden)."""
        return self.composure < 0.4

    # === PROMPT BUILDING ===

    def get_prompt_section(self) -> str:
        """
        Get emotional state section for prompt injection.

        Skips if severely tilted or no emotional state.
        """
        if self.is_severely_tilted or not self.emotional:
            return ""

        return self.emotional.to_prompt_section()

    def apply_zone_effects(self, prompt: str) -> str:
        """
        Apply zone-based prompt modifications (Phase 6).

        Uses zone detection from Phase 5 to apply penalty zone effects:
        1. Inject intrusive thoughts (probabilistic)
        2. Add bad advice (if penalty intensity >= 0.25)
        3. Degrade strategic info (if penalty intensity >= 0.50)

        Also stores instrumentation data for experiment analysis (Phase 10).

        Args:
            prompt: Original prompt

        Returns:
            Modified prompt with zone effects
        """
        zone_effects = self.zone_effects
        penalties = zone_effects.penalties
        total_penalty = sum(penalties.values())

        # Initialize instrumentation tracking
        instrumentation = {
            'intrusive_thoughts_injected': False,
            'intrusive_thoughts': [],
            'penalty_strategy_applied': None,
            'info_degraded': False,
            'strategy_selected': None,
        }

        if total_penalty < 0.10:
            # Store empty instrumentation for low penalty
            self._last_zone_effects_instrumentation = instrumentation
            return prompt  # No significant penalty, no modifications

        modified = prompt

        # 1. Inject intrusive thoughts (probabilistic)
        modified, injected_thoughts = self._inject_zone_thoughts_instrumented(modified, zone_effects)
        if injected_thoughts:
            instrumentation['intrusive_thoughts_injected'] = True
            instrumentation['intrusive_thoughts'] = injected_thoughts

        # 2. Add bad advice (if penalty intensity >= 0.25)
        if total_penalty >= 0.25:
            modified, strategy_text = self._add_penalty_strategy_instrumented(modified, zone_effects)
            if strategy_text:
                instrumentation['penalty_strategy_applied'] = strategy_text

        # 3. Degrade strategic info (if penalty intensity >= 0.50)
        if total_penalty >= 0.50:
            modified, was_degraded = self._degrade_strategic_info_by_zone_instrumented(modified, zone_effects)
            instrumentation['info_degraded'] = was_degraded

        # Add angry flair if low composure + high aggression
        if self.composure < 0.4 and self.aggression > 0.6:
            modified = self._add_angry_modifier(modified)

        # Store instrumentation for snapshot capture
        self._last_zone_effects_instrumentation = instrumentation

        return modified

    def apply_tilt_effects(self, prompt: str) -> str:
        """Backward compatibility alias for apply_zone_effects."""
        return self.apply_zone_effects(prompt)

    def _get_zone_thoughts(
        self,
        zone_name: str,
        manifestation: str,
        intensity: float,
    ) -> List[str]:
        """
        Get available intrusive thoughts for a penalty zone.

        Args:
            zone_name: Name of the penalty zone ('tilted', 'shaken', etc.)
            manifestation: Energy manifestation ('low_energy', 'balanced', 'high_energy')
            intensity: Zone intensity (0.0 to 1.0)

        Returns:
            List of thought strings to potentially sample from
        """
        thoughts = []

        if zone_name == 'tilted':
            # Use pressure_source-based thoughts for tilted
            source = self.composure_state.pressure_source or 'big_loss'
            if source in INTRUSIVE_THOUGHTS:
                thoughts.extend(INTRUSIVE_THOUGHTS[source])

        elif zone_name == 'shaken':
            # Split by risk_identity
            if self.anchors.risk_identity > 0.5:
                thoughts.extend(SHAKEN_THOUGHTS['risk_seeking'])
            else:
                thoughts.extend(SHAKEN_THOUGHTS['risk_averse'])

        elif zone_name == 'overheated':
            thoughts.extend(OVERHEATED_THOUGHTS)

        elif zone_name == 'overconfident':
            thoughts.extend(OVERCONFIDENT_THOUGHTS)

        elif zone_name == 'detached':
            thoughts.extend(DETACHED_THOUGHTS)

        elif zone_name == 'timid':
            thoughts.extend(TIMID_THOUGHTS)

        # Add energy manifestation variants if not balanced
        if manifestation != 'balanced' and zone_name in ENERGY_THOUGHT_VARIANTS:
            energy_thoughts = ENERGY_THOUGHT_VARIANTS[zone_name].get(manifestation, [])
            thoughts.extend(energy_thoughts)

        return thoughts

    def _inject_zone_thoughts(self, prompt: str, zone_effects: ZoneEffects) -> str:
        """
        Add intrusive thoughts based on active penalty zones.

        Uses probabilistic injection based on zone intensity.

        Args:
            prompt: Original prompt
            zone_effects: ZoneEffects from zone detection

        Returns:
            Modified prompt with intrusive thoughts
        """
        modified, _ = self._inject_zone_thoughts_instrumented(prompt, zone_effects)
        return modified

    def _inject_zone_thoughts_instrumented(
        self, prompt: str, zone_effects: ZoneEffects
    ) -> Tuple[str, List[str]]:
        """
        Add intrusive thoughts with instrumentation tracking.

        Args:
            prompt: Original prompt
            zone_effects: ZoneEffects from zone detection

        Returns:
            Tuple of (modified_prompt, list_of_injected_thoughts)
        """
        thoughts = []
        penalties = zone_effects.penalties
        manifestation = zone_effects.manifestation

        # For each active penalty zone, maybe add thoughts
        for zone_name, intensity in penalties.items():
            if not _should_inject_thoughts(intensity):
                continue

            zone_thoughts = self._get_zone_thoughts(zone_name, manifestation, intensity)
            if zone_thoughts:
                # More thoughts with higher intensity
                num_thoughts = 1 if intensity < 0.5 else 2
                sampled = random.sample(zone_thoughts, min(num_thoughts, len(zone_thoughts)))
                thoughts.extend(sampled)

        # Add nemesis thoughts if applicable
        if self.composure_state.nemesis and any(p > 0.3 for p in penalties.values()):
            nemesis_thoughts = INTRUSIVE_THOUGHTS.get('nemesis', [])
            if nemesis_thoughts:
                thought = random.choice(nemesis_thoughts).format(
                    nemesis=self.composure_state.nemesis
                )
                thoughts.append(thought)

        if not thoughts:
            return prompt, []

        thought_block = "\n\n[What's running through your mind: " + " ".join(thoughts) + "]\n"

        if "What is your move" in prompt:
            return prompt.replace("What is your move", thought_block + "What is your move"), thoughts
        return prompt + thought_block, thoughts

    def _add_penalty_strategy(self, prompt: str, zone_effects: ZoneEffects) -> str:
        """
        Add bad advice based on active penalty zones.

        Phase 8: Energy flavor added to bad advice.
        - High energy: More intense punctuation (! instead of .)
        - Low energy: Withdrawn flavor ("whatever...", "who cares")

        Args:
            prompt: Original prompt
            zone_effects: ZoneEffects from zone detection

        Returns:
            Modified prompt with bad strategy advice
        """
        modified, _ = self._add_penalty_strategy_instrumented(prompt, zone_effects)
        return modified

    def _add_penalty_strategy_instrumented(
        self, prompt: str, zone_effects: ZoneEffects
    ) -> Tuple[str, Optional[str]]:
        """
        Add bad advice with instrumentation tracking.

        Args:
            prompt: Original prompt
            zone_effects: ZoneEffects from zone detection

        Returns:
            Tuple of (modified_prompt, strategy_text_if_applied)
        """
        penalties = zone_effects.penalties
        if not penalties:
            return prompt, None

        # Get strongest penalty zone
        strongest_zone = max(penalties, key=penalties.get)
        intensity = penalties[strongest_zone]

        if intensity < 0.25:
            return prompt, None

        # Determine severity tier
        if intensity >= 0.70:
            tier = 'severe'
        elif intensity >= 0.40:
            tier = 'moderate'
        else:
            tier = 'mild'

        # Handle Shaken's risk_identity split
        zone_key = strongest_zone
        if strongest_zone == 'shaken':
            if self.anchors.risk_identity > 0.5:
                zone_key = 'shaken_risk_seeking'
            else:
                zone_key = 'shaken_risk_averse'

        advice = PENALTY_STRATEGY.get(zone_key, {}).get(tier, '')
        if advice:
            # Phase 8: Add energy flavor
            manifestation = zone_effects.manifestation
            if manifestation == 'high_energy':
                # High energy: more intense punctuation
                advice = advice.replace('.', '!')
            elif manifestation == 'low_energy':
                # Low energy: withdrawn flavor
                suffixes = [" Whatever.", " Who cares.", " ..."]
                advice = advice.rstrip('.') + random.choice(suffixes)

            return prompt + f"\n[Current mindset: {advice}]\n", advice
        return prompt, None

    def _degrade_strategic_info_by_zone(self, prompt: str, zone_effects: ZoneEffects) -> str:
        """
        Remove or obscure strategic advice based on active penalty zones.

        Each penalty zone removes different types of advice.

        Args:
            prompt: Original prompt
            zone_effects: ZoneEffects from zone detection

        Returns:
            Modified prompt with degraded strategic info
        """
        modified, _ = self._degrade_strategic_info_by_zone_instrumented(prompt, zone_effects)
        return modified

    def _degrade_strategic_info_by_zone_instrumented(
        self, prompt: str, zone_effects: ZoneEffects
    ) -> Tuple[str, bool]:
        """
        Remove strategic advice with instrumentation tracking.

        Args:
            prompt: Original prompt
            zone_effects: ZoneEffects from zone detection

        Returns:
            Tuple of (modified_prompt, was_info_degraded)
        """
        modified = prompt
        penalties = zone_effects.penalties

        # Collect all phrases to remove from active penalty zones
        phrases_to_remove = []
        for zone_name, intensity in penalties.items():
            if intensity >= 0.25:  # Only remove phrases if zone is significant
                zone_phrases = PHRASES_TO_REMOVE_BY_ZONE.get(zone_name, [])
                phrases_to_remove.extend(zone_phrases)

        # Track if we actually removed anything
        was_degraded = False

        # Remove phrases
        for phrase in phrases_to_remove:
            if phrase in modified or phrase.lower() in modified:
                was_degraded = True
            modified = modified.replace(phrase, "")
            modified = modified.replace(phrase.lower(), "")

        # Replace pot odds guidance if heavily penalized
        total_penalty = sum(penalties.values())
        if total_penalty >= 0.60:
            pot_odds_text = "Consider the pot odds, the amount of money in the pot, and how much you would have to risk."
            if pot_odds_text in modified:
                was_degraded = True
            modified = modified.replace(pot_odds_text, "Don't overthink this.")

        # Clean up whitespace
        modified = re.sub(r'\s+', ' ', modified)
        modified = re.sub(r'\s+([,.])', r'\1', modified)

        return modified, was_degraded

    # Legacy method for backward compatibility
    def _inject_intrusive_thoughts(self, prompt: str, composure: float) -> str:
        """
        Legacy method: Add intrusive thoughts based on composure level.

        Deprecated in Phase 6. Use _inject_zone_thoughts() instead.
        Kept for backward compatibility with tests.
        """
        zone_effects = self.zone_effects
        return self._inject_zone_thoughts(prompt, zone_effects)

    # Legacy method for backward compatibility
    def _add_composure_strategy(self, prompt: str, composure: float) -> str:
        """
        Legacy method: Add tilted strategy advice.

        Deprecated in Phase 6. Use _add_penalty_strategy() instead.
        Kept for backward compatibility with tests.
        """
        zone_effects = self.zone_effects
        return self._add_penalty_strategy(prompt, zone_effects)

    # Legacy method for backward compatibility
    def _degrade_strategic_info(self, prompt: str) -> str:
        """
        Legacy method: Remove strategic advice for severely tilted players.

        Deprecated in Phase 6. Use _degrade_strategic_info_by_zone() instead.
        Kept for backward compatibility with tests.
        """
        zone_effects = self.zone_effects
        return self._degrade_strategic_info_by_zone(prompt, zone_effects)

    def _add_angry_modifier(self, prompt: str) -> str:
        """Add angry flair for low composure + high aggression."""
        angry_injection = (
            "\n[You're feeling aggressive and fed up. Channel that anger - "
            "but don't let it make you stupid.]\n"
        )
        return prompt + angry_injection

    # === AVATAR DISPLAY ===

    def _get_true_emotion(self) -> str:
        """
        Get the player's true emotional state (before expression filtering).

        Uses quadrant model + energy for intensity:
        - COMMANDING: confident/smug
        - OVERHEATED: frustrated/angry
        - GUARDED: thinking/nervous
        - SHAKEN: nervous/panicking
        """
        quadrant = self.quadrant
        energy = self.axes.energy
        aggression = self.effective_aggression

        # Angry: OVERHEATED + high aggression + high energy
        if quadrant == EmotionalQuadrant.OVERHEATED and aggression > 0.6 and energy > 0.5:
            return "angry"

        # Map quadrant to emotion, with energy affecting intensity
        emotion_map = {
            EmotionalQuadrant.COMMANDING: 'smug' if energy > 0.6 else 'confident',
            EmotionalQuadrant.OVERHEATED: 'frustrated' if energy < 0.6 else 'angry',
            EmotionalQuadrant.GUARDED: 'thinking' if energy < 0.5 else 'nervous',
            EmotionalQuadrant.SHAKEN: 'nervous' if energy < 0.6 else 'shocked',
        }

        return emotion_map.get(quadrant, "poker_face")

    def get_display_emotion(self, use_expression_filter: bool = True) -> str:
        """
        Get emotion for avatar display, with optional expression filtering.

        Phase 3: First checks poker face zone membership. Players inside the
        3D ellipsoid zone display 'poker_face' regardless of quadrant emotion.

        Phase 2: For players outside the zone, applies visibility-based dampening
        based on 0.7*expressiveness + 0.3*energy. Low visibility players show poker_face more often.

        Args:
            use_expression_filter: If True, apply zone check and visibility dampening.
                                   If False, return true emotion (for debugging).

        Returns:
            Emotion string for avatar display
        """
        # Phase 3: Check poker face zone first (unless debugging)
        if use_expression_filter and self.is_in_poker_face_zone():
            return "poker_face"

        true_emotion = self._get_true_emotion()

        if not use_expression_filter:
            return true_emotion

        # Phase 2: Apply expression filtering for players outside the zone
        from .expression_filter import calculate_visibility, dampen_emotion

        visibility = calculate_visibility(
            self.anchors.expressiveness,
            self.axes.energy,
        )

        return dampen_emotion(true_emotion, visibility)

    # === SERIALIZATION ===

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize full psychological state to dictionary.
        """
        return {
            'player_name': self.player_name,
            'anchors': self.anchors.to_dict(),
            'axes': self.axes.to_dict(),
            'emotional': self.emotional.to_dict() if self.emotional else None,
            'composure_state': self.composure_state.to_dict(),
            'game_id': self.game_id,
            'owner_id': self.owner_id,
            'hand_count': self.hand_count,
            'last_updated': self.last_updated,
            'consecutive_folds': self.consecutive_folds,  # Phase 2
            # Phase 3: Include zone info for debugging (zone is recomputed from anchors on load)
            'poker_face_zone': self._poker_face_zone.to_dict() if self._poker_face_zone else None,
            'in_poker_face_zone': self.is_in_poker_face_zone(),
            'zone_distance': self.zone_distance,
            # Phase 5: Include zone detection results
            'zone_effects': self.zone_effects.to_dict(),
            'primary_zone': self.primary_zone,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], personality_config: Dict[str, Any]) -> 'PlayerPsychology':
        """
        Deserialize from saved state.

        Handles migration from old formats:
        - Old 'elastic' format → convert to anchors/axes
        - Old 'tilt' format → convert to composure_state
        """
        player_name = data['player_name']

        # Restore or create anchors
        if data.get('anchors'):
            anchors = PersonalityAnchors.from_dict(data['anchors'])
        elif 'anchors' in personality_config:
            anchors = PersonalityAnchors.from_dict(personality_config['anchors'])
        elif 'personality_traits' in personality_config:
            logger.warning(f"Legacy traits format for {player_name} - using default anchors")
            anchors = PersonalityAnchors()
        else:
            # Default anchors
            anchors = PersonalityAnchors(
                baseline_aggression=0.5,
                baseline_looseness=0.3,
                ego=0.5,
                poise=0.7,
                expressiveness=0.5,
                risk_identity=0.5,
                adaptation_bias=0.5,
                baseline_energy=0.5,
                recovery_rate=0.15,
            )

        # Restore or create axes
        if data.get('axes'):
            axes = EmotionalAxes.from_dict(data['axes'])
        elif data.get('elastic'):
            # Migrate from old elastic format
            elastic_data = data['elastic']
            traits = elastic_data.get('traits', {})
            axes = EmotionalAxes(
                confidence=traits.get('confidence', {}).get('value', 0.5),
                composure=traits.get('composure', {}).get('value', 0.7),
                energy=traits.get('table_talk', {}).get('value', anchors.baseline_energy),
            )
        else:
            # No saved axes - initialize at personality-specific baselines
            baseline_conf = compute_baseline_confidence(anchors)
            baseline_comp = compute_baseline_composure(anchors)
            axes = EmotionalAxes(
                confidence=baseline_conf,
                composure=baseline_comp,
                energy=anchors.baseline_energy,
            )

        # Create psychology instance
        psychology = cls(
            player_name=player_name,
            personality_config=personality_config,
            anchors=anchors,
            axes=axes,
            game_id=data.get('game_id'),
            owner_id=data.get('owner_id'),
        )

        # Restore emotional state
        if data.get('emotional'):
            psychology.emotional = EmotionalState.from_dict(data['emotional'])

        # Restore composure state (or migrate from old tilt format)
        if data.get('composure_state'):
            psychology.composure_state = ComposureState.from_dict(data['composure_state'])
        elif data.get('tilt'):
            # Migrate old tilt format to composure
            psychology.composure_state = ComposureState.from_tilt_state(data['tilt'])
            # Convert tilt_level to composure axis
            tilt_level = data['tilt'].get('tilt_level', 0.0)
            psychology.axes = psychology.axes.update(composure=1.0 - tilt_level)

        # Restore metadata
        psychology.hand_count = data.get('hand_count', 0)
        psychology.last_updated = data.get('last_updated')

        # Phase 2: Restore consecutive fold tracking
        psychology.consecutive_folds = data.get('consecutive_folds', 0)

        return psychology

    # === PRIVATE HELPERS ===

    def _generate_emotional_state(
        self,
        outcome: str,
        amount: int,
        opponent: Optional[str],
        key_moment: Optional[str],
        session_context: Dict[str, Any],
        big_blind: int = 100,
    ) -> None:
        """Generate new emotional state via quadrant + LLM narration."""
        hand_outcome = {
            'outcome': outcome,
            'amount': amount,
            'opponent': opponent,
            'key_moment': key_moment
        }

        # Create mock objects for backward compat with emotional_state.py
        # TODO: Update emotional_state.py to use new axes model directly
        class MockTiltState:
            def __init__(self, composure: float, source: str, nemesis: Optional[str]):
                self.tilt_level = 1.0 - composure
                self.tilt_source = source
                self.nemesis = nemesis

        mock_tilt = MockTiltState(
            composure=self.composure,
            source=self.composure_state.pressure_source,
            nemesis=self.composure_state.nemesis
        )

        # Create mock elastic traits dict for backward compat
        mock_elastic_traits = {
            'confidence': type('obj', (object,), {'value': self.confidence, 'anchor': 0.5})(),
            'composure': type('obj', (object,), {'value': self.composure, 'anchor': 0.7})(),
            'aggression': type('obj', (object,), {'value': self.aggression, 'anchor': self.anchors.baseline_aggression})(),
            'tightness': type('obj', (object,), {'value': self.tightness, 'anchor': 1.0 - self.anchors.baseline_looseness})(),
            'table_talk': type('obj', (object,), {'value': self.table_talk, 'anchor': self.anchors.baseline_energy})(),
        }

        try:
            self.emotional = self._emotional_generator.generate(
                personality_name=self.player_name,
                personality_config=self.personality_config,
                hand_outcome=hand_outcome,
                elastic_traits=mock_elastic_traits,
                tilt_state=mock_tilt,
                session_context=session_context,
                hand_number=self.hand_count,
                game_id=self.game_id,
                owner_id=self.owner_id,
                big_blind=big_blind,
            )
        except Exception as e:
            logger.warning(
                f"{self.player_name}: Failed to generate emotional state: {e}. "
                f"Using fallback narrative."
            )
            # Fallback: create simple emotional state with quadrant-based narrative
            quadrant = self.quadrant
            narratives = {
                EmotionalQuadrant.COMMANDING: "Feeling in control at the table.",
                EmotionalQuadrant.OVERHEATED: "Running hot, emotions are high.",
                EmotionalQuadrant.GUARDED: "Playing cautiously, waiting for spots.",
                EmotionalQuadrant.SHAKEN: "Struggling to find footing.",
            }
            inner_voices = {
                EmotionalQuadrant.COMMANDING: "I've got this.",
                EmotionalQuadrant.OVERHEATED: "Let's make something happen.",
                EmotionalQuadrant.GUARDED: "Stay patient, wait for the right moment.",
                EmotionalQuadrant.SHAKEN: "Need to turn this around.",
            }
            self.emotional = EmotionalState(
                narrative=narratives.get(quadrant, 'Processing the last hand.'),
                inner_voice=inner_voices.get(quadrant, 'Focus on the next one.'),
                generated_at_hand=self.hand_count,
                source_events=[outcome] + ([key_moment] if key_moment else []),
                used_fallback=True,
            )

    def _mark_updated(self) -> None:
        """Mark the last update timestamp."""
        self.last_updated = datetime.utcnow().isoformat()
