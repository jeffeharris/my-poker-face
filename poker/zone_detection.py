"""
Zone detection system: sweet spots, penalties, gravity, and strategy guidance.

Detects which zones a player occupies based on confidence/composure/energy,
computes zone effects, and builds strategy guidance for prompts.
"""

import logging
import math
import random
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Tuple

from .zone_config import get_zone_param

# Type hint for forward reference to PromptManager
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .prompt_manager import PromptManager

logger = logging.getLogger(__name__)


# === ZONE CONSTANTS ===

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

# Zone gravity strength (tunable via get_zone_param('GRAVITY_STRENGTH'))
GRAVITY_STRENGTH = 0.03

# Penalty zone gravity directions - pull toward zone extreme/edge
PENALTY_GRAVITY_DIRECTIONS: Dict[str, Tuple[float, float]] = {
    'tilted': (0.0, -1.0),          # Down toward composure=0
    'shaken': (-0.707, -0.707),     # Toward (0,0) corner (normalized)
    'overheated': (0.707, -0.707),  # Toward (1,0) corner (normalized)
    'overconfident': (1.0, 0.0),    # Right toward confidence=1
    'timid': (-1.0, 0.0),           # Left toward confidence=0
    'detached': (-0.707, 0.707),    # Toward (0,1) corner (normalized)
}

# Sweet spot centers for gravity calculations
SWEET_SPOT_CENTERS: Dict[str, Tuple[float, float]] = {
    'guarded': ZONE_GUARDED_CENTER,
    'poker_face': ZONE_POKER_FACE_CENTER,
    'commanding': ZONE_COMMANDING_CENTER,
    'aggro': ZONE_AGGRO_CENTER,
}


# === ZONE STRATEGY SYSTEM ===

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

# Per-zone energy labels for header display
ENERGY_MANIFESTATION_LABELS = {
    'poker_face': {
        'low_energy': 'Measured',
        'balanced': '',
        'high_energy': 'Running hot',
    },
    'guarded': {
        'low_energy': 'Measured',
        'balanced': '',
        'high_energy': 'Alert',
    },
    'commanding': {
        'low_energy': 'Composed',
        'balanced': '',
        'high_energy': 'Dominant',
    },
    'aggro': {
        'low_energy': 'Watchful',
        'balanced': '',
        'high_energy': 'Hunting',
    },
}


# === ZONE EFFECTS ===

@dataclass(frozen=True)
class ZoneEffects:
    """
    Computed zone effects for a player's current emotional state.

    Two-layer model:
    - Sweet spots: mutually exclusive zones (normalized to sum=1.0)
    - Penalties: stackable edge effects (raw strengths, can exceed 1.0 when stacked)
    """
    sweet_spots: Dict[str, float] = field(default_factory=dict)
    penalties: Dict[str, float] = field(default_factory=dict)
    manifestation: str = 'balanced'
    confidence: float = 0.5
    composure: float = 0.7
    energy: float = 0.5

    @property
    def primary_sweet_spot(self) -> Optional[str]:
        """Get the dominant sweet spot zone name, or None if in neutral territory."""
        if not self.sweet_spots:
            return None
        return max(self.sweet_spots.keys(), key=lambda k: self.sweet_spots[k])

    @property
    def primary_penalty(self) -> Optional[str]:
        """Get the dominant penalty zone name, or None if not in any penalty zone."""
        if not self.penalties:
            return None
        return max(self.penalties.keys(), key=lambda k: self.penalties[k])

    @property
    def total_penalty_strength(self) -> float:
        """Sum of all penalty zone strengths (can exceed 1.0 when stacked)."""
        return sum(self.penalties.values())

    @property
    def in_neutral_territory(self) -> bool:
        """True if outside all sweet spots AND all penalty zones."""
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


# === ZONE DETECTION FUNCTIONS ===

def _calculate_sweet_spot_strength(
    confidence: float,
    composure: float,
    center: Tuple[float, float],
    radius: float,
) -> float:
    """
    Calculate strength within a circular sweet spot zone.

    Inner-radius full strength + linear outer falloff:
    - Within inner_radius (40% of radius): full strength (1.0)
    - Between inner_radius and radius: linear falloff from 1.0 to 0.0
    - Outside radius: 0.0
    """
    distance = math.sqrt(
        (confidence - center[0]) ** 2 + (composure - center[1]) ** 2
    )

    if distance >= radius:
        return 0.0

    inner_radius = radius * 0.4
    if distance <= inner_radius:
        return 1.0

    return 1.0 - (distance - inner_radius) / (radius - inner_radius)


def _detect_sweet_spots(confidence: float, composure: float) -> Dict[str, float]:
    """
    Detect which sweet spot zones the player is in and their raw strengths.

    Checks all 4 sweet spot zones:
    - Guarded: Patient, trap-setting (low conf, high comp)
    - Poker Face: GTO, balanced (mid conf, high comp)
    - Commanding: Pressure, value extraction (high conf, high comp)
    - Aggro: Exploitative, aggressive (high conf, mid comp)
    """
    sweet_spots = {}

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
    - Tilted: Bottom edge (composure < threshold)
    - Overconfident: Right edge (confidence > threshold)
    - Timid: Left edge (confidence < threshold)
    - Shaken: Lower-left corner (low conf AND low comp)
    - Overheated: Lower-right corner (high conf AND low comp)
    - Detached: Upper-left corner (low conf AND high comp)
    """
    penalties = {}

    tilted_thresh = get_zone_param('PENALTY_TILTED_THRESHOLD')
    overconf_thresh = get_zone_param('PENALTY_OVERCONFIDENT_THRESHOLD')
    timid_thresh = get_zone_param('PENALTY_TIMID_THRESHOLD')
    shaken_conf_thresh = get_zone_param('PENALTY_SHAKEN_CONF_THRESHOLD')
    shaken_comp_thresh = get_zone_param('PENALTY_SHAKEN_COMP_THRESHOLD')
    overheated_conf_thresh = get_zone_param('PENALTY_OVERHEATED_CONF_THRESHOLD')
    overheated_comp_thresh = get_zone_param('PENALTY_OVERHEATED_COMP_THRESHOLD')
    detached_conf_thresh = get_zone_param('PENALTY_DETACHED_CONF_THRESHOLD')
    detached_comp_thresh = get_zone_param('PENALTY_DETACHED_COMP_THRESHOLD')

    if composure < tilted_thresh:
        penalties['tilted'] = (tilted_thresh - composure) / tilted_thresh

    if confidence > overconf_thresh:
        penalties['overconfident'] = (confidence - overconf_thresh) / (1.0 - overconf_thresh)

    if confidence < timid_thresh:
        penalties['timid'] = (timid_thresh - confidence) / timid_thresh

    if confidence < shaken_conf_thresh and composure < shaken_comp_thresh:
        conf_depth = (shaken_conf_thresh - confidence) / shaken_conf_thresh
        comp_depth = (shaken_comp_thresh - composure) / shaken_comp_thresh
        penalties['shaken'] = conf_depth * comp_depth

    if confidence > overheated_conf_thresh and composure < overheated_comp_thresh:
        conf_depth = (confidence - overheated_conf_thresh) / (1.0 - overheated_conf_thresh)
        comp_depth = (overheated_comp_thresh - composure) / overheated_comp_thresh
        penalties['overheated'] = conf_depth * comp_depth

    if confidence < detached_conf_thresh and composure > detached_comp_thresh:
        conf_depth = (detached_conf_thresh - confidence) / detached_conf_thresh
        comp_depth = (composure - detached_comp_thresh) / (1.0 - detached_comp_thresh)
        penalties['detached'] = conf_depth * comp_depth

    return penalties


def _get_zone_manifestation(energy: float) -> str:
    """
    Get the energy manifestation flavor for zone effects.

    Returns: 'low_energy', 'balanced', or 'high_energy'
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
    """
    raw_sweet_spots = _detect_sweet_spots(confidence, composure)

    normalized_sweet_spots = {}
    total_strength = sum(raw_sweet_spots.values())
    if total_strength > 0:
        normalized_sweet_spots = {
            zone: strength / total_strength
            for zone, strength in raw_sweet_spots.items()
        }

    penalties = _detect_penalty_zones(confidence, composure)
    manifestation = _get_zone_manifestation(energy)

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
    - Sweet spot gravity: Pulls toward zone CENTER (stabilizing)
    - Penalty zone gravity: Pulls toward zone EXTREME/edge (trap effect)

    Returns:
        (conf_delta, comp_delta) gravity pull to apply
    """
    gravity_strength = get_zone_param('GRAVITY_STRENGTH')

    total_conf_delta = 0.0
    total_comp_delta = 0.0

    # Sweet spot gravity: pull toward center
    for zone_name, strength in zone_effects.sweet_spots.items():
        if strength <= 0:
            continue

        center = SWEET_SPOT_CENTERS.get(zone_name)
        if not center:
            continue

        to_center_conf = center[0] - confidence
        to_center_comp = center[1] - composure

        dist = math.sqrt(to_center_conf ** 2 + to_center_comp ** 2)
        if dist > 0.001:
            dir_conf = to_center_conf / dist
            dir_comp = to_center_comp / dist

            pull = gravity_strength * strength
            total_conf_delta += dir_conf * pull
            total_comp_delta += dir_comp * pull

    # Penalty zone gravity: pull toward extreme
    for zone_name, strength in zone_effects.penalties.items():
        if strength <= 0:
            continue

        direction = PENALTY_GRAVITY_DIRECTIONS.get(zone_name)
        if not direction:
            continue

        pull = gravity_strength * strength
        total_conf_delta += direction[0] * pull
        total_comp_delta += direction[1] * pull

    return (total_conf_delta, total_comp_delta)


# === ZONE STRATEGY SELECTION ===

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
    """
    strategies = ZONE_STRATEGIES.get(zone_name, [])

    eligible = [s for s in strategies if strength >= s.min_strength]
    eligible = [s for s in eligible if all(context.has(r) for r in s.requires)]

    if not eligible:
        return None

    weights = [s.weight for s in eligible]
    total = sum(weights)
    weights = [w / total for w in weights]

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
    """
    sweet_spots = zone_strengths.get('sweet_spots', {})

    if not sweet_spots:
        return ""

    primary_zone = max(sweet_spots.items(), key=lambda x: x[1])
    zone_name, strength = primary_zone

    if strength < 0.1:
        return ""

    strategy = select_zone_strategy(zone_name, strength, context)
    if not strategy:
        return ""

    manifestation = zone_strengths.get('manifestation', 'balanced')

    try:
        template = prompt_manager.get_template('decision')
        base_template_key = strategy.template_key

        if manifestation == 'low_energy':
            variant_key = f"{base_template_key}_low"
        elif manifestation == 'high_energy':
            variant_key = f"{base_template_key}_high"
        else:
            variant_key = base_template_key

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

    zone_labels = ENERGY_MANIFESTATION_LABELS.get(zone_name, {})
    energy_label = zone_labels.get(manifestation, '')
    if energy_label and f'| {energy_label}]' not in guidance:
        guidance = guidance.replace(']', f' | {energy_label}]', 1)

    secondary = [(n, s) for n, s in sweet_spots.items() if n != zone_name and s > 0.25]
    if secondary:
        sec_name, sec_strength = max(secondary, key=lambda x: x[1])
        guidance = guidance.replace(']', f' | {sec_name.replace("_", " ").title()} edge]', 1)

    return guidance
