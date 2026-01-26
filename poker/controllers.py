import json
from datetime import datetime
from typing import List, Optional, Dict
import logging

from core.card import Card, CardRenderer
from .poker_game import Player
from .poker_state_machine import PokerStateMachine
from .poker_player import AIPokerPlayer
from .utils import prepare_ui_data
from .prompt_manager import PromptManager
from .chattiness_manager import ChattinessManager
from .response_validator import ResponseValidator
from .config import MIN_RAISE, BIG_POT_THRESHOLD, MEMORY_CONTEXT_TOKENS, OPPONENT_SUMMARY_TOKENS, is_development_mode
from .prompt_config import PromptConfig
from .ai_resilience import (
    with_ai_fallback,
    expects_json,
    parse_json_response,
    validate_ai_response,
    get_fallback_chat_response,
    AIFallbackStrategy,
    AIResponseError,
    DecisionErrorType,
    classify_response_error,
    describe_response_error,
    FallbackActionSelector,
)
from .player_psychology import PlayerPsychology
from .memory.commentary_generator import DecisionPlan

logger = logging.getLogger(__name__)

# Hand strength evaluation for clearer AI decision making
SUIT_MAP = {'♣': 'c', '♦': 'd', '♠': 's', '♥': 'h'}


def _convert_card_for_eval(card_str: str) -> str:
    """Convert unicode card string to eval7 format."""
    for unicode_suit, letter_suit in SUIT_MAP.items():
        if unicode_suit in card_str:
            card_str = card_str.replace(unicode_suit, letter_suit)
            break
    if card_str.startswith('10'):
        card_str = 'T' + card_str[2:]
    return card_str


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
        hand = [eval7.Card(_convert_card_for_eval(c)) for c in hole_cards]
        board = [eval7.Card(_convert_card_for_eval(c)) for c in community_cards]

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


def calculate_quick_equity(hole_cards: List[str], community_cards: List[str],
                           num_simulations: int = 300) -> Optional[float]:
    """
    Calculate quick equity estimate against random opponent hands.

    Uses Monte Carlo simulation with eval7. Returns equity as 0.0-1.0.
    ~30-50ms for 300 simulations - acceptable for real-time use.
    """
    if not community_cards:
        return None

    try:
        import eval7

        hand = [eval7.Card(_convert_card_for_eval(c)) for c in hole_cards]
        board_cards = [eval7.Card(_convert_card_for_eval(c)) for c in community_cards]

        wins = 0
        for _ in range(num_simulations):
            deck = eval7.Deck()
            for c in hand + board_cards:
                deck.cards.remove(c)
            deck.shuffle()
            opp_hand = list(deck.deal(2))

            # Complete board if needed (flop/turn)
            remaining = 5 - len(board_cards)
            full_board = board_cards + list(deck.deal(remaining))

            hero_score = eval7.evaluate(hand + full_board)
            opp_score = eval7.evaluate(opp_hand + full_board)

            # Higher score = better hand in eval7
            if hero_score > opp_score:
                wins += 1
            elif hero_score == opp_score:
                wins += 0.5

        return wins / num_simulations

    except ImportError:
        return None
    except Exception as e:
        logger.debug(f"Equity calculation failed: {e}")
        return None


# Preflop hand rankings - neutral/informational only
# Based on standard poker hand rankings (169 unique starting hands)
PREMIUM_HANDS = {'AA', 'KK', 'QQ', 'JJ', 'AKs'}  # Top ~3%
TOP_10_HANDS = PREMIUM_HANDS | {'TT', 'AKo', 'AQs', 'AJs', 'KQs'}  # Top ~10%
TOP_20_HANDS = TOP_10_HANDS | {'99', '88', '77', 'ATs', 'AQo', 'AJo', 'KJs', 'KTs', 'QJs', 'QTs', 'JTs'}  # Top ~20%
TOP_35_HANDS = TOP_20_HANDS | {
    '66', '55', '44', '33', '22',  # Small pairs
    'A9s', 'A8s', 'A7s', 'A6s', 'A5s', 'A4s', 'A3s', 'A2s',  # Suited aces
    'KQo', 'K9s', 'K8s', 'Q9s', 'J9s', 'T9s', '98s', '87s', '76s', '65s', '54s',  # Suited connectors
}


def _get_canonical_hand(hole_cards: List[str]) -> str:
    """Convert hole cards to canonical notation (e.g., 'AKs', 'QQ', 'T9o')."""
    if len(hole_cards) != 2:
        return ''

    # Normalize cards
    c1 = _convert_card_for_eval(hole_cards[0])
    c2 = _convert_card_for_eval(hole_cards[1])

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
    def __init__(self, player_name, state_machine=None, llm_config=None,
                 session_memory=None, opponent_model_manager=None,
                 game_id=None, owner_id=None, debug_capture=False, persistence=None,
                 prompt_config=None):
        self.player_name = player_name
        self.state_machine = state_machine
        self.llm_config = llm_config or {}
        self.game_id = game_id
        self.owner_id = owner_id
        self._persistence = persistence
        self.ai_player = AIPokerPlayer(
            player_name,
            llm_config=self.llm_config,
            game_id=game_id,
            owner_id=owner_id
        )
        self.assistant = self.ai_player.assistant
        self.prompt_manager = PromptManager(enable_hot_reload=is_development_mode())
        self.chattiness_manager = ChattinessManager()
        self.response_validator = ResponseValidator()

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

        # Hand number tracking (set by memory manager)
        self.current_hand_number = None

        # Decision plans for current hand (captured during decide_action)
        self._current_hand_plans: List[DecisionPlan] = []
        
    def get_current_personality_traits(self):
        """Get current trait values from psychology (elastic personality)."""
        return self.psychology.traits

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

    def decide_action(self, game_messages) -> Dict:
        game_state = self.state_machine.game_state

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

        game_messages = summarize_messages(
            game_messages,
            self.player_name)

        # Get current chattiness and determine if should speak
        current_traits = self.get_current_personality_traits()
        chattiness = current_traits.get('chattiness', 0.5)

        # Build game context for chattiness decision (use original messages for address detection)
        game_context = self._build_game_context(game_state, original_messages)
        should_speak = self.chattiness_manager.should_speak(
            self.player_name, chattiness, game_context
        )
        speaking_context = self.chattiness_manager.get_speaking_context(self.player_name)
        
        # Build message with game state (respecting prompt_config toggles)
        message = convert_game_to_hand_state(
            game_state,
            game_state.current_player,
            self.state_machine.phase,
            game_messages,
            include_pot_odds=self.prompt_config.pot_odds,
            include_hand_strength=self.prompt_config.hand_strength)

        # Get valid actions early so we can include in guidance
        player_options = game_state.current_player_options

        # Inject memory context if available (respecting prompt_config toggles)
        memory_context = self._build_memory_context(
            game_state,
            include_session=self.prompt_config.session_memory,
            include_opponents=self.prompt_config.opponent_intel
        )
        if memory_context:
            message = memory_context + "\n\n" + message

        # Add chattiness guidance to message (if enabled)
        if self.prompt_config.chattiness:
            chattiness_guidance = self._build_chattiness_guidance(
                chattiness, should_speak, speaking_context, player_options
            )
            message = message + "\n\n" + chattiness_guidance

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
            message = message + "\n\n" + "ADDITIONAL GUIDANCE:\n" + self.prompt_config.guidance_injection

        print(message)

        # Context for fallback
        player_stack = game_state.current_player.stack
        raw_cost_to_call = game_state.highest_bet - game_state.current_player.bet
        # Effective cost is capped at player's stack (they can only risk what they have)
        cost_to_call = min(raw_cost_to_call, player_stack)

        # Calculate max raise: capped at largest opponent stack (can only raise what they can match)
        max_opponent_stack = max(
            (p.stack for p in game_state.players
             if not p.is_folded and not p.is_all_in and p.name != game_state.current_player.name),
            default=0
        )
        max_raise = min(player_stack, max_opponent_stack, game_state.pot['total'] * 2)
        # Collar min_raise to not exceed what's actually possible
        min_raise = min(game_state.min_raise_amount, max_raise) if max_raise > 0 else 0

        # Use resilient AI call
        response_dict = self._get_ai_decision(
            message=message,
            valid_actions=player_options,
            call_amount=cost_to_call,
            min_raise=min_raise,
            max_raise=max_raise,
            should_speak=should_speak
        )
        
        # Clean response based on speaking decision
        cleaned_response = self.response_validator.clean_response(
            response_dict,
            {'should_speak': should_speak}
        )

        # Capture DecisionPlan for reflection system (if enabled)
        if self.prompt_config.strategic_reflection:
            # Convert PokerPhase enum to string for JSON serialization
            phase_str = self.state_machine.phase.name if hasattr(self.state_machine.phase, 'name') else str(self.state_machine.phase)
            plan = DecisionPlan(
                hand_number=self.current_hand_number or 0,
                phase=phase_str,
                player_name=self.player_name,
                hand_strategy=response_dict.get('hand_strategy'),
                inner_monologue=response_dict.get('inner_monologue', ''),
                action=response_dict.get('action', ''),
                amount=response_dict.get('raise_to', 0),
                pot_size=game_state.pot.get('total', 0),
                timestamp=datetime.now()
            )
            self._current_hand_plans.append(plan)

        print(json.dumps(cleaned_response, indent=4))
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
        decision_prompt = self._build_decision_prompt(message, context)

        # Track captures for linking
        parent_capture_id = [None]
        final_capture_id = [None]
        capture_enrichment = [None]

        def make_enricher(parent_id=None, error_type=None, correction_attempt=0):
            """Create an enricher callback with optional resilience fields."""
            def enrich_capture(capture_data: Dict) -> Dict:
                player = game_state.current_player
                cost_to_call = context.get('call_amount', 0)
                pot_total = game_state.pot.get('total', 0)
                player_stack = player.stack
                already_bet = player.bet
                big_blind = game_state.current_ante or 250

                stack_bb = player_stack / big_blind if big_blind > 0 else None
                already_bet_bb = already_bet / big_blind if big_blind > 0 else None

                enrichment = {
                    'phase': self.state_machine.current_phase.name if self.state_machine.current_phase else None,
                    'pot_total': pot_total,
                    'cost_to_call': cost_to_call,
                    'pot_odds': pot_total / cost_to_call if cost_to_call > 0 else None,
                    'player_stack': player_stack,
                    'stack_bb': round(stack_bb, 2) if stack_bb is not None else None,
                    'already_bet_bb': round(already_bet_bb, 2) if already_bet_bb is not None else None,
                    'community_cards': [str(c) for c in game_state.community_cards] if game_state.community_cards else [],
                    'player_hand': [str(c) for c in player.hand] if player.hand else [],
                    'valid_actions': valid_actions,
                    'prompt_config': self.prompt_config.to_dict() if self.prompt_config else None,
                    # Resilience fields
                    'parent_id': parent_id,
                    'error_type': error_type,
                    'correction_attempt': correction_attempt,
                    '_on_captured': lambda cid: final_capture_id.__setitem__(0, cid),
                }
                capture_data.update(enrichment)
                capture_enrichment[0] = enrichment
                return capture_data
            return enrich_capture

        # ========== ATTEMPT 1: Initial AI call ==========
        response_dict = None
        error_type = None
        original_response_json = None

        try:
            llm_response = self.assistant.chat_full(
                decision_prompt,
                json_format=True,
                hand_number=self.current_hand_number,
                prompt_template='decision',
                capture_enricher=make_enricher(),
            )
            original_response_json = llm_response.content
            parent_capture_id[0] = final_capture_id[0]
            self._last_llm_response = llm_response

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
            logger.warning(f"[RESILIENCE] {self.player_name}: Error detected ({error_type.value}), attempting recovery")

            # Generate error description for logging and correction prompt
            if error_type == DecisionErrorType.MALFORMED_JSON:
                error_description = "Could not parse JSON response. Please respond with valid JSON."
            else:
                error_description = describe_response_error(error_type, response_dict, valid_actions)

            # Mark original capture with error
            if parent_capture_id[0]:
                update_prompt_capture(
                    parent_capture_id[0],
                    error_type=error_type.value,
                    error_description=error_description
                )

            try:
                # Determine recovery prompt
                if error_type == DecisionErrorType.MALFORMED_JSON:
                    # Full retry with same prompt
                    recovery_prompt = decision_prompt
                    logger.info(f"[RESILIENCE] {self.player_name}: Full retry for malformed JSON")
                else:
                    # Targeted correction prompt
                    recovery_prompt = self.prompt_manager.render_correction_prompt(
                        original_response=original_response_json or str(response_dict),
                        error_description=error_description,
                        valid_actions=valid_actions,
                        context=context,
                    )
                    logger.info(f"[RESILIENCE] {self.player_name}: Targeted correction for {error_type.value}")

                # Make recovery call
                correction_response = self.assistant.chat_full(
                    recovery_prompt,
                    json_format=True,
                    hand_number=self.current_hand_number,
                    prompt_template='decision_correction',
                    capture_enricher=make_enricher(
                        parent_id=parent_capture_id[0],
                        error_type=error_type.value,
                        correction_attempt=1
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
                            parent_capture_id[0],
                            error_type=None,
                            error_description=None
                        )
                else:
                    logger.warning(f"[RESILIENCE] {self.player_name}: Recovery still has error ({correction_error.value})")
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
                            error_description=correction_error_description
                        )

            except Exception as e:
                logger.error(f"[RESILIENCE] {self.player_name}: Recovery attempt failed - {e}")

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
        self._analyze_decision(response_dict, context, final_capture_id[0])

        # Update capture with final action
        if final_capture_id[0]:
            action = response_dict.get('action')
            raise_amount = response_dict.get('raise_to') if action == 'raise' else None
            update_prompt_capture(final_capture_id[0], action_taken=action, raise_amount=raise_amount)

            # Compute and store auto-labels
            if self._persistence and capture_enrichment[0]:
                label_data = capture_enrichment[0].copy()
                label_data['action_taken'] = action
                self._persistence.compute_and_store_auto_labels(final_capture_id[0], label_data)

        return response_dict

    def _build_decision_prompt(self, message: str, context: Dict) -> str:
        """Build the decision prompt with situational guidance."""
        game_state = self.state_machine.game_state
        player = game_state.current_player
        pot_committed_info = None
        short_stack_info = None
        made_hand_info = None

        if self.prompt_config.situational_guidance:
            cost_to_call = context.get('call_amount', 0)
            pot_total = game_state.pot.get('total', 0)
            already_bet = player.bet
            player_stack = player.stack
            big_blind = game_state.current_ante or 250

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
                        'cost_to_call_bb': round(cost_to_call_bb, 1)
                    }

            if stack_bb < 3:
                short_stack_info = {'stack_bb': round(stack_bb, 1)}

            if game_state.community_cards:
                def card_to_str(c):
                    if isinstance(c, dict):
                        rank = c.get('rank', '')
                        suit = c.get('suit', '')[0].lower() if c.get('suit') else ''
                        if rank == '10':
                            rank = 'T'
                        return f"{rank}{suit}"
                    else:
                        s = str(c)
                        suit_map = {'♠': 's', '♥': 'h', '♦': 'd', '♣': 'c'}
                        for symbol, letter in suit_map.items():
                            s = s.replace(symbol, letter)
                        s = s.replace('10', 'T')
                        return s

                hole_cards = [card_to_str(c) for c in player.hand] if player.hand else []
                community_cards = [card_to_str(c) for c in game_state.community_cards]

                equity = calculate_quick_equity(hole_cards, community_cards)
                if equity is not None:
                    is_tilted = False
                    if self.psychology and self.psychology.emotional:
                        valence = self.psychology.emotional.valence
                        is_tilted = valence < -0.2

                    if equity >= 0.80:
                        hand_strength = evaluate_hand_strength(hole_cards, community_cards)
                        hand_name = hand_strength.split(' - ')[0] if hand_strength else 'a strong hand'
                        made_hand_info = {
                            'hand_name': hand_name,
                            'equity': round(equity * 100),
                            'is_tilted': is_tilted,
                            'tier': 'strong'
                        }
                    elif equity >= 0.65:
                        hand_strength = evaluate_hand_strength(hole_cards, community_cards)
                        hand_name = hand_strength.split(' - ')[0] if hand_strength else 'a decent hand'
                        made_hand_info = {
                            'hand_name': hand_name,
                            'equity': round(equity * 100),
                            'is_tilted': is_tilted,
                            'tier': 'moderate'
                        }

        return self.prompt_manager.render_decision_prompt(
            message=message,
            include_mind_games=self.prompt_config.mind_games,
            include_persona_response=self.prompt_config.persona_response,
            pot_committed_info=pot_committed_info,
            short_stack_info=short_stack_info,
            made_hand_info=made_hand_info
        )

    def _normalize_response(self, response_dict: Dict) -> Dict:
        """Normalize response: lowercase action, convert raise_to to int."""
        if 'action' in response_dict and response_dict['action']:
            response_dict['action'] = response_dict['action'].lower()

        if 'raise_to' not in response_dict:
            response_dict['raise_to'] = 0
        else:
            try:
                response_dict['raise_to'] = int(response_dict['raise_to'])
            except (ValueError, TypeError):
                response_dict['raise_to'] = 0

        return response_dict

    def _apply_final_fixes(self, response_dict: Dict, context: Dict, game_state) -> Dict:
        """Apply final fixes like extracting raise amount from persona_response."""
        import re
        valid_actions = context.get('valid_actions', [])

        # Fix raise with 0 amount by extracting from persona_response
        if response_dict.get('action') == 'raise' and response_dict.get('raise_to', 0) == 0:
            persona_response = response_dict.get('persona_response', '')
            highest_bet = game_state.highest_bet
            min_raise = context.get('min_raise', MIN_RAISE)

            raise_match = re.search(r'raise.*?\$(\d+)', persona_response, re.IGNORECASE)
            if raise_match:
                mentioned_amount = int(raise_match.group(1))
                response_dict['raise_to'] = mentioned_amount
                response_dict['raise_amount_corrected'] = True
                logger.warning(f"[RAISE_CORRECTION] {self.player_name} said 'raise to ${mentioned_amount}'")
            else:
                min_raise_to = highest_bet + min_raise
                response_dict['raise_to'] = min_raise_to
                response_dict['raise_amount_corrected'] = True
                logger.warning(f"[RAISE_CORRECTION] {self.player_name} raise with 0, defaulting to ${min_raise_to}")

        # Validate action is in valid_actions
        if valid_actions and response_dict.get('action') not in valid_actions:
            logger.warning(f"AI chose invalid action {response_dict['action']}, validating...")
            validated = validate_ai_response(response_dict, valid_actions)
            response_dict['action'] = validated['action']
            if response_dict.get('raise_to', 0) == 0:
                response_dict['raise_to'] = validated.get('raise_to', 0)

        return response_dict

    def _analyze_decision(self, response_dict: Dict, context: Dict, capture_id: Optional[int] = None) -> None:
        """Analyze decision quality and save to database.

        This runs for EVERY AI decision to track quality metrics.

        Args:
            response_dict: AI response with action and optional raise_to
            context: Game context dictionary
            capture_id: Optional ID of the prompt capture for linking
        """
        if not self._persistence:
            return

        try:
            from poker.decision_analyzer import get_analyzer

            game_state = self.state_machine.game_state
            player = game_state.current_player

            # Get cards in format equity calculator understands
            def card_to_string(c):
                """Convert card (dict or Card object) to short string like '8h'."""
                if isinstance(c, dict):
                    rank = c.get('rank', '')
                    suit = c.get('suit', '')[0].lower() if c.get('suit') else ''
                    # Handle 10 -> T
                    if rank == '10':
                        rank = 'T'
                    return f"{rank}{suit}"
                else:
                    # Card object - use str() which gives "8♥" format
                    s = str(c)
                    # Convert Unicode suits to letters
                    suit_map = {'♠': 's', '♥': 'h', '♦': 'd', '♣': 'c'}
                    for symbol, letter in suit_map.items():
                        s = s.replace(symbol, letter)
                    # Handle 10 -> T
                    s = s.replace('10', 'T')
                    return s

            community_cards = [card_to_string(c) for c in game_state.community_cards] if game_state.community_cards else []
            player_hand = [card_to_string(c) for c in player.hand] if player.hand else []

            # Count opponents still in hand
            opponents_in_hand = [
                p for p in game_state.players
                if not p.is_folded and p.name != player.name
            ]
            num_opponents = len(opponents_in_hand)

            # Get positions for range-based equity calculation
            table_positions = game_state.table_positions
            position_by_name = {name: pos for pos, name in table_positions.items()}
            player_position = position_by_name.get(self.player_name)
            opponent_positions = [
                position_by_name.get(p.name, "button")  # Default to button (widest range) if unknown
                for p in opponents_in_hand
            ]

            # Build OpponentInfo objects with observed stats and personality data
            from .hand_ranges import build_opponent_info
            opponent_infos = []
            for opp in opponents_in_hand:
                opp_position = position_by_name.get(opp.name, "button")

                # Get observed stats from opponent model manager
                opp_model_data = None
                if self.opponent_model_manager:
                    opp_model = self.opponent_model_manager.get_model(self.player_name, opp.name)
                    if opp_model and opp_model.tendencies:
                        opp_model_data = opp_model.tendencies.to_dict()

                opponent_infos.append(build_opponent_info(
                    name=opp.name,
                    position=opp_position,
                    opponent_model=opp_model_data,
                ))

            # Get request_id from last LLM response
            llm_response = getattr(self, '_last_llm_response', None)
            request_id = llm_response.request_id if llm_response else None

            analyzer = get_analyzer()
            analysis = analyzer.analyze(
                game_id=self.game_id,
                player_name=self.player_name,
                hand_number=self.current_hand_number,
                phase=self.state_machine.current_phase.name if self.state_machine.current_phase else None,
                player_hand=player_hand,
                community_cards=community_cards,
                pot_total=game_state.pot.get('total', 0),
                cost_to_call=context.get('call_amount', 0),
                player_stack=player.stack,
                num_opponents=num_opponents,
                action_taken=response_dict.get('action'),
                raise_amount=response_dict.get('raise_to'),
                request_id=request_id,
                capture_id=capture_id,
                player_position=player_position,
                opponent_positions=opponent_positions,
                opponent_infos=opponent_infos,
            )

            self._persistence.save_decision_analysis(analysis)
            equity_str = f"{analysis.equity:.2f}" if analysis.equity is not None else "N/A"
            logger.debug(
                f"[DECISION_ANALYSIS] {self.player_name}: {analysis.decision_quality} "
                f"(equity={equity_str}, ev_lost={analysis.ev_lost:.0f})"
            )
        except Exception as e:
            logger.warning(f"[DECISION_ANALYSIS] Failed to analyze decision: {e}")

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

    def _build_memory_context(self, game_state, include_session: bool = True,
                               include_opponents: bool = True) -> str:
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
                p.name for p in game_state.players
                if p.name != self.player_name and not p.is_folded
            ]
            opponent_ctx = self.opponent_model_manager.get_table_summary(
                self.player_name, opponents, OPPONENT_SUMMARY_TOKENS
            )
            if opponent_ctx:
                parts.append(f"=== Opponent Intel ===\n{opponent_ctx}")

        return "\n\n".join(parts) if parts else ""


    def _build_chattiness_guidance(self, chattiness: float, should_speak: bool,
                                  speaking_context: Dict, valid_actions: List[str]) -> str:
        """Build guidance for AI about speaking behavior."""
        guidance = f"Your chattiness level: {chattiness:.1f}/1.0\n"

        if should_speak:
            guidance += "You feel inclined to say something this turn.\n"
            style = self.chattiness_manager.suggest_speaking_style(
                self.player_name, chattiness
            )
            guidance += f"Speaking style: {style}\n"
        else:
            guidance += "You don't feel like talking this turn. Stay quiet.\n"
            guidance += "Focus on your action and inner thoughts only.\n"
            guidance += "DO NOT include 'persona_response' or 'physical' in your response.\n"

        # Add context about conversation flow
        if speaking_context['turns_since_spoke'] > 3:
            guidance += f"(You haven't spoken in {speaking_context['turns_since_spoke']} turns)\n"
        if speaking_context['table_silent_turns'] > 2:
            guidance += "(The table has been quiet for a while)\n"

        return guidance


def human_player_action(ui_data: dict, player_options: List[str]) -> Dict:
    """
    Console UI is used to update the player with the relevant game state info and receives input.
    This will return a tuple as ( action, amount ) for the players bet.
    """
    # Get user choice
    player_choice = None
    while player_choice not in player_options:
        player_choice = input(f"{ui_data['player_name']}, what would you like to do? ").lower().replace("-","_")
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
            [ensure_card(c) for c in player_hand])
    except:
        print(f"{player_name} has no cards.")
        raise ValueError('Missing cards. Please check your hand.')

    # Display information to the user
    if len(ui_data['community_cards']) > 0:
        rendered_community_cards = CardRenderer().render_cards(
            [ensure_card(c) for c in ui_data['community_cards']])
        print(f"\nCommunity Cards:\n{rendered_community_cards}")

    print(f"Your Hand:\n{rendered_hole_cards}")
    print(f"Pot: {ui_data['pot_total']}")
    print(f"Your Stack: {ui_data['player_stack']}")
    print(f"Cost to Call: {ui_data['cost_to_call']}")
    print(f"Options: {player_options}\n")


def _ensure_card(c):
    """Convert card to Card object if it's a dict, otherwise return as-is."""
    return c if isinstance(c, Card) else Card(c['rank'], c['suit'])


def convert_game_to_hand_state(game_state, player: Player, phase, messages,
                               include_pot_odds: bool = True,
                               include_hand_strength: bool = True):
    """
    Convert game state to a human-readable prompt message.

    Args:
        game_state: Current game state
        player: Current player
        phase: Current betting phase
        messages: Recent actions/chat
        include_pot_odds: Whether to include pot odds guidance
        include_hand_strength: Whether to include hand strength evaluation
    """
    # Currently used values
    persona = player.name
    # attitude = player.attitude
    # confidence = player.confidence
    table_positions = game_state.table_positions
    opponent_status = game_state.opponent_status
    current_round = phase
    community_cards = [str(_ensure_card(c)) for c in game_state.community_cards]
    # opponents = [p.name for p in game_state.players if p.name != player.name]
    # number_of_opponents = len(opponents)
    player_money = player.stack
    player_positions = [position for position, name in table_positions.items() if name == player.name]
    current_situation = f"The {current_round} cards have just been dealt"
    hole_cards = [str(_ensure_card(c)) for c in player.hand]
    current_pot = game_state.pot['total']
    current_bet = game_state.current_player.bet
    raw_cost_to_call = game_state.highest_bet - game_state.current_player.bet
    # Effective cost is capped at player's stack (they can only risk what they have)
    cost_to_call = min(raw_cost_to_call, player_money)
    player_options = game_state.current_player_options

    # create a list of the action comments and then send them to the table manager to summarize
    # action_comment_list = [action.action_comment for action in hand_state["poker_actions"]]
    # action_summary = "We're just getting started! You're first to go."
    # if len(action_comment_list) > 0:
    #     action_summary = hand_state["table_manager"].summarize_actions_for_player(
    #         action_comment_list[-number_of_opponents:], self.name)
    action_summary = messages

    # Evaluate hand strength - preflop uses classification, post-flop uses eval7
    hand_strength_line = ""
    if include_hand_strength:
        if community_cards:
            hand_strength = evaluate_hand_strength(hole_cards, community_cards)
        else:
            hand_strength = classify_preflop_hand(hole_cards)
        hand_strength_line = f"Your Hand Strength: {hand_strength}\n" if hand_strength else ""

    persona_state = (
        f"Persona: {persona}\n"
        # f"Attitude: {attitude}\n"
        # f"Confidence: {confidence}\n"
        f"Your Cards: {hole_cards}\n"
        f"{hand_strength_line}"
        f"Your Money: {player_money}\n"
    )

    hand_state = (
        # f"{current_situation}\n"
        f"Current Round: {current_round}\n"
        f"Community Cards: {community_cards}\n"
        f"Table Positions: {table_positions}\n"
        f"Opponent Status:\n{opponent_status}\n"
        f"Recent Actions:\n{action_summary}\n"
    )

    # Blind levels
    big_blind = game_state.current_ante
    small_blind = big_blind // 2
    blinds_remaining = player_money / big_blind if big_blind > 0 else float('inf')

    pot_state = (
        f"Pot Total: ${current_pot}\n"
        f"How much you've bet: ${current_bet}\n"
        f"Your cost to call: ${cost_to_call}\n"
        f"Blinds: ${small_blind}/${big_blind}\n"
        f"Your stack in big blinds: {blinds_remaining:.1f} BB\n"
    )

    # Calculate pot odds for clearer decision making (if enabled)
    pot_odds_guidance = ""
    if include_pot_odds:
        if cost_to_call > 0:
            pot_odds = current_pot / cost_to_call
            equity_needed = 100 / (pot_odds + 1)
            pot_odds_guidance = (
                f"POT ODDS: You're getting {pot_odds:.1f}:1 odds (${current_pot} pot / ${cost_to_call} to call). "
                f"You only need {equity_needed:.0f}% equity to break even on a call. "
            )
            if pot_odds >= 10:
                pot_odds_guidance += f"With {pot_odds:.0f}:1 odds, you should rarely fold - you only need to win 1 in {pot_odds+1:.0f} times."
            elif pot_odds >= 4:
                pot_odds_guidance += "These are favorable odds for calling with reasonable hands."
        else:
            pot_odds_guidance = "You can check for free - no cost to see more cards."

    hand_update_message = persona_state + hand_state + pot_state + pot_odds_guidance + "\n" + (
        f"You cannot bet more than you have, ${player_money}.\n"
        f"You must select from these options: {player_options}\n"
        f"Your table position: {player_positions}\n"
        f"What is your move, {persona}?\n\n"
    )

    return hand_update_message
