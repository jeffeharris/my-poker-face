"""ExpressionContext: read-only input for Layer 3 LLM narration.

The Tiered Bot Architecture's Layer 3 receives the *already-decided* action
(decided by Layer 1+2 math) along with character and game context, and
generates personality-appropriate table talk. Crucially, the LLM never
influences the action — it only narrates.

See docs/technical/TIERED_BOT_ARCHITECTURE.md (Layer 3 section).
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from .narration_facts import NarrationFacts


@dataclass(frozen=True)
class ExpressionContext:
    """All inputs the LLM needs to narrate a decided action.

    A subset of the spec's full ExpressionContext (lines 931-959). Richer
    fields like `was_bluff`, `baseline_action`, and `deviation_magnitude`
    are follow-ups — they require pre-distortion state tracking that
    Layer 2 doesn't expose yet.
    """

    # What happened (already decided — read-only)
    action_taken: str
    raise_to: int

    # Game situation
    hand_cards: List[str]
    community_cards: List[str]
    phase: str                  # 'pre_flop' | 'flop' | 'turn' | 'river'
    pot_size: int
    opponent_count: int

    # Character context (from personality_config)
    personality_name: str
    play_style: str
    default_attitude: str
    verbal_tics: List[str] = field(default_factory=list)
    physical_tics: List[str] = field(default_factory=list)

    # Dramatic calibration (from MomentAnalyzer)
    drama_level: str = 'routine'    # routine | notable | high_stakes | climactic
    drama_tone: str = 'neutral'     # neutral | confident | desperate | triumphant

    # Emotional state (from psychology)
    emotional_state: str = 'composed'
    emotional_severity: str = 'none'

    # Phase 7.6 Step 5: per-decision strategy reads from the
    # intervention trace, mapped via narration_facts adapter. When
    # present and non-empty, ExpressionGenerator appends a
    # "WHAT YOU NOTICED / WHAT YOU DECIDED" block to the prompt so the
    # LLM narration is grounded in the bot's actual reads.
    #
    # Optional and defaults to None — pre-7.6 callers / hybrid path
    # produce identical prompts to before.
    narration_facts: Optional['NarrationFacts'] = None
