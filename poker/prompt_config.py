"""
Prompt Configuration System.

Allows toggling individual prompt components on/off for testing,
A/B comparison, and mid-game adjustment based on circumstances.
"""
import logging
from dataclasses import dataclass, fields
from typing import Dict, Any

from poker.game_modes_loader import get_preset_configs

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
        strategic_reflection: Include past strategic reflections in prompts
        memory_keep_exchanges: Number of conversation exchanges to retain (0=clear each turn)
        chattiness: Chattiness guidance (when/how to speak)
        emotional_state: Emotional state narrative and dimensions
        tilt_effects: Tilt-based prompt modifications (intrusive thoughts, etc.)
        mind_games: MIND GAMES instruction (read opponent table talk)
        dramatic_sequence: Dramatic sequence instruction (character expression and table talk)
        situational_guidance: Coaching prompts for specific situations (pot-committed, short-stack, made hand)
        gto_equity: Always show equity vs required equity comparison for all decisions
        gto_verdict: Show explicit +EV/-EV verdict (CALL is +EV, FOLD is correct)
        include_personality: Include personality system prompt (when False, uses generic prompt)
        use_simple_response_format: Use simple JSON response format instead of rich format
        guidance_injection: Extra text to append to decision prompts (for experiments)
    """

    # Game state components
    pot_odds: bool = True
    hand_strength: bool = True
    range_guidance: bool = True  # Looseness-aware preflop range classification

    # Memory components
    session_memory: bool = True
    opponent_intel: bool = True
    strategic_reflection: bool = True    # Include past reflections in prompts
    memory_keep_exchanges: int = 0       # Conversation messages to retain (0=clear)

    # Psychological components
    chattiness: bool = True
    emotional_state: bool = True
    tilt_effects: bool = True
    expression_filtering: bool = True  # Phase 2: visibility-based expression dampening
    zone_benefits: bool = True         # Phase 7: zone-based strategy guidance

    # Template instruction components
    mind_games: bool = True
    dramatic_sequence: bool = True
    betting_discipline: bool = True  # BETTING DISCIPLINE block in every decision prompt

    # Situational guidance components (coaching for specific game states)
    situational_guidance: bool = True  # pot_committed, short_stack, made_hand

    # GTO Foundation components (math-first decision support)
    gto_equity: bool = False  # Always show equity verdict for all decisions
    gto_verdict: bool = False  # Show "CALL is +EV" / "FOLD is correct" verdict
    use_enhanced_ranges: bool = True   # Use PFR/action-based range estimation (vs VPIP-only)

    # Personality toggle — when False, uses a generic system prompt instead of personality
    include_personality: bool = True

    # Response format toggle
    # When True, expect simple {"action": "...", "raise_to": ...} instead of rich format
    use_simple_response_format: bool = False

    # Lean bounded mode — bypass full prompt pipeline, use minimal options-only prompt
    lean_bounded: bool = False

    # Style-aware options — map psychology playstyle to option profiles in lean mode
    style_aware_options: bool = True

    # Hand plan — generate per-hand strategy via Phase 0 before lean decisions
    hand_plan: bool = False

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
        - show_equity_always -> gto_equity
        - show_equity_verdict -> gto_verdict
        """
        if not data:
            logger.warning("PromptConfig.from_dict called with empty/None data, using defaults")
            return cls()

        # Migrate legacy field names
        data = dict(data)  # Don't mutate caller's dict
        # Drop removed fields silently
        data.pop('bb_normalized', None)
        data.pop('use_dollar_amounts', None)

        if 'show_equity_always' in data and 'gto_equity' not in data:
            data['gto_equity'] = data.pop('show_equity_always')
        elif 'show_equity_always' in data:
            data.pop('show_equity_always')

        if 'show_equity_verdict' in data and 'gto_verdict' not in data:
            data['gto_verdict'] = data.pop('show_equity_verdict')
        elif 'show_equity_verdict' in data:
            data.pop('show_equity_verdict')

        # Migrate use_minimal_prompt -> include_personality + use_simple_response_format
        if 'use_minimal_prompt' in data:
            if data.pop('use_minimal_prompt'):
                data['include_personality'] = False
                data['use_simple_response_format'] = True

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
    # NOTE: YAML (config/game_modes.yaml) is the source of truth for game mode presets.
    # These factory methods are kept as fallbacks for migrations, tests, and
    # environments without YAML/DB (e.g., experiments run outside Flask).
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

    EXPLOITATIVE_GUIDANCE = (
        "EXPLOIT AGGRESSIVE OPPONENTS: When facing players who rarely fold and raise frequently, "
        "adjust your strategy: (1) Trap with strong hands - check to induce bluffs rather than betting, "
        "(2) Call wider - their raising range is weaker than normal, "
        "(3) Don't bluff them - they won't fold, value bet relentlessly instead, "
        "(4) Let them hang themselves with aggression."
    )

    @classmethod
    def pro(cls) -> 'PromptConfig':
        """Pro mode - GTO-focused analytical poker. AIs don't tilt (harder opponents)."""
        return cls(
            gto_equity=True,
            gto_verdict=True,
            chattiness=False,
            dramatic_sequence=False,
            tilt_effects=False,  # Harder AIs - no penalty zones / intrusive thoughts
            guidance_injection=cls.EXPLOITATIVE_GUIDANCE,
        )

    @classmethod
    def competitive(cls) -> 'PromptConfig':
        """Competitive mode - full GTO guidance with personality and trash talk."""
        return cls(
            gto_equity=True,
            gto_verdict=True,
            guidance_injection=cls.EXPLOITATIVE_GUIDANCE,
        )

    @classmethod
    def from_mode_name(cls, mode: str) -> 'PromptConfig':
        """Resolve a game mode by name string.

        Tries YAML config first, falls back to factory methods.
        """
        mode = mode.lower()

        # Try YAML-based config first
        try:
            yaml_presets = get_preset_configs()
            if mode in yaml_presets:
                return cls.from_dict(yaml_presets[mode])
        except Exception as e:
            logger.debug(f"YAML preset lookup failed for '{mode}', using factory fallback: {e}")

        # Fallback to factory methods
        modes = {
            'casual': cls.casual,
            'standard': cls.standard,
            'pro': cls.pro,
            'competitive': cls.competitive,
        }
        if mode not in modes:
            raise ValueError(f"Invalid game mode: {mode}. Valid: {list(modes.keys())}")
        return modes[mode]()
