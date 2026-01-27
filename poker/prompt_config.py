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
        use_dollar_amounts: Show monetary amounts in dollars instead of BB (default False = BB mode)
        session_memory: Session stats (win rate, streaks, observations)
        opponent_intel: Opponent tendencies and playing style summaries
        strategic_reflection: Include past strategic reflections in prompts
        memory_keep_exchanges: Number of conversation exchanges to retain (0=clear each turn)
        chattiness: Chattiness guidance (when/how to speak)
        emotional_state: Emotional state narrative and dimensions
        tilt_effects: Tilt-based prompt modifications (intrusive thoughts, etc.)
        mind_games: MIND GAMES instruction (read opponent table talk)
        persona_response: PERSONA RESPONSE instruction (trash talk guidance)
        situational_guidance: Coaching prompts for specific situations (pot-committed, short-stack, made hand)
        gto_equity: Always show equity vs required equity comparison for all decisions
        gto_verdict: Show explicit +EV/-EV verdict (CALL is +EV, FOLD is correct)
        include_personality: Include personality system prompt (Phase 2 implementation)
        use_simple_response_format: Use simple JSON response format (Phase 2 implementation)
        guidance_injection: Extra text to append to decision prompts (for experiments)
    """

    # Game state components
    pot_odds: bool = True
    hand_strength: bool = True
    use_dollar_amounts: bool = False  # False = BB mode (default), True = dollar amounts

    # Memory components
    session_memory: bool = True
    opponent_intel: bool = True
    strategic_reflection: bool = True    # Include past reflections in prompts
    memory_keep_exchanges: int = 0       # Conversation messages to retain (0=clear)

    # Psychological components
    chattiness: bool = True
    emotional_state: bool = True
    tilt_effects: bool = True

    # Template instruction components
    mind_games: bool = True
    persona_response: bool = True

    # Situational guidance components (coaching for specific game states)
    situational_guidance: bool = True  # pot_committed, short_stack, made_hand

    # GTO Foundation components (math-first decision support)
    gto_equity: bool = False  # Always show equity verdict for all decisions
    gto_verdict: bool = False  # Show "CALL is +EV" / "FOLD is correct" verdict
    use_enhanced_ranges: bool = True   # Use PFR/action-based range estimation (vs VPIP-only)

    # Minimal prompt mode - strips everything to bare game state
    # When True, uses minimal_prompt.py instead of full prompt system
    # Disables personality, psychology, guidance - just pure game theory inputs
    use_minimal_prompt: bool = False

    # Personality toggle (Phase 2 implementation - field added now for config readiness)
    include_personality: bool = True

    # Response format toggle (Phase 2 implementation - field added now for config readiness)
    # When True, expect simple {"action": "...", "raise_to": ...} instead of rich format
    use_simple_response_format: bool = False

    # Experiment support
    guidance_injection: str = ""  # Extra text appended to decision prompts

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for persistence."""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PromptConfig':
        """
        Deserialize from persistence.

        Logs warnings for empty data or unknown fields, and errors are
        caught and logged with fallback to defaults.

        Supports legacy field names for backward compatibility:
        - bb_normalized -> use_dollar_amounts (inverted)
        - show_equity_always -> gto_equity
        - show_equity_verdict -> gto_verdict
        """
        if not data:
            logger.warning("PromptConfig.from_dict called with empty/None data, using defaults")
            return cls()

        # Migrate legacy field names
        data = dict(data)  # Don't mutate caller's dict
        if 'bb_normalized' in data and 'use_dollar_amounts' not in data:
            # Inverted: bb_normalized=True meant BB mode, use_dollar_amounts=False means BB mode
            data['use_dollar_amounts'] = not data.pop('bb_normalized')
        elif 'bb_normalized' in data:
            data.pop('bb_normalized')

        if 'show_equity_always' in data and 'gto_equity' not in data:
            data['gto_equity'] = data.pop('show_equity_always')
        elif 'show_equity_always' in data:
            data.pop('show_equity_always')

        if 'show_equity_verdict' in data and 'gto_verdict' not in data:
            data['gto_verdict'] = data.pop('show_equity_verdict')
        elif 'show_equity_verdict' in data:
            data.pop('show_equity_verdict')

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
        """Return new config with all boolean components disabled."""
        return PromptConfig(**{
            f.name: False if f.type == bool else getattr(self, f.name)
            for f in fields(self)
        })

    def enable_all(self) -> 'PromptConfig':
        """Return new config with all boolean components enabled."""
        return PromptConfig(**{
            f.name: True if f.type == bool else getattr(self, f.name)
            for f in fields(self)
        })

    def copy(self, **overrides) -> 'PromptConfig':
        """Return a copy with optional overrides."""
        data = self.to_dict()
        data.update(overrides)
        return PromptConfig.from_dict(data)

    def __repr__(self) -> str:
        """Compact representation showing only disabled boolean components."""
        disabled = [f.name for f in fields(self) if f.type == bool and not getattr(self, f.name)]
        extras = []
        if self.memory_keep_exchanges > 0:
            extras.append(f"memory_keep_exchanges={self.memory_keep_exchanges}")
        if not disabled and not extras:
            return "PromptConfig(all enabled)"
        parts = []
        if disabled:
            parts.append(f"disabled: {disabled}")
        if extras:
            parts.append(", ".join(extras))
        return f"PromptConfig({', '.join(parts)})"

    # Game mode factory methods
    @classmethod
    def casual(cls) -> 'PromptConfig':
        """Casual mode - personality-driven fun poker."""
        return cls()  # Default config is casual

    @classmethod
    def standard(cls) -> 'PromptConfig':
        """Standard mode - balanced personality + GTO awareness."""
        return cls(
            gto_equity=True,
            gto_verdict=False,
        )

    @classmethod
    def pro(cls) -> 'PromptConfig':
        """Pro mode - GTO-focused analytical poker."""
        return cls(
            gto_equity=True,
            gto_verdict=True,
            chattiness=False,
            persona_response=False,
        )

    @classmethod
    def competitive(cls) -> 'PromptConfig':
        """Competitive mode - full GTO guidance with personality and trash talk."""
        return cls(
            gto_equity=True,
            gto_verdict=True,
        )

    @classmethod
    def from_mode_name(cls, mode: str) -> 'PromptConfig':
        """Resolve a game mode by name string."""
        mode = mode.lower()
        modes = {
            'casual': cls.casual,
            'standard': cls.standard,
            'pro': cls.pro,
            'competitive': cls.competitive,
        }
        if mode not in modes:
            raise ValueError(f"Invalid game mode: {mode}. Valid: {list(modes.keys())}")
        return modes[mode]()
