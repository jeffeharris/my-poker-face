"""
Emotional State System for AI Poker Players (v2.1).

Emotional state is driven entirely by the quadrant model (Commanding,
Overheated, Guarded, Shaken) projected from the psychology axes. This module
is responsible for ONE thing: turning that quadrant state into
personality-authentic LLM narration (``narrative`` + ``inner_voice``).

The deprecated 4D dimensional model (valence/arousal/control/focus) was
removed in schema v136. Emotion *labels* now come from the family/quadrant
matrix in ``PlayerPsychology.get_display_emotion``; this object carries only
the narration text plus metadata.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.llm_categorizer import CategorizationSchema, StructuredLLMCategorizer

from .psychology_model import EmotionalQuadrant, get_quadrant

logger = logging.getLogger(__name__)


# LLM narration schema — the LLM produces text only; the emotional state
# itself is the quadrant (computed in PlayerPsychology).
EMOTIONAL_NARRATION_SCHEMA = CategorizationSchema(
    fields={
        'narrative': {
            'type': 'string',
            'default': '',
            'description': (
                'One present-tense sentence, third person: a concrete physical tell an '
                'opponent would catch (no feeling-words, no similes)'
            ),
        },
        'inner_voice': {
            'type': 'string',
            'default': '',
            'description': "One sharp first-person thought, in the character's own voice",
        },
    },
    example_output={
        'narrative': "Gordon's jaw sets hard and his stare locks on the dealer as Phil rakes the pot.",
        'inner_voice': 'Called with nothing and hit his miracle card. Unbelievable.',
    },
)


# --- Narration tone descriptors (quadrant-derived) ---
# Plain-language cues handed to the narrator LLM in place of the old 4D scalars.

_QUADRANT_MOOD = {
    EmotionalQuadrant.COMMANDING: 'in command — positive and firmly in control',
    EmotionalQuadrant.OVERHEATED: 'riled up — confident but losing the reins',
    EmotionalQuadrant.GUARDED: 'wary — holding back, picking spots',
    EmotionalQuadrant.SHAKEN: 'rattled — shaken and scrambling',
}


def _composure_descriptor(composure: float) -> str:
    if composure >= 0.8:
        return 'focused'
    if composure >= 0.6:
        return 'alert'
    if composure >= 0.4:
        return 'rattled'
    return 'tilted'


def _confidence_descriptor(confidence: float) -> str:
    if confidence >= 0.7:
        return 'riding high'
    if confidence >= 0.5:
        return 'steady'
    if confidence >= 0.3:
        return 'shaky'
    return 'crushed'


def _energy_descriptor(energy: float) -> str:
    if energy >= 0.66:
        return 'high'
    if energy >= 0.33:
        return 'moderate'
    return 'low'


@dataclass
class EmotionalState:
    """
    A player's narrated emotional state at a point in time.

    v2.1: the emotion *itself* is the quadrant (from PlayerPsychology). This
    object carries only the LLM-generated narration text plus metadata. The
    legacy 4D scalars (valence/arousal/control/focus) were removed in v136.
    """

    # Narrative elements (LLM-generated)
    narrative: str = ""  # Third person description
    inner_voice: str = ""  # First person thought

    # Metadata
    generated_at_hand: int = 0
    source_events: List[str] = field(default_factory=list)
    created_at: Optional[str] = None  # ISO format timestamp
    used_fallback: bool = False

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for persistence."""
        return {
            'narrative': self.narrative,
            'inner_voice': self.inner_voice,
            'generated_at_hand': self.generated_at_hand,
            'source_events': self.source_events,
            'created_at': self.created_at,
            'used_fallback': self.used_fallback,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EmotionalState':
        """Deserialize from dictionary.

        Tolerant of legacy rows that still carry the removed 4D scalar keys —
        they are simply ignored.
        """
        return cls(
            narrative=data.get('narrative', ''),
            inner_voice=data.get('inner_voice', ''),
            generated_at_hand=data.get('generated_at_hand', 0),
            source_events=data.get('source_events', []),
            created_at=data.get('created_at'),
            used_fallback=data.get('used_fallback', False),
        )

    @classmethod
    def neutral(cls) -> 'EmotionalState':
        """Return a neutral emotional state for game start."""
        return cls(
            narrative="Ready to play.",
            inner_voice="Let's see what we've got.",
        )


class EmotionalStateGenerator:
    """
    Generates narrated emotional state for AI players after each hand.

    The emotional *state* is the quadrant (confidence x composure) computed by
    PlayerPsychology. This generator's only job is a cheap LLM call that turns
    that state + the hand outcome into personality-authentic ``narrative`` and
    ``inner_voice`` text. If the LLM call fails, the narrative falls back to
    generic text.
    """

    SYSTEM_PROMPT = """Two lines that capture where this poker character's head is the instant a hand ends. Stay hard in their voice.

The emotional state below is already set — show it, don't name it.

- narrative: one vivid present-tense sentence (third person) — a concrete tell an opponent would catch, not a feeling-word. State the literal tell; no similes or "like a…" comparisons. Vary the tell hand to hand — reach past hands and fingers (eyes, jaw, breath, posture, voice, stillness, how they handle their chips or cards).
- inner_voice: one first-person thought, the way THEY'd actually say it — sharp, unfinished, alive.

Write fresh phrasing every time. The listed voice reference is for TONE only — never repeat it word-for-word. No clichés, no preamble."""

    def __init__(self, timeout_seconds: float = 3.0):
        """Initialize the generator with a categorizer for narration."""
        self.categorizer = StructuredLLMCategorizer(
            schema=EMOTIONAL_NARRATION_SCHEMA,
            timeout_seconds=timeout_seconds,
            fallback_generator=self._generate_narration_fallback,
        )

    def generate(
        self,
        personality_name: str,
        personality_config: Dict[str, Any],
        hand_outcome: Dict[str, Any],
        session_context: Optional[Dict[str, Any]] = None,
        hand_number: int = 0,
        # Tracking context for cost analysis
        game_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        big_blind: int = 100,
        # Quadrant-model state (v2.1)
        confidence: Optional[float] = None,
        composure: Optional[float] = None,
        energy: Optional[float] = None,
        baseline_anchors: Optional[Dict[str, float]] = None,
        composure_state: Optional[Any] = None,
    ) -> EmotionalState:
        """
        Generate narrated emotional state after a hand completes.

        The emotional state is the quadrant derived from confidence/composure;
        the LLM is called only to produce narrative + inner_voice text.

        Args:
            personality_name: Name of the AI player
            personality_config: Personality configuration dict
            hand_outcome: Dict with outcome, amount, key_moment, opponent, etc.
            session_context: Session memory context
            hand_number: Current hand number
            game_id: Game ID for usage tracking
            owner_id: User ID for usage tracking
            big_blind: Big blind size (kept for signature stability)
            confidence/composure/energy: Current psychology axis values
            baseline_anchors: Anchor dict (kept for signature stability)
            composure_state: ComposureState object (pressure_source, nemesis)

        Returns:
            EmotionalState with LLM narrative (no dimensional scalars)
        """
        if session_context is None:
            session_context = {}

        confidence = 0.5 if confidence is None else confidence
        composure = 0.7 if composure is None else composure
        energy = 0.5 if energy is None else energy

        tilt_source = ''
        nemesis = None
        if composure_state is not None:
            tilt_source = getattr(composure_state, 'pressure_source', '')
            nemesis = getattr(composure_state, 'nemesis', None)

        # --- Narration: LLM produces text for the quadrant state ---
        context = self._build_narration_context(
            personality_name=personality_name,
            personality_config=personality_config,
            hand_outcome=hand_outcome,
            confidence=confidence,
            composure=composure,
            energy=energy,
            tilt_source=tilt_source,
            nemesis=nemesis,
            session_context=session_context,
        )

        additional = {
            'personality': personality_name,
            'personality_description': personality_config.get('play_style', ''),
            'tilt_level': round(max(0.0, 1.0 - composure), 3),
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
        confidence: float,
        composure: float,
        energy: float,
        tilt_source: str,
        nemesis: Optional[str],
        session_context: Dict[str, Any],
    ) -> str:
        """Build the context string for the LLM narrator.

        Describes the quadrant state in plain language (feeling, confidence,
        composure, energy) so the LLM has semantic cues, not raw numbers.
        """
        lines = [f"PLAYER: {personality_name}"]

        # Personality description
        play_style = personality_config.get('play_style', '')
        if play_style:
            lines.append(f"PERSONALITY: {play_style}")

        # Verbal tics for voice reference — labeled "do not quote" because the
        # model otherwise lifts these lines verbatim as the inner_voice.
        verbal_tics = personality_config.get('verbal_tics', [])
        if verbal_tics and isinstance(verbal_tics, list):
            lines.append(f"VOICE REFERENCE (do not quote): {', '.join(verbal_tics[:3])}")

        # Quadrant emotional state with descriptors
        quadrant = get_quadrant(confidence, composure)
        lines.append("")
        lines.append("CURRENT EMOTIONAL STATE (already determined):")
        lines.append(f"  - Feeling: {_QUADRANT_MOOD.get(quadrant, 'processing the hand')}")
        lines.append(f"  - Confidence: {_confidence_descriptor(confidence)} ({confidence:.0%})")
        lines.append(f"  - Composure: {_composure_descriptor(composure)} ({composure:.0%})")
        lines.append(f"  - Energy: {_energy_descriptor(energy)} ({energy:.0%})")

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

        # Psychological state (only when composure has slipped)
        if composure < 0.6:
            lines.append("")
            lines.append("PSYCHOLOGICAL STATE:")
            lines.append(f"  - Composure slipping ({composure:.0%})")
            if tilt_source:
                lines.append(f"  - Source: {tilt_source}")
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
        lines.append("Write the narrative and inner_voice for this character, right now.")

        return "\n".join(lines)

    def _generate_narration_fallback(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Generate fallback narrative when LLM fails."""
        return {'narrative': 'Processing the last hand.', 'inner_voice': 'Focus on the next one.'}
