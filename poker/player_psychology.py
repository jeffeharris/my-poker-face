"""
Unified Player Psychology System.

Consolidates all psychological state for an AI poker player:
- Elastic personality traits (dynamic trait values with pressure/recovery)
- Emotional state (dimensional model + LLM-generated narrative)
- Tilt state (tilt level, source, nemesis tracking)

This simplifies the architecture by providing a single entry point for
pressure events and end-of-hand updates.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional

from .elasticity_manager import ElasticPersonality
from .emotional_state import EmotionalState, EmotionalStateGenerator
from .tilt_modifier import TiltState, TiltPromptModifier

logger = logging.getLogger(__name__)

# Standard trait names tracked by elastic personality
TRAIT_NAMES = ['bluff_tendency', 'aggression', 'chattiness', 'emoji_usage']


@dataclass
class PlayerPsychology:
    """
    Single source of truth for AI player psychological state.

    Combines:
    - Elastic personality (trait dynamics with pressure/recovery)
    - Emotional state (valence, arousal, control, focus + narrative)
    - Tilt state (tilt level, source, nemesis, streaks)

    Provides unified event handling and prompt building.
    """

    # Identity
    player_name: str
    personality_config: Dict[str, Any]

    # Psychological systems
    elastic: ElasticPersonality
    emotional: Optional[EmotionalState] = None
    tilt: TiltState = field(default_factory=TiltState)

    # Internal helpers
    _emotional_generator: EmotionalStateGenerator = field(default=None, repr=False, compare=False)

    # Metadata
    hand_count: int = 0
    last_updated: Optional[str] = None

    def __post_init__(self):
        """Initialize emotional state generator."""
        if self._emotional_generator is None:
            self._emotional_generator = EmotionalStateGenerator()

    @classmethod
    def from_personality_config(cls, name: str, config: Dict[str, Any]) -> 'PlayerPsychology':
        """
        Create PlayerPsychology from a personality configuration.

        Args:
            name: Player name (e.g., "Donald Trump")
            config: Personality config dict from personalities.json

        Returns:
            New PlayerPsychology instance
        """
        elastic = ElasticPersonality.from_base_personality(
            name=name,
            personality_config=config
        )

        return cls(
            player_name=name,
            personality_config=config,
            elastic=elastic
        )

    # === UNIFIED EVENT HANDLING ===

    def apply_pressure_event(self, event_name: str, opponent: Optional[str] = None) -> None:
        """
        Single entry point for pressure events.

        Updates both elastic personality AND tilt state from the same event.
        This is cleaner than separate calls.

        Args:
            event_name: Event type (e.g., 'bluff_called', 'big_win', 'bad_beat')
            opponent: Optional opponent who triggered the event
        """
        # Update elastic personality traits
        self.elastic.apply_pressure_event(event_name)

        # Update tilt state
        self.tilt.apply_pressure_event(event_name, opponent)

        self._mark_updated()

        logger.debug(
            f"{self.player_name}: Pressure event '{event_name}' applied. "
            f"Tilt={self.tilt.tilt_level:.2f}"
        )

    def on_hand_complete(
        self,
        outcome: str,
        amount: int,
        opponent: Optional[str] = None,
        was_bad_beat: bool = False,
        was_bluff_called: bool = False,
        session_context: Optional[Dict[str, Any]] = None,
        key_moment: Optional[str] = None
    ) -> None:
        """
        Called after each hand completes.

        Updates tilt from outcome AND generates new emotional state.

        Args:
            outcome: 'won', 'lost', 'folded'
            amount: Net win/loss amount
            opponent: Opponent involved in the hand
            was_bad_beat: True if lost with strong hand
            was_bluff_called: True if bluff was called
            session_context: Session stats for emotional state generation
            key_moment: Optional key moment ('bad_beat', 'bluff_called', etc.)
        """
        # Update tilt from hand outcome
        self.tilt.update_from_hand(
            outcome=outcome,
            amount=amount,
            opponent=opponent,
            was_bad_beat=was_bad_beat,
            was_bluff_called=was_bluff_called
        )

        # Generate new emotional state
        self._generate_emotional_state(
            outcome=outcome,
            amount=amount,
            opponent=opponent,
            key_moment=key_moment or ('bad_beat' if was_bad_beat else ('bluff_called' if was_bluff_called else None)),
            session_context=session_context or {}
        )

        self.hand_count += 1
        self._mark_updated()

        logger.info(
            f"{self.player_name}: Hand complete ({outcome}, ${amount}). "
            f"Tilt={self.tilt.tilt_level:.2f}, "
            f"Emotional={self.emotional.valence_descriptor if self.emotional else 'none'}"
        )

    def recover(self, recovery_rate: float = 0.1) -> None:
        """
        Apply recovery between hands.

        - Elastic traits drift back toward anchor
        - Tilt naturally decays

        Args:
            recovery_rate: Rate of elastic trait recovery (0.0-1.0)
        """
        self.elastic.recover_traits(recovery_rate)
        self.tilt.decay()
        self._mark_updated()

    # === TRAIT ACCESS ===

    @property
    def traits(self) -> Dict[str, float]:
        """
        Get current trait values.

        Used for:
        - Fallback AI behavior when LLM fails
        - Chattiness calculations
        - Personality modifiers

        Returns:
            Dict of trait names to current values (0.0-1.0)
        """
        return {
            name: self.elastic.get_trait_value(name)
            for name in TRAIT_NAMES
        }

    @property
    def mood(self) -> str:
        """Get current mood from elastic personality."""
        return self.elastic.get_current_mood()

    @property
    def tilt_level(self) -> float:
        """Current tilt level (0.0-1.0)."""
        return self.tilt.tilt_level

    @property
    def tilt_category(self) -> str:
        """Tilt severity: 'none', 'mild', 'moderate', 'severe'."""
        return self.tilt.get_tilt_category()

    @property
    def is_tilted(self) -> bool:
        """True if tilt >= 0.2 (mild or above)."""
        return self.tilt.tilt_level >= 0.2

    @property
    def is_severely_tilted(self) -> bool:
        """True if tilt >= 0.6 (emotional state should be overridden)."""
        return self.tilt.tilt_level >= 0.6

    # === PROMPT BUILDING ===

    def get_prompt_section(self) -> str:
        """
        Get emotional state section for prompt injection.

        Skips if:
        - No emotional state exists yet
        - Player is severely tilted (tilt overrides emotion)

        Returns:
            Formatted emotional state section or empty string
        """
        if self.is_severely_tilted or not self.emotional:
            return ""

        return self.emotional.to_prompt_section()

    def apply_tilt_effects(self, prompt: str) -> str:
        """
        Apply tilt-based prompt modifications.

        At different tilt levels:
        - 0.2+: Adds intrusive thoughts
        - 0.3+: Adds tilted strategy advice
        - 0.4+: Degrades strategic guidance
        - 0.5+: Hides pot odds
        - 0.7+: Removes all strategic advice

        Args:
            prompt: Original prompt

        Returns:
            Modified prompt with tilt effects
        """
        if self.tilt.tilt_level < 0.2:
            return prompt

        modifier = TiltPromptModifier(self.tilt)
        return modifier.modify_prompt(prompt)

    # === AVATAR DISPLAY ===

    def get_display_emotion(self) -> str:
        """
        Get emotion for avatar display.

        Maps emotional state to discrete avatar emotions:
        - angry, shocked, nervous, happy, thinking, confident

        Returns:
            Emotion name for avatar selection
        """
        if self.emotional:
            return self.emotional.get_display_emotion()
        return 'confident'

    # === SERIALIZATION ===

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize full psychological state to dictionary.

        Returns:
            Dict suitable for JSON serialization and database storage
        """
        return {
            'player_name': self.player_name,
            'elastic': self.elastic.to_dict() if self.elastic else None,
            'emotional': self.emotional.to_dict() if self.emotional else None,
            'tilt': self.tilt.to_dict(),
            'hand_count': self.hand_count,
            'last_updated': self.last_updated
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], personality_config: Dict[str, Any]) -> 'PlayerPsychology':
        """
        Deserialize from saved state.

        Args:
            data: Serialized psychology state
            personality_config: Personality config from personalities.json

        Returns:
            Restored PlayerPsychology instance
        """
        player_name = data['player_name']

        # Restore elastic personality
        elastic = None
        if data.get('elastic'):
            elastic = ElasticPersonality.from_dict(data['elastic'])
        else:
            # Fallback: create from config
            elastic = ElasticPersonality.from_base_personality(
                name=player_name,
                personality_config=personality_config
            )

        # Create psychology instance
        psychology = cls(
            player_name=player_name,
            personality_config=personality_config,
            elastic=elastic
        )

        # Restore emotional state
        if data.get('emotional'):
            psychology.emotional = EmotionalState.from_dict(data['emotional'])

        # Restore tilt state
        if data.get('tilt'):
            psychology.tilt = TiltState.from_dict(data['tilt'])

        # Restore metadata
        psychology.hand_count = data.get('hand_count', 0)
        psychology.last_updated = data.get('last_updated')

        return psychology

    # === PRIVATE HELPERS ===

    def _generate_emotional_state(
        self,
        outcome: str,
        amount: int,
        opponent: Optional[str],
        key_moment: Optional[str],
        session_context: Dict[str, Any]
    ) -> None:
        """Generate new emotional state via LLM."""
        hand_outcome = {
            'outcome': outcome,
            'amount': amount,
            'opponent': opponent,
            'key_moment': key_moment
        }

        # Build elastic traits dict for generator
        elastic_traits = {}
        for trait_name in TRAIT_NAMES:
            if trait_name in self.elastic.traits:
                trait = self.elastic.traits[trait_name]
                elastic_traits[trait_name] = {
                    'value': trait.value,
                    'anchor': trait.anchor,
                    'pressure': trait.pressure
                }

        try:
            self.emotional = self._emotional_generator.generate(
                personality_name=self.player_name,
                personality_config=self.personality_config,
                hand_outcome=hand_outcome,
                elastic_traits=elastic_traits,
                tilt_state=self.tilt,
                session_context=session_context,
                hand_number=self.hand_count
            )
        except Exception as e:
            logger.warning(
                f"{self.player_name}: Failed to generate emotional state: {e}. "
                f"Using fallback."
            )
            # Emotional generator handles fallback internally, but be safe
            if not self.emotional:
                self.emotional = None

    def _mark_updated(self) -> None:
        """Mark the last update timestamp."""
        self.last_updated = datetime.utcnow().isoformat()
