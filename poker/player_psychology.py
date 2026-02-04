"""
Unified Player Psychology System.

Consolidates all psychological state for an AI poker player:
- Elastic personality traits (5-trait poker-native model with pressure/recovery)
- Emotional state (derived from confidence × composure + LLM-generated narrative)
- Composure-based prompt effects (replaces separate tilt system)

5-Trait Poker-Native Model:
- tightness: Range selectivity (0=loose, 1=tight)
- aggression: Bet frequency (0=passive, 1=aggressive)
- confidence: Sizing/commitment (0=scared, 1=fearless)
- composure: Decision quality (0=tilted, 1=focused)
- table_talk: Chat frequency (0=silent, 1=chatty)

Composure replaces the old TiltState system. Low composure = tilted.
"""

import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional, List

from .elasticity_manager import ElasticPersonality
from .emotional_state import (
    EmotionalState, EmotionalStateGenerator,
    compute_baseline_mood, compute_reactive_spike, blend_emotional_state,
)
from .trait_converter import (
    NEW_TRAIT_NAMES,
    convert_tilt_to_composure,
)
from .range_guidance import derive_bluff_propensity, get_player_archetype

logger = logging.getLogger(__name__)

# Standard trait names for the 5-trait poker-native model
TRAIT_NAMES = NEW_TRAIT_NAMES


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
    Single source of truth for AI player psychological state.

    Combines:
    - Elastic personality (5-trait poker-native model with pressure/recovery)
    - Emotional state (derived from confidence × composure + narrative)
    - Composure tracking (source, nemesis - for intrusive thoughts)

    Composure is now a trait (0=tilted, 1=focused), not a separate system.
    """

    # Identity
    player_name: str
    personality_config: Dict[str, Any]

    # Psychological systems
    elastic: ElasticPersonality
    emotional: Optional[EmotionalState] = None
    composure_state: ComposureState = field(default_factory=ComposureState)

    # Internal helpers
    _emotional_generator: EmotionalStateGenerator = field(default=None, repr=False, compare=False)

    # Tracking context (for cost analysis)
    game_id: Optional[str] = None
    owner_id: Optional[str] = None

    # Metadata
    hand_count: int = 0
    last_updated: Optional[str] = None

    def __post_init__(self):
        """Initialize emotional state generator."""
        if self._emotional_generator is None:
            self._emotional_generator = EmotionalStateGenerator()

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

        Auto-detects old 4-trait format and converts to new 5-trait model.
        """
        elastic = ElasticPersonality.from_base_personality(
            name=name,
            personality_config=config
        )

        # Generate initial baseline emotional state from personality traits
        baseline = compute_baseline_mood(elastic.traits)
        initial_emotional = EmotionalState(
            valence=baseline['valence'],
            arousal=baseline['arousal'],
            control=baseline['control'],
            focus=baseline['focus'],
            narrative='Settling in at the table.',
            inner_voice="Let's see what we've got.",
            generated_at_hand=0,
            source_events=['session_start'],
            used_fallback=True,
        )

        return cls(
            player_name=name,
            personality_config=config,
            elastic=elastic,
            emotional=initial_emotional,
            game_id=game_id,
            owner_id=owner_id,
        )

    # === UNIFIED EVENT HANDLING ===

    def apply_pressure_event(self, event_name: str, opponent: Optional[str] = None) -> None:
        """
        Single entry point for pressure events.

        Updates elastic traits (including composure) and tracks pressure source.
        """
        # Update elastic personality traits (including composure)
        self.elastic.apply_pressure_event(event_name)

        # Update composure tracking (source, nemesis)
        self.composure_state.update_from_event(event_name, opponent)

        self._mark_updated()

        composure = self.composure
        logger.debug(
            f"{self.player_name}: Pressure event '{event_name}' applied. "
            f"Composure={composure:.2f}"
        )

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
            f"Composure={self.composure:.2f}, "
            f"Emotional={self.emotional.valence_descriptor if self.emotional else 'none'}"
        )

    def recover(self, recovery_rate: float = 0.1) -> None:
        """
        Apply recovery between hands.

        - Elastic traits (including composure) drift back toward anchor
        - Emotional state decays toward baseline
        """
        self.elastic.recover_traits(recovery_rate)

        # Decay emotional state toward current baseline
        if self.emotional:
            baseline = compute_baseline_mood(self.elastic.traits)
            self.emotional = self.emotional.decay_toward_baseline(
                baseline=baseline, rate=recovery_rate
            )

        self._mark_updated()

    # === TRAIT ACCESS ===

    @property
    def traits(self) -> Dict[str, float]:
        """
        Get current trait values.

        Returns:
            Dict of trait names to current values (0.0-1.0)
        """
        return {
            name: self.elastic.get_trait_value(name)
            for name in TRAIT_NAMES
        }

    @property
    def composure(self) -> float:
        """Current composure level (0.0=tilted, 1.0=focused)."""
        return self.elastic.get_trait_value('composure')

    @property
    def confidence(self) -> float:
        """Current confidence level (0.0=scared, 1.0=fearless)."""
        return self.elastic.get_trait_value('confidence')

    @property
    def tightness(self) -> float:
        """Current tightness level (0.0=loose, 1.0=tight)."""
        return self.elastic.get_trait_value('tightness')

    @property
    def aggression(self) -> float:
        """Current aggression level (0.0=passive, 1.0=aggressive)."""
        return self.elastic.get_trait_value('aggression')

    @property
    def table_talk(self) -> float:
        """Current table talk level (0.0=silent, 1.0=chatty)."""
        return self.elastic.get_trait_value('table_talk')

    @property
    def bluff_propensity(self) -> float:
        """Derived bluff tendency from tightness and aggression."""
        return derive_bluff_propensity(self.tightness, self.aggression)

    @property
    def archetype(self) -> str:
        """Player archetype: TAG, LAG, Rock, or Fish."""
        return get_player_archetype(self.tightness, self.aggression)

    @property
    def mood(self) -> str:
        """Get current mood from elastic personality."""
        return self.elastic.get_current_mood()

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

    def get_display_emotion(self) -> str:
        """
        Get emotion for avatar display.

        Uses confidence × composure matrix + aggression for angry flair.
        """
        composure = self.composure
        confidence = self.confidence
        aggression = self.aggression

        # Angry: low composure + high aggression
        if composure < 0.4 and aggression > 0.6:
            return "angry"

        # Use emotional state if available
        if self.emotional:
            return self.emotional.get_display_emotion()

        # Fallback based on confidence × composure
        if confidence > 0.6 and composure > 0.6:
            return "confident"
        elif confidence < 0.4 and composure < 0.4:
            return "nervous"
        elif confidence > 0.6 and composure < 0.4:
            return "frustrated"
        elif confidence < 0.4 and composure > 0.6:
            return "thinking"

        return "poker_face"

    # === SERIALIZATION ===

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize full psychological state to dictionary.
        """
        return {
            'player_name': self.player_name,
            'elastic': self.elastic.to_dict() if self.elastic else None,
            'emotional': self.emotional.to_dict() if self.emotional else None,
            'composure_state': self.composure_state.to_dict(),
            'game_id': self.game_id,
            'owner_id': self.owner_id,
            'hand_count': self.hand_count,
            'last_updated': self.last_updated
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], personality_config: Dict[str, Any]) -> 'PlayerPsychology':
        """
        Deserialize from saved state.

        Handles migration from old TiltState format to new ComposureState.
        """
        player_name = data['player_name']

        # Restore elastic personality
        elastic = None
        if data.get('elastic'):
            elastic = ElasticPersonality.from_dict(data['elastic'])
        else:
            elastic = ElasticPersonality.from_base_personality(
                name=player_name,
                personality_config=personality_config
            )

        # Create psychology instance
        psychology = cls(
            player_name=player_name,
            personality_config=personality_config,
            elastic=elastic,
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
            # Convert tilt_level to composure trait
            tilt_level = data['tilt'].get('tilt_level', 0.0)
            composure_value = convert_tilt_to_composure(tilt_level)
            if 'composure' in psychology.elastic.traits:
                psychology.elastic.traits['composure'].value = composure_value

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
        session_context: Dict[str, Any],
        big_blind: int = 100,
    ) -> None:
        """Generate new emotional state via two-layer model + LLM narration."""
        hand_outcome = {
            'outcome': outcome,
            'amount': amount,
            'opponent': opponent,
            'key_moment': key_moment
        }

        # Create a mock tilt state for backward compatibility with emotional_state.py
        # This will be removed when emotional_state.py is updated
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

        try:
            self.emotional = self._emotional_generator.generate(
                personality_name=self.player_name,
                personality_config=self.personality_config,
                hand_outcome=hand_outcome,
                elastic_traits=self.elastic.traits,
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
                f"Using computed dimensions with fallback narrative."
            )
            baseline = compute_baseline_mood(self.elastic.traits)
            spike = compute_reactive_spike(
                outcome=outcome, amount=amount,
                tilt_level=self.tilt_level, big_blind=big_blind,
            )
            dimensions = blend_emotional_state(baseline, spike)
            self.emotional = EmotionalState(
                valence=dimensions['valence'],
                arousal=dimensions['arousal'],
                control=dimensions['control'],
                focus=dimensions['focus'],
                narrative='Processing the last hand.',
                inner_voice='Focus on the next one.',
                generated_at_hand=self.hand_count,
                source_events=[outcome] + ([key_moment] if key_moment else []),
                used_fallback=True,
            )

    def _mark_updated(self) -> None:
        """Mark the last update timestamp."""
        self.last_updated = datetime.utcnow().isoformat()
