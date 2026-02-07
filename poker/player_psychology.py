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

import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple

from .emotional_state import (
    EmotionalState, EmotionalStateGenerator,
)
from .range_guidance import get_player_archetype

logger = logging.getLogger(__name__)


# === RE-EXPORTS ===
# All existing `from poker.player_psychology import X` statements continue working.

# From zone_config
from .zone_config import (  # noqa: F401
    get_zone_param,
    set_zone_params,
    clear_zone_params,
    get_all_zone_params,
    _load_zone_params,
    SEVERITY_MINOR,
    SEVERITY_NORMAL,
    SEVERITY_MAJOR,
    RECOVERY_BELOW_BASELINE_FLOOR,
    RECOVERY_BELOW_BASELINE_RANGE,
    RECOVERY_ABOVE_BASELINE,
    EVENT_SEVERITY,
    _get_severity_floor,
    _calculate_sensitivity,
)

# From psychology_model
from .psychology_model import (  # noqa: F401
    _clamp,
    EmotionalQuadrant,
    PersonalityAnchors,
    EmotionalAxes,
    ComposureState,
    PokerFaceZone,
    create_poker_face_zone,
    compute_baseline_confidence,
    compute_baseline_composure,
    get_quadrant,
    compute_modifiers,
)

# From zone_detection
from .zone_detection import (  # noqa: F401
    ZONE_GUARDED_CENTER,
    ZONE_POKER_FACE_CENTER,
    ZONE_COMMANDING_CENTER,
    ZONE_AGGRO_CENTER,
    ZONE_GUARDED_RADIUS,
    ZONE_POKER_FACE_RADIUS,
    ZONE_COMMANDING_RADIUS,
    ZONE_AGGRO_RADIUS,
    PENALTY_TILTED_THRESHOLD,
    PENALTY_OVERCONFIDENT_THRESHOLD,
    PENALTY_TIMID_THRESHOLD,
    PENALTY_SHAKEN_CONF_THRESHOLD,
    PENALTY_SHAKEN_COMP_THRESHOLD,
    PENALTY_OVERHEATED_CONF_THRESHOLD,
    PENALTY_OVERHEATED_COMP_THRESHOLD,
    PENALTY_DETACHED_CONF_THRESHOLD,
    PENALTY_DETACHED_COMP_THRESHOLD,
    ENERGY_LOW_THRESHOLD,
    ENERGY_HIGH_THRESHOLD,
    ZoneStrategy,
    ZoneContext,
    ZONE_STRATEGIES,
    ENERGY_MANIFESTATION_LABELS,
    ZoneEffects,
    _calculate_sweet_spot_strength,
    _detect_sweet_spots,
    _detect_penalty_zones,
    _get_zone_manifestation,
    get_zone_effects,
    select_zone_strategy,
    build_zone_guidance,
)

# From playstyle_selector
from .playstyle_selector import (  # noqa: F401
    PlaystyleState,
    PlaystyleBriefing,
    compute_playstyle_affinities,
    derive_primary_playstyle,
    compute_identity_bias,
    compute_exploit_scores,
    compute_election_interval,
    select_playstyle,
    build_playstyle_briefing,
)

# From zone_effects
from .zone_effects import (  # noqa: F401
    INTRUSIVE_THOUGHTS,
    SHAKEN_THOUGHTS,
    OVERHEATED_THOUGHTS,
    OVERCONFIDENT_THOUGHTS,
    DETACHED_THOUGHTS,
    TIMID_THOUGHTS,
    ENERGY_THOUGHT_VARIANTS,
    PENALTY_STRATEGY,
    PHRASES_TO_REMOVE_BY_ZONE,
    _should_inject_thoughts,
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

    # Personality anchors (static identity)
    anchors: PersonalityAnchors

    # Dynamic emotional axes
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

    # Consecutive fold tracking for card_dead events
    consecutive_folds: int = 0

    # Poker Face Zone (2D ellipse in confidence/composure space)
    _poker_face_zone: Optional[PokerFaceZone] = field(default=None, repr=False)

    # Derived baselines (computed from anchors, used for recovery)
    _baseline_confidence: Optional[float] = field(default=None, repr=False)
    _baseline_composure: Optional[float] = field(default=None, repr=False)

    # Playstyle selection state
    _playstyle_state: Optional[PlaystyleState] = field(default=None, repr=False)
    _identity_biases: Optional[Dict[str, float]] = field(default=None, repr=False)

    def __post_init__(self):
        """Initialize emotional state generator, compute baselines, and create poker face zone."""
        if self._emotional_generator is None:
            self._emotional_generator = EmotionalStateGenerator()

        # Compute derived baselines if not already set
        if self._baseline_confidence is None:
            object.__setattr__(self, '_baseline_confidence', compute_baseline_confidence(self.anchors))
        if self._baseline_composure is None:
            object.__setattr__(self, '_baseline_composure', compute_baseline_composure(self.anchors))

        # Create personality-adjusted poker face zone
        if self._poker_face_zone is None:
            object.__setattr__(self, '_poker_face_zone', create_poker_face_zone(self.anchors))

        # Initialize playstyle from baseline axes
        if self._playstyle_state is None:
            primary = derive_primary_playstyle(self._baseline_confidence, self._baseline_composure)
            object.__setattr__(self, '_playstyle_state', PlaystyleState(
                active_playstyle=primary,
                primary_playstyle=primary,
            ))
        if self._identity_biases is None:
            object.__setattr__(self, '_identity_biases', compute_identity_bias(
                self._playstyle_state.primary_playstyle
            ))

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
        - "Being wrong" events -> Confidence (filtered by Ego)
        - "Bad outcome" events -> Composure (filtered by Poise)
        - Energy events -> Direct application (no sensitivity filter)

        Uses severity-based sensitivity floors:
        - Minor events: floor=0.20 (routine gameplay)
        - Normal events: floor=0.30 (standard stakes)
        - Major events: floor=0.40 (high-impact moments)
        """
        pressure_impacts = self._get_pressure_impacts(event_name)
        floor = _get_severity_floor(event_name)

        new_conf = self.axes.confidence
        new_comp = self.axes.composure
        new_energy = self.axes.energy

        if 'confidence' in pressure_impacts:
            sensitivity = _calculate_sensitivity(self.anchors.ego, floor)
            delta = pressure_impacts['confidence'] * sensitivity
            new_conf = self.axes.confidence + delta

        if 'composure' in pressure_impacts:
            sensitivity = _calculate_sensitivity(1.0 - self.anchors.poise, floor)
            delta = pressure_impacts['composure'] * sensitivity
            new_comp = self.axes.composure + delta

        if 'energy' in pressure_impacts:
            delta = pressure_impacts['energy']
            new_energy = self.axes.energy + delta

        self.axes = self.axes.update(
            confidence=new_conf,
            composure=new_comp,
            energy=new_energy,
        )

        self.composure_state = self.composure_state.update_from_event(event_name, opponent)
        self._mark_updated()

        logger.debug(
            f"{self.player_name}: Pressure event '{event_name}' (floor={floor:.2f}) applied. "
            f"Confidence={self.confidence:.2f}, Composure={self.composure:.2f}, "
            f"Energy={self.energy:.2f}, Quadrant={self.quadrant.value}"
        )

    # === EVENT RESOLUTION CONSTANTS ===

    # Event categories for resolve_hand_events()
    OUTCOME_EVENTS = {'win', 'loss', 'big_win', 'big_loss', 'headsup_win', 'headsup_loss'}
    EGO_EVENTS = {'successful_bluff', 'bluff_called', 'nemesis_win', 'nemesis_loss'}
    EQUITY_SHOCK_EVENTS = {'bad_beat', 'cooler', 'suckout', 'got_sucked_out'}
    PRESSURE_EVENTS = {'big_pot_involved', 'all_in_moment', 'card_dead_5', 'consecutive_folds_3', 'not_in_hand', 'disciplined_fold', 'short_stack_survival'}
    DESPERATION_EVENTS = {'short_stack', 'crippled', 'fold_under_pressure'}
    STREAK_EVENTS = {'winning_streak', 'losing_streak'}

    # Outcome priority (higher index = higher priority)
    OUTCOME_PRIORITY = ['loss', 'win', 'headsup_loss', 'headsup_win', 'big_loss', 'big_win']

    # Equity shock priority (higher index = higher priority)
    EQUITY_SHOCK_PRIORITY = ['suckout', 'cooler', 'got_sucked_out', 'bad_beat']

    def _get_pressure_impacts(self, event_name: str) -> Dict[str, float]:
        """Get axis impacts for a pressure event."""
        pressure_events = {
            # Outcomes (pick ONE via resolve_hand_events)
            'win': {'confidence': 0.02, 'energy': 0.02},
            'loss': {'confidence': -0.02, 'energy': -0.02},
            'big_win': {'confidence': 0.12, 'composure': 0.02, 'energy': 0.08},
            'big_loss': {'confidence': -0.15, 'composure': -0.05, 'energy': -0.08},
            'headsup_win': {'confidence': 0.06, 'composure': 0.02, 'energy': 0.05},
            'headsup_loss': {'confidence': -0.06, 'composure': -0.02, 'energy': -0.05},
            # Ego/Agency (at most ONE, scaled 50% via resolve_hand_events)
            'successful_bluff': {'confidence': 0.20, 'composure': 0.05, 'energy': 0.05},
            'bluff_called': {'confidence': -0.25, 'composure': -0.10, 'energy': -0.05},
            'nemesis_win': {'confidence': 0.18, 'composure': 0.05, 'energy': 0.05},
            'nemesis_loss': {'confidence': -0.18, 'composure': -0.05, 'energy': -0.05},
            # Equity Shock (at most ONE, composure+energy only — no confidence)
            'bad_beat': {'composure': -0.35, 'energy': -0.10},
            'cooler': {'composure': -0.20, 'energy': -0.05},
            'suckout': {'composure': 0.10, 'energy': 0.05},
            'got_sucked_out': {'composure': -0.30, 'energy': -0.15},
            # Streaks (additive)
            'winning_streak': {'confidence': 0.10, 'composure': -0.05, 'energy': 0.05},
            'losing_streak': {'confidence': -0.12, 'composure': -0.20, 'energy': -0.10},
            # Pressure/Fatigue (additive, no confidence)
            'big_pot_involved': {'composure': -0.05, 'energy': -0.05},
            'all_in_moment': {'composure': -0.08, 'energy': -0.08},
            'card_dead_5': {'confidence': -0.03, 'composure': 0.03, 'energy': -0.10},
            'consecutive_folds_3': {'composure': -0.05, 'energy': -0.08},
            'not_in_hand': {'energy': -0.02},
            'disciplined_fold': {'confidence': -0.06, 'composure': 0.12, 'energy': -0.02},
            'short_stack_survival': {'confidence': -0.04, 'composure': 0.06, 'energy': -0.05},
            # Desperation (additive)
            'short_stack': {'confidence': -0.08, 'composure': -0.15, 'energy': -0.10},
            'crippled': {'confidence': -0.20, 'composure': -0.25, 'energy': -0.15},
            'fold_under_pressure': {'confidence': -0.10, 'composure': 0.05},
        }

        return pressure_events.get(event_name, {})

    def resolve_hand_events(
        self,
        events: List[str],
        opponent: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Resolve a set of detected events into a single psychological update.

        Resolution rules:
        1. Select ONE outcome event (highest priority) -> full strength
        2. Apply at most ONE ego/agency modifier -> scaled 50%
        3. Apply ALL pressure/fatigue events -> additive
        4. Apply ALL desperation + streak events -> additive
        5. Apply at most ONE equity shock event -> full strength (composure-only)
        6. Clamp axes

        Args:
            events: List of event names detected for this player
            opponent: Optional opponent name for composure tracking

        Returns:
            Dict with events_applied, per-axis deltas, and final values
        """
        events_applied = []
        # Track per-event raw deltas so callers can persist accurate breakdowns
        per_event_raw = []  # parallel to events_applied: (conf, comp, energy)
        total_conf_delta = 0.0
        total_comp_delta = 0.0
        total_energy_delta = 0.0

        def _record_event(name: str, conf: float, comp: float, energy: float):
            nonlocal total_conf_delta, total_comp_delta, total_energy_delta
            total_conf_delta += conf
            total_comp_delta += comp
            total_energy_delta += energy
            events_applied.append(name)
            per_event_raw.append((conf, comp, energy))

        # 1. Select ONE outcome (highest priority wins)
        outcome_events = [e for e in events if e in self.OUTCOME_EVENTS]
        if outcome_events:
            best_outcome = max(outcome_events, key=lambda e: self.OUTCOME_PRIORITY.index(e))
            impacts = self._get_pressure_impacts(best_outcome)
            _record_event(best_outcome,
                          impacts.get('confidence', 0),
                          impacts.get('composure', 0),
                          impacts.get('energy', 0))

        # 2. At most ONE ego/agency modifier, scaled 50%
        ego_events = [e for e in events if e in self.EGO_EVENTS]
        if ego_events:
            ego_event = ego_events[0]  # Take first detected
            impacts = self._get_pressure_impacts(ego_event)
            _record_event(ego_event,
                          impacts.get('confidence', 0) * 0.5,
                          impacts.get('composure', 0) * 0.5,
                          impacts.get('energy', 0) * 0.5)

        # 3. ALL pressure/fatigue events (additive)
        for event in events:
            if event in self.PRESSURE_EVENTS:
                impacts = self._get_pressure_impacts(event)
                _record_event(event,
                              impacts.get('confidence', 0),
                              impacts.get('composure', 0),
                              impacts.get('energy', 0))

        # 4. ALL desperation + streak events (additive)
        for event in events:
            if event in self.DESPERATION_EVENTS or event in self.STREAK_EVENTS:
                impacts = self._get_pressure_impacts(event)
                _record_event(event,
                              impacts.get('confidence', 0),
                              impacts.get('composure', 0),
                              impacts.get('energy', 0))

        # 5. At most ONE equity shock event (highest priority)
        shock_events = [e for e in events if e in self.EQUITY_SHOCK_EVENTS]
        if shock_events:
            best_shock = max(shock_events, key=lambda e: self.EQUITY_SHOCK_PRIORITY.index(e))
            impacts = self._get_pressure_impacts(best_shock)
            _record_event(best_shock,
                          0,  # equity shocks don't affect confidence
                          impacts.get('composure', 0),
                          impacts.get('energy', 0))

        # Apply deltas through sensitivity system
        pre_conf = self.axes.confidence
        pre_comp = self.axes.composure
        pre_energy = self.axes.energy

        # Use a blended severity floor based on the most impactful event
        floor = max(
            (_get_severity_floor(e) for e in events_applied),
            default=SEVERITY_NORMAL,
        )

        new_conf = pre_conf
        new_comp = pre_comp
        new_energy = pre_energy

        # Compute per-axis sensitivities (linear multipliers applied to raw deltas)
        conf_sensitivity = 1.0
        comp_sensitivity = 1.0
        if total_conf_delta != 0:
            conf_sensitivity = _calculate_sensitivity(self.anchors.ego, floor)
            new_conf = pre_conf + total_conf_delta * conf_sensitivity

        if total_comp_delta != 0:
            comp_sensitivity = _calculate_sensitivity(1.0 - self.anchors.poise, floor)
            new_comp = pre_comp + total_comp_delta * comp_sensitivity

        if total_energy_delta != 0:
            new_energy = pre_energy + total_energy_delta

        self.axes = self.axes.update(
            confidence=new_conf,
            composure=new_comp,
            energy=new_energy,
        )

        # Build per-event deltas with sensitivity applied
        # Since sensitivity is a linear multiplier, per-event deltas sum to the total
        per_event_deltas = {}
        for i, event_name in enumerate(events_applied):
            raw_conf, raw_comp, raw_energy = per_event_raw[i]
            per_event_deltas[event_name] = {
                'conf_delta': round(raw_conf * conf_sensitivity, 6),
                'comp_delta': round(raw_comp * comp_sensitivity, 6),
                'energy_delta': round(raw_energy, 6),
            }

        # Update composure tracking
        for event in events_applied:
            self.composure_state = self.composure_state.update_from_event(event, opponent)

        self._mark_updated()

        logger.debug(
            f"{self.player_name}: Resolved {len(events_applied)} events {events_applied}. "
            f"Conf={self.confidence:.2f} (d={total_conf_delta:+.3f}), "
            f"Comp={self.composure:.2f} (d={total_comp_delta:+.3f}), "
            f"Energy={self.energy:.2f} (d={total_energy_delta:+.3f})"
        )

        return {
            'events_applied': events_applied,
            'per_event_deltas': per_event_deltas,
            'conf_delta': round(new_conf - pre_conf, 6),
            'comp_delta': round(new_comp - pre_comp, 6),
            'energy_delta': round(new_energy - pre_energy, 6),
            'conf_after': round(self.confidence, 4),
            'comp_after': round(self.composure, 4),
            'energy_after': round(self.energy, 4),
        }

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
        """Called after each hand completes. Updates composure tracking and generates new emotional state."""
        self.composure_state = self.composure_state.update_from_hand(
            outcome=outcome,
            amount=amount,
            opponent=opponent,
            was_bad_beat=was_bad_beat,
            was_bluff_called=was_bluff_called,
        )

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

    def recover(self, recovery_rate: Optional[float] = None) -> Dict[str, Any]:
        """
        Apply recovery between hands.

        Axes drift toward personality-specific baselines:
        - Confidence -> _baseline_confidence
        - Composure -> _baseline_composure
        - Energy -> baseline_energy (with edge springs at extremes)

        Asymmetric recovery:
        - Below baseline: sticky recovery (modifier = floor + range * current)
        - Above baseline: slow decay (modifier = 0.8)

        Returns:
            Dict with recovery deltas and before/after values
        """
        rate = recovery_rate if recovery_rate is not None else self.anchors.recovery_rate

        # Capture before state
        pre_conf = self.axes.confidence
        pre_comp = self.axes.composure
        pre_energy = self.axes.energy

        # Confidence (asymmetric)
        conf_baseline = self._baseline_confidence

        if pre_conf < conf_baseline:
            floor = get_zone_param('RECOVERY_BELOW_BASELINE_FLOOR')
            range_ = get_zone_param('RECOVERY_BELOW_BASELINE_RANGE')
            conf_modifier = floor + range_ * pre_conf
        else:
            conf_modifier = get_zone_param('RECOVERY_ABOVE_BASELINE')

        new_conf = pre_conf + (conf_baseline - pre_conf) * rate * conf_modifier

        # Composure (asymmetric)
        comp_baseline = self._baseline_composure

        if pre_comp < comp_baseline:
            floor = get_zone_param('RECOVERY_BELOW_BASELINE_FLOOR')
            range_ = get_zone_param('RECOVERY_BELOW_BASELINE_RANGE')
            comp_modifier = floor + range_ * pre_comp
        else:
            comp_modifier = get_zone_param('RECOVERY_ABOVE_BASELINE')

        new_comp = pre_comp + (comp_baseline - pre_comp) * rate * comp_modifier

        # Energy (edge springs)
        energy_rate = rate
        energy_target = self.anchors.baseline_energy

        if pre_energy < 0.15:
            spring = (0.15 - pre_energy) * 0.33
            energy_rate += spring
        elif pre_energy > 0.85:
            spring = (pre_energy - 0.85) * 0.33
            energy_rate += spring

        new_energy = pre_energy + (energy_target - pre_energy) * energy_rate

        self.axes = self.axes.update(
            confidence=new_conf,
            composure=new_comp,
            energy=new_energy,
        )

        self._mark_updated()

        return {
            'recovery_conf': round(new_conf - pre_conf, 6),
            'recovery_comp': round(new_comp - pre_comp, 6),
            'recovery_energy': round(new_energy - pre_energy, 6),
            'conf_after': round(new_conf, 4),
            'comp_after': round(new_comp, 4),
            'energy_after': round(new_energy, 4),
        }

    def on_action_taken(self, action: str) -> List[str]:
        """
        Track player action for consecutive fold detection.

        Returns:
            List of energy events triggered
        """
        events = []

        if action == 'fold':
            self.consecutive_folds += 1
            if self.consecutive_folds == 3:
                events.append('consecutive_folds_3')
            elif self.consecutive_folds == 5:
                events.append('card_dead_5')
        else:
            self.consecutive_folds = 0

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
        """Current emotional quadrant from confidence x composure."""
        return get_quadrant(self.axes.confidence, self.axes.composure)

    # === POKER FACE ZONE ===

    def is_in_poker_face_zone(self) -> bool:
        """Check if player is currently in the poker face zone."""
        return self._poker_face_zone.contains(
            self.axes.confidence,
            self.axes.composure,
        )

    @property
    def zone_distance(self) -> float:
        """Normalized distance from the poker face zone center."""
        return self._poker_face_zone.distance(
            self.axes.confidence,
            self.axes.composure,
        )

    # === ZONE DETECTION ===

    @property
    def zone_effects(self) -> ZoneEffects:
        """Get current zone effects based on emotional state."""
        return get_zone_effects(
            self.axes.confidence,
            self.axes.composure,
            self.axes.energy,
        )

    @property
    def primary_zone(self) -> str:
        """Get the name of the strongest zone, or 'neutral'."""
        effects = self.zone_effects

        if effects.primary_penalty:
            return effects.primary_penalty

        if effects.primary_sweet_spot:
            return effects.primary_sweet_spot

        return 'neutral'

    # === PLAYSTYLE SELECTION ===

    def update_playstyle(
        self,
        opponent_models: Optional[Dict[str, Any]] = None,
        hand_number: int = 0,
    ) -> PlaystyleState:
        """
        Select the appropriate playstyle based on current emotional state,
        identity, and opponent exploitation opportunities.

        Updates internal _playstyle_state and returns it.
        """
        nemesis = self.composure_state.nemesis if self.composure_state else None

        self._playstyle_state = select_playstyle(
            current_state=self._playstyle_state,
            confidence=self.confidence,
            composure=self.composure,
            energy=self.energy,
            adaptation_bias=self.anchors.adaptation_bias,
            identity_biases=self._identity_biases,
            opponent_models=opponent_models,
            nemesis=nemesis,
            hand_number=hand_number,
        )

        return self._playstyle_state

    @property
    def playstyle_state(self) -> PlaystyleState:
        """Get current playstyle state."""
        return self._playstyle_state

    @property
    def active_playstyle(self) -> str:
        """Get current active playstyle name."""
        return self._playstyle_state.active_playstyle

    # === DERIVED VALUES ===

    @property
    def effective_aggression(self) -> float:
        """Derived aggression = baseline + emotional modifier."""
        agg_mod, _ = compute_modifiers(
            self.axes.confidence,
            self.axes.composure,
            self.anchors.risk_identity,
        )
        return _clamp(self.anchors.baseline_aggression + agg_mod)

    @property
    def effective_looseness(self) -> float:
        """Derived looseness = baseline + emotional modifier."""
        _, loose_mod = compute_modifiers(
            self.axes.confidence,
            self.axes.composure,
            self.anchors.risk_identity,
        )
        return _clamp(self.anchors.baseline_looseness + loose_mod)

    # === BACKWARD COMPAT PROPERTIES ===

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
        """Table talk (energy proxy)."""
        return self.axes.energy

    @property
    def traits(self) -> Dict[str, float]:
        """Get current trait values (backward compat)."""
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
        """Backward compatibility property for accessing composure state."""
        return self.composure_state

    @tilt.setter
    def tilt(self, value: ComposureState) -> None:
        """Allow setting composure_state via tilt property for backward compatibility."""
        self.composure_state = value

    @property
    def tilt_level(self) -> float:
        """Tilt level for backward compatibility. Tilt = 1.0 - composure."""
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
        """Tilt category for backward compatibility: 'none', 'mild', 'moderate', 'severe'."""
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
        """True if composure < 0.4."""
        return self.composure < 0.4

    # === PROMPT BUILDING ===

    def get_prompt_section(self) -> str:
        """Get emotional state section for prompt injection."""
        if self.is_severely_tilted or not self.emotional:
            return ""
        return self.emotional.to_prompt_section()

    def apply_zone_effects(self, prompt: str) -> str:
        """
        Apply zone-based prompt modifications.

        Uses zone detection to apply penalty zone effects:
        1. Inject intrusive thoughts (probabilistic)
        2. Add bad advice (if penalty intensity >= 0.25)
        3. Degrade strategic info (if penalty intensity >= 0.50)
        """
        zone_fx = self.zone_effects
        penalties = zone_fx.penalties
        total_penalty = sum(penalties.values())

        instrumentation = {
            'intrusive_thoughts_injected': False,
            'intrusive_thoughts': [],
            'penalty_strategy_applied': None,
            'info_degraded': False,
            'strategy_selected': None,
        }

        if total_penalty < 0.10:
            self._last_zone_effects_instrumentation = instrumentation
            return prompt

        modified = prompt

        # 1. Inject intrusive thoughts (probabilistic)
        modified, injected_thoughts = self._inject_zone_thoughts_instrumented(modified, zone_fx)
        if injected_thoughts:
            instrumentation['intrusive_thoughts_injected'] = True
            instrumentation['intrusive_thoughts'] = injected_thoughts

        # 2. Add bad advice (if penalty intensity >= 0.25)
        if total_penalty >= 0.25:
            modified, strategy_text = self._add_penalty_strategy_instrumented(modified, zone_fx)
            if strategy_text:
                instrumentation['penalty_strategy_applied'] = strategy_text

        # 3. Degrade strategic info (if penalty intensity >= 0.50)
        if total_penalty >= 0.50:
            modified, was_degraded = self._degrade_strategic_info_by_zone_instrumented(modified, zone_fx)
            instrumentation['info_degraded'] = was_degraded

        # Add angry flair if low composure + high aggression
        if self.composure < 0.4 and self.aggression > 0.6:
            modified = self._add_angry_modifier(modified)

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
        """Get available intrusive thoughts for a penalty zone."""
        thoughts = []

        if zone_name == 'tilted':
            source = self.composure_state.pressure_source or 'big_loss'
            if source in INTRUSIVE_THOUGHTS:
                thoughts.extend(INTRUSIVE_THOUGHTS[source])
        elif zone_name == 'shaken':
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
        """Add intrusive thoughts based on active penalty zones."""
        modified, _ = self._inject_zone_thoughts_instrumented(prompt, zone_effects)
        return modified

    def _inject_zone_thoughts_instrumented(
        self, prompt: str, zone_effects: ZoneEffects
    ) -> Tuple[str, List[str]]:
        """Add intrusive thoughts with instrumentation tracking."""
        thoughts = []
        penalties = zone_effects.penalties
        manifestation = zone_effects.manifestation

        for zone_name, intensity in penalties.items():
            if not _should_inject_thoughts(intensity):
                continue

            zone_thoughts = self._get_zone_thoughts(zone_name, manifestation, intensity)
            if zone_thoughts:
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
        """Add bad advice based on active penalty zones."""
        modified, _ = self._add_penalty_strategy_instrumented(prompt, zone_effects)
        return modified

    def _add_penalty_strategy_instrumented(
        self, prompt: str, zone_effects: ZoneEffects
    ) -> Tuple[str, Optional[str]]:
        """Add bad advice with instrumentation tracking."""
        penalties = zone_effects.penalties
        if not penalties:
            return prompt, None

        strongest_zone = max(penalties, key=penalties.get)
        intensity = penalties[strongest_zone]

        if intensity < 0.25:
            return prompt, None

        if intensity >= 0.70:
            tier = 'severe'
        elif intensity >= 0.40:
            tier = 'moderate'
        else:
            tier = 'mild'

        zone_key = strongest_zone
        if strongest_zone == 'shaken':
            if self.anchors.risk_identity > 0.5:
                zone_key = 'shaken_risk_seeking'
            else:
                zone_key = 'shaken_risk_averse'

        advice = PENALTY_STRATEGY.get(zone_key, {}).get(tier, '')
        if advice:
            manifestation = zone_effects.manifestation
            if manifestation == 'high_energy':
                advice = advice.replace('.', '!')
            elif manifestation == 'low_energy':
                suffixes = [" Whatever.", " Who cares.", " ..."]
                advice = advice.rstrip('.') + random.choice(suffixes)

            return prompt + f"\n[Current mindset: {advice}]\n", advice
        return prompt, None

    def _degrade_strategic_info_by_zone(self, prompt: str, zone_effects: ZoneEffects) -> str:
        """Remove strategic advice based on active penalty zones."""
        modified, _ = self._degrade_strategic_info_by_zone_instrumented(prompt, zone_effects)
        return modified

    def _degrade_strategic_info_by_zone_instrumented(
        self, prompt: str, zone_effects: ZoneEffects
    ) -> Tuple[str, bool]:
        """Remove strategic advice with instrumentation tracking."""
        modified = prompt
        penalties = zone_effects.penalties

        phrases_to_remove = []
        for zone_name, intensity in penalties.items():
            if intensity >= 0.25:
                zone_phrases = PHRASES_TO_REMOVE_BY_ZONE.get(zone_name, [])
                phrases_to_remove.extend(zone_phrases)

        was_degraded = False

        for phrase in phrases_to_remove:
            if phrase in modified or phrase.lower() in modified:
                was_degraded = True
            modified = modified.replace(phrase, "")
            modified = modified.replace(phrase.lower(), "")

        total_penalty = sum(penalties.values())
        if total_penalty >= 0.60:
            pot_odds_text = "Consider the pot odds, the amount of money in the pot, and how much you would have to risk."
            if pot_odds_text in modified:
                was_degraded = True
            modified = modified.replace(pot_odds_text, "Don't overthink this.")

        modified = re.sub(r'\s+', ' ', modified)
        modified = re.sub(r'\s+([,.])', r'\1', modified)

        return modified, was_degraded

    # Legacy methods for backward compatibility
    def _inject_intrusive_thoughts(self, prompt: str, composure: float) -> str:
        """Legacy method: Use _inject_zone_thoughts() instead."""
        return self._inject_zone_thoughts(prompt, self.zone_effects)

    def _add_composure_strategy(self, prompt: str, composure: float) -> str:
        """Legacy method: Use _add_penalty_strategy() instead."""
        return self._add_penalty_strategy(prompt, self.zone_effects)

    def _degrade_strategic_info(self, prompt: str) -> str:
        """Legacy method: Use _degrade_strategic_info_by_zone() instead."""
        return self._degrade_strategic_info_by_zone(prompt, self.zone_effects)

    def _add_angry_modifier(self, prompt: str) -> str:
        """Add angry flair for low composure + high aggression."""
        angry_injection = (
            "\n[You're feeling aggressive and fed up. Channel that anger - "
            "but don't let it make you stupid.]\n"
        )
        return prompt + angry_injection

    # === AVATAR DISPLAY ===

    def _get_true_emotion(self) -> str:
        """Get the player's true emotional state (before expression filtering)."""
        quadrant = self.quadrant
        energy = self.axes.energy
        aggression = self.effective_aggression

        if quadrant == EmotionalQuadrant.OVERHEATED and aggression > 0.6 and energy > 0.5:
            return "angry"

        emotion_map = {
            EmotionalQuadrant.COMMANDING: 'smug' if energy > 0.6 else 'confident',
            EmotionalQuadrant.OVERHEATED: 'frustrated' if energy < 0.6 else 'angry',
            EmotionalQuadrant.GUARDED: 'thinking' if energy < 0.5 else 'nervous',
            EmotionalQuadrant.SHAKEN: 'nervous' if energy < 0.6 else 'shocked',
        }

        return emotion_map.get(quadrant, "poker_face")

    def get_display_emotion(self, use_expression_filter: bool = True) -> str:
        """Get emotion for avatar display, with optional expression filtering."""
        if use_expression_filter and self.is_in_poker_face_zone():
            return "poker_face"

        true_emotion = self._get_true_emotion()

        if not use_expression_filter:
            return true_emotion

        from .expression_filter import calculate_visibility, dampen_emotion

        visibility = calculate_visibility(
            self.anchors.expressiveness,
            self.axes.energy,
        )

        return dampen_emotion(true_emotion, visibility)

    # === SERIALIZATION ===

    def to_dict(self) -> Dict[str, Any]:
        """Serialize full psychological state to dictionary."""
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
            'consecutive_folds': self.consecutive_folds,
            'poker_face_zone': self._poker_face_zone.to_dict() if self._poker_face_zone else None,
            'in_poker_face_zone': self.is_in_poker_face_zone(),
            'zone_distance': self.zone_distance,
            'zone_effects': self.zone_effects.to_dict(),
            'primary_zone': self.primary_zone,
            'playstyle_state': self._playstyle_state.to_dict() if self._playstyle_state else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], personality_config: Dict[str, Any]) -> 'PlayerPsychology':
        """
        Deserialize from saved state.

        Handles migration from old formats:
        - Old 'elastic' format -> convert to anchors/axes
        - Old 'tilt' format -> convert to composure_state
        """
        player_name = data['player_name']

        if data.get('anchors'):
            anchors = PersonalityAnchors.from_dict(data['anchors'])
        elif 'anchors' in personality_config:
            anchors = PersonalityAnchors.from_dict(personality_config['anchors'])
        elif 'personality_traits' in personality_config:
            logger.warning(f"Legacy traits format for {player_name} - using default anchors")
            anchors = PersonalityAnchors()
        else:
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

        if data.get('axes'):
            axes = EmotionalAxes.from_dict(data['axes'])
        elif data.get('elastic'):
            elastic_data = data['elastic']
            traits = elastic_data.get('traits', {})
            axes = EmotionalAxes(
                confidence=traits.get('confidence', {}).get('value', 0.5),
                composure=traits.get('composure', {}).get('value', 0.7),
                energy=traits.get('table_talk', {}).get('value', anchors.baseline_energy),
            )
        else:
            baseline_conf = compute_baseline_confidence(anchors)
            baseline_comp = compute_baseline_composure(anchors)
            axes = EmotionalAxes(
                confidence=baseline_conf,
                composure=baseline_comp,
                energy=anchors.baseline_energy,
            )

        psychology = cls(
            player_name=player_name,
            personality_config=personality_config,
            anchors=anchors,
            axes=axes,
            game_id=data.get('game_id'),
            owner_id=data.get('owner_id'),
        )

        if data.get('emotional'):
            psychology.emotional = EmotionalState.from_dict(data['emotional'])

        if data.get('composure_state'):
            psychology.composure_state = ComposureState.from_dict(data['composure_state'])
        elif data.get('tilt'):
            psychology.composure_state = ComposureState.from_tilt_state(data['tilt'])
            tilt_level = data['tilt'].get('tilt_level', 0.0)
            psychology.axes = psychology.axes.update(composure=1.0 - tilt_level)

        psychology.hand_count = data.get('hand_count', 0)
        psychology.last_updated = data.get('last_updated')
        psychology.consecutive_folds = data.get('consecutive_folds', 0)

        # Restore playstyle state if saved; otherwise __post_init__ already derived it
        if data.get('playstyle_state'):
            psychology._playstyle_state = PlaystyleState.from_dict(data['playstyle_state'])
            psychology._identity_biases = compute_identity_bias(
                psychology._playstyle_state.primary_playstyle
            )

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

        try:
            self.emotional = self._emotional_generator.generate(
                personality_name=self.player_name,
                personality_config=self.personality_config,
                hand_outcome=hand_outcome,
                session_context=session_context,
                hand_number=self.hand_count,
                game_id=self.game_id,
                owner_id=self.owner_id,
                big_blind=big_blind,
                confidence=self.confidence,
                composure=self.composure,
                energy=self.energy,
                baseline_anchors=self.anchors.to_dict(),
                composure_state=self.composure_state,
            )
        except Exception as e:
            logger.warning(
                f"{self.player_name}: Failed to generate emotional state: {e}. "
                f"Using fallback narrative."
            )
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
