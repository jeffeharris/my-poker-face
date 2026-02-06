"""
Emotional State System for AI Poker Players (v2.1).

In v2.1, emotional state is simplified:
- The 4D model (valence, arousal, control, focus) is DEPRECATED
- Emotion labels come from the quadrant model (Commanding, Overheated, Guarded, Shaken)
- This module now primarily handles LLM narration (narrative + inner_voice)

The LLM's role is narration only: given the quadrant and axes, it produces
personality-authentic narrative text and inner_voice.

Legacy 4D model is kept for backward compatibility but will be removed in future.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.llm_categorizer import (
    CategorizationSchema,
    StructuredLLMCategorizer,
    CategorizationResult
)

logger = logging.getLogger(__name__)


class _SimpleAttr:
    """Lightweight attribute bag used to bridge old duck-typed interfaces."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


# Layer 1: Baseline mood from elastic traits (deterministic)
#
# Updated for 5-trait poker-native model:
# - Valence: confidence × composure (overall positivity)
# - Arousal: aggression + inverse composure (energy/agitation)
# - Control: directly from composure
# - Focus: composure with table_talk modifier

def compute_baseline_mood(elastic_traits: Dict[str, Any]) -> Dict[str, float]:
    """
    Compute baseline emotional dimensions from current elastic trait values.

    For 5-trait poker-native model:
    - confidence × composure → valence (mood)
    - aggression + (1 - composure) → arousal
    - composure → control
    - composure - table_talk → focus

    Also supports old 4-trait model for backward compatibility.

    Args:
        elastic_traits: Dict of trait name -> ElasticTrait (or dict with
                       'value', 'anchor', 'pressure' keys)

    Returns:
        Dict with 'valence', 'arousal', 'control', 'focus' baseline values
    """
    def _trait_val(name: str, default: float = 0.5) -> float:
        t = elastic_traits.get(name)
        if t is None:
            return default
        return t.value if hasattr(t, 'value') else t.get('value', default)

    def _trait_anchor(name: str, default: float = 0.5) -> float:
        t = elastic_traits.get(name)
        if t is None:
            return default
        return t.anchor if hasattr(t, 'anchor') else t.get('anchor', default)

    def _trait_drift(name: str) -> float:
        return _trait_val(name) - _trait_anchor(name)

    # Detect trait format (new 5-trait vs old 4-trait)
    is_new_format = 'composure' in elastic_traits or 'confidence' in elastic_traits

    if is_new_format:
        # New 5-trait poker-native model
        confidence = _trait_val('confidence', 0.5)
        composure = _trait_val('composure', 0.7)
        aggression = _trait_val('aggression', 0.5)
        table_talk = _trait_val('table_talk', 0.5)

        # Valence: confidence × composure drives overall mood
        # High confidence + high composure = positive
        # Low of either = negative
        valence = _clamp((confidence * 0.6 + composure * 0.4) * 2 - 1, -1.0, 1.0)

        # Arousal: aggression + inverse composure (tilted = more aroused)
        arousal = _clamp(aggression * 0.4 + (1.0 - composure) * 0.4 + 0.2, 0.0, 1.0)

        # Control: directly from composure
        control = _clamp(composure, 0.0, 1.0)

        # Focus: composure dominates, table_talk slightly reduces
        focus = _clamp(composure * 0.85 - table_talk * 0.1, 0.0, 1.0)
    else:
        # Old 4-trait model (backward compatibility)
        aggression = _trait_val('aggression')
        chattiness = _trait_val('chattiness', 0.5)
        emoji = _trait_val('emoji_usage', 0.3)

        # Average drift from anchor across all traits
        avg_drift = sum(_trait_drift(t) for t in elastic_traits) / max(len(elastic_traits), 1)
        avg_abs_drift = sum(abs(_trait_drift(t)) for t in elastic_traits) / max(len(elastic_traits), 1)

        valence = _clamp(avg_drift * 3.0, -1.0, 1.0)
        arousal = _clamp(0.35 + aggression * 0.25 + avg_abs_drift * 2.0, 0.0, 1.0)
        control = _clamp(0.7 - avg_abs_drift * 3.0, 0.0, 1.0)
        focus = _clamp(0.7 - chattiness * 0.15 - emoji * 0.1 - avg_abs_drift * 1.5, 0.0, 1.0)

    return {
        'valence': round(valence, 3),
        'arousal': round(arousal, 3),
        'control': round(control, 3),
        'focus': round(focus, 3),
    }


# Layer 2: Reactive spike from hand outcome (deterministic)

def compute_reactive_spike(
    outcome: str,
    amount: int,
    tilt_level: float = 0.0,
    big_blind: int = 100,
) -> Dict[str, float]:
    """
    Compute an emotional spike from a single hand outcome.

    This is the fast-moving reaction — a big win or bad beat creates an
    immediate emotional shift that decays back toward baseline.

    Args:
        outcome: 'won', 'lost', or 'folded'
        amount: Net chip change (positive for wins, negative for losses)
        tilt_level: Current tilt (0-1), amplifies the spike
        big_blind: Big blind size for normalizing amount significance

    Returns:
        Dict with delta values for 'valence', 'arousal', 'control', 'focus'
    """
    # Normalize amount significance: how many big blinds was this?
    bb_magnitude = min(abs(amount) / max(big_blind, 1), 10.0) / 10.0  # 0-1 scale

    if outcome == 'won':
        valence = 0.3 + bb_magnitude * 0.4    # +0.3 to +0.7
        arousal = 0.1 + bb_magnitude * 0.3    # mild excitement
        control = 0.1 + bb_magnitude * 0.15   # winning feels in-control
        focus = 0.05                           # slight clarity boost
    elif outcome == 'lost':
        valence = -0.3 - bb_magnitude * 0.4   # -0.3 to -0.7
        arousal = 0.15 + bb_magnitude * 0.35  # frustration/agitation
        control = -0.15 - bb_magnitude * 0.2  # losing control
        focus = -0.1 - bb_magnitude * 0.15    # harder to think clearly
    else:  # folded
        valence = -0.05 - bb_magnitude * 0.1  # mild negative
        arousal = 0.05                         # barely registers
        control = 0.0                          # neutral
        focus = 0.0                            # neutral

    # Tilt amplifies all spikes — tilted players react more intensely
    amplifier = 1.0 + tilt_level * 0.8
    valence *= amplifier
    arousal *= amplifier
    control *= amplifier
    focus *= amplifier

    return {
        'valence': round(valence, 3),
        'arousal': round(arousal, 3),
        'control': round(control, 3),
        'focus': round(focus, 3),
    }


# Blending baseline + spike

def blend_emotional_state(
    baseline: Dict[str, float],
    spike: Dict[str, float],
) -> Dict[str, float]:
    """
    Combine baseline mood and reactive spike into final emotional dimensions.

    Simply adds the spike to the baseline and clamps to valid ranges.

    Args:
        baseline: Baseline mood from elastic traits
        spike: Reactive spike from hand outcome

    Returns:
        Dict with clamped 'valence', 'arousal', 'control', 'focus' values
    """
    return {
        'valence': _clamp(baseline['valence'] + spike['valence'], -1.0, 1.0),
        'arousal': _clamp(baseline['arousal'] + spike['arousal'], 0.0, 1.0),
        'control': _clamp(baseline['control'] + spike['control'], 0.0, 1.0),
        'focus': _clamp(baseline['focus'] + spike['focus'], 0.0, 1.0),
    }


def _clamp(value: float, min_val: float, max_val: float) -> float:
    return max(min_val, min(max_val, value))


# LLM narration schema — LLM produces text only, dimensions are computed above
EMOTIONAL_NARRATION_SCHEMA = CategorizationSchema(
    fields={
        'narrative': {
            'type': 'string',
            'default': '',
            'description': '1-2 sentences describing how the character is feeling, in third person'
        },
        'inner_voice': {
            'type': 'string',
            'default': '',
            'description': 'A short thought echoing in their head, in first person, in their voice'
        }
    },
    example_output={
        'narrative': 'Gordon is seething after Phil\'s lucky river card. His jaw is tight and his patience is wearing thin.',
        'inner_voice': 'That idiot called with nothing and got rewarded. Unbelievable.'
    }
)


@dataclass
class EmotionalState:
    """
    Represents a player's emotional state at a point in time.

    v2.1 NOTE: The 4D dimensional model (valence, arousal, control, focus)
    is DEPRECATED. New code should use quadrant-based emotion from
    PlayerPsychology.quadrant. These fields are kept for backward
    compatibility with existing saved games and display code.

    The primary use of EmotionalState in v2.1 is for LLM narration
    (narrative + inner_voice fields).
    """

    # DEPRECATED: Dimensional scores (kept for backward compat)
    # In v2.1, emotion is determined by quadrant from PlayerPsychology
    valence: float = 0.0      # -1 (miserable) to 1 (elated)
    arousal: float = 0.5      # 0 (calm) to 1 (agitated)
    control: float = 0.5      # 0 (losing grip) to 1 (in command)
    focus: float = 0.5        # 0 (tunnel vision) to 1 (clear-headed)

    # Narrative elements (LLM-generated) - KEPT FOR v2.1
    narrative: str = ""       # Third person description
    inner_voice: str = ""     # First person thought

    # Metadata
    generated_at_hand: int = 0
    source_events: List[str] = field(default_factory=list)
    created_at: Optional[str] = None  # ISO format timestamp
    used_fallback: bool = False

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow().isoformat()

    # Descriptor properties for prompt generation
    @property
    def valence_descriptor(self) -> str:
        if self.valence > 0.5:
            return "positive"
        if self.valence > 0.2:
            return "slightly positive"
        if self.valence > -0.2:
            return "neutral"
        if self.valence > -0.5:
            return "slightly negative"
        return "negative"

    @property
    def arousal_descriptor(self) -> str:
        if self.arousal > 0.7:
            return "highly agitated"
        if self.arousal > 0.5:
            return "restless"
        if self.arousal > 0.3:
            return "alert"
        return "calm"

    @property
    def control_descriptor(self) -> str:
        if self.control > 0.7:
            return "in command"
        if self.control > 0.5:
            return "steady"
        if self.control > 0.3:
            return "wavering"
        return "slipping"

    @property
    def focus_descriptor(self) -> str:
        if self.focus > 0.7:
            return "clear-headed"
        if self.focus > 0.5:
            return "focused"
        if self.focus > 0.3:
            return "distracted"
        return "tunnel vision"

    def get_display_emotion(self) -> str:
        """
        Map dimensional emotional state to discrete display emotion for avatar.

        Returns one of: angry, elated, shocked, smug, frustrated, nervous,
                        confident, happy, thinking, poker_face
        Priority order: most extreme/specific emotions checked first.
        """
        # Angry: red-hot fury, very negative with high agitation
        if self.valence < -0.4 and self.arousal > 0.7:
            return "angry"

        # Elated: big win excitement, high positive energy
        if self.valence > 0.6 and self.arousal > 0.6:
            return "elated"

        # Shocked: extreme surprise/overwhelm (arousal must be very high)
        if self.arousal > 0.85:
            return "shocked"

        # Smug: winning streak swagger, positive and firmly in control
        if self.valence > 0.5 and self.control > 0.7:
            return "smug"

        # Frustrated: simmering negative, not quite angry
        if self.valence < -0.2 and self.arousal > 0.5 and self.arousal <= 0.7:
            return "frustrated"

        # Nervous: negative mood, losing grip
        if self.valence < 0 and self.control < 0.5:
            return "nervous"

        # Confident: positive mood with steady control (before happy so
        # controlled-positive states read as confidence, not just happiness)
        if self.valence > 0.2 and self.control > 0.5:
            return "confident"

        # Happy: warm positive feeling without strong control
        if self.valence > 0.3:
            return "happy"

        # Thinking: contemplative, clear-headed focus
        if self.focus > 0.6 and self.arousal < 0.5:
            return "thinking"

        # Default: neutral poker face mask
        return "poker_face"

    def to_prompt_section(self) -> str:
        """Generate the prompt section for this emotional state."""
        lines = ["[YOUR EMOTIONAL STATE]"]

        if self.narrative:
            lines.append(self.narrative)
            lines.append("")

        lines.append("How you're feeling right now:")
        lines.append(f"  - Mood: {self.valence_descriptor} ({self.valence:+.1f})")
        lines.append(f"  - Energy: {self.arousal_descriptor} ({self.arousal:.0%})")
        lines.append(f"  - Sense of control: {self.control_descriptor} ({self.control:.0%})")
        lines.append(f"  - Mental clarity: {self.focus_descriptor} ({self.focus:.0%})")

        if self.inner_voice:
            lines.append("")
            lines.append(f"What's echoing in your head: \"{self.inner_voice}\"")

        lines.append("")
        lines.append("Let this influence your thinking and behavior - but you decide how much.")

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for persistence."""
        return {
            'valence': self.valence,
            'arousal': self.arousal,
            'control': self.control,
            'focus': self.focus,
            'narrative': self.narrative,
            'inner_voice': self.inner_voice,
            'generated_at_hand': self.generated_at_hand,
            'source_events': self.source_events,
            'created_at': self.created_at,
            'used_fallback': self.used_fallback
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EmotionalState':
        """Deserialize from dictionary."""
        return cls(
            valence=data.get('valence', 0.0),
            arousal=data.get('arousal', 0.5),
            control=data.get('control', 0.5),
            focus=data.get('focus', 0.5),
            narrative=data.get('narrative', ''),
            inner_voice=data.get('inner_voice', ''),
            generated_at_hand=data.get('generated_at_hand', 0),
            source_events=data.get('source_events', []),
            created_at=data.get('created_at'),
            used_fallback=data.get('used_fallback', False)
        )

    @classmethod
    def neutral(cls) -> 'EmotionalState':
        """Return a neutral emotional state for game start."""
        return cls(
            valence=0.0,
            arousal=0.3,
            control=0.7,
            focus=0.7,
            narrative="Ready to play.",
            inner_voice="Let's see what we've got."
        )

    def decay_toward_baseline(
        self,
        baseline: Dict[str, float],
        rate: float = 0.1,
    ) -> 'EmotionalState':
        """
        Return a new state decayed toward the elastic-trait baseline.

        The baseline is the slow-moving mood derived from personality traits.
        Between hands, the reactive spike fades and the emotional state
        drifts back toward who this player fundamentally is right now.

        Args:
            baseline: Dict with 'valence', 'arousal', 'control', 'focus'
                     from compute_baseline_mood()
            rate: Decay rate (0-1). Higher = faster return to baseline.

        Returns:
            New EmotionalState decayed toward baseline
        """
        def decay(current: float, target: float, r: float) -> float:
            return current + (target - current) * r

        return EmotionalState(
            valence=decay(self.valence, baseline.get('valence', 0.0), rate),
            arousal=decay(self.arousal, baseline.get('arousal', 0.4), rate),
            control=decay(self.control, baseline.get('control', 0.6), rate),
            focus=decay(self.focus, baseline.get('focus', 0.6), rate),
            narrative=self.narrative,  # Keep narrative until regenerated
            inner_voice=self.inner_voice,
            generated_at_hand=self.generated_at_hand,
            source_events=self.source_events,
            created_at=self.created_at,
            used_fallback=self.used_fallback
        )


class EmotionalStateGenerator:
    """
    Generates emotional state for AI players after each hand.

    Two-layer architecture:
    1. Dimensions (valence, arousal, control, focus) are computed
       deterministically from elastic traits (baseline) + hand outcome (spike).
    2. Narrative text (narrative, inner_voice) is generated by a cheap LLM call
       that receives the computed dimensions as context — narration only.

    If the LLM call fails, dimensions are still valid; only narrative falls
    back to generic text.
    """

    SYSTEM_PROMPT = """You are narrating the emotional state of a poker player character.

The emotional dimensions have already been determined. Your job is to describe
how this character FEELS and THINKS right now, authentically in their voice.

Write:
- narrative: 1-2 sentences in THIRD PERSON describing how they're feeling
- inner_voice: A SHORT thought in FIRST PERSON in their speaking style

Be authentic to the character's personality and verbal tics. The emotional
dimensions tell you the intensity — your job is to give it personality."""

    def __init__(self, timeout_seconds: float = 3.0):
        """Initialize the generator with a categorizer for narration."""
        self.categorizer = StructuredLLMCategorizer(
            schema=EMOTIONAL_NARRATION_SCHEMA,
            timeout_seconds=timeout_seconds,
            fallback_generator=self._generate_narration_fallback
        )

    def generate(
        self,
        personality_name: str,
        personality_config: Dict[str, Any],
        hand_outcome: Dict[str, Any],
        elastic_traits: Optional[Dict[str, Any]] = None,
        tilt_state: Any = None,
        session_context: Optional[Dict[str, Any]] = None,
        hand_number: int = 0,
        # Tracking context for cost analysis
        game_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        big_blind: int = 100,
        # New-model params (v2.1): when provided, elastic_traits/tilt_state are ignored
        confidence: Optional[float] = None,
        composure: Optional[float] = None,
        energy: Optional[float] = None,
        baseline_anchors: Optional[Dict[str, float]] = None,
        composure_state: Optional[Any] = None,
    ) -> EmotionalState:
        """
        Generate emotional state after a hand completes.

        Dimensions are computed deterministically from traits + outcome.
        The LLM is called only to produce narrative text.

        Supports two calling conventions:
        1. New model (v2.1): pass confidence, composure, energy, baseline_anchors,
           composure_state directly.
        2. Legacy: pass elastic_traits dict and tilt_state object.

        Args:
            personality_name: Name of the AI player
            personality_config: Personality configuration dict
            hand_outcome: Dict with outcome, amount, key_moment, etc.
            elastic_traits: (Legacy) Current elastic trait values
            tilt_state: (Legacy) Current TiltState object
            session_context: Session memory context
            hand_number: Current hand number
            game_id: Game ID for usage tracking
            owner_id: User ID for usage tracking
            big_blind: Big blind size for spike amount normalization
            confidence: (v2.1) Current confidence axis value
            composure: (v2.1) Current composure axis value
            energy: (v2.1) Current energy axis value
            baseline_anchors: (v2.1) Dict with baseline_aggression, baseline_looseness, etc.
            composure_state: (v2.1) ComposureState object

        Returns:
            EmotionalState with deterministic dimensions and LLM narrative
        """
        if session_context is None:
            session_context = {}

        # Determine tilt_level and build traits for baseline mood computation
        if confidence is not None:
            # New-model path: build traits from axis values
            tilt_level = 1.0 - composure if composure is not None else 0.0
            aggression_anchor = baseline_anchors.get('baseline_aggression', 0.5) if baseline_anchors else 0.5
            looseness_anchor = baseline_anchors.get('baseline_looseness', 0.3) if baseline_anchors else 0.3
            energy_anchor = baseline_anchors.get('baseline_energy', 0.5) if baseline_anchors else 0.5
            _traits = {
                'confidence': _SimpleAttr(value=confidence, anchor=0.5),
                'composure': _SimpleAttr(value=composure or 0.7, anchor=0.7),
                'aggression': _SimpleAttr(value=confidence * 0.7 + 0.15, anchor=aggression_anchor),
                'tightness': _SimpleAttr(value=1.0 - (looseness_anchor or 0.3), anchor=1.0 - looseness_anchor),
                'table_talk': _SimpleAttr(value=energy or 0.5, anchor=energy_anchor),
            }
            tilt_source = ''
            nemesis = None
            if composure_state is not None:
                tilt_source = getattr(composure_state, 'pressure_source', '')
                nemesis = getattr(composure_state, 'nemesis', None)
        else:
            # Legacy path: use elastic_traits and tilt_state directly
            _traits = elastic_traits or {}
            tilt_level = getattr(tilt_state, 'tilt_level', 0.0)
            tilt_source = getattr(tilt_state, 'tilt_source', '')
            nemesis = getattr(tilt_state, 'nemesis', None)

        outcome = hand_outcome.get('outcome', 'unknown')
        amount = hand_outcome.get('amount', 0)

        baseline = compute_baseline_mood(_traits)
        spike = compute_reactive_spike(
            outcome=outcome,
            amount=amount,
            tilt_level=tilt_level,
            big_blind=big_blind,
        )
        dimensions = blend_emotional_state(baseline, spike)

        # --- Narration: LLM produces text for the computed dimensions ---
        context = self._build_narration_context(
            personality_name=personality_name,
            personality_config=personality_config,
            hand_outcome=hand_outcome,
            dimensions=dimensions,
            tilt_state=_SimpleAttr(tilt_level=tilt_level, tilt_source=tilt_source, nemesis=nemesis),
            session_context=session_context,
        )

        additional = {
            'personality': personality_name,
            'personality_description': personality_config.get('play_style', ''),
            'tilt_level': tilt_level,
            'tilt_source': tilt_source,
        }

        result = self.categorizer.categorize(
            context=context,
            system_prompt=self.SYSTEM_PROMPT,
            additional_context=additional,
            game_id=game_id,
            owner_id=owner_id,
            player_name=personality_name,
            hand_number=hand_number,
            prompt_template='emotional_state',
        )

        # Build source events list
        source_events = []
        if hand_outcome.get('outcome'):
            source_events.append(hand_outcome['outcome'])
        if hand_outcome.get('key_moment'):
            source_events.append(hand_outcome['key_moment'])

        # Narrative from LLM (or fallback)
        narrative = ''
        inner_voice = ''
        used_fallback = True
        if result.success and result.data:
            narrative = result.data.get('narrative', '')
            inner_voice = result.data.get('inner_voice', '')
            used_fallback = result.used_fallback

        return EmotionalState(
            valence=dimensions['valence'],
            arousal=dimensions['arousal'],
            control=dimensions['control'],
            focus=dimensions['focus'],
            narrative=narrative,
            inner_voice=inner_voice,
            generated_at_hand=hand_number,
            source_events=source_events,
            used_fallback=used_fallback,
        )

    def _build_narration_context(
        self,
        personality_name: str,
        personality_config: Dict[str, Any],
        hand_outcome: Dict[str, Any],
        dimensions: Dict[str, float],
        tilt_state: Any,
        session_context: Dict[str, Any],
    ) -> str:
        """Build the context string for the LLM narrator.

        Includes the computed dimensions with descriptors so the LLM has
        rich semantic cues, not just raw numbers.
        """
        lines = [f"PLAYER: {personality_name}"]

        # Personality description
        play_style = personality_config.get('play_style', '')
        if play_style:
            lines.append(f"PERSONALITY: {play_style}")

        # Verbal tics for voice reference
        verbal_tics = personality_config.get('verbal_tics', [])
        if verbal_tics and isinstance(verbal_tics, list):
            lines.append(f"SPEAKING STYLE EXAMPLES: {', '.join(verbal_tics[:3])}")

        # Computed emotional dimensions with descriptors
        lines.append("")
        lines.append("CURRENT EMOTIONAL STATE (already determined):")
        # Create a temporary EmotionalState to get descriptors
        temp = EmotionalState(
            valence=dimensions['valence'],
            arousal=dimensions['arousal'],
            control=dimensions['control'],
            focus=dimensions['focus'],
        )
        lines.append(f"  - Mood: {temp.valence_descriptor} ({dimensions['valence']:+.2f})")
        lines.append(f"  - Energy: {temp.arousal_descriptor} ({dimensions['arousal']:.0%})")
        lines.append(f"  - Control: {temp.control_descriptor} ({dimensions['control']:.0%})")
        lines.append(f"  - Focus: {temp.focus_descriptor} ({dimensions['focus']:.0%})")

        # Hand outcome
        lines.append("")
        lines.append("WHAT JUST HAPPENED:")
        outcome = hand_outcome.get('outcome', 'unknown')
        amount = hand_outcome.get('amount', 0)
        lines.append(f"  - Outcome: {outcome.upper()}")
        if amount != 0:
            if amount > 0:
                lines.append(f"  - Won: ${amount}")
            else:
                lines.append(f"  - Lost: ${abs(amount)}")

        key_moment = hand_outcome.get('key_moment')
        if key_moment:
            lines.append(f"  - Key moment: {key_moment}")

        opponent = hand_outcome.get('opponent')
        if opponent:
            lines.append(f"  - Against: {opponent}")

        # Tilt state
        tilt_level = getattr(tilt_state, 'tilt_level', 0.0)
        tilt_source = getattr(tilt_state, 'tilt_source', '')
        nemesis = getattr(tilt_state, 'nemesis', None)
        if tilt_level > 0.1:
            lines.append("")
            lines.append("PSYCHOLOGICAL STATE:")
            lines.append(f"  - Tilt level: {tilt_level:.0%}")
            if tilt_source:
                lines.append(f"  - Tilt source: {tilt_source}")
            if nemesis:
                lines.append(f"  - Nemesis: {nemesis}")

        # Session context
        if session_context:
            lines.append("")
            lines.append("SESSION CONTEXT:")
            net_change = session_context.get('net_change', 0)
            if net_change > 0:
                lines.append(f"  - Session: Up ${net_change}")
            elif net_change < 0:
                lines.append(f"  - Session: Down ${abs(net_change)}")

            streak_type = session_context.get('streak_type')
            streak_count = session_context.get('streak_count', 0)
            if streak_type and streak_count > 1:
                lines.append(f"  - Streak: {streak_count}-hand {streak_type} streak")

        lines.append("")
        lines.append("Write a narrative and inner_voice that express this emotional state authentically for this character.")

        return "\n".join(lines)

    def _generate_narration_fallback(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Generate fallback narrative when LLM fails."""
        return {
            'narrative': 'Processing the last hand.',
            'inner_voice': 'Focus on the next one.'
        }


# Convenience function for external use
def generate_emotional_state(
    personality_name: str,
    personality_config: Dict[str, Any],
    hand_outcome: Dict[str, Any],
    elastic_traits: Dict[str, Any],
    tilt_state: Any,
    session_context: Dict[str, Any],
    hand_number: int,
    generator: Optional[EmotionalStateGenerator] = None,
    # Tracking context for cost analysis
    game_id: Optional[str] = None,
    owner_id: Optional[str] = None,
    big_blind: int = 100,
) -> EmotionalState:
    """
    Convenience function to generate emotional state.

    Creates a generator if not provided. Dimensions are computed
    deterministically; the LLM is called only for narrative text.
    """
    if generator is None:
        generator = EmotionalStateGenerator()

    return generator.generate(
        personality_name=personality_name,
        personality_config=personality_config,
        hand_outcome=hand_outcome,
        elastic_traits=elastic_traits,
        tilt_state=tilt_state,
        session_context=session_context,
        hand_number=hand_number,
        game_id=game_id,
        owner_id=owner_id,
        big_blind=big_blind,
    )
