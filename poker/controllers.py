import json
import logging
import random
from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from core.card import Card, CardRenderer

if TYPE_CHECKING:
    from .narration_gate import NarrationGate

from .ai_resilience import (
    AIFallbackStrategy,
    AIResponseError,
    DecisionErrorType,
    FallbackActionSelector,
    classify_response_error,
    describe_response_error,
    parse_json_response,
    validate_ai_response,
)
from .card_utils import card_to_string, normalize_card_string
from .chattiness_manager import ChattinessManager
from .config import (
    BIG_POT_THRESHOLD,
    MEMORY_CONTEXT_TOKENS,
    MIN_RAISE,
    OPPONENT_SUMMARY_TOKENS,
    is_development_mode,
)
from .decision_analyzer import calculate_max_winnable
from .hand_narrator import narrate_hand_breakdown
from .hand_ranges import (
    EquityConfig,
    build_opponent_info,
    calculate_equity_vs_ranges,
    format_opponent_stats,
)
from .memory.commentary_generator import DecisionPlan
from .moment_analyzer import MomentAnalyzer
from .player_psychology import (
    PlayerPsychology,
    ZoneContext,
    build_playstyle_briefing,
)
from .poker_game import Player
from .poker_player import AIPokerPlayer
from .poker_state_machine import PokerStateMachine
from .prompt_config import PromptConfig
from .prompt_manager import PromptManager
from .range_guidance import classify_preflop_hand_for_player
from .response_validator import ResponseValidator
from .utils import prepare_ui_data

logger = logging.getLogger(__name__)


def _serialize_intervention_trace(traces, *, player_name: str) -> Optional[str]:
    """Phase 7.6 (Step 3b): JSON-encode a list of InterventionTrace
    objects for persistence in `player_decision_analysis.intervention_
    trace_json`.

    Returns None when `traces` is falsy (controller doesn't expose a
    trace accumulator, or the list is empty). Any error during
    serialization is logged at WARN and returns None — gameplay must
    continue past trace failures (Codex r3 risk #12: "Trace
    persistence failure must not block gameplay").

    The on-disk payload is a JSON array of trace dicts produced by
    `trace_to_json_dict`. Schema: see TRACE_SCHEMA_VERSION in
    poker/strategy/intervention_trace.py.
    """
    if not traces:
        return None
    try:
        from .strategy.intervention_trace import trace_to_json_dict

        return json.dumps([trace_to_json_dict(t) for t in traces])
    except Exception as e:  # noqa: BLE001 — observability degradation by design
        logger.warning(
            f"[INTERVENTION_TRACE] {player_name}: failed to serialize "
            f"{len(traces) if hasattr(traces, '__len__') else '?'} trace(s): {e}"
        )
        return None


def _serialize_pipeline_snapshot(snapshot, *, player_name: str) -> Optional[str]:
    """Phase 7.6 (Step 6): JSON-encode the strategy pipeline snapshot
    for Mode 1 (shadow-eval) replay.

    Returns None when `snapshot` is falsy. Any error during
    serialization is logged at WARN and returns None — Mode 1 simply
    skips decisions that lack a usable snapshot.
    """
    if not snapshot:
        return None
    try:
        from .strategy.intervention_trace import _safe_serialize

        # _safe_serialize handles enums, dataclasses, non-finite floats.
        return json.dumps(_safe_serialize(snapshot))
    except Exception as e:  # noqa: BLE001 — observability degradation
        logger.warning(f"[PIPELINE_SNAPSHOT] {player_name}: failed to serialize " f"snapshot: {e}")
        return None


# =============================================================================
# Functional Helpers for Message Parsing
# =============================================================================

# Aggression priority for postflop actions (higher = more aggressive)
AGGRESSION_PRIORITY = {
    'raise': 4,
    'bet': 3,
    'check_call': 2,
    'check': 1,
}

# Raise-level helpers live in raise_utils.py so other modules can import
# them without pulling in the full controllers surface (avoids the lazy
# absolute import workaround in bounded_options).
from .raise_utils import RAISE_LEVEL_ACTIONS, _classify_raise_action  # noqa: E402,F401


def _parse_game_messages(game_messages) -> Optional[List[str]]:
    """Parse game messages into a list of non-empty string lines."""
    if isinstance(game_messages, str):
        return [line for line in game_messages.strip().split('\n') if line]
    elif isinstance(game_messages, list):
        return [line for line in game_messages if line and isinstance(line, str)]
    return None


def _get_preflop_lines(lines: List[str]) -> List[str]:
    """Extract lines from preflop phase only, stopping at postflop indicators."""
    postflop_indicators = ('flop', 'turn', 'river', 'community')
    result = []
    for line in lines:
        line_lower = line.lower()
        if any(indicator in line_lower for indicator in postflop_indicators):
            break
        result.append(line)
    return result


def _get_street_lines(lines: List[str], current_phase: str) -> List[str]:
    """Extract lines from the specified street phase only."""
    street_markers = {'FLOP': 'flop', 'TURN': 'turn', 'RIVER': 'river'}
    current_marker = street_markers.get(current_phase, '').lower()
    if not current_marker:
        return []

    next_streets = [s for s in ('turn', 'river', 'showdown') if s != current_marker]
    in_current_street = False
    result = []

    for line in lines:
        line_lower = line.lower()

        # Check for current street marker
        if current_marker in line_lower:
            in_current_street = True
            continue

        # Check for next street marker (stop processing)
        if in_current_street and any(s in line_lower for s in next_streets):
            break

        if in_current_street:
            result.append(line)

    return result


def _is_raise_action(line_lower: str) -> bool:
    """Check if line contains a raise or bet (not big blind post)."""
    return 'raise' in line_lower or ('bet' in line_lower and 'big blind' not in line_lower)


def _is_allin_action(line_lower: str) -> bool:
    """Check if line contains an all-in action."""
    return 'all' in line_lower and 'in' in line_lower


def _process_preflop_lines(
    lines: List[str], opponent_lower: str, opponent_name: str, bb_player: Optional[str]
) -> Optional[str]:
    """
    Process preflop lines and extract opponent's action.

    Returns the opponent's preflop action or None if they folded/not determinable.
    """
    raise_count = 0
    opponent_action = None

    for line in lines:
        line_lower = line.lower()

        # Check if this line is about our opponent
        if opponent_lower not in line_lower:
            # Track raises by others to count raise level
            if _is_raise_action(line_lower):
                raise_count += 1
            continue

        # This line is about our opponent - determine their action
        if 'fold' in line_lower:
            return None  # Folded preflop - not in hand

        if 'raise' in line_lower:
            opponent_action = _classify_raise_action(raise_count)
            raise_count += 1
        elif 'call' in line_lower:
            if raise_count == 0:
                # Called with no raise = limp (unless they're BB)
                opponent_action = None if opponent_name == bb_player else 'limp'
            else:
                opponent_action = 'call'
        elif 'check' in line_lower:
            # Check preflop only happens in BB with no raise
            pass
        elif _is_allin_action(line_lower):
            # All-in preflop - treat as raise
            opponent_action = _classify_raise_action(raise_count)

    return opponent_action


def _classify_aggression(line_lower: str) -> Optional[str]:
    """Classify a single line's aggression level."""
    if 'raise' in line_lower or _is_allin_action(line_lower):
        return 'raise'
    elif 'bet' in line_lower:
        return 'bet'
    elif 'call' in line_lower:
        return 'check_call'
    elif 'check' in line_lower:
        return 'check'
    return None


# =============================================================================
# Hand Evaluation
# =============================================================================


def _format_money(amount: int, big_blind: int, as_bb: bool) -> str:
    """Format money as dollars or BB based on mode.

    Args:
        amount: Dollar amount
        big_blind: Big blind size in dollars
        as_bb: If True, format as BB; if False, format as dollars

    Returns:
        Formatted string like "$500" or "10.00 BB"
    """
    if not as_bb:
        return f"${amount}"

    if big_blind == 0:
        return f"${amount}"  # Fallback if BB not set

    bb_value = amount / big_blind
    return f"{bb_value:.2f} BB"


def _convert_messages_to_bb(messages: str, big_blind: int) -> str:
    """Convert dollar amounts in messages to BB format for AI prompts."""
    if big_blind == 0:
        return messages  # Fallback if BB not set

    import re

    def replace_dollar(match):
        amount = int(match.group(1))
        bb_value = amount / big_blind
        return f"{bb_value:.2f} BB"

    return re.sub(r'\$(\d+)', replace_dollar, messages)


def evaluate_hand_strength(hole_cards: List[str], community_cards: List[str]) -> Optional[str]:
    """
    Evaluate hand strength and return a human-readable description.

    Returns None if eval7 is not available or cards are insufficient.
    """
    if not community_cards:  # Pre-flop - no hand to evaluate
        return None

    try:
        import eval7

        # Convert cards
        hand = [eval7.Card(normalize_card_string(c)) for c in hole_cards]
        board = [eval7.Card(normalize_card_string(c)) for c in community_cards]

        # Evaluate
        score = eval7.evaluate(hand + board)
        hand_type = eval7.handtype(score)

        # Map to clearer descriptions
        strength_map = {
            'High Card': ('High Card', 'Weak - only high card'),
            'Pair': ('One Pair', 'Marginal'),
            'Two Pair': ('Two Pair', 'Strong'),
            'Trips': ('Three of a Kind', 'Very Strong'),
            'Straight': ('Straight', 'Very Strong'),
            'Flush': ('Flush', 'Very Strong'),
            'Full House': ('Full House', 'Monster'),
            'Quads': ('Four of a Kind', 'Monster'),
            'Straight Flush': ('Straight Flush', 'Nuts'),
        }

        name, assessment = strength_map.get(hand_type, (hand_type, 'Unknown'))
        return f"{name} - {assessment}"

    except ImportError:
        return None
    except Exception as e:
        logger.debug(f"Hand evaluation failed: {e}")
        return None


def calculate_quick_equity(
    hole_cards: List[str],
    community_cards: List[str],
    num_simulations: int = 64,
    num_opponents: int = 1,
) -> Optional[float]:
    """
    Calculate quick equity estimate against random opponent hands.

    Uses Monte Carlo simulation with eval7. Returns equity as 0.0-1.0.
    Default 64 sims (~6-10ms): plenty for the COARSE consumers this feeds —
    rule-bot hand-strength buckets (premium/strong/medium/weak/air) and the
    GTO-equity coaching number. 300 was overkill (~5x slower) for bucketed use;
    validated that CaseBotV2's results are unchanged at 64. Bump back up only if
    a consumer needs fine equity resolution. NB tiered (solver) bots do NOT use
    this for decisions — they classify made_tier deterministically; this MC is
    decision-critical only for rule bots, and analysis/telemetry elsewhere.

    Args:
        hole_cards: Hero's hole cards
        community_cards: Community cards on board
        num_simulations: Number of Monte Carlo iterations
        num_opponents: Number of opponents to simulate (important for multi-way pots)
    """
    if not community_cards:
        return None

    try:
        import eval7

        hand = [eval7.Card(normalize_card_string(c)) for c in hole_cards]
        board_cards = [eval7.Card(normalize_card_string(c)) for c in community_cards]

        wins = 0
        for _ in range(num_simulations):
            deck = eval7.Deck()
            for c in hand + board_cards:
                deck.cards.remove(c)
            deck.shuffle()

            # Deal cards to all opponents
            opponent_hands = []
            for _ in range(num_opponents):
                opp_hand = list(deck.deal(2))
                opponent_hands.append(opp_hand)

            # Complete board if needed (flop/turn)
            remaining = 5 - len(board_cards)
            full_board = board_cards + list(deck.deal(remaining))

            hero_score = eval7.evaluate(hand + full_board)

            # Must beat ALL opponents to win
            hero_wins = True
            hero_ties = True
            for opp_hand in opponent_hands:
                opp_score = eval7.evaluate(opp_hand + full_board)
                if opp_score > hero_score:
                    hero_wins = False
                    hero_ties = False
                    break
                elif opp_score < hero_score:
                    hero_ties = False

            if hero_wins and not hero_ties:
                wins += 1
            elif hero_wins and hero_ties:
                # Tie with all opponents - split pot
                wins += 0.5

        return wins / num_simulations

    except ImportError:
        return None
    except Exception as e:
        logger.debug(f"Equity calculation failed: {e}")
        return None


# Preflop hand rankings - neutral/informational only
from poker.hand_tiers import PREMIUM_HANDS, TOP_10_HANDS, TOP_20_HANDS, TOP_35_HANDS  # noqa: E402


def _get_canonical_hand(hole_cards: List[str]) -> str:
    """Convert hole cards to canonical notation (e.g., 'AKs', 'QQ', 'T9o')."""
    if len(hole_cards) != 2:
        return ''

    # Normalize cards
    c1 = normalize_card_string(hole_cards[0])
    c2 = normalize_card_string(hole_cards[1])

    # Extract rank and suit
    rank1, suit1 = c1[0], c1[1] if len(c1) > 1 else ''
    rank2, suit2 = c2[0], c2[1] if len(c2) > 1 else ''

    # Rank order for comparison
    rank_order = '23456789TJQKA'
    idx1 = rank_order.index(rank1) if rank1 in rank_order else -1
    idx2 = rank_order.index(rank2) if rank2 in rank_order else -1

    # Order by rank (higher first)
    if idx1 < idx2:
        rank1, rank2 = rank2, rank1
        suit1, suit2 = suit2, suit1

    # Build canonical notation
    if rank1 == rank2:
        return f"{rank1}{rank2}"  # Pair
    elif suit1 == suit2:
        return f"{rank1}{rank2}s"  # Suited
    else:
        return f"{rank1}{rank2}o"  # Offsuit


def _get_hand_category(canonical: str) -> str:
    """Get descriptive category for a hand."""
    if len(canonical) == 2:  # Pair
        rank = canonical[0]
        if rank in 'AKQJ':
            return "High pocket pair"
        elif rank in 'T987':
            return "Medium pocket pair"
        else:
            return "Low pocket pair"

    rank1, rank2 = canonical[0], canonical[1]
    suited = canonical.endswith('s')

    # Broadway cards (T+)
    broadway = 'AKQJT'
    if rank1 in broadway and rank2 in broadway:
        return "Suited broadway" if suited else "Offsuit broadway"

    # Ace-x hands
    if rank1 == 'A':
        return "Suited ace" if suited else "Offsuit ace"

    # Connectors/gappers
    rank_order = '23456789TJQKA'
    idx1 = rank_order.index(rank1) if rank1 in rank_order else -1
    idx2 = rank_order.index(rank2) if rank2 in rank_order else -1
    gap = abs(idx1 - idx2)

    if gap == 1:
        return "Suited connector" if suited else "Offsuit connector"
    elif gap <= 3 and suited:
        return "Suited gapper"

    # Default
    if suited:
        return "Suited cards"
    else:
        return "Unconnected cards"


def _get_hand_percentile(canonical: str) -> str:
    """Get percentile ranking for a hand."""
    if canonical in PREMIUM_HANDS:
        return "Top 3% of starting hands"
    elif canonical in TOP_10_HANDS:
        return "Top 10% of starting hands"
    elif canonical in TOP_20_HANDS:
        return "Top 20% of starting hands"
    elif canonical in TOP_35_HANDS:
        return "Top 35% of starting hands"
    else:
        # Check for weak hands
        rank1 = canonical[0] if canonical else ''
        rank2 = canonical[1] if len(canonical) > 1 else ''
        low_ranks = '23456'

        if rank1 in low_ranks and rank2 in low_ranks:
            return "Bottom 10% of starting hands"
        elif rank1 in '789' and rank2 in low_ranks:
            return "Bottom 25% of starting hands"
        else:
            return "Below average starting hand"


def classify_preflop_hand(hole_cards: List[str]) -> Optional[str]:
    """
    Classify preflop hand strength - neutral/informational only.

    Returns a factual description without prescriptive action advice,
    preserving AI personality-driven decision making.
    """
    try:
        canonical = _get_canonical_hand(hole_cards)
        if not canonical:
            return None

        category = _get_hand_category(canonical)
        percentile = _get_hand_percentile(canonical)

        return f"{canonical} - {category}, {percentile}"
    except Exception as e:
        logger.debug(f"Preflop classification failed: {e}")
        return None


def classify_preflop_hand_with_range(
    hole_cards: List[str],
    psychology: 'PlayerPsychology',
    game_position: str,
    num_opponents: int = None,
) -> Optional[str]:
    """Range-aware preflop classification using player's effective looseness.

    Falls back to generic classify_preflop_hand() on any error.
    """
    try:
        canonical = _get_canonical_hand(hole_cards)
        if not canonical:
            return None
        result = classify_preflop_hand_for_player(
            canonical,
            psychology.effective_looseness,
            game_position,
            num_opponents=num_opponents,
        )
        return result if result else classify_preflop_hand(hole_cards)
    except Exception as e:
        logger.debug(f"Range-aware preflop classification failed: {e}")
        return classify_preflop_hand(hole_cards)


class ConsolePlayerController:
    def __init__(self, player_name, state_machine: PokerStateMachine = None):
        self.player_name = player_name
        self.state_machine = state_machine

    def decide_action(self) -> Dict:
        ui_data, player_options = prepare_ui_data(self.state_machine.game_state)
        display_player_turn_update(ui_data, player_options)
        return human_player_action(ui_data, player_options)


def summarize_messages(messages: List[Dict[str, str]], name: str) -> str:
    """
    Summarize messages since the player's last message, with clear separation
    between previous hand and current hand actions.
    """
    # Find the player's last message
    last_message_index = -1

    for i, msg in enumerate(messages):
        if msg['sender'] == name:
            last_message_index = i

    # Convert a single message to string
    def format_message(msg):
        sender = msg['sender']
        content = msg.get('content', msg.get('message', ''))
        action = msg.get('action', '')

        # Skip the raw "NEW HAND DEALT" system message - we'll add our own separator
        if 'NEW HAND DEALT' in content:
            return None

        if action and content:
            return f"  {sender} {action}: \"{content}\""
        elif action:
            return f"  {sender} {action}"
        else:
            # Chat or system message
            return f"  {content}" if sender == 'Table' else f"  {sender}: \"{content}\""

    # Determine which messages to include (since player's last message)
    start_idx = last_message_index if last_message_index >= 0 else 0
    relevant_messages = messages[start_idx:]

    # Split into previous hand and current hand
    previous_hand = []
    current_hand = []

    for msg in relevant_messages:
        content = msg.get('content', msg.get('message', ''))
        if 'NEW HAND DEALT' in content:
            # Everything after this is current hand
            previous_hand = current_hand
            current_hand = []
        else:
            formatted = format_message(msg)
            if formatted:
                current_hand.append(formatted)

    # Build output
    parts = []

    if previous_hand:
        parts.append("Previous hand:")
        parts.extend(previous_hand)
        parts.append("")

    parts.append("This hand:")
    if current_hand:
        parts.extend(current_hand)
    else:
        parts.append("  (No actions yet)")

    return "\n".join(parts)


class AIPlayerController:
    # Whether this controller's decision path consumes the LLM emotional
    # narration (narrative / inner_voice) — it injects it into the decision
    # prompt via psychology.get_prompt_section(). True for the LLM table-talk
    # controllers (chaos base + hybrid). Solver/rule controllers override this
    # to False: they never read the prose, so generating it for them at a full
    # table is pure waste. The post-hand pipeline uses this to gate the
    # (now async) narration call — see PsychologyPipeline._update_composure.
    USES_EMOTIONAL_NARRATION = True

    # Whether this controller persists its own player_decision_analysis row
    # (with the fresh, in-call pipeline snapshot + intervention trace) from
    # inside its decision path. True for every LLM/solver controller whose
    # `_get_ai_decision` calls `_analyze_decision` (chaos base, hybrid/lean,
    # tiered). RuleBot overrides this to False — it never self-saves and
    # relies on the handler-level analyzer instead. The handler uses this flag
    # to decide whether to skip writing a (snapshot-less) duplicate row, which
    # replaces the old fragile in-memory `_last_analyzed_decision` handshake.
    WRITES_OWN_DECISION_ANALYSIS = True

    def __init__(
        self,
        player_name,
        state_machine=None,
        llm_config=None,
        session_memory=None,
        opponent_model_manager=None,
        game_id=None,
        owner_id=None,
        debug_capture=False,
        capture_label_repo=None,
        decision_analysis_repo=None,
        prompt_config=None,
    ):
        self.player_name = player_name
        self.state_machine = state_machine
        self.llm_config = llm_config or {}
        self.game_id = game_id
        self.owner_id = owner_id
        # The human player's free-text self-description ("about me"), if they
        # wrote one. Runtime context (not config) set per-decision by the game
        # handler so the AI can trash-talk / comment on it. Empty = no bio.
        self.human_bio = ""
        # The human's room-level reputation tone hint (hook 3 of the prestige
        # system), set per-decision by the game handler. A one-line nudge to how
        # the AI's table talk should address the human, keyed to their
        # reputation quadrant. Empty = no hook (low-renown / tournament). Flavor
        # only — surfaced to the narration prompt, never action selection.
        self.human_reputation_tone = ""
        self._capture_label_repo = capture_label_repo
        self._decision_analysis_repo = decision_analysis_repo
        self.ai_player = AIPokerPlayer(
            player_name, llm_config=self.llm_config, game_id=game_id, owner_id=owner_id
        )
        self.assistant = self.ai_player.assistant
        self.prompt_manager = PromptManager(enable_hot_reload=is_development_mode())
        self.chattiness_manager = ChattinessManager()
        self.response_validator = ResponseValidator()

        # Anti-repetition memory — sliding windows of the player's own
        # recent beats from prior turns, injected into the next prompt so
        # the LLM doesn't recycle the same lines. Speech and action beats
        # are tracked separately so each can be presented to the LLM as
        # its own "vary these" block — action tics like *shrugs* recur
        # just as often as speech lines when not gated.
        self._recent_own_speech_beats: deque = deque(maxlen=5)
        self._recent_own_action_beats: deque = deque(maxlen=5)

        # Prompt configuration (controls which components are included)
        self.prompt_config = prompt_config or PromptConfig()

        # Unified psychological state
        self.psychology = PlayerPsychology.from_personality_config(
            name=player_name,
            config=self.ai_player.personality_config,
            game_id=game_id,
            owner_id=owner_id,
        )

        # Memory systems (optional - set by memory manager)
        self.session_memory = session_memory
        self.opponent_model_manager = opponent_model_manager
        # Optional back-reference to the full AIMemoryManager (set by
        # production attachment paths). Used by Phase 6.6 HU c-bet
        # exploitation to read MemoryManager.last_preflop_aggressor.
        # Simulators that bypass MemoryManager can set
        # _sim_last_preflop_aggressor / _sim_recent_aggressor directly.
        self.memory_manager = None
        self._sim_last_preflop_aggressor: Optional[str] = None
        # Phase 6.7a: per-street live aggressor (postflop) for sim paths
        # that bypass MemoryManager. Production reads
        # memory_manager.recent_aggressor_name instead.
        self._sim_recent_aggressor: Optional[str] = None

        # Hand number tracking (set by memory manager)
        self.current_hand_number = None

        # Decision plans for current hand (captured during decide_action)
        self._current_hand_plans: List[DecisionPlan] = []

        # Max self-reported bluff_likelihood across all actions this hand (0-100)
        self._hand_max_bluff_likelihood: int = 0

    def get_current_personality_traits(self):
        """Get current trait values from psychology (elastic personality)."""
        return self.psychology.traits

    def remember_own_beats(self, beats) -> None:
        """Record this turn's beats for next turn's anti-repetition prompt.

        Routes asterisk-wrapped action beats (`*shrugs*`, `*taps chips*`)
        to a separate ring buffer from spoken lines. Both are surfaced to
        the LLM next turn so it varies tics as well as phrasing — quiet
        characters were looping the same gesture every turn. Very short
        mechanical speech utterances ("Call.", "Fold.") are still
        filtered since they aren't worth varying.
        """
        if not beats or not isinstance(beats, list):
            return
        for b in beats:
            if not isinstance(b, str):
                continue
            text = b.strip()
            if not text:
                continue
            is_action = text.startswith('*') and text.endswith('*')
            if is_action:
                self._recent_own_action_beats.append(text)
            else:
                # Skip very short mechanical utterances ("Call.", "Fold.")
                if len(text.split()) <= 2:
                    continue
                self._recent_own_speech_beats.append(text)

    def recent_own_speech_beats(self) -> List[str]:
        """Return a copy of the speech-beat ring buffer for prompt injection."""
        return list(self._recent_own_speech_beats)

    def recent_own_action_beats(self) -> List[str]:
        """Return a copy of the action-beat ring buffer for prompt injection."""
        return list(self._recent_own_action_beats)

    def find_callouts(self, messages, max_results: int = 3) -> List[str]:
        """Find recent opponent chat that DIRECTLY addressed this player.

        Authoritative signal: the speaker LLM declares its targets via an
        `addressing: List[str]` field, attached to broadcast messages as
        msg['addressing']. When that key is present we trust it — empty
        list means "general table talk, no direct callout."

        Legacy fallback (substring scan) only fires when the addressing
        key is ABSENT from the message — old responses that predate the
        field. Once the rollout has propagated through stored history,
        the fallback path is essentially dead.

        Returns formatted lines like `Bob said: "Your move, Alice."` for
        the LAST `max_results` callouts.
        """
        if not messages or not isinstance(messages, list):
            return []
        name = self.player_name or ''
        if not name:
            return []
        name_lower = name.lower()
        out: List[str] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            sender = msg.get('sender', '') or ''
            if not sender or sender == name or sender in ('Table', 'System'):
                continue
            if msg.get('message_type') not in ('ai', 'user'):
                continue
            content = msg.get('content', '') or msg.get('message', '') or ''
            if not content:
                continue

            if 'addressing' in msg:
                # Trust the explicit signal — no substring fallback when
                # the speaker declared their intent.
                addressing = msg.get('addressing') or []
                if not isinstance(addressing, list):
                    continue
                if name in addressing:
                    out.append(f'{sender} said: "{content.strip()}"')
            else:
                # Legacy substring fallback for messages that predate
                # the addressing field.
                if name_lower in content.lower():
                    out.append(f'{sender} said: "{content.strip()}"')
        return out[-max_results:]

    def _get_cleanup_client(self):
        """Lazy fast-tier LLM client for dramatic_sequence beat cleanup.

        Built on first use so callers that never produce malformed beats
        never pay the cost of instantiating a second client. Falls back
        to the decision-time client if the fast-tier config can't be
        loaded.
        """
        client = getattr(self, '_cleanup_client_cache', None)
        if client is not None:
            return client
        try:
            from core.llm import LLMClient
            from core.llm.config import FAST_LLM_TIMEOUT_SECONDS
            from core.llm.settings import get_nano_model, get_nano_provider

            client = LLMClient(
                provider=get_nano_provider(),
                model=get_nano_model(),
                # NANO tier: cosmetic beat-repair is mechanical and never read —
                # cheapest/fastest model. minimal reasoning + a bounded timeout so it
                # never hangs the AI turn it runs (synchronously) inside.
                reasoning_effort="minimal",
                default_timeout=FAST_LLM_TIMEOUT_SECONDS,
            )
        except Exception as e:
            logger.warning(f"[CLEANUP_CLIENT] Falling back to decision client: {e}")
            client = getattr(self.assistant, '_client', None) or self.assistant
        self._cleanup_client_cache = client
        return client

    def compute_narration_gate(
        self,
        game_state,
        drama_level: str = 'routine',
        game_messages=None,
    ) -> 'NarrationGate':
        """Roll the per-turn speech + gesture gates for this bot.

        Single source of truth for "when does the bot talk / react?" so
        hybrid (full-prompt path) and tiered (expression layer) share
        identical behavior. Both axes can fail silently and default to
        permissive (True) — better to occasionally over-narrate than mute
        a bot due to an unrelated error.

        Args:
            game_state: current PokerGameState — drives situational
                modifiers (big_pot, all_in, heads_up, etc.).
            drama_level: 'routine' | 'notable' | 'high_stakes' | 'climactic'
                from MomentAnalyzer. Boosts the gesture roll on big spots
                so even reserved characters react physically when stakes
                spike. Defaults to 'routine' for callers that don't track
                drama yet.
            game_messages: optional raw message list — currently unused by
                _build_game_context but passed through for forward-compat
                (address-detection, long-silence modifiers).

        Returns:
            NarrationGate with should_speak / should_gesture booleans.
        """
        from .narration_gate import NarrationGate

        # Speech roll — chattiness trait × situation
        try:
            traits = self.get_current_personality_traits() or {}
            chattiness = float(traits.get('table_talk', traits.get('chattiness', 0.5)))
        except Exception:
            chattiness = 0.5

        try:
            ctx = self._build_game_context(game_state, game_messages) or {}
        except Exception:
            ctx = {}
        if drama_level == 'climactic':
            ctx['big_pot'] = True

        try:
            should_speak = bool(
                self.chattiness_manager.should_speak(
                    self.player_name,
                    chattiness,
                    ctx,
                )
            )
        except Exception as e:
            logger.debug(f"[NARRATION] {self.player_name}: speech roll failed safely: {e}")
            should_speak = True

        # Gesture roll — psychology.energy × drama-level boost. Independent
        # of speech so a silent character can still slam chips when the
        # pot blows up. Floor lowered so reserved characters are actually
        # reserved — the prior 40% floor meant a poker_face personality
        # still gestured nearly every turn, which gated a separate LLM
        # call on the tiered-bot path.
        #   energy 0.0 → 15%
        #   energy 0.5 → 33%
        #   energy 1.0 → 65%
        # Plus +30% on climactic moments, +15% on high-stakes.
        try:
            psy = getattr(self, 'psychology', None)
            energy = float(getattr(psy, 'energy', 0.5)) if psy else 0.5
            probability = 0.15 + (energy**1.5) * 0.50
            if drama_level == 'climactic':
                probability += 0.30
            elif drama_level == 'high_stakes':
                probability += 0.15
            probability = max(0.0, min(1.0, probability))
            should_gesture = random.random() < probability
        except Exception as e:
            logger.debug(f"[NARRATION] {self.player_name}: gesture roll failed safely: {e}")
            should_gesture = True

        logger.debug(
            f"[NARRATION] {self.player_name}: "
            f"speak={should_speak} gesture={should_gesture} drama={drama_level}"
        )

        return NarrationGate(
            should_speak=should_speak,
            should_gesture=should_gesture,
        )

    @property
    def personality_traits(self):
        """Compatibility property for ai_resilience fallback."""
        return self.psychology.traits

    def set_prompt_component(self, component: str, enabled: bool) -> None:
        """
        Toggle a specific prompt component on/off.

        Args:
            component: Name of the component (e.g., 'mind_games', 'pot_odds')
            enabled: Whether the component should be enabled
        """
        if not hasattr(self.prompt_config, component):
            logger.error(f"Unknown prompt component: {component}")
            return
        setattr(self.prompt_config, component, enabled)
        logger.info(f"Prompt component '{component}' set to {enabled} for {self.player_name}")

    def get_decision_plans(self) -> List[DecisionPlan]:
        """Get all decision plans captured for the current hand."""
        return self._current_hand_plans.copy()

    def clear_decision_plans(self) -> List[DecisionPlan]:
        """Clear and return decision plans for the current hand.

        Called at end of hand to pass plans to commentary, then reset for next hand.
        """
        plans = self._current_hand_plans.copy()
        self._current_hand_plans = []
        return plans

    def get_hand_bluff_likelihood(self) -> int:
        """Get the max self-reported bluff_likelihood for this hand (0-100)."""
        return self._hand_max_bluff_likelihood

    def clear_hand_bluff_likelihood(self):
        """Reset bluff tracking for next hand."""
        self._hand_max_bluff_likelihood = 0

    def decide_action(self, game_messages) -> Dict:
        game_state = self.state_machine.game_state

        # Store original messages for action extraction in _analyze_decision
        self._current_game_messages = game_messages

        # Manage conversation memory based on prompt_config setting
        # Table chatter is preserved via game_messages -> Recent Actions
        # Mental state is preserved via PlayerPsychology (separate system)
        if hasattr(self, 'assistant') and self.assistant and self.assistant.memory:
            keep_exchanges = getattr(self.prompt_config, 'memory_keep_exchanges', 0)
            if keep_exchanges > 0:
                # Keep last N exchanges (user-assistant pairs)
                self.assistant.memory.trim_to_exchanges(keep_exchanges)
            else:
                # Clear all memory (default behavior)
                self.assistant.memory.clear()

        # Save original messages before summarizing (for address detection)
        original_messages = game_messages

        game_messages = summarize_messages(game_messages, self.player_name)

        # Always convert messages to BB format
        big_blind = game_state.current_ante or 100
        game_messages = _convert_messages_to_bb(game_messages, big_blind)

        # Narration gate — shared with tiered/sharp path via
        # compute_narration_gate so "when to speak" behavior stays
        # identical across bot types. table_talk is still resolved
        # locally so _build_chattiness_guidance can show its level.
        current_traits = self.get_current_personality_traits()
        table_talk = current_traits.get('table_talk', current_traits.get('chattiness', 0.5))
        gate = self.compute_narration_gate(
            game_state,
            game_messages=original_messages,
        )
        should_speak = gate.should_speak
        should_gesture = gate.should_gesture
        speaking_context = self.chattiness_manager.get_speaking_context(self.player_name)

        # Build message with game state — always BB-normalized, pot odds handled by YAML template
        message = build_base_game_state(
            game_state,
            game_state.current_player,
            self.state_machine.phase,
            game_messages,
            include_hand_strength=self.prompt_config.hand_strength,
            psychology=self.psychology,
            range_guidance=self.prompt_config.range_guidance,
            include_persona=self.prompt_config.include_personality,
        )

        # Get valid actions early so we can include in guidance
        player_options = game_state.current_player_options

        # Inject memory context if available (respecting prompt_config toggles)
        memory_context = self._build_memory_context(
            game_state,
            include_session=self.prompt_config.session_memory,
            include_opponents=self.prompt_config.opponent_intel,
        )
        if memory_context:
            message = memory_context + "\n\n" + message

        # Add chattiness guidance to message (if enabled)
        if self.prompt_config.chattiness:
            chattiness_guidance = self._build_chattiness_guidance(
                table_talk,
                should_speak,
                speaking_context,
                player_options,
                should_gesture=should_gesture,
            )
            message = message + "\n\n" + chattiness_guidance

        # Anti-repetition memory — list recent speech beats so the LLM
        # doesn't recycle the same lines. Skipped when buffer is empty.
        recent_beats = self.recent_own_speech_beats()
        if recent_beats:
            quoted = "\n".join(f'  - "{b}"' for b in recent_beats)
            message = message + (
                "\n\nYour recent SPEECH beats this session — vary your "
                "phrasing, do not repeat these lines:\n" + quoted
            )

        # Same anti-repetition pass for action beats (`*shrugs*`,
        # `*taps chips*`). Without this characters loop the same tic
        # several times per hand.
        recent_actions = self.recent_own_action_beats()
        if recent_actions:
            quoted = "\n".join(f'  - {b}' for b in recent_actions)
            message = message + (
                "\n\nYour recent ACTION beats this session — vary your "
                "gestures, do not repeat these tics:\n" + quoted
            )

        # Direct callouts — opponent chat that mentioned this player by
        # name. Surface explicitly so the LLM can react instead of
        # burying it in the message log. Prompt suggestion only.
        callouts = self.find_callouts(original_messages)
        if callouts:
            block = "\n".join(f"  - {c}" for c in callouts)
            message = message + (
                "\n\n[CALLED OUT] An opponent just mentioned you by name — "
                "consider reacting:\n" + block
            )

        # Inject emotional state context (before tilt effects, if enabled)
        if self.prompt_config.emotional_state:
            emotional_section = self.psychology.get_prompt_section()
            if emotional_section:
                message = emotional_section + "\n\n" + message

        # Apply tilt effects if player is tilted (after emotional state, if enabled)
        if self.prompt_config.tilt_effects:
            message = self.psychology.apply_tilt_effects(message)

        # Apply guidance injection (for experiments - extra instructions appended to prompt)
        if self.prompt_config.guidance_injection:
            message = (
                message + "\n\n" + "ADDITIONAL GUIDANCE:\n" + self.prompt_config.guidance_injection
            )

        logger.debug(f"[AI_DECISION] Prompt:\n{message}")

        # Context for fallback
        player_stack = game_state.current_player.stack
        raw_cost_to_call = game_state.highest_bet - game_state.current_player.bet
        # Effective cost is capped at player's stack (they can only risk what they have)
        cost_to_call = min(raw_cost_to_call, player_stack)

        # Calculate raise TO bounds for the AI prompt
        # max_raise_to: capped by largest opponent stack (no point raising beyond what they can match)
        highest_bet = game_state.highest_bet
        max_opponent_stack = max(
            (
                p.stack
                for p in game_state.players
                if not p.is_folded and not p.is_all_in and p.name != game_state.current_player.name
            ),
            default=0,
        )
        max_raise_by = min(player_stack, max_opponent_stack)
        max_raise_to = highest_bet + max_raise_by
        # min_raise_to: highest bet + minimum raise increment
        min_raise_by = min(game_state.min_raise_amount, max_raise_by) if max_raise_by > 0 else 0
        min_raise_to = highest_bet + min_raise_by

        # Use resilient AI call
        response_dict = self._get_ai_decision(
            message=message,
            valid_actions=player_options,
            call_amount=cost_to_call,
            min_raise=min_raise_to,
            max_raise=max_raise_to,
            should_speak=should_speak,
            big_blind=game_state.current_ante or 100,
        )

        # Clean response based on narration gate. should_gesture lets a
        # silent character keep *action* beats when not speaking; both
        # False strips dramatic_sequence entirely.
        cleaned_response = self.response_validator.clean_response(
            response_dict, {'should_speak': should_speak, 'should_gesture': should_gesture}
        )

        # LLM-based beat cleanup — shared with the tiered/sharp path.
        # When the cheap heuristic flags malformed beats (mixed action+
        # speech, missing asterisks, quote-wrapped), a fast-tier model
        # repairs the format while preserving wording. Silent on failure.
        seq = cleaned_response.get('dramatic_sequence')
        if seq:
            from .response_validator import (
                llm_normalize_beats,
                needs_llm_normalization,
            )

            if needs_llm_normalization(seq):
                cleaned_response['dramatic_sequence'] = llm_normalize_beats(
                    seq,
                    self._get_cleanup_client(),
                    game_id=self.game_id,
                    player_name=self.player_name,
                    owner_id=self.owner_id,
                )

        # Record this turn's speech beats for next turn's anti-repetition
        # prompt. Pure actions and short utterances are filtered inside
        # remember_own_beats.
        self.remember_own_beats(cleaned_response.get('dramatic_sequence'))

        # Capture DecisionPlan for reflection system (if enabled)
        if self.prompt_config.strategic_reflection:
            # Convert PokerPhase enum to string for JSON serialization
            phase_str = (
                self.state_machine.phase.name
                if hasattr(self.state_machine.phase, 'name')
                else str(self.state_machine.phase)
            )
            plan = DecisionPlan(
                hand_number=self.current_hand_number or 0,
                phase=phase_str,
                player_name=self.player_name,
                hand_strategy=response_dict.get('hand_strategy'),
                inner_monologue=response_dict.get('inner_monologue', ''),
                action=response_dict.get('action', ''),
                amount=response_dict.get('raise_to', 0),
                pot_size=game_state.pot.get('total', 0),
                timestamp=datetime.now(),
            )
            self._current_hand_plans.append(plan)

        logger.debug(f"[AI_DECISION] Response:\n{json.dumps(cleaned_response, indent=4)}")

        # Track max bluff_likelihood across all actions this hand
        try:
            bl = int(cleaned_response.get('bluff_likelihood', 0))
            self._hand_max_bluff_likelihood = max(self._hand_max_bluff_likelihood, bl)
        except (ValueError, TypeError):
            pass

        # Phase 2: Track action for consecutive fold detection (energy events)
        action = cleaned_response.get('action', '')
        self.last_energy_events = []
        if action and self.psychology:
            self.last_energy_events = self.psychology.on_action_taken(action)

        return cleaned_response

    def _get_ai_decision(self, message: str, **context) -> Dict:
        """Get AI decision with automatic error recovery and fallback.

        Recovery strategy:
        - MALFORMED_JSON: Full retry with same prompt
        - Semantic errors (missing fields, invalid action, raise=0): Targeted correction prompt
        - 1 recovery attempt, then fallback to personality-based action
        """
        from core.llm.tracking import update_prompt_capture

        # Store context for potential fallback
        self._fallback_context = context
        valid_actions = context.get('valid_actions', [])
        game_state = self.state_machine.game_state

        # Build the decision prompt with situational guidance
        decision_prompt, drama_context = self._build_decision_prompt(message, context)

        # Track captures for linking
        parent_capture_id = [None]
        final_capture_id = [None]
        capture_enrichment = [None]

        def make_enricher(
            parent_id=None, error_type=None, correction_attempt=0, drama_context=None
        ):
            """Create an enricher callback with resilience and drama context fields."""

            def enrich_capture(capture_data: Dict) -> Dict:
                player = game_state.current_player
                cost_to_call = context.get('call_amount', 0)
                pot_total = game_state.pot.get('total', 0)
                player_stack = player.stack
                already_bet = player.bet
                big_blind = game_state.current_ante or 100

                stack_bb = player_stack / big_blind if big_blind > 0 else None
                already_bet_bb = already_bet / big_blind if big_blind > 0 else None

                # Calculate effective pot odds for short-stack scenarios
                effective_pot_odds = None
                max_winnable = None
                if cost_to_call > 0:
                    all_players_bets = [(p.bet, p.is_folded) for p in game_state.players]
                    max_winnable = calculate_max_winnable(
                        player_bet=already_bet,
                        player_stack=player_stack,
                        cost_to_call=cost_to_call,
                        all_players_bets=all_players_bets,
                    )
                    effective_pot = min(max_winnable, pot_total)
                    effective_call = min(cost_to_call, player_stack)
                    effective_pot_odds = (
                        effective_pot / effective_call if effective_call > 0 else None
                    )

                enrichment = {
                    'phase': self.state_machine.current_phase.name
                    if self.state_machine.current_phase
                    else None,
                    'pot_total': pot_total,
                    'cost_to_call': cost_to_call,
                    'pot_odds': pot_total / cost_to_call if cost_to_call > 0 else None,
                    'effective_pot_odds': effective_pot_odds,
                    'max_winnable': max_winnable,
                    'player_stack': player_stack,
                    'stack_bb': round(stack_bb, 2) if stack_bb is not None else None,
                    'already_bet_bb': round(already_bet_bb, 2)
                    if already_bet_bb is not None
                    else None,
                    'community_cards': [str(c) for c in game_state.community_cards]
                    if game_state.community_cards
                    else [],
                    'player_hand': [str(c) for c in player.hand] if player.hand else [],
                    'valid_actions': valid_actions,
                    'prompt_config': self.prompt_config.to_dict() if self.prompt_config else None,
                    # Resilience fields
                    'parent_id': parent_id,
                    'error_type': error_type,
                    'correction_attempt': correction_attempt,
                    # Drama context for auto-labeling
                    'drama_context': drama_context,
                    '_on_captured': lambda cid: final_capture_id.__setitem__(0, cid),
                }
                capture_data.update(enrichment)
                capture_enrichment[0] = enrichment
                return capture_data

            return enrich_capture

        # ========== Personality toggle ==========
        original_system_message = None
        if not self.prompt_config.include_personality:
            original_system_message = self.assistant.system_message
            self.assistant.system_message = (
                "You are an expert poker player in a tournament. "
                "Make mathematically sound decisions based on hand strength, pot odds, position, and opponent tendencies.\n\n"
                "All amounts are in Big Blinds (BB).\n\n"
                "Respond with JSON only:\n"
                '{"action": "<fold|check|call|raise|all_in>", "raise_to": <BB amount if raising>}\n\n'
                "When the prompt tells you your hand strength and equity, trust those evaluations — "
                "they are calculated from actual card combinations, not estimates."
            )

        # ========== ATTEMPT 1: Initial AI call ==========
        response_dict = None
        error_type = None
        original_response_json = None
        # PRH-19: set when the first call failed at the transport layer (timeout /
        # connection / budget block, status=="error") — used to skip the recovery
        # LLM call (a second hit on the same down provider) and go to fallback.
        transport_failed = False

        try:
            try:
                llm_response = self.assistant.chat_full(
                    decision_prompt,
                    json_format=True,
                    hand_number=self.current_hand_number,
                    prompt_template='decision',
                    capture_enricher=make_enricher(drama_context=drama_context),
                )
                original_response_json = llm_response.content
                parent_capture_id[0] = final_capture_id[0]
                self._last_llm_response = llm_response

                # PRH-19: a transport-level failure returns status=="error" with
                # empty content. Parsing it would mislabel it as malformed JSON
                # AND trip the recovery branch into a SECOND chat_full against the
                # same down provider — doubling the hang for zero benefit. Detect
                # it and route straight to the deterministic fallback.
                if getattr(llm_response, 'status', 'ok') == 'error':
                    logger.warning(
                        f"[RESILIENCE] {self.player_name}: LLM transport error "
                        f"(code={getattr(llm_response, 'error_code', None)}) — "
                        f"skipping recovery, using fallback"
                    )
                    error_type = DecisionErrorType.MALFORMED_JSON
                    response_dict = {}
                    transport_failed = True
                else:
                    response_dict = parse_json_response(original_response_json)
                    response_dict = self._normalize_response(response_dict)

                    # Classify any errors
                    error_type = classify_response_error(response_dict, valid_actions)

            except AIResponseError as e:
                # JSON parse failure
                logger.warning(f"[RESILIENCE] {self.player_name}: Malformed JSON - {e}")
                error_type = DecisionErrorType.MALFORMED_JSON
                response_dict = {}

            except Exception as e:
                logger.error(f"[RESILIENCE] {self.player_name}: Unexpected error - {e}")
                error_type = DecisionErrorType.MALFORMED_JSON
                response_dict = {}

            # ========== ATTEMPT 2: Recovery if needed ==========
            if error_type is not None:
                logger.warning(
                    f"[RESILIENCE] {self.player_name}: Error detected ({error_type.value}), attempting recovery"
                )

                # Generate error description for logging and correction prompt
                if transport_failed:
                    error_description = (
                        "LLM transport error (timeout/connection/budget; "
                        f"code={getattr(self._last_llm_response, 'error_code', None)}); "
                        "used deterministic fallback."
                    )
                elif error_type == DecisionErrorType.MALFORMED_JSON:
                    error_description = (
                        "Could not parse JSON response. Please respond with valid JSON."
                    )
                else:
                    error_description = describe_response_error(
                        error_type, response_dict, valid_actions
                    )

                # Mark original capture with error
                if parent_capture_id[0]:
                    update_prompt_capture(
                        parent_capture_id[0],
                        error_type=error_type.value,
                        error_description=error_description,
                    )

                if transport_failed:
                    # PRH-19: the provider just failed at the transport layer —
                    # a recovery call would only hit the same down provider again
                    # (doubling the hang). Skip it; the deterministic fallback
                    # below handles the decision.
                    logger.info(
                        f"[RESILIENCE] {self.player_name}: transport error — "
                        f"deterministic fallback, no recovery LLM call"
                    )
                else:
                    try:
                        # Determine recovery prompt
                        if error_type == DecisionErrorType.MALFORMED_JSON:
                            # Full retry with same prompt
                            recovery_prompt = decision_prompt
                            logger.info(
                                f"[RESILIENCE] {self.player_name}: Full retry for malformed JSON"
                            )
                        else:
                            # Targeted correction prompt
                            recovery_prompt = self.prompt_manager.render_correction_prompt(
                                original_response=original_response_json or str(response_dict),
                                error_description=error_description,
                                valid_actions=valid_actions,
                                context=context,
                            )
                            logger.info(
                                f"[RESILIENCE] {self.player_name}: Targeted correction for {error_type.value}"
                            )

                        # Make recovery call
                        correction_response = self.assistant.chat_full(
                            recovery_prompt,
                            json_format=True,
                            hand_number=self.current_hand_number,
                            prompt_template='decision_correction',
                            capture_enricher=make_enricher(
                                parent_id=parent_capture_id[0],
                                error_type=error_type.value,
                                correction_attempt=1,
                                drama_context=drama_context,
                            ),
                        )
                        self._last_llm_response = correction_response

                        corrected_dict = parse_json_response(correction_response.content)
                        corrected_dict = self._normalize_response(corrected_dict)

                        # Check if correction succeeded
                        correction_error = classify_response_error(corrected_dict, valid_actions)
                        if correction_error is None:
                            logger.info(f"[RESILIENCE] {self.player_name}: Recovery successful!")
                            response_dict = corrected_dict
                            error_type = None
                            # Clear error from parent since recovery succeeded
                            if parent_capture_id[0]:
                                update_prompt_capture(
                                    parent_capture_id[0], error_type=None, error_description=None
                                )
                        else:
                            logger.warning(
                                f"[RESILIENCE] {self.player_name}: Recovery still has error ({correction_error.value})"
                            )
                            # Record the correction's actual error details
                            if final_capture_id[0]:
                                if correction_error == DecisionErrorType.MALFORMED_JSON:
                                    correction_error_description = "Could not parse JSON response."
                                else:
                                    correction_error_description = describe_response_error(
                                        correction_error, corrected_dict, valid_actions
                                    )
                                update_prompt_capture(
                                    final_capture_id[0],
                                    error_type=correction_error.value,
                                    error_description=correction_error_description,
                                )

                    except Exception as e:
                        logger.error(
                            f"[RESILIENCE] {self.player_name}: Recovery attempt failed - {e}"
                        )

            # ========== FALLBACK if still invalid ==========
            if error_type is not None:
                logger.warning(f"[RESILIENCE] {self.player_name}: Using fallback action")
                response_dict = FallbackActionSelector.select_action(
                    valid_actions=valid_actions,
                    strategy=AIFallbackStrategy.MIMIC_PERSONALITY,
                    personality_traits=self.personality_traits,
                    call_amount=context.get('call_amount', 0),
                    min_raise=context.get('min_raise', MIN_RAISE),
                    max_raise=context.get('max_raise', MIN_RAISE * 10),
                )
                response_dict['_used_fallback'] = True

            # ========== Final validation and analysis ==========
            # Apply any remaining fixes (raise amount extraction, etc.)
            response_dict = self._apply_final_fixes(response_dict, context, game_state)

            # Analyze decision quality (only for the final decision)
            # Pass player bet info for max_winnable calculation in analyzer
            player = game_state.current_player
            self._analyze_decision(
                response_dict,
                context,
                final_capture_id[0],
                player_bet=player.bet,
                all_players_bets=[(p.bet, p.is_folded) for p in game_state.players],
            )

            # Update capture with final action
            if final_capture_id[0]:
                action = response_dict.get('action')
                raise_amount = response_dict.get('raise_to') if action == 'raise' else None
                update_prompt_capture(
                    final_capture_id[0], action_taken=action, raise_amount=raise_amount
                )

                # Compute and store auto-labels
                if self._capture_label_repo and capture_enrichment[0]:
                    label_data = capture_enrichment[0].copy()
                    label_data['action_taken'] = action
                    self._capture_label_repo.compute_and_store_auto_labels(
                        final_capture_id[0], label_data
                    )

            return response_dict

        finally:
            if original_system_message is not None:
                self.assistant.system_message = original_system_message

    def _build_decision_prompt(self, message: str, context: Dict) -> tuple:
        """Build the decision prompt with situational guidance.

        Returns:
            tuple: (prompt_string, drama_context_dict_or_none)
        """
        game_state = self.state_machine.game_state
        player = game_state.current_player
        pot_committed_info = None
        short_stack_info = None
        made_hand_info = None
        equity_verdict_info = None
        drama_context = None
        hand_equity = 0.0  # Track for drama detection

        if self.prompt_config.situational_guidance:
            cost_to_call = context.get('call_amount', 0)
            pot_total = game_state.pot.get('total', 0)
            already_bet = player.bet
            player_stack = player.stack
            big_blind = game_state.current_ante or 100

            already_bet_bb = already_bet / big_blind if big_blind > 0 else 0
            stack_bb = player_stack / big_blind if big_blind > 0 else float('inf')
            cost_to_call_bb = cost_to_call / big_blind if big_blind > 0 else 0

            if cost_to_call > 0:
                pot_odds = pot_total / cost_to_call
                required_equity = 100 / (pot_odds + 1) if pot_odds > 0 else 100
                is_pot_committed = already_bet_bb > stack_bb
                is_extreme_odds = pot_odds >= 20 and cost_to_call_bb < 5

                if is_pot_committed or is_extreme_odds:
                    pot_committed_info = {
                        'pot_odds': round(pot_odds, 0),
                        'required_equity': round(required_equity, 1),
                        'already_bet_bb': round(already_bet_bb, 1),
                        'stack_bb': round(stack_bb, 1),
                        'cost_to_call_bb': round(cost_to_call_bb, 1),
                    }

            if stack_bb < 3:
                short_stack_info = {'stack_bb': round(stack_bb, 1)}

            if game_state.community_cards:
                hole_cards = [card_to_string(c) for c in player.hand] if player.hand else []
                community_cards = [card_to_string(c) for c in game_state.community_cards]

                # Count opponents still in hand for accurate multi-way equity
                num_opponents = len(
                    [p for p in game_state.players if not p.is_folded and p.name != player.name]
                )
                equity = calculate_quick_equity(
                    hole_cards, community_cards, num_opponents=num_opponents
                )
                if equity is not None:
                    hand_equity = equity  # Store for drama detection
                    is_tilted = bool(self.psychology and self.psychology.is_tilted)

                    if equity >= 0.80:
                        hand_strength = evaluate_hand_strength(hole_cards, community_cards)
                        hand_name = (
                            hand_strength.split(' - ')[0] if hand_strength else 'a strong hand'
                        )
                        made_hand_info = {
                            'hand_name': hand_name,
                            'equity': round(equity * 100),
                            'is_tilted': is_tilted,
                            'tier': 'strong',
                        }
                    elif equity >= 0.65:
                        hand_strength = evaluate_hand_strength(hole_cards, community_cards)
                        hand_name = (
                            hand_strength.split(' - ')[0] if hand_strength else 'a decent hand'
                        )
                        made_hand_info = {
                            'hand_name': hand_name,
                            'equity': round(equity * 100),
                            'is_tilted': is_tilted,
                            'tier': 'moderate',
                        }

            # Drama level for response intensity calibration
            analysis = MomentAnalyzer.analyze(
                game_state=game_state,
                player=player,
                cost_to_call=cost_to_call,
                big_blind=big_blind,
                last_raise_amount=game_state.last_raise_amount,
                hand_equity=hand_equity,
            )
            drama_context = {
                'level': analysis.level,
                'factors': analysis.factors,
                'tone': analysis.tone,
            }

        # Calculate equity verdict if enabled (GTO foundation - always show the math)
        cost_to_call_for_equity = context.get('call_amount', 0)
        if self.prompt_config.gto_equity and cost_to_call_for_equity > 0:
            pot_total = game_state.pot.get('total', 0)
            pot_odds = pot_total / cost_to_call_for_equity
            required_equity = 100 / (pot_odds + 1) if pot_odds > 0 else 100

            # Convert cards for equity calculation
            hole_cards = [card_to_string(c) for c in player.hand] if player.hand else []
            community_cards = [card_to_string(c) for c in game_state.community_cards]

            # Get opponents still in hand (needed for accurate multi-way equity)
            opponents_in_hand = [
                p for p in game_state.players if not p.is_folded and p.name != player.name
            ]
            num_opponents = len(opponents_in_hand)

            # Calculate equity (post-flop) or use preflop estimates
            if community_cards:
                equity = calculate_quick_equity(
                    hole_cards, community_cards, num_opponents=num_opponents
                )
            else:
                # Preflop: use hand ranking as rough equity estimate
                # These estimates are for heads-up; adjust for multi-way
                canonical = _get_canonical_hand(hole_cards)
                if canonical in PREMIUM_HANDS:
                    base_equity = 0.75  # Premium hands ~75% equity vs random
                elif canonical in TOP_10_HANDS:
                    base_equity = 0.65  # Top 10% ~65% equity
                elif canonical in TOP_20_HANDS:
                    base_equity = 0.55  # Top 20% ~55% equity
                elif canonical in TOP_35_HANDS:
                    base_equity = 0.50  # Top 35% ~50% equity
                else:
                    base_equity = 0.40  # Below average ~40% equity
                # Rough multi-way adjustment: equity decreases with more opponents
                # Simplified model: equity^(num_opponents) gives approximate multi-way equity
                equity = (
                    base_equity ** max(1, num_opponents * 0.7) if num_opponents > 1 else base_equity
                )

            if equity is not None:
                equity_pct = round(equity * 100)

                # Calculate equity vs ranges (uses opponent position/stats)
                equity_ranges_pct = None
                opponent_stats_str = ""
                try:
                    if opponents_in_hand:
                        # Get positions
                        table_positions = game_state.table_positions
                        position_by_name = {name: pos for pos, name in table_positions.items()}

                        # Build OpponentInfo objects
                        opponent_infos = []
                        for opp in opponents_in_hand:
                            opp_position = position_by_name.get(opp.name, "button")

                            # Get observed stats from opponent model manager
                            opp_model_data = None
                            if self.opponent_model_manager:
                                opp_model = self.opponent_model_manager.get_model(
                                    self.player_name, opp.name
                                )
                                if opp_model and opp_model.tendencies:
                                    opp_model_data = opp_model.tendencies.to_dict()

                            opponent_infos.append(
                                build_opponent_info(
                                    name=opp.name,
                                    position=opp_position,
                                    opponent_model=opp_model_data,
                                )
                            )

                        # Calculate equity vs ranges (use config setting for range mode)
                        equity_config = EquityConfig(
                            use_enhanced_ranges=self.prompt_config.use_enhanced_ranges
                        )
                        equity_vs_ranges = calculate_equity_vs_ranges(
                            hole_cards,
                            community_cards,
                            opponent_infos,
                            iterations=300,
                            config=equity_config,
                        )
                        if equity_vs_ranges is not None:
                            equity_ranges_pct = round(equity_vs_ranges * 100)

                        # Format opponent stats for display
                        opponent_stats_str = format_opponent_stats(opponent_infos)
                except Exception as e:
                    logger.debug(f"Range equity calculation failed: {e}")

                # Determine verdict (consider both equities)
                # Range-based equity is more accurate than vs-random, so weight it heavily
                if self.prompt_config.gto_verdict:
                    if equity_ranges_pct is not None:
                        # When we have range-based equity, use it as primary signal
                        if equity_ranges_pct >= required_equity:
                            if equity_pct >= required_equity:
                                verdict = "CALL is +EV vs both"
                            else:
                                verdict = "CALL is +EV vs ranges"
                        elif equity_ranges_pct >= required_equity * 0.85:
                            # Close to break-even vs ranges - truly marginal
                            verdict = "MARGINAL - close to break-even vs ranges"
                        else:
                            # Clearly below required equity vs ranges - fold
                            verdict = "FOLD - below required equity vs ranges"
                    else:
                        # No range data, use random equity only
                        if equity_pct >= required_equity:
                            verdict = "CALL is +EV"
                        else:
                            verdict = "FOLD is correct"
                else:
                    verdict = None

                equity_verdict_info = {
                    'equity_random': equity_pct,
                    'equity_ranges': equity_ranges_pct
                    if equity_ranges_pct is not None
                    else equity_pct,
                    'required_equity': round(required_equity, 1),
                    'verdict': verdict,
                    'pot_odds': round(pot_odds, 1),
                    'cost_to_call': cost_to_call_for_equity,
                    'opponent_stats': opponent_stats_str,
                }

        # Build pot odds info for YAML template rendering
        pot_odds_info = None
        if self.prompt_config.pot_odds:
            cost_to_call_for_pot_odds = context.get('call_amount', 0)
            pot_total_for_pot_odds = game_state.pot.get('total', 0)
            big_blind_for_pot_odds = game_state.current_ante or 100

            if cost_to_call_for_pot_odds > 0:
                po = pot_total_for_pot_odds / cost_to_call_for_pot_odds
                eq_needed = 100 / (po + 1)
                pot_fmt = _format_money(pot_total_for_pot_odds, big_blind_for_pot_odds, True)
                call_fmt = _format_money(cost_to_call_for_pot_odds, big_blind_for_pot_odds, True)

                if po >= 10:
                    pot_odds_extra = f"With {po:.0f}:1 odds, you should rarely fold - you only need to win 1 in {po+1:.0f} times."
                elif po >= 4:
                    pot_odds_extra = "These are favorable odds for calling with reasonable hands."
                else:
                    pot_odds_extra = ""

                pot_odds_info = {
                    'pot_odds': po,
                    'equity_needed': eq_needed,
                    'pot_fmt': pot_fmt,
                    'call_fmt': call_fmt,
                    'pot_odds_extra': pot_odds_extra,
                }
            else:
                pot_odds_info = None  # Model already sees cost_to_call: 0.00 BB

        # Phase 2: Add expression filtering guidance (visibility + tempo)
        expression_guidance = None
        if self.prompt_config.expression_filtering and self.psychology:
            from .expression_filter import get_expression_guidance

            expression_guidance = get_expression_guidance(
                expressiveness=self.psychology.anchors.expressiveness,
                energy=self.psychology.energy,
                include_tempo=True,
            )

        # Playstyle selection + briefing (replaces Phase 7 zone guidance)
        zone_guidance = None
        playstyle_briefing = None
        if self.prompt_config.zone_benefits and self.psychology:
            # 1. Get opponent models
            opponent_models = {}
            if self.opponent_model_manager:
                opponent_models = self.opponent_model_manager.get_all_models_for_observer(
                    self.player_name
                )

            # 2. Select playstyle
            playstyle_state = self.psychology.update_playstyle(
                opponent_models=opponent_models,
                hand_number=self.current_hand_number,
            )

            # 3. Build briefing with curated stats + framing + suppression flags
            focal_opponent = None
            active_opponents = [
                p for p in game_state.players if not p.is_folded and p.name != player.name
            ]
            if active_opponents:
                focal_opponent = active_opponents[0]

            zone_context = self._build_zone_context(game_state, focal_opponent)

            # Compute game stats for briefing
            active_stacks = [p.stack for p in game_state.players if not p.is_folded]
            avg_stack = sum(active_stacks) / max(len(active_stacks), 1)

            # Extract threat info from exploit scoring
            threat_name, threat_summary = None, None
            threat_model = None
            if opponent_models:
                from .playstyle_selector import _select_biggest_threat

                nemesis = (
                    self.psychology.composure_state.nemesis
                    if self.psychology.composure_state
                    else None
                )
                threat_model = _select_biggest_threat(opponent_models, nemesis)
                if threat_model:
                    threat_name = threat_model.opponent
                    threat_summary = threat_model.tendencies.get_summary()

            # Get focal opponent model for exploit tips
            focal_opp_model = None
            if self.opponent_model_manager and focal_opponent:
                focal_opp_model = self.opponent_model_manager.get_model(
                    self.player_name, focal_opponent.name
                )
                # Avoid duplicate if focal is the same as threat
                if (
                    focal_opp_model
                    and threat_model
                    and focal_opp_model.opponent == threat_model.opponent
                ):
                    focal_opp_model = None

            # Suppress opponent emotion display for poker_face at full engagement
            if (
                playstyle_state.active_playstyle == 'poker_face'
                and playstyle_state.engagement == 'full'
            ):
                zone_context.opponent_displayed_emotion = None

            playstyle_briefing = build_playstyle_briefing(
                active_playstyle=playstyle_state.active_playstyle,
                zone_effects=self.psychology.zone_effects,
                zone_context=zone_context,
                prompt_manager=self.prompt_manager,
                active_affinity=playstyle_state.active_affinity,
                engagement=playstyle_state.engagement,
                player_stack=player.stack,
                avg_stack=avg_stack,
                pot_total=game_state.pot.get('total', 0),
                big_blind=game_state.current_ante or 100,
                threat_name=threat_name,
                threat_summary=threat_summary,
                threat_model=threat_model,
                focal_model=focal_opp_model,
            )
            zone_guidance = playstyle_briefing.guidance

            # 4. Apply suppressions to this decision's prompt args
            if playstyle_briefing.suppress_equity_verdict:
                equity_verdict_info = None
            if playstyle_briefing.suppress_pot_odds:
                pot_odds_info = None

        # Suppress drama context in prompt when using simple response format
        # (still return full drama_context in tuple for capture enrichment)
        prompt_drama_context = (
            None if self.prompt_config.use_simple_response_format else drama_context
        )

        # Use the prompt manager for the decision prompt (respecting prompt_config toggles)
        prompt = self.prompt_manager.render_decision_prompt(
            message=message,
            include_mind_games=self.prompt_config.mind_games,
            include_dramatic_sequence=self.prompt_config.dramatic_sequence,
            include_betting_discipline=self.prompt_config.betting_discipline,
            pot_committed_info=pot_committed_info,
            short_stack_info=short_stack_info,
            made_hand_info=made_hand_info,
            equity_verdict_info=equity_verdict_info,
            drama_context=prompt_drama_context,
            include_pot_odds=self.prompt_config.pot_odds,
            pot_odds_info=pot_odds_info,
            use_simple_response_format=self.prompt_config.use_simple_response_format,
            expression_guidance=expression_guidance,
            zone_guidance=zone_guidance,
        )

        prompt = self._append_relationship_context_if_enabled(prompt, game_state, player)
        return (prompt, drama_context)

    def _append_relationship_context_if_enabled(
        self,
        prompt: str,
        game_state,
        player,
    ) -> str:
        """Append a relationship-context block to a decision prompt.

        Shared by chaos (`_build_decision_prompt`) and standard
        (`HybridAIController._build_choice_prompt`) so both LLM paths
        get the same opponent-history framing. Tiered's narration
        layer uses a different prompt assembly (`ExpressionContext`)
        and is wired separately if/when that lands.

        No-op when:
          - `relationship_context` flag is off
          - `opponent_model_manager` isn't wired (e.g., human-only
            games, tests that skip memory bootstrap)
          - the formatter returns "" (no opponent qualifies as
            rival/friendly — the common case in early sessions)
        """
        if not self.prompt_config.relationship_context:
            return prompt
        if self.opponent_model_manager is None:
            return prompt
        from .memory.relationship_prompt import build_relationship_context

        active_opponent_names = [
            p.name for p in game_state.players if not p.is_folded and p.name != player.name
        ]
        rel_block = build_relationship_context(
            observer_name=self.player_name,
            opponents=active_opponent_names,
            opponent_model_manager=self.opponent_model_manager,
        )
        if not rel_block:
            return prompt
        return prompt + "\n\n" + rel_block

    def _normalize_response(self, response_dict: Dict) -> Dict:
        """Normalize response: lowercase action, keep raise_to as float (BB).

        raise_to is always kept as float to preserve decimal BB values
        (e.g., 8.5 BB) until _apply_final_fixes converts to dollars.
        When use_simple_response_format is True, missing rich fields get defaults.
        """
        if 'action' in response_dict and response_dict['action']:
            response_dict['action'] = response_dict['action'].lower()

        if 'raise_to' not in response_dict:
            response_dict['raise_to'] = 0
        else:
            try:
                response_dict['raise_to'] = float(response_dict['raise_to'])
            except (ValueError, TypeError):
                response_dict['raise_to'] = 0

        # Keep bet_sizing as string, default to empty
        if 'bet_sizing' not in response_dict:
            response_dict['bet_sizing'] = ''
        else:
            response_dict['bet_sizing'] = str(response_dict['bet_sizing'])

        # Set defaults for missing rich fields when using simple response format
        if self.prompt_config.use_simple_response_format:
            response_dict.setdefault('inner_monologue', '')
            response_dict.setdefault('hand_strategy', '')
            response_dict.setdefault('dramatic_sequence', [])

        return response_dict

    def _apply_final_fixes(self, response_dict: Dict, context: Dict, game_state) -> Dict:
        """Apply final fixes to AI response.

        Always converts raise_to from BB to dollars (prompts are always BB-normalized).
        Falls back to min_raise if raise action has no amount set.
        """
        valid_actions = context.get('valid_actions', [])
        big_blind = game_state.current_ante or 100

        # Always convert BB raise_to to dollars
        if response_dict.get('action') == 'raise' and response_dict.get('raise_to', 0) > 0:
            bb_value = response_dict['raise_to']
            dollar_value = round(bb_value * big_blind)
            response_dict['_raise_to_bb'] = bb_value  # Store original BB for tracking
            response_dict['raise_to'] = dollar_value
            logger.debug(
                f"[BB_CONVERSION] {self.player_name} raise_to: {bb_value} BB → ${dollar_value}"
            )

        # Fix raise with 0 amount - fallback to min raise
        # Note: The resilience layer should have already asked the AI to fix this,
        # so this is a last-resort fallback if the AI still didn't provide an amount.
        if response_dict.get('action') == 'raise' and response_dict.get('raise_to', 0) == 0:
            # context['min_raise'] is already a "raise TO" value (includes highest_bet)
            min_raise_to = context.get('min_raise', game_state.highest_bet + MIN_RAISE)
            response_dict['raise_to'] = min_raise_to
            response_dict['raise_amount_corrected'] = True
            logger.warning(
                f"[RAISE_CORRECTION] {self.player_name} raise with 0, defaulting to ${min_raise_to}"
            )

        # Validate action is in valid_actions
        if valid_actions and response_dict.get('action') not in valid_actions:
            logger.warning(f"AI chose invalid action {response_dict['action']}, validating...")
            validated = validate_ai_response(response_dict, valid_actions)
            response_dict['action'] = validated['action']
            if response_dict.get('raise_to', 0) == 0:
                response_dict['raise_to'] = validated.get('raise_to', 0)

        return response_dict

    def _analyze_decision(
        self,
        response_dict: Dict,
        context: Dict,
        capture_id: Optional[int] = None,
        player_bet: int = 0,
        all_players_bets: Optional[List[Tuple[int, bool]]] = None,
        bounded_options: Optional[List[Dict]] = None,
    ) -> None:
        """Analyze decision quality and save to database.

        This runs for EVERY AI decision to track quality metrics.

        Args:
            response_dict: AI response with action and optional raise_to
            context: Game context dictionary
            capture_id: Optional ID of the prompt capture for linking
            player_bet: Player's current round bet (for max_winnable calculation)
            all_players_bets: List of (bet, is_folded) tuples for ALL players
            bounded_options: Optional list of bounded option dicts for menu compliance
        """
        if not self._decision_analysis_repo:
            return

        try:
            from poker.decision_analyzer import get_analyzer

            game_state = self.state_machine.game_state
            player = game_state.current_player

            # Get cards in format equity calculator understands
            community_cards = (
                [card_to_string(c) for c in game_state.community_cards]
                if game_state.community_cards
                else []
            )
            player_hand = [card_to_string(c) for c in player.hand] if player.hand else []

            # Count opponents still in hand
            opponents_in_hand = [
                p for p in game_state.players if not p.is_folded and p.name != player.name
            ]
            num_opponents = len(opponents_in_hand)

            # Get positions for range-based equity calculation
            table_positions = game_state.table_positions
            position_by_name = {name: pos for pos, name in table_positions.items()}
            player_position = position_by_name.get(self.player_name)
            opponent_positions = [
                position_by_name.get(
                    p.name, "button"
                )  # Default to button (widest range) if unknown
                for p in opponents_in_hand
            ]

            # Build OpponentInfo objects with observed stats, personality data, and action context
            from .hand_ranges import build_opponent_info

            opponent_infos = []

            # Get game messages for action extraction
            game_messages = getattr(self, '_current_game_messages', None)
            current_phase = (
                self.state_machine.current_phase.name
                if self.state_machine.current_phase
                else 'PRE_FLOP'
            )

            for opp in opponents_in_hand:
                opp_position = position_by_name.get(opp.name, "button")

                # Get observed stats from opponent model manager
                opp_model_data = None
                if self.opponent_model_manager:
                    opp_model = self.opponent_model_manager.get_model(self.player_name, opp.name)
                    if opp_model and opp_model.tendencies:
                        opp_model_data = opp_model.tendencies.to_dict()

                # Extract opponent's preflop and postflop actions from game messages
                preflop_action = None
                postflop_aggression = None
                if game_messages:
                    preflop_action = self._extract_opponent_preflop_action(
                        opp.name, game_messages, game_state
                    )
                    postflop_aggression = self._extract_opponent_postflop_aggression(
                        opp.name, game_messages, current_phase
                    )

                opponent_infos.append(
                    build_opponent_info(
                        name=opp.name,
                        position=opp_position,
                        opponent_model=opp_model_data,
                        preflop_action=preflop_action,
                        postflop_aggression=postflop_aggression,
                    )
                )

            # Get request_id from last LLM response
            llm_response = getattr(self, '_last_llm_response', None)
            request_id = llm_response.request_id if llm_response else None

            # Build psychology snapshot for decision tracking
            psychology_snapshot = None
            if self.psychology:
                psych = self.psychology
                snapshot = {
                    'tilt_level': psych.tilt_level,
                    'tilt_source': psych.tilt.tilt_source if psych.tilt else None,
                }
                if psych.emotional:
                    snapshot['display_emotion'] = psych.get_display_emotion()
                traits = psych.traits
                # New 5-trait model
                snapshot['elastic_aggression'] = traits.get('aggression')
                snapshot['elastic_tightness'] = traits.get('tightness')
                snapshot['elastic_confidence'] = traits.get('confidence')
                snapshot['elastic_composure'] = traits.get('composure')
                snapshot['elastic_table_talk'] = traits.get('table_talk')
                # Backward compatibility: also include old trait name if present
                snapshot['elastic_bluff_tendency'] = traits.get('bluff_tendency')

                # Zone detection data (Phase 10)
                zone_effects = psych.zone_effects
                snapshot['zone_confidence'] = zone_effects.confidence
                snapshot['zone_composure'] = zone_effects.composure
                snapshot['zone_energy'] = zone_effects.energy
                snapshot['zone_manifestation'] = zone_effects.manifestation
                snapshot['zone_sweet_spots_json'] = json.dumps(zone_effects.sweet_spots)
                snapshot['zone_penalties_json'] = json.dumps(zone_effects.penalties)
                snapshot['zone_primary_sweet_spot'] = zone_effects.primary_sweet_spot
                snapshot['zone_primary_penalty'] = zone_effects.primary_penalty
                snapshot['zone_total_penalty_strength'] = zone_effects.total_penalty_strength
                snapshot['zone_in_neutral_territory'] = zone_effects.in_neutral_territory

                # Zone effects instrumentation (Phase 10)
                instr = getattr(psych, '_last_zone_effects_instrumentation', None)
                if instr:
                    snapshot['zone_intrusive_thoughts_injected'] = instr.get(
                        'intrusive_thoughts_injected'
                    )
                    snapshot['zone_intrusive_thoughts_json'] = json.dumps(
                        instr.get('intrusive_thoughts', [])
                    )
                    snapshot['zone_penalty_strategy_applied'] = instr.get(
                        'penalty_strategy_applied'
                    )
                    snapshot['zone_info_degraded'] = instr.get('info_degraded')
                    snapshot['zone_strategy_selected'] = instr.get('strategy_selected')

                # Playstyle tracking
                if psych.playstyle_state:
                    ps = psych.playstyle_state
                    snapshot['playstyle_active'] = ps.active_playstyle
                    snapshot['playstyle_primary'] = ps.primary_playstyle
                    snapshot['playstyle_scores_json'] = json.dumps(ps.style_scores)
                    snapshot['playstyle_effective_adaptation'] = ps.last_effective_adaptation
                    snapshot['playstyle_engagement'] = ps.engagement
                    snapshot['playstyle_active_affinity'] = ps.active_affinity

                psychology_snapshot = snapshot

            analyzer = get_analyzer()
            analysis = analyzer.analyze(
                game_id=self.game_id,
                player_name=self.player_name,
                hand_number=self.current_hand_number,
                phase=self.state_machine.current_phase.name
                if self.state_machine.current_phase
                else None,
                player_hand=player_hand,
                community_cards=community_cards,
                pot_total=game_state.pot.get('total', 0),
                cost_to_call=context.get('call_amount', 0),
                player_stack=player.stack,
                num_opponents=num_opponents,
                action_taken=response_dict.get('action'),
                raise_amount=response_dict.get('raise_to'),
                raise_amount_bb=response_dict.get('_raise_to_bb'),  # BB amount if BB mode
                bet_sizing=response_dict.get('bet_sizing', ''),
                request_id=request_id,
                capture_id=capture_id,
                player_position=player_position,
                opponent_positions=opponent_positions,
                opponent_infos=opponent_infos,
                player_bet=player_bet,
                all_players_bets=all_players_bets,
                psychology_snapshot=psychology_snapshot,
                skip_equity=getattr(self, 'skip_equity_in_analysis', False),
            )

            # Menu compliance: score against bounded options if available
            if bounded_options:
                analyzer.evaluate_menu_compliance(analysis, bounded_options)

            # Phase 7.6 (Step 3b): attach per-decision intervention trace
            # when the controller exposes one (tiered bot only today).
            # Serialization failures degrade gracefully — the analysis
            # row still persists without the trace.
            analysis.intervention_trace_json = _serialize_intervention_trace(
                getattr(self, '_last_intervention_trace', None),
                player_name=self.player_name,
            )

            # Phase 7.6 (Step 6): attach pipeline snapshot for Mode 1
            # shadow-eval replay. Same degrade-gracefully contract.
            analysis.strategy_pipeline_snapshot_json = _serialize_pipeline_snapshot(
                getattr(self, '_last_pipeline_snapshot', None),
                player_name=self.player_name,
            )

            self._decision_analysis_repo.save_decision_analysis(analysis)

            equity_str = f"{analysis.equity:.2f}" if analysis.equity is not None else "N/A"
            menu_str = (
                f", menu_best={analysis.menu_picked_best}"
                if analysis.menu_picked_best is not None
                else ""
            )
            logger.debug(
                f"[DECISION_ANALYSIS] {self.player_name}: {analysis.decision_quality} "
                f"(equity={equity_str}, ev_lost={analysis.ev_lost:.0f}{menu_str})"
            )
        except Exception as e:
            logger.warning(f"[DECISION_ANALYSIS] Failed to analyze decision: {e}")

    def _extract_opponent_preflop_action(
        self, opponent_name: str, game_messages, game_state
    ) -> Optional[str]:
        """
        Extract what preflop action an opponent took this hand.

        Analyzes game messages to determine if opponent:
        - open_raise: First to raise preflop
        - call: Called a raise (or limped behind)
        - 3bet: Re-raised a raise
        - 4bet+: Re-raised a 3-bet or more
        - limp: Just called the big blind (no raise faced)

        Args:
            opponent_name: Name of opponent to check
            game_messages: List of game messages or string of messages
            game_state: Current game state for position info

        Returns:
            Action string or None if not determinable
        """
        lines = _parse_game_messages(game_messages)
        if lines is None:
            return None

        # Get BB player name to ignore forced BB
        bb_player = game_state.table_positions.get('big_blind_player')
        opponent_lower = opponent_name.lower()

        # Filter to preflop lines only
        preflop_lines = _get_preflop_lines(lines)

        # Process lines, accumulating state as (raise_count, opponent_action)
        # Using reduce-like iteration but keeping it readable
        state = _process_preflop_lines(preflop_lines, opponent_lower, opponent_name, bb_player)

        return state

    def _extract_opponent_postflop_aggression(
        self, opponent_name: str, game_messages, current_phase: str
    ) -> Optional[str]:
        """
        Extract opponent's postflop aggression in current street.

        Args:
            opponent_name: Name of opponent
            game_messages: Game messages
            current_phase: Current phase (FLOP, TURN, RIVER)

        Returns:
            'bet', 'raise', 'check_call', 'check', or None
        """
        if current_phase == 'PRE_FLOP':
            return None

        lines = _parse_game_messages(game_messages)
        if lines is None:
            return None

        # Get lines from current street for the opponent
        street_lines = _get_street_lines(lines, current_phase)
        opponent_lower = opponent_name.lower()
        opponent_lines = [line for line in street_lines if opponent_lower in line.lower()]

        # Classify each line's aggression and find the highest priority action
        actions = [
            action
            for line in opponent_lines
            if (action := _classify_aggression(line.lower())) is not None
        ]

        if not actions:
            return None

        # Return highest priority action (aggressive actions override passive ones)
        return max(actions, key=lambda a: AGGRESSION_PRIORITY.get(a, 0))

    def _build_game_context(self, game_state, game_messages=None) -> Dict:
        """Build context for chattiness decisions."""
        context = {}

        # Check pot size
        pot_total = game_state.pot.get('total', 0)
        if pot_total > BIG_POT_THRESHOLD:
            context['big_pot'] = True

        # Check if all-in situation
        if any(p.is_all_in for p in game_state.players if p.is_active):
            context['all_in'] = True

        # Check if heads-up
        active_players = [p for p in game_state.players if p.is_active]
        if len(active_players) == 2:
            context['heads_up'] = True
        elif len(active_players) > 3:
            context['multi_way_pot'] = True

        # Add phase-specific context
        if self.state_machine.phase == 'SHOWDOWN':
            context['showdown'] = True

        return context

    def _build_memory_context(
        self, game_state, include_session: bool = True, include_opponents: bool = True
    ) -> str:
        """
        Build context from session memory and opponent models for injection into prompts.

        Args:
            game_state: Current game state
            include_session: Whether to include session memory context
            include_opponents: Whether to include opponent intel
        """
        parts = []

        # Session context (recent outcomes, streak, observations)
        if include_session and self.session_memory:
            session_ctx = self.session_memory.get_context_for_prompt(MEMORY_CONTEXT_TOKENS)
            if session_ctx:
                parts.append(f"=== Your Session ===\n{session_ctx}")

        # Opponent summaries
        if include_opponents and self.opponent_model_manager:
            # Get active opponents
            opponents = [
                p.name for p in game_state.players if p.name != self.player_name and not p.is_folded
            ]
            opponent_ctx = self.opponent_model_manager.get_table_summary(
                self.player_name, opponents, OPPONENT_SUMMARY_TOKENS
            )
            if opponent_ctx:
                parts.append(f"=== Opponent Intel ===\n{opponent_ctx}")

            # Dedicated narrative-observations section: 1-2 reads pulled
            # from `OpponentModel.narrative_observations`, weighted toward
            # the opponent hero is facing (highest current bet, falling
            # back to first active opponent) and toward any nemesis (high
            # heat in relationship state). The LLM can key on these in
            # its dramatic_sequence or ignore them — they're "extra info,"
            # not strategy directives.
            from .memory.opponent_model import format_opponent_observations

            facing_opponent = self._infer_facing_opponent(game_state, opponents)
            obs_pairs = self.opponent_model_manager.select_opponent_observations(
                self.player_name,
                active_opponents=opponents,
                facing_opponent=facing_opponent,
            )
            obs_block = format_opponent_observations(obs_pairs)
            if obs_block:
                parts.append(f"=== Opponent Observations ===\n{obs_block}")

        # The human player's self-description, if they wrote one. They authored
        # this for the table to see — it's fair game for trash talk and table
        # banter in your dramatic_sequence. Extra color, not a strategy directive.
        if self.human_bio:
            human_name = next(
                (p.name for p in game_state.players if getattr(p, 'is_human', False)),
                None,
            )
            who = human_name or "The human player"
            # Neutralize the section delimiter so a crafted bio can't forge a
            # fake "=== ... ===" prompt block (mild prompt-injection defense).
            safe_bio = self.human_bio.replace('===', '==')
            parts.append(
                f"=== About {who} (in their own words) ===\n"
                f"{safe_bio}\n"
                "(Feel free to needle them about this at the table.)"
            )

        return "\n\n".join(parts) if parts else ""

    def _infer_facing_opponent(self, game_state, opponents: List[str]) -> Optional[str]:
        """Best-effort guess at which opponent hero is reacting to.

        Used by the narrative-observation section's relevance scoring.
        Returns the active opponent with the highest current bet (the
        most-recent aggressor when there's only one). Falls back to None
        if no opponent has a non-zero bet — caller's selection helper
        then weights purely by recency + nemesis.

        Pure heuristic — wrong guesses don't break anything, they just
        slightly under-prioritize the right read.
        """
        if not opponents:
            return None
        try:
            opp_bets = [
                (p.name, getattr(p, 'bet', 0) or 0)
                for p in game_state.players
                if p.name in opponents
            ]
            if not opp_bets:
                return None
            best = max(opp_bets, key=lambda nb: nb[1])
            if best[1] <= 0:
                return None
            return best[0]
        except Exception:
            return None

    def _build_chattiness_guidance(
        self,
        chattiness: float,
        should_speak: bool,
        speaking_context: Dict,
        valid_actions: List[str],
        should_gesture: bool = True,
    ) -> str:
        """Build guidance for AI about speaking + gesturing behavior.

        Three modes:
          speak=True                 → full speech + actions encouraged
          speak=False, gesture=True  → quiet reaction: *action* beats only
          speak=False, gesture=False → fully silent, no dramatic_sequence
        """
        guidance = f"Your chattiness level: {chattiness:.1f}/1.0\n"

        if should_speak:
            guidance += "You feel inclined to say something this turn.\n"
            style = self.chattiness_manager.suggest_speaking_style(self.player_name, chattiness)
            guidance += f"Speaking style: {style}\n"
        elif should_gesture:
            guidance += "You don't feel like talking this turn — no speech.\n"
            guidance += (
                "You MAY still react physically: 1–2 short *action* beats "
                "(gestures only, lowercase, wrapped in asterisks) are fine "
                "if the moment warrants it. NEVER include speech beats.\n"
            )
        else:
            guidance += "You don't feel like talking this turn. Stay quiet.\n"
            guidance += "Focus on your action and inner thoughts only.\n"
            guidance += "DO NOT include 'dramatic_sequence' in your response.\n"

        # Add context about conversation flow
        if speaking_context['turns_since_spoke'] > 3:
            guidance += f"(You haven't spoken in {speaking_context['turns_since_spoke']} turns)\n"
        if speaking_context['table_silent_turns'] > 2:
            guidance += "(The table has been quiet for a while)\n"

        return guidance

    def _build_zone_context(self, game_state, focal_opponent=None) -> ZoneContext:
        """
        Build ZoneContext from game state and memory for zone-based strategy guidance.

        Args:
            game_state: Current game state
            focal_opponent: Primary opponent to gather intel on (optional)

        Returns:
            ZoneContext with available data populated
        """
        context = ZoneContext()

        # Get opponent's displayed emotion if available
        if focal_opponent and hasattr(focal_opponent, '_controller'):
            controller = focal_opponent._controller
            if hasattr(controller, 'psychology') and controller.psychology:
                context.opponent_displayed_emotion = controller.psychology.get_display_emotion()

        # Add opponent stats if available from opponent model manager
        if self.opponent_model_manager and focal_opponent:
            opp_model = self.opponent_model_manager.get_model(self.player_name, focal_opponent.name)
            if opp_model and opp_model.tendencies:
                tendencies = opp_model.tendencies

                # Build opponent stats string (for commanding templates)
                parts = []
                if tendencies.fold_to_cbet is not None:
                    parts.append(f"folds to c-bets {tendencies.fold_to_cbet:.0%}")
                if tendencies.aggression_factor is not None:
                    parts.append(f"aggression {tendencies.aggression_factor:.1f}")
                if parts:
                    context.opponent_stats = f"{focal_opponent.name}: {', '.join(parts)}"

                # Build opponent analysis (more detailed) for Aggro zone
                analysis_parts = []
                if tendencies.fold_to_cbet > 0.6:
                    analysis_parts.append(f"folds to c-bets {tendencies.fold_to_cbet:.0%}")
                if tendencies.vpip > 0.45:
                    analysis_parts.append(f"plays {tendencies.vpip:.0%} of hands")
                elif tendencies.vpip < 0.20:
                    analysis_parts.append(f"very tight ({tendencies.vpip:.0%} VPIP)")
                if tendencies.bluff_frequency > 0.4:
                    analysis_parts.append(f"bluffs {tendencies.bluff_frequency:.0%}")
                if analysis_parts:
                    context.opponent_analysis = (
                        f"{focal_opponent.name}: {', '.join(analysis_parts)}"
                    )

        # Check for weak player based on displayed emotion
        if context.opponent_displayed_emotion in ['nervous', 'shaken', 'panicking', 'shocked']:
            if focal_opponent:
                context.weak_player_note = (
                    f"{focal_opponent.name} appears {context.opponent_displayed_emotion}"
                )

        # Add equity vs ranges if available (stored during equity calculation)
        if hasattr(self, '_equity_info') and self._equity_info:
            context.equity_vs_ranges = self._equity_info

        return context


def human_player_action(ui_data: dict, player_options: List[str]) -> Dict:
    """
    Console UI is used to update the player with the relevant game state info and receives input.
    This will return a tuple as ( action, amount ) for the players bet.
    """
    # Get user choice
    player_choice = None
    while player_choice not in player_options:
        player_choice = (
            input(f"{ui_data['player_name']}, what would you like to do? ")
            .lower()
            .replace("-", "_")
        )
        if player_choice in ["all-in", "allin", "all in"]:
            player_choice = "all_in"
        if player_choice not in player_options:
            print("Invalid choice. Please select from the available options.")
            print(f"{player_options}\n")

    # Set or get bet amount if necessary
    bet_amount = 0
    if player_choice == "raise":
        while True:
            try:
                bet_amount = int(input("How much would you like to raise? "))
                break
            except ValueError:
                print("Please enter a valid number.")
    elif player_choice == "call":
        bet_amount = ui_data['cost_to_call']

    response_dict = {
        "action": player_choice,
        "raise_to": bet_amount,
    }

    return response_dict


def display_player_turn_update(ui_data, player_options: Optional[List] = None) -> None:
    player_name = ui_data['player_name']
    player_hand = ui_data['player_hand']

    def ensure_card(c):
        return c if isinstance(c, Card) else Card(c['rank'], c['suit'])

    try:
        # Render the player's cards using the CardRenderer.
        rendered_hole_cards = CardRenderer().render_hole_cards(
            [ensure_card(c) for c in player_hand]
        )
    except (ValueError, TypeError, KeyError) as e:
        print(f"{player_name} has no cards.")
        raise ValueError('Missing cards. Please check your hand.') from e

    # Display information to the user
    if len(ui_data['community_cards']) > 0:
        rendered_community_cards = CardRenderer().render_cards(
            [ensure_card(c) for c in ui_data['community_cards']]
        )
        print(f"\nCommunity Cards:\n{rendered_community_cards}")

    print(f"Your Hand:\n{rendered_hole_cards}")
    print(f"Pot: {ui_data['pot_total']}")
    print(f"Your Stack: {ui_data['player_stack']}")
    print(f"Cost to Call: {ui_data['cost_to_call']}")
    print(f"Options: {player_options}\n")


def _ensure_card(c):
    """Convert card to Card object if it's a dict, otherwise return as-is."""
    return c if isinstance(c, Card) else Card(c['rank'], c['suit'])


def build_base_game_state(
    game_state,
    player: Player,
    phase,
    messages,
    include_hand_strength: bool = True,
    psychology: 'PlayerPsychology' = None,
    range_guidance: bool = False,
    include_persona: bool = True,
) -> str:
    """
    Build BB-normalized game state prompt for AI decisions.

    This is the unified base prompt builder that always uses BB normalization.
    Pot odds are NOT included here — they are handled by the decision YAML template.
    Messages should already be BB-converted by the caller.

    Args:
        game_state: Current game state
        player: Current player
        phase: Current betting phase
        messages: Recent actions/chat (should already be BB-converted)
        include_hand_strength: Whether to include hand strength evaluation
        psychology: Player psychology for range-aware preflop (optional)
        range_guidance: Whether to use looseness-aware preflop classification
        include_persona: Whether to include persona name in prompt (False for lean experiments)
    """
    persona = player.name
    table_positions = game_state.table_positions
    current_round = phase
    community_cards = [str(_ensure_card(c)) for c in game_state.community_cards]
    player_money = player.stack
    player_positions = [
        position for position, name in table_positions.items() if name == player.name
    ]
    hole_cards = [str(_ensure_card(c)) for c in player.hand]
    current_pot = game_state.pot['total']
    current_bet = game_state.current_player.bet
    raw_cost_to_call = game_state.highest_bet - game_state.current_player.bet
    cost_to_call = min(raw_cost_to_call, player_money)
    player_options = game_state.current_player_options

    big_blind = game_state.current_ante or 100

    # Hand strength evaluation + breakdown
    hand_info_line = ""
    if include_hand_strength:
        if community_cards:
            hand_strength = evaluate_hand_strength(hole_cards, community_cards)
            # Extract strength tier (e.g., "Strong" from "Two Pair - Strong")
            strength_tier = (
                hand_strength.split(' - ')[1] if hand_strength and ' - ' in hand_strength else None
            )

            # Try detailed breakdown (merges strength tier into header)
            try:
                hole_card_objects = [_ensure_card(c) for c in player.hand]
                community_card_objects = [_ensure_card(c) for c in game_state.community_cards]
                breakdown = narrate_hand_breakdown(
                    hole_card_objects,
                    community_card_objects,
                    strength_tier=strength_tier,
                )
                if breakdown:
                    hand_info_line = f"{breakdown}\n"
            except Exception as e:
                logger.debug(f"Hand breakdown failed: {e}")

            # Fallback to old-style line if breakdown didn't produce output
            if not hand_info_line and hand_strength:
                hand_info_line = f"Your Hand Strength: {hand_strength}\n"
        else:
            # Pre-flop: range-aware classification if psychology available
            hand_strength = None
            if range_guidance and psychology and player_positions:
                num_opponents = len(
                    [p for p in game_state.players if not p.is_folded and p.name != player.name]
                )
                hand_strength = classify_preflop_hand_with_range(
                    hole_cards,
                    psychology,
                    player_positions[0],
                    num_opponents=num_opponents,
                )
            if not hand_strength:
                hand_strength = classify_preflop_hand(hole_cards)
            hand_info_line = f"Your Hand Strength: {hand_strength}\n" if hand_strength else ""

    persona_line = f"Persona: {persona}\n" if include_persona else ""
    persona_state = (
        f"{persona_line}"
        f"Your Cards: {hole_cards}\n"
        f"{hand_info_line}"
        f"Your Stack: {_format_money(player_money, big_blind, True)}\n"
    )

    # Format opponent status in BB
    opponent_status = ''.join(
        [
            f'{p.name} has {_format_money(p.stack, big_blind, True)}'
            + (' and they have folded' if p.is_folded else '')
            + '.\n'
            for p in game_state.players
        ]
    )

    hand_state = (
        f"Current Round: {current_round}\n"
        f"Community Cards: {community_cards}\n"
        f"Table Positions: {table_positions}\n"
        f"Opponent Status:\n{opponent_status}\n"
        f"Recent Actions:\n{messages}\n"
    )

    pot_state = (
        f"Pot Total: {_format_money(current_pot, big_blind, True)}\n"
        f"How much you've bet: {_format_money(current_bet, big_blind, True)}\n"
        f"Your cost to call: {_format_money(cost_to_call, big_blind, True)}\n"
        f"Blinds: 0.5/1 BB\n"
    )

    stack_limit = _format_money(player_money, big_blind, True)

    # Calculate raise TO bounds for the prompt (opponent-aware)
    highest_bet = game_state.highest_bet
    max_opponent_stack = max(
        (
            p.stack
            for p in game_state.players
            if not p.is_folded and not p.is_all_in and p.name != player.name
        ),
        default=0,
    )
    max_raise_by = min(player_money, max_opponent_stack)
    max_raise_to = highest_bet + max_raise_by
    min_raise_by = min(game_state.min_raise_amount, max_raise_by) if max_raise_by > 0 else 0
    min_raise_to = highest_bet + min_raise_by
    min_raise_to_fmt = _format_money(min_raise_to, big_blind, True)
    max_raise_to_fmt = _format_money(max_raise_to, big_blind, True)

    raise_guidance = ""
    if 'raise' in player_options:
        if current_round == 'PRE_FLOP':
            sizing_hint = " Standard open: 2.5-3x BB. 3-bet (re-raise): 8-12 BB total."
        else:
            pot_fmt = _format_money(current_pot, big_blind, True)
            sizing_hint = (
                f" Size relative to the pot ({pot_fmt}): half-pot to two-thirds pot is standard."
            )
        raise_guidance = f"If raising, set raise_to between {min_raise_to_fmt} and {max_raise_to_fmt} (the total bet, not the increment).{sizing_hint}\n"

    hand_update_message = (
        persona_state
        + hand_state
        + pot_state
        + "\n"
        + (
            f"NOTE: All amounts are in Big Blinds (BB). When raising, set raise_to to BB amount (e.g., raise_to=8 means 8 BB).\n"
            f"You cannot bet more than you have, {stack_limit}.\n"
            f"{raise_guidance}"
            f"You must select from these options: {player_options}\n"
            f"Your table position: {player_positions}\n"
            f"What is your move{', ' + persona if include_persona else ''}?\n\n"
        )
    )

    return hand_update_message
