"""
Prompt Configuration System.

Allows toggling individual prompt components on/off for testing,
A/B comparison, and mid-game adjustment based on circumstances.
"""
import logging
from dataclasses import dataclass, fields
from typing import Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class PromptConfig:
    """
    Configuration for which prompt components are enabled.

    Each boolean flag controls whether a specific component is included
    in the AI's decision prompt. All default to True for backward compatibility.

    Components:
        pot_odds: Pot odds guidance and equity calculations
        hand_strength: Hand strength evaluation (preflop ranking, postflop eval)
        session_memory: Session stats (win rate, streaks, observations)
        opponent_intel: Opponent tendencies and playing style summaries
        chattiness: Chattiness guidance (when/how to speak)
        emotional_state: Emotional state narrative and dimensions
        tilt_effects: Tilt-based prompt modifications (intrusive thoughts, etc.)
        mind_games: MIND GAMES instruction (read opponent table talk)
        persona_response: PERSONA RESPONSE instruction (trash talk guidance)
    """

    # Game state components
    pot_odds: bool = True
    hand_strength: bool = True

    # Memory components
    session_memory: bool = True
    opponent_intel: bool = True

    # Psychological components
    chattiness: bool = True
    emotional_state: bool = True
    tilt_effects: bool = True

    # Template instruction components
    mind_games: bool = True
    persona_response: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for persistence."""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PromptConfig':
        """
        Deserialize from persistence.

        Logs warnings for empty data or unknown fields, and errors are
        caught and logged with fallback to defaults.
        """
        if not data:
            logger.warning("PromptConfig.from_dict called with empty/None data, using defaults")
            return cls()

        # Get known field names
        known_fields = {f.name for f in fields(cls)}
        provided_fields = set(data.keys())

        # Log unknown fields
        unknown_fields = provided_fields - known_fields
        if unknown_fields:
            logger.warning(f"PromptConfig.from_dict ignoring unknown fields: {unknown_fields}")

        # Filter to only known fields
        valid_data = {k: v for k, v in data.items() if k in known_fields}

        try:
            return cls(**valid_data)
        except Exception as e:
            logger.error(f"PromptConfig.from_dict failed to create config: {e}, using defaults")
            return cls()

    def disable_all(self) -> 'PromptConfig':
        """Return new config with all components disabled."""
        return PromptConfig(**{f.name: False for f in fields(self)})

    def enable_all(self) -> 'PromptConfig':
        """Return new config with all components enabled."""
        return PromptConfig(**{f.name: True for f in fields(self)})

    def copy(self, **overrides) -> 'PromptConfig':
        """Return a copy with optional overrides."""
        data = self.to_dict()
        data.update(overrides)
        return PromptConfig.from_dict(data)

    def __repr__(self) -> str:
        """Compact representation showing only disabled components."""
        disabled = [f.name for f in fields(self) if not getattr(self, f.name)]
        if not disabled:
            return "PromptConfig(all enabled)"
        return f"PromptConfig(disabled: {disabled})"
