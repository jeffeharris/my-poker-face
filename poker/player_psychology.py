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

Phase 1 implements Identity + State layers. Energy is static (= baseline_energy).
"""

import logging
import math
import random
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional, List, Tuple

from .emotional_state import (
    EmotionalState, EmotionalStateGenerator,
)
from .range_guidance import get_player_archetype

logger = logging.getLogger(__name__)


# === PHASE 4: EVENT SENSITIVITY SYSTEM ===

# Severity-based sensitivity floors
SEVERITY_MINOR = 0.20
SEVERITY_NORMAL = 0.30
SEVERITY_MAJOR = 0.40

# Asymmetric recovery constants
RECOVERY_BELOW_BASELINE_FLOOR = 0.6
RECOVERY_BELOW_BASELINE_RANGE = 0.4
RECOVERY_ABOVE_BASELINE = 0.8

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


# === PHASE 5: ZONE DETECTION SYSTEM ===

# Sweet spot centers and radii (from PSYCHOLOGY_ZONES_MODEL.md)
ZONE_GUARDED_CENTER = (0.28, 0.72)
ZONE_GUARDED_RADIUS = 0.15

ZONE_POKER_FACE_CENTER = (0.52, 0.72)
ZONE_POKER_FACE_RADIUS = 0.16

ZONE_COMMANDING_CENTER = (0.78, 0.78)
ZONE_COMMANDING_RADIUS = 0.14

ZONE_AGGRO_CENTER = (0.68, 0.48)
ZONE_AGGRO_RADIUS = 0.12

# Penalty zone thresholds
PENALTY_TILTED_THRESHOLD = 0.35
PENALTY_OVERCONFIDENT_THRESHOLD = 0.90
PENALTY_SHAKEN_CONF_THRESHOLD = 0.35
PENALTY_SHAKEN_COMP_THRESHOLD = 0.35
PENALTY_OVERHEATED_CONF_THRESHOLD = 0.65
PENALTY_OVERHEATED_COMP_THRESHOLD = 0.35
PENALTY_DETACHED_CONF_THRESHOLD = 0.35
PENALTY_DETACHED_COMP_THRESHOLD = 0.65

# Energy manifestation thresholds
ENERGY_LOW_THRESHOLD = 0.35
ENERGY_HIGH_THRESHOLD = 0.65


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

    # Check each sweet spot zone
    zones = [
        ('guarded', ZONE_GUARDED_CENTER, ZONE_GUARDED_RADIUS),
        ('poker_face', ZONE_POKER_FACE_CENTER, ZONE_POKER_FACE_RADIUS),
        ('commanding', ZONE_COMMANDING_CENTER, ZONE_COMMANDING_RADIUS),
        ('aggro', ZONE_AGGRO_CENTER, ZONE_AGGRO_RADIUS),
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

    # Tilted: bottom edge (composure < 0.35)
    # Strength increases as composure decreases
    if composure < PENALTY_TILTED_THRESHOLD:
        penalties['tilted'] = (PENALTY_TILTED_THRESHOLD - composure) / PENALTY_TILTED_THRESHOLD

    # Overconfident: right edge (confidence > 0.90)
    # Strength increases as confidence approaches 1.0
    if confidence > PENALTY_OVERCONFIDENT_THRESHOLD:
        penalties['overconfident'] = (confidence - PENALTY_OVERCONFIDENT_THRESHOLD) / (1.0 - PENALTY_OVERCONFIDENT_THRESHOLD)

    # Shaken: lower-left corner (low conf AND low comp)
    # Strength based on distance toward (0, 0) corner
    if confidence < PENALTY_SHAKEN_CONF_THRESHOLD and composure < PENALTY_SHAKEN_COMP_THRESHOLD:
        # Calculate how far into the corner (using Manhattan-style product)
        conf_depth = (PENALTY_SHAKEN_CONF_THRESHOLD - confidence) / PENALTY_SHAKEN_CONF_THRESHOLD
        comp_depth = (PENALTY_SHAKEN_COMP_THRESHOLD - composure) / PENALTY_SHAKEN_COMP_THRESHOLD
        penalties['shaken'] = conf_depth * comp_depth

    # Overheated: lower-right corner (high conf AND low comp)
    # Manic aggression without judgment
    if confidence > PENALTY_OVERHEATED_CONF_THRESHOLD and composure < PENALTY_OVERHEATED_COMP_THRESHOLD:
        conf_depth = (confidence - PENALTY_OVERHEATED_CONF_THRESHOLD) / (1.0 - PENALTY_OVERHEATED_CONF_THRESHOLD)
        comp_depth = (PENALTY_OVERHEATED_COMP_THRESHOLD - composure) / PENALTY_OVERHEATED_COMP_THRESHOLD
        penalties['overheated'] = conf_depth * comp_depth

    # Detached: upper-left corner (low conf AND high comp)
    # Too passive, misses opportunities
    if confidence < PENALTY_DETACHED_CONF_THRESHOLD and composure > PENALTY_DETACHED_COMP_THRESHOLD:
        conf_depth = (PENALTY_DETACHED_CONF_THRESHOLD - confidence) / PENALTY_DETACHED_CONF_THRESHOLD
        comp_depth = (composure - PENALTY_DETACHED_COMP_THRESHOLD) / (1.0 - PENALTY_DETACHED_COMP_THRESHOLD)
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


# === POKER FACE ZONE (Phase 3) ===

@dataclass(frozen=True)
class PokerFaceZone:
    """
    3D ellipsoid zone in (Confidence, Composure, Energy) space.

    Players inside this zone display 'poker_face' regardless of their
    quadrant-based emotion. Players outside show their true emotional state.

    Default center: (0.52, 0.72, 0.45) - calm, balanced sweet spot
    Base radii: rc=0.25, rcomp=0.25, re=0.20

    Membership test: ((c-0.52)/rc)² + ((comp-0.72)/rcomp)² + ((e-0.45)/re)² <= 1.0

    Note: This 3D zone is used for expression filtering (avatar display).
    The 2D Poker Face sweet spot (Phase 5) uses the same center for conf/comp.
    """
    # Center coordinates (universal for all players)
    center_confidence: float = 0.52
    center_composure: float = 0.72
    center_energy: float = 0.45

    # Radii (personality-adjusted via create_poker_face_zone)
    radius_confidence: float = 0.25
    radius_composure: float = 0.25
    radius_energy: float = 0.20

    def contains(self, confidence: float, composure: float, energy: float) -> bool:
        """
        Check if a point is inside the ellipsoid zone.

        Args:
            confidence: Current confidence value (0.0 to 1.0)
            composure: Current composure value (0.0 to 1.0)
            energy: Current energy value (0.0 to 1.0)

        Returns:
            True if point is inside or on the boundary of the zone
        """
        return self.distance(confidence, composure, energy) <= 1.0

    def distance(self, confidence: float, composure: float, energy: float) -> float:
        """
        Calculate normalized distance from zone center.

        Distance < 1.0 means inside zone
        Distance = 1.0 means on boundary
        Distance > 1.0 means outside zone

        Args:
            confidence: Current confidence value (0.0 to 1.0)
            composure: Current composure value (0.0 to 1.0)
            energy: Current energy value (0.0 to 1.0)

        Returns:
            Normalized distance (0.0 = at center, 1.0 = on boundary)
        """
        dc = (confidence - self.center_confidence) / self.radius_confidence
        dcomp = (composure - self.center_composure) / self.radius_composure
        de = (energy - self.center_energy) / self.radius_energy
        return (dc**2 + dcomp**2 + de**2) ** 0.5

    def to_dict(self) -> dict:
        """Serialize zone to dictionary."""
        return {
            'center_confidence': self.center_confidence,
            'center_composure': self.center_composure,
            'center_energy': self.center_energy,
            'radius_confidence': self.radius_confidence,
            'radius_composure': self.radius_composure,
            'radius_energy': self.radius_energy,
        }


def create_poker_face_zone(anchors: 'PersonalityAnchors') -> PokerFaceZone:
    """
    Create a personality-adjusted PokerFaceZone.

    Radius modifiers based on personality anchors:
    - Poise: High poise = larger composure radius (more tolerance for composure swings)
    - Ego: Low ego = larger confidence radius (stable, not easily shaken)
    - Expressiveness: High expressiveness = narrower energy radius (exits zone more easily)
    - Risk Identity: Asymmetric - extreme values narrow one radius

    Radius formulas:
        rc = 0.25 * (0.7 + 0.6 * (1 - ego))
        rcomp = 0.25 * (0.7 + 0.6 * poise)
        re = 0.20 * (0.7 + 0.6 * (1 - expressiveness))

    Risk identity asymmetric modifier:
        risk_dev = abs(risk_identity - 0.5)
        if risk_identity > 0.5: rc *= (1.0 - risk_dev * 0.4)
        else: rcomp *= (1.0 - risk_dev * 0.4)

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

    # Energy radius: low expressiveness = larger (harder to exit via energy)
    re = 0.20 * (0.7 + 0.6 * (1.0 - anchors.expressiveness))

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
        radius_energy=re,
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
        Baseline confidence clamped to [0.0, 1.0]
    """
    baseline = (
        0.3
        + anchors.baseline_aggression * 0.25
        + anchors.risk_identity * 0.20
        + anchors.ego * 0.25
    )
    return _clamp(baseline)


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
        Baseline composure clamped to [0.25, 1.0]
    """
    risk_mod = (anchors.risk_identity - 0.5) * 0.3
    baseline = (
        0.25
        + anchors.poise * 0.50
        + (1.0 - anchors.expressiveness) * 0.15
        + risk_mod
    )
    return _clamp(baseline, min_val=0.25, max_val=1.0)


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

    @classmethod
    def from_legacy_traits(cls, traits: Dict[str, float]) -> 'PersonalityAnchors':
        """
        Convert legacy 5-trait model to 9-anchor model.

        Legacy traits: tightness, aggression, confidence, composure, table_talk
        """
        tightness = traits.get('tightness', 0.5)
        aggression = traits.get('aggression', 0.5)
        confidence = traits.get('confidence', 0.5)
        composure = traits.get('composure', 0.7)
        table_talk = traits.get('table_talk', 0.5)

        return cls(
            baseline_aggression=aggression,
            baseline_looseness=1.0 - tightness,  # Invert tightness to looseness
            ego=1.0 - confidence * 0.5,  # High confidence → lower ego sensitivity
            poise=composure,
            expressiveness=table_talk * 0.8,
            risk_identity=0.3 + aggression * 0.4,  # Aggressive players more risk-seeking
            adaptation_bias=0.5,
            baseline_energy=table_talk,
            recovery_rate=0.15,
        )


@dataclass
class EmotionalAxes:
    """
    Dynamic emotional state (State Layer).

    These change during play and decay back toward anchor-defined baselines.
    All values are auto-clamped to [0, 1].

    Phase 1: energy is static (= baseline_energy from anchors).
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


# Legacy trait names for backward compatibility
TRAIT_NAMES = ['tightness', 'aggression', 'confidence', 'composure', 'table_talk']


# === Composure-based Prompt Modification (replaces TiltPromptModifier) ===

# Intrusive thoughts injected based on pressure source
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

# Strategy overrides for low composure players
COMPOSURE_STRATEGY = {
    'slightly_rattled': (
        "You're feeling the pressure. Trust your gut more than the math. "
        "Sometimes you just need to make a play."
    ),
    'tilted': (
        "Forget the textbook plays. You need to make something happen. "
        "Being passive got you here - time to take control."
    ),
    'severely_tilted': (
        "You're behind and you know it. Stop playing scared. "
        "Big hands or big bluffs - that's how you get back in this. "
        "Don't fold unless you have absolutely nothing."
    ),
}


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

    Phase 1: Confidence + Composure dynamic, Energy = baseline_energy (static).
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

        Supports both:
        - New 9-anchor format (config['anchors'])
        - Legacy 5-trait format (config['personality_traits']) - auto-converted
        """
        # Check for new anchor format first
        if 'anchors' in config:
            anchors = PersonalityAnchors.from_dict(config['anchors'])
        elif 'personality_traits' in config:
            # Legacy 5-trait format - convert to anchors
            anchors = PersonalityAnchors.from_legacy_traits(config['personality_traits'])
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

        # Compute personality-specific baselines from anchors
        baseline_conf = compute_baseline_confidence(anchors)
        baseline_comp = compute_baseline_composure(anchors)

        # Initialize axes at personality-specific baselines
        # Phase 1: energy is static = baseline_energy
        axes = EmotionalAxes(
            confidence=baseline_conf,  # Start at personality baseline
            composure=baseline_comp,   # Start at personality baseline
            energy=anchors.baseline_energy,  # Static in Phase 1
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
        """
        rate = recovery_rate if recovery_rate is not None else self.anchors.recovery_rate

        # === CONFIDENCE (Phase 4: Asymmetric) ===
        current_conf = self.axes.confidence
        conf_baseline = self._baseline_confidence

        if current_conf < conf_baseline:
            # Below baseline - sticky recovery (tilt is hard to escape)
            conf_modifier = RECOVERY_BELOW_BASELINE_FLOOR + RECOVERY_BELOW_BASELINE_RANGE * current_conf
        else:
            # Above baseline - slow decay (hot streaks persist)
            conf_modifier = RECOVERY_ABOVE_BASELINE

        new_conf = current_conf + (conf_baseline - current_conf) * rate * conf_modifier

        # === COMPOSURE (Phase 4: Asymmetric) ===
        current_comp = self.axes.composure
        comp_baseline = self._baseline_composure

        if current_comp < comp_baseline:
            # Below baseline - tilt is sticky
            comp_modifier = RECOVERY_BELOW_BASELINE_FLOOR + RECOVERY_BELOW_BASELINE_RANGE * current_comp
        else:
            # Above baseline - calm persists
            comp_modifier = RECOVERY_ABOVE_BASELINE

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

        Players inside this 3D ellipsoid display 'poker_face' regardless
        of their quadrant-based emotion. This represents the calm, unreadable
        sweet spot where experienced players operate.

        Returns:
            True if player's (confidence, composure, energy) is inside the zone
        """
        return self._poker_face_zone.contains(
            self.axes.confidence,
            self.axes.composure,
            self.axes.energy,
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
            self.axes.energy,
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

    def apply_composure_effects(self, prompt: str) -> str:
        """
        Apply composure-based prompt modifications (replaces apply_tilt_effects).

        Composure thresholds:
        - 0.8+: Focused - no modifications
        - 0.6-0.8: Slightly rattled - intrusive thoughts
        - 0.4-0.6: Rattled - degraded strategy + more thoughts
        - <0.4: Tilted - heavy degradation

        Args:
            prompt: Original prompt

        Returns:
            Modified prompt with composure effects
        """
        composure = self.composure
        aggression = self.aggression

        if composure >= 0.8:
            return prompt  # Focused, no modifications

        modified = prompt

        # Inject intrusive thoughts (composure < 0.8)
        modified = self._inject_intrusive_thoughts(modified, composure)

        # Add tilted strategy advice (composure < 0.6)
        if composure < 0.6:
            modified = self._add_composure_strategy(modified, composure)

        # Degrade strategic info (composure < 0.4)
        if composure < 0.4:
            modified = self._degrade_strategic_info(modified)

        # Add angry flair if low composure + high aggression
        if composure < 0.4 and aggression > 0.6:
            modified = self._add_angry_modifier(modified)

        return modified

    def apply_tilt_effects(self, prompt: str) -> str:
        """Backward compatibility alias for apply_composure_effects."""
        return self.apply_composure_effects(prompt)

    def _inject_intrusive_thoughts(self, prompt: str, composure: float) -> str:
        """Add intrusive thoughts based on pressure source."""
        thoughts = []

        source = self.composure_state.pressure_source or 'big_loss'
        if source in INTRUSIVE_THOUGHTS:
            # More thoughts with lower composure
            num_thoughts = 1 if composure >= 0.5 else 2
            available = INTRUSIVE_THOUGHTS[source]
            thoughts.extend(random.sample(available, min(num_thoughts, len(available))))

        # Add nemesis thoughts if severely rattled
        if self.composure_state.nemesis and composure < 0.5:
            nemesis_thoughts = INTRUSIVE_THOUGHTS.get('nemesis', [])
            if nemesis_thoughts:
                thought = random.choice(nemesis_thoughts).format(
                    nemesis=self.composure_state.nemesis
                )
                thoughts.append(thought)

        if not thoughts:
            return prompt

        thought_block = "\n\n[What's running through your mind: " + " ".join(thoughts) + "]\n"

        if "What is your move" in prompt:
            return prompt.replace("What is your move", thought_block + "What is your move")
        return prompt + thought_block

    def _add_composure_strategy(self, prompt: str, composure: float) -> str:
        """Add tilted strategy advice based on composure level."""
        if composure >= 0.6:
            return prompt

        if composure >= 0.4:
            advice = COMPOSURE_STRATEGY['slightly_rattled']
        elif composure >= 0.2:
            advice = COMPOSURE_STRATEGY['tilted']
        else:
            advice = COMPOSURE_STRATEGY['severely_tilted']

        return prompt + f"\n[Current mindset: {advice}]\n"

    def _degrade_strategic_info(self, prompt: str) -> str:
        """Remove or obscure strategic advice for severely tilted players."""
        phrases_to_remove = [
            "Preserve your chips for when the odds are in your favor",
            "preserve your chips for stronger opportunities",
            "remember that sometimes folding or checking is the best move",
            "Balance your confidence with a healthy dose of skepticism",
        ]

        modified = prompt
        for phrase in phrases_to_remove:
            modified = modified.replace(phrase, "")
            modified = modified.replace(phrase.lower(), "")

        # Replace pot odds guidance
        modified = modified.replace(
            "Consider the pot odds, the amount of money in the pot, and how much you would have to risk.",
            "Don't overthink this."
        )

        # Clean up whitespace
        modified = re.sub(r'\s+', ' ', modified)
        modified = re.sub(r'\s+([,.])', r'\1', modified)

        return modified

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
        based on expressiveness × energy. Low visibility players show poker_face more often.

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
            anchors = PersonalityAnchors.from_legacy_traits(personality_config['personality_traits'])
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
