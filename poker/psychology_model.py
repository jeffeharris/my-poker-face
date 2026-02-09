"""
Core data structures for the psychology system.

Contains the identity layer (PersonalityAnchors), state layer (EmotionalAxes),
quadrant model, poker face zone, and baseline computation functions.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, Optional, List, Tuple

from .zone_config import get_zone_param

logger = logging.getLogger(__name__)


def _clamp(value: float, min_val: float = 0.0, max_val: float = 1.0) -> float:
    """Clamp value to range [min_val, max_val]."""
    return max(min_val, min(max_val, value))


class EmotionalQuadrant(Enum):
    """
    Emotional quadrant from Confidence x Composure projection.

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


@dataclass(frozen=True)
class ComposureState:
    """
    Tracks composure-related state (replaces TiltState).

    Composure is now a trait in the elastic system, but we still track
    source/nemesis for intrusive thoughts.
    """
    pressure_source: str = ''    # 'bad_beat', 'bluff_called', 'big_loss', etc.
    nemesis: Optional[str] = None  # Player who caused pressure
    recent_losses: Tuple[Dict[str, Any], ...] = ()
    losing_streak: int = 0

    def update_from_event(self, event_name: str, opponent: Optional[str] = None) -> 'ComposureState':
        """Return new ComposureState updated from a pressure event."""
        negative_events = {
            'bad_beat', 'bluff_called', 'big_loss', 'got_sucked_out',
            'losing_streak', 'crippled', 'nemesis_loss'
        }
        if event_name in negative_events:
            return ComposureState(
                pressure_source=event_name,
                nemesis=opponent if opponent else self.nemesis,
                recent_losses=self.recent_losses,
                losing_streak=self.losing_streak,
            )
        return self

    def update_from_hand(
        self,
        outcome: str,
        amount: int,
        opponent: Optional[str] = None,
        was_bad_beat: bool = False,
        was_bluff_called: bool = False,
        big_blind: int = 100,
    ) -> 'ComposureState':
        """Return new ComposureState updated from hand outcome."""
        # Folding blinds (< 3 BB invested) is routine — doesn't count as a loss
        if outcome == 'folded' and abs(amount) < 3 * big_blind:
            return self

        if outcome == 'lost' or outcome == 'folded':
            new_streak = self.losing_streak + 1
            if new_streak >= 3:
                new_source = 'losing_streak'
            elif was_bad_beat:
                new_source = 'bad_beat'
            elif was_bluff_called:
                new_source = 'bluff_called'
            elif amount < -1000:  # Big loss
                new_source = 'big_loss'
            else:
                new_source = self.pressure_source

            new_losses = (self.recent_losses + ({
                'amount': amount,
                'opponent': opponent,
                'was_bad_beat': was_bad_beat
            },))[-5:]

            return ComposureState(
                pressure_source=new_source,
                nemesis=opponent if opponent else self.nemesis,
                recent_losses=new_losses,
                losing_streak=new_streak,
            )

        elif outcome == 'won':
            return ComposureState(
                pressure_source='' if amount > 500 else self.pressure_source,
                nemesis=self.nemesis,
                recent_losses=self.recent_losses,
                losing_streak=0,
            )

        return self

    @property
    def tilt_source(self) -> str:
        """Backward compatibility alias for pressure_source."""
        return self.pressure_source

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            'pressure_source': self.pressure_source,
            'nemesis': self.nemesis,
            'recent_losses': list(self.recent_losses),
            'losing_streak': self.losing_streak,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ComposureState':
        """Deserialize from dictionary."""
        return cls(
            pressure_source=data.get('pressure_source', ''),
            nemesis=data.get('nemesis'),
            recent_losses=tuple(data.get('recent_losses', ())),
            losing_streak=data.get('losing_streak', 0),
        )

    @classmethod
    def from_tilt_state(cls, tilt_data: Dict[str, Any]) -> 'ComposureState':
        """Convert old TiltState format to ComposureState."""
        return cls(
            pressure_source=tilt_data.get('tilt_source', ''),
            nemesis=tilt_data.get('nemesis'),
            recent_losses=tuple(tilt_data.get('recent_losses', ())),
            losing_streak=tilt_data.get('losing_streak', 0),
        )


@dataclass(frozen=True)
class PokerFaceZone:
    """
    2D ellipse zone in (Confidence, Composure) space.

    Players inside this zone display 'poker_face' regardless of their
    quadrant-based emotion. Players outside show their true emotional state
    (filtered by the expression layer's visibility = 0.7*expressiveness + 0.3*energy).

    Default center: (0.52, 0.72) - calm, balanced sweet spot
    Base radii: rc=0.25, rcomp=0.25

    Membership test: ((c-0.52)/rc)^2 + ((comp-0.72)/rcomp)^2 <= 1.0

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
        """Check if a point is inside the ellipse zone."""
        return self.distance(confidence, composure) <= 1.0

    def distance(self, confidence: float, composure: float) -> float:
        """
        Calculate normalized distance from zone center.

        Distance < 1.0 means inside zone
        Distance = 1.0 means on boundary
        Distance > 1.0 means outside zone
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


def create_poker_face_zone(anchors: PersonalityAnchors) -> PokerFaceZone:
    """
    Create a personality-adjusted PokerFaceZone (2D: confidence x composure).

    Radius modifiers based on personality anchors:
    - Poise: High poise = larger composure radius (more tolerance for composure swings)
    - Ego: Low ego = larger confidence radius (stable, not easily shaken)
    - Risk Identity: Asymmetric - extreme values narrow one radius
    """
    # Base radius modifiers (0.7 floor + 0.6 range = 0.7 to 1.3 multiplier)
    rc = 0.25 * (0.7 + 0.6 * (1.0 - anchors.ego))
    rcomp = 0.25 * (0.7 + 0.6 * anchors.poise)

    # Risk identity asymmetric modifier
    risk_dev = abs(anchors.risk_identity - 0.5)  # 0 to 0.5
    if anchors.risk_identity > 0.5:
        rc *= (1.0 - risk_dev * 0.4)
    else:
        rcomp *= (1.0 - risk_dev * 0.4)

    return PokerFaceZone(
        radius_confidence=rc,
        radius_composure=rcomp,
    )


def compute_baseline_confidence(anchors: PersonalityAnchors) -> float:
    """
    Derive baseline confidence from personality anchors.

    Formula:
        baseline_confidence = 0.3 (floor)
            + baseline_aggression * 0.25  (aggressive = confident)
            + risk_identity * 0.20        (risk-seekers expect to win)
            + ego * 0.25                  (high ego = high self-regard)

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
    margin = 0.10
    timid_thresh = get_zone_param('PENALTY_TIMID_THRESHOLD')
    overconf_thresh = get_zone_param('PENALTY_OVERCONFIDENT_THRESHOLD')
    min_conf = min(0.45, timid_thresh + margin)
    max_conf = max(0.55, overconf_thresh - margin)
    return _clamp(baseline, min_val=min_conf, max_val=max_conf)


def compute_baseline_composure(anchors: PersonalityAnchors) -> float:
    """
    Derive baseline composure from personality anchors.

    Formula:
        risk_mod = (risk_identity - 0.5) * 0.3  (range: -0.15 to +0.15)
        baseline_composure = 0.25 (floor)
            + poise * 0.50                (primary driver)
            + (1 - expressiveness) * 0.15 (low expressiveness = control)
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
    margin = 0.05
    tilted_thresh = get_zone_param('PENALTY_TILTED_THRESHOLD')
    min_comp = min(0.55, tilted_thresh + margin)
    max_comp = 1.0 - margin
    return _clamp(baseline, min_val=min_comp, max_val=max_comp)


def get_quadrant(confidence: float, composure: float) -> EmotionalQuadrant:
    """
    Determine emotional quadrant from Confidence x Composure.

    Quadrant boundaries:
    - COMMANDING: confidence > 0.5 AND composure > 0.5
    - OVERHEATED: confidence > 0.5 AND composure <= 0.5
    - GUARDED: confidence <= 0.5 AND composure > 0.5
    - SHAKEN: confidence <= 0.5 AND composure <= 0.5

    An early check returns SHAKEN when both < 0.35 (deep SHAKEN shortcut).
    """
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
    - aggression_mod = (confidence - 0.5) * 0.3 + (0.5 - composure) * 0.2
    - looseness_mod = (confidence - 0.5) * 0.2 + (0.5 - composure) * 0.15
    - Clamped to +/-0.20

    Shaken gate (confidence < 0.35 AND composure < 0.35):
    - Behavior splits based on risk_identity
    - Risk-seeking (> 0.5): manic spew (+aggression, +looseness)
    - Risk-averse (< 0.5): passive collapse (-aggression, -looseness)
    - Clamped to +/-0.30

    Returns:
        (aggression_modifier, looseness_modifier)
    """
    aggression_mod = (confidence - 0.5) * 0.3 + (0.5 - composure) * 0.2
    looseness_mod = (confidence - 0.5) * 0.2 + (0.5 - composure) * 0.15

    if confidence < 0.35 and composure < 0.35:
        shaken_intensity = (0.35 - confidence) + (0.35 - composure)

        if risk_identity > 0.5:
            aggression_mod += shaken_intensity * 0.3
            looseness_mod += shaken_intensity * 0.3
        else:
            aggression_mod -= shaken_intensity * 0.3
            looseness_mod -= shaken_intensity * 0.3

        return (
            _clamp(aggression_mod, -0.30, 0.30),
            _clamp(looseness_mod, -0.30, 0.30),
        )

    return (
        _clamp(aggression_mod, -0.20, 0.20),
        _clamp(looseness_mod, -0.20, 0.20),
    )
