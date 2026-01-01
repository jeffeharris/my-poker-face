"""
Emotional State System for AI Poker Players.

Tracks the emotional state of AI players using a dimensional model:
- Valence: Negative to positive feeling (-1 to 1)
- Arousal: Calm to agitated (0 to 1)
- Control: Losing grip to in command (0 to 1)
- Focus: Tunnel vision to clear-headed (0 to 1)

Plus LLM-generated narrative elements for rich, personality-specific
emotional expression.
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


# Schema for emotional state categorization
EMOTIONAL_STATE_SCHEMA = CategorizationSchema(
    fields={
        'valence': {
            'type': 'float',
            'min': -1.0,
            'max': 1.0,
            'default': 0.0,
            'description': 'Mood from miserable (-1) to elated (1)'
        },
        'arousal': {
            'type': 'float',
            'min': 0.0,
            'max': 1.0,
            'default': 0.5,
            'description': 'Energy level from calm (0) to highly agitated (1)'
        },
        'control': {
            'type': 'float',
            'min': 0.0,
            'max': 1.0,
            'default': 0.5,
            'description': 'Sense of control from losing grip (0) to fully in command (1)'
        },
        'focus': {
            'type': 'float',
            'min': 0.0,
            'max': 1.0,
            'default': 0.5,
            'description': 'Mental clarity from tunnel vision/fixated (0) to clear-headed (1)'
        },
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
        'valence': -0.4,
        'arousal': 0.7,
        'control': 0.3,
        'focus': 0.4,
        'narrative': 'Gordon is seething after Phil\'s lucky river card. His jaw is tight and his patience is wearing thin.',
        'inner_voice': 'That idiot called with nothing and got rewarded. Unbelievable.'
    }
)


@dataclass
class EmotionalState:
    """Represents a player's emotional state at a point in time."""

    # Dimensional scores
    valence: float = 0.0      # -1 (miserable) to 1 (elated)
    arousal: float = 0.5      # 0 (calm) to 1 (agitated)
    control: float = 0.5      # 0 (losing grip) to 1 (in command)
    focus: float = 0.5        # 0 (tunnel vision) to 1 (clear-headed)

    # Narrative elements (LLM-generated)
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

    def decay_toward_neutral(self, rate: float = 0.1) -> 'EmotionalState':
        """
        Return a new state decayed toward neutral values.

        Used between hands to gradually calm down.
        """
        def decay(current: float, target: float, r: float) -> float:
            return current + (target - current) * r

        return EmotionalState(
            valence=decay(self.valence, 0.0, rate),
            arousal=decay(self.arousal, 0.4, rate),
            control=decay(self.control, 0.6, rate),
            focus=decay(self.focus, 0.6, rate),
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

    Uses the StructuredLLMCategorizer to get dimensional scores
    and narrative from a cheap/fast LLM call.
    """

    SYSTEM_PROMPT = """You are analyzing the emotional state of a poker player character.

Based on their personality and what just happened in the game, determine their current emotional state.

Be authentic to the character. Consider:
- Their personality traits and how they typically react
- What just happened (win/loss, bad beat, etc.)
- Their session so far (winning/losing streak)
- Their tilt level and psychological pressure

The narrative should be 1-2 sentences in THIRD PERSON describing how they're feeling.
The inner_voice should be a SHORT thought in FIRST PERSON in their voice/speaking style."""

    def __init__(self, timeout_seconds: float = 3.0):
        """Initialize the generator with a categorizer."""
        self.categorizer = StructuredLLMCategorizer(
            schema=EMOTIONAL_STATE_SCHEMA,
            timeout_seconds=timeout_seconds,
            fallback_generator=self._generate_fallback
        )

    def generate(
        self,
        personality_name: str,
        personality_config: Dict[str, Any],
        hand_outcome: Dict[str, Any],
        elastic_traits: Dict[str, Any],
        tilt_state: Any,  # TiltState
        session_context: Dict[str, Any],
        hand_number: int
    ) -> EmotionalState:
        """
        Generate emotional state after a hand completes.

        Args:
            personality_name: Name of the AI player
            personality_config: Personality configuration dict
            hand_outcome: Dict with outcome, amount, was_bad_beat, etc.
            elastic_traits: Current elastic trait values
            tilt_state: Current TiltState object
            session_context: Session memory context
            hand_number: Current hand number

        Returns:
            EmotionalState with dimensions and narrative
        """
        # Build context for the LLM
        context = self._build_context(
            personality_name,
            personality_config,
            hand_outcome,
            elastic_traits,
            tilt_state,
            session_context
        )

        # Build additional context dict
        additional = {
            'personality': personality_name,
            'personality_description': personality_config.get('play_style', ''),
            'tilt_level': getattr(tilt_state, 'tilt_level', 0.0),
            'tilt_source': getattr(tilt_state, 'tilt_source', ''),
        }

        # Call the categorizer
        result = self.categorizer.categorize(
            context=context,
            system_prompt=self.SYSTEM_PROMPT,
            additional_context=additional
        )

        # Build EmotionalState from result
        if result.success and result.data:
            source_events = []
            if hand_outcome.get('outcome'):
                source_events.append(hand_outcome['outcome'])
            if hand_outcome.get('key_moment'):
                source_events.append(hand_outcome['key_moment'])

            return EmotionalState(
                valence=result.data.get('valence', 0.0),
                arousal=result.data.get('arousal', 0.5),
                control=result.data.get('control', 0.5),
                focus=result.data.get('focus', 0.5),
                narrative=result.data.get('narrative', ''),
                inner_voice=result.data.get('inner_voice', ''),
                generated_at_hand=hand_number,
                source_events=source_events,
                used_fallback=result.used_fallback
            )
        else:
            # Return neutral state on failure
            logger.warning(
                f"[EMOTIONAL_STATE] Failed to generate for {personality_name}: {result.error}"
            )
            return EmotionalState.neutral()

    def _build_context(
        self,
        personality_name: str,
        personality_config: Dict[str, Any],
        hand_outcome: Dict[str, Any],
        elastic_traits: Dict[str, Any],
        tilt_state: Any,
        session_context: Dict[str, Any]
    ) -> str:
        """Build the context string for the LLM."""
        lines = [f"PLAYER: {personality_name}"]

        # Personality description
        play_style = personality_config.get('play_style', '')
        if play_style:
            lines.append(f"PERSONALITY: {play_style}")

        # Verbal tics for voice reference
        verbal_tics = personality_config.get('verbal_tics', [])
        if verbal_tics and isinstance(verbal_tics, list):
            lines.append(f"SPEAKING STYLE EXAMPLES: {', '.join(verbal_tics[:3])}")

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

        # Elastic trait shifts
        if elastic_traits:
            trait_shifts = []
            for trait_name, trait_data in elastic_traits.items():
                if isinstance(trait_data, dict):
                    value = trait_data.get('value', 0.5)
                    anchor = trait_data.get('anchor', 0.5)
                    delta = value - anchor
                    if abs(delta) > 0.05:
                        direction = "+" if delta > 0 else ""
                        trait_shifts.append(f"{trait_name}: {direction}{delta:.0%}")

            if trait_shifts:
                lines.append("")
                lines.append("TRAIT SHIFTS FROM BASELINE:")
                for shift in trait_shifts:
                    lines.append(f"  - {shift}")

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

        return "\n".join(lines)

    def _generate_fallback(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate fallback emotional state when LLM fails.

        Uses simple heuristics based on available context.
        """
        tilt_level = context.get('tilt_level', 0.0)

        # Parse outcome from context string if available
        context_str = context.get('context', '')
        outcome = 'unknown'
        if 'WON' in context_str.upper():
            outcome = 'won'
        elif 'LOST' in context_str.upper():
            outcome = 'lost'
        elif 'FOLDED' in context_str.upper():
            outcome = 'folded'

        # Generate dimensions from heuristics
        if outcome == 'won':
            valence = 0.5
            arousal = 0.5
            control = 0.7
        elif outcome == 'lost':
            valence = -0.3
            arousal = 0.6
            control = 0.4
        else:  # folded or unknown
            valence = -0.1
            arousal = 0.4
            control = 0.5

        # Tilt affects arousal and control
        arousal = min(1.0, arousal + tilt_level * 0.3)
        control = max(0.0, control - tilt_level * 0.4)
        focus = max(0.0, 0.7 - tilt_level * 0.5)

        return {
            'valence': valence,
            'arousal': arousal,
            'control': control,
            'focus': focus,
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
    generator: Optional[EmotionalStateGenerator] = None
) -> EmotionalState:
    """
    Convenience function to generate emotional state.

    Creates a generator if not provided.
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
        hand_number=hand_number
    )
