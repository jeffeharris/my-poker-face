"""ExpressionContext: read-only input for Layer 3 LLM narration.

The Tiered Bot Architecture's Layer 3 receives the *already-decided* action
(decided by Layer 1+2 math) along with character and game context, and
generates personality-appropriate table talk. Crucially, the LLM never
influences the action — it only narrates.

See docs/technical/TIERED_BOT_ARCHITECTURE.md (Layer 3 section).
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional, Tuple

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
    phase: str  # 'pre_flop' | 'flop' | 'turn' | 'river'
    pot_size: int
    opponent_count: int

    # Character context (from personality_config)
    personality_name: str
    play_style: str
    default_attitude: str
    verbal_tics: List[str] = field(default_factory=list)
    physical_tics: List[str] = field(default_factory=list)

    # Dramatic calibration (from MomentAnalyzer)
    drama_level: str = 'routine'  # routine | notable | high_stakes | climactic
    drama_tone: str = 'neutral'  # neutral | confident | desperate | triumphant

    # Emotional state (from psychology)
    emotional_state: str = 'composed'
    emotional_severity: str = 'none'

    # Richer situation context (Phase: tieredbot-messages). All optional and
    # default to empty — pre-existing callers render identical prompts.
    position: str = ''
    stack_bb: float = 0.0
    pot_bb: float = 0.0
    cost_to_call_bb: float = 0.0

    # Readable hand-strength label (e.g. "Two Pair - Strong" postflop,
    # "Premium" preflop). When non-empty, the prompt includes a "Your read
    # on your hand" line so the narration can riff on hand strength.
    hand_name: str = ''

    # Coarse strength tier — Monster / Strong / Marginal / Weak / Drawing.
    # Derived from hand_name. Drives narration tone (confident vs nervous,
    # value vs bluff).
    hand_strength_tier: str = ''

    # Situational reads borrowed from the hybrid path. Each flag is
    # cheaply derivable from BB-normalized situation and informs how the
    # narration should feel:
    #   short_stack:   < 3 BB → do-or-die mode
    #   pot_committed: invested too much to fold; "I'm in this now"
    short_stack: bool = False
    pot_committed: bool = False

    # Pre-formatted multi-line recent-actions text (already BB-converted by
    # the caller — same shape hybrid's Recent Actions block uses). When
    # non-empty, the prompt includes a Recent Actions block so the narration
    # can reference opponents by name.
    recent_actions: str = ''

    # Narration gates. Chattiness controls speech (matches hybrid's
    # ChattinessManager flow); energy controls physical gestures so a
    # silent character can still react with body language on the right
    # moments. Combinations:
    #   speak=T, gesture=T → full speech + actions (default)
    #   speak=F, gesture=T → quiet reaction: only *action* beats, no speech
    #   speak=F, gesture=F → fully silent (caller may skip the LLM entirely)
    #   speak=T, gesture=F → unusual; treated as full speak for now
    # When speak is False the generator strips speech beats from any
    # response. inner_monologue / hand_strategy are still produced — they
    # are debug-only fields, not visible table chat.
    should_speak: bool = True
    should_gesture: bool = True

    # Anti-repetition memory — the player's own recent beats from prior
    # turns. Speech and action gestures are tracked in separate ring
    # buffers and surfaced to the LLM as distinct "vary these" blocks.
    # Without action history the same tic (*shrugs*, *taps chips*) loops
    # several times per hand for reserved characters.
    recent_own_speech_beats: List[str] = field(default_factory=list)
    recent_own_action_beats: List[str] = field(default_factory=list)

    # Direct callouts — opponent chat that mentioned this player by name.
    # Formatted as `Sender said: "content"`. Surfaced as a [CALLED OUT]
    # prompt block so the LLM can react instead of burying it under
    # recent_actions.
    callouts: List[str] = field(default_factory=list)

    # Phase 7.6 Step 5: per-decision strategy reads from the
    # intervention trace, mapped via narration_facts adapter. When
    # present and non-empty, ExpressionGenerator appends a
    # "WHAT YOU NOTICED / WHAT YOU DECIDED" block to the prompt so the
    # LLM narration is grounded in the bot's actual reads.
    #
    # Optional and defaults to None — pre-7.6 callers / hybrid path
    # produce identical prompts to before.
    narration_facts: Optional['NarrationFacts'] = None

    # Up to N (opponent_name, observation_text) pairs from prior hands'
    # narrative observations, selected by relevance via
    # `OpponentModelManager.select_opponent_observations`. When non-empty,
    # ExpressionGenerator renders a "Your reads on opponents" block in
    # the user prompt so the narration can riff on accumulated reads
    # (or ignore them — they're extra info, not directives).
    #
    # Optional and defaults to empty list — pre-existing callers /
    # tests render identical prompts when no observations exist.
    opponent_observations: List[Tuple[str, str]] = field(default_factory=list)

    # Pre-formatted relationship-context block (rival/friendly labels +
    # most-recent memorable hands per qualifying opponent). Built by
    # `poker/memory/relationship_prompt.py:build_relationship_context`
    # so the chaos/standard/sharp paths frame the same situation the
    # same way. Empty default preserves baseline prompt for pre-existing
    # callers; gating lives at the controller (prompt_config.relationship_context),
    # not here.
    relationship_context: str = ''

    # Pre-formatted "about the human player" block (the human's own
    # self-description). Built by the controller so the sharp/tiered path can
    # needle the human about it the same way chaos/standard see it in their
    # decision prompts. Empty default preserves the baseline prompt.
    human_bio: str = ''
