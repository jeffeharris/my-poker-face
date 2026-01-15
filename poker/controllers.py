import json
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
from .ai_resilience import (
    with_ai_fallback,
    expects_json,
    parse_json_response,
    validate_ai_response,
    get_fallback_chat_response,
    AIFallbackStrategy
)
from .player_psychology import PlayerPsychology

logger = logging.getLogger(__name__)


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
                 game_id=None, owner_id=None, debug_capture=False, persistence=None):
        self.player_name = player_name
        self.state_machine = state_machine
        self.llm_config = llm_config or {}
        self.game_id = game_id
        self.owner_id = owner_id
        self.debug_capture = debug_capture
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
        
    def get_current_personality_traits(self):
        """Get current trait values from psychology (elastic personality)."""
        return self.psychology.traits

    @property
    def personality_traits(self):
        """Compatibility property for ai_resilience fallback."""
        return self.psychology.traits

    def decide_action(self, game_messages) -> Dict:
        game_state = self.state_machine.game_state

        # Clear conversation memory before each decision to avoid context overload
        # Table chatter is preserved via game_messages -> Recent Actions
        # Mental state is preserved via PlayerPsychology (separate system)
        if hasattr(self, 'assistant') and self.assistant and self.assistant.memory:
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
        
        # Build message with chattiness context
        message = convert_game_to_hand_state(
            game_state,
            game_state.current_player,
            self.state_machine.phase,
            game_messages)

        # Get valid actions early so we can include in guidance
        player_options = game_state.current_player_options

        # Inject memory context if available
        memory_context = self._build_memory_context(game_state)
        if memory_context:
            message = memory_context + "\n\n" + message

        # Add chattiness guidance to message
        chattiness_guidance = self._build_chattiness_guidance(
            chattiness, should_speak, speaking_context, player_options
        )
        message = message + "\n\n" + chattiness_guidance

        # Inject emotional state context (before tilt effects)
        emotional_section = self.psychology.get_prompt_section()
        if emotional_section:
            message = emotional_section + "\n\n" + message

        # Apply tilt effects if player is tilted (after emotional state)
        message = self.psychology.apply_tilt_effects(message)

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
        
        print(json.dumps(cleaned_response, indent=4))
        return cleaned_response
    
    @with_ai_fallback(fallback_strategy=AIFallbackStrategy.MIMIC_PERSONALITY)
    @expects_json
    def _get_ai_decision(self, message: str, **context) -> Dict:
        """Get AI decision with automatic fallback on failure"""
        # Store context for fallback
        self._fallback_context = context

        # Use the prompt manager for the decision prompt
        decision_prompt = self.prompt_manager.render_prompt(
            'decision',
            message=message
        )

        # Create enricher callback with game state for capture
        def enrich_capture(capture_data: Dict) -> Dict:
            """Add game state to capture data."""
            game_state = self.state_machine.game_state
            player = game_state.current_player

            cost_to_call = context.get('call_amount', 0)
            pot_total = game_state.pot.get('total', 0)

            capture_data.update({
                'phase': self.state_machine.current_phase.name if self.state_machine.current_phase else None,
                'pot_total': pot_total,
                'cost_to_call': cost_to_call,
                'pot_odds': pot_total / cost_to_call if cost_to_call > 0 else None,
                'player_stack': player.stack,
                'community_cards': [str(c) for c in game_state.community_cards] if game_state.community_cards else [],
                'player_hand': [str(c) for c in player.hand] if player.hand else [],
                'valid_actions': context.get('valid_actions', []),
            })
            return capture_data

        # Use JSON mode for more reliable structured responses
        llm_response = self.assistant.chat_full(
            decision_prompt,
            json_format=True,
            hand_number=self.current_hand_number,
            prompt_template='decision',
            capture_enricher=enrich_capture,
        )
        response_json = llm_response.content
        response_dict = parse_json_response(response_json)

        # Store LLM response for capture (latency, tokens, etc.)
        self._last_llm_response = llm_response
        
        # Validate response has required keys (only action is truly required)
        required_keys = ('action',)
        valid_actions = context.get('valid_actions', [])
        if not all(key in response_dict for key in required_keys):
            # Try to fix missing keys - prefer check over fold (folding when you can check is never correct)
            default_action = 'check' if 'check' in valid_actions else 'fold'
            response_dict.setdefault('action', default_action)
            logger.warning(f"AI response was missing action, defaulted to {default_action}")
        
        # Set default for adding_to_pot if not present
        if 'adding_to_pot' not in response_dict:
            response_dict['adding_to_pot'] = 0
        
        # Normalize action to lowercase for consistency (before validation checks)
        if 'action' in response_dict:
            response_dict['action'] = response_dict['action'].lower()
        
        # Fix common AI mistake: saying "raise" but setting adding_to_pot to 0
        if response_dict.get('action') == 'raise' and response_dict.get('adding_to_pot', 0) == 0:
            # Try to extract amount from persona_response
            import re
            persona_response = response_dict.get('persona_response', '')
            
            # Look for patterns like "raise by $500" or "raise you $500" or "raise to $500"
            raise_match = re.search(r'raise.*?\$(\d+)', persona_response, re.IGNORECASE)
            if raise_match:
                mentioned_amount = int(raise_match.group(1))
                
                # Check if it's "raise to" vs "raise by"
                if 'raise to' in persona_response.lower():
                    # Convert "raise to" to "raise by"
                    cost_to_call = context.get('call_amount', 0)
                    response_dict['adding_to_pot'] = max(10, mentioned_amount - cost_to_call)
                    response_dict['raise_amount_corrected'] = True
                    logger.warning(f"[RAISE_CORRECTION] {self.player_name} said 'raise to ${mentioned_amount}', converting to raise by ${response_dict['adding_to_pot']} (cost to call: ${cost_to_call})")
                else:
                    # Direct "raise by" amount
                    response_dict['adding_to_pot'] = mentioned_amount
                    response_dict['raise_amount_corrected'] = True
                    logger.warning(f"[RAISE_CORRECTION] {self.player_name} said raise but adding_to_pot was 0, extracted ${mentioned_amount} from persona_response")
            else:
                # Default to minimum raise
                response_dict['adding_to_pot'] = context.get('min_raise', MIN_RAISE)
                response_dict['raise_amount_corrected'] = True
                logger.warning(f"[RAISE_CORRECTION] {self.player_name} chose raise with 0 amount and no amount in message, defaulting to minimum raise of ${response_dict['adding_to_pot']}")
        
        # Validate action is valid
        if valid_actions and response_dict['action'] not in valid_actions:
            logger.warning(f"AI chose invalid action {response_dict['action']}, validating...")
            validated = validate_ai_response(response_dict, valid_actions)
            response_dict['action'] = validated['action']
            # Preserve adding_to_pot if it was set, otherwise use validated value
            if response_dict.get('adding_to_pot', 0) == 0:
                response_dict['adding_to_pot'] = validated.get('adding_to_pot', 0)

        # Analyze decision quality (always, for monitoring)
        self._analyze_decision(response_dict, context)

        # Update capture with action_taken (now that we've parsed the response)
        if llm_response.capture_id:
            from core.llm.tracking import update_prompt_capture
            action = response_dict.get('action')
            raise_amount = response_dict.get('adding_to_pot') if action == 'raise' else None
            update_prompt_capture(llm_response.capture_id, action_taken=action, raise_amount=raise_amount)

        return response_dict

    def _analyze_decision(self, response_dict: Dict, context: Dict) -> None:
        """Analyze decision quality and save to database.

        This runs for EVERY AI decision to track quality metrics.
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
                raise_amount=response_dict.get('adding_to_pot'),
                request_id=request_id,
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

    def _build_memory_context(self, game_state) -> str:
        """Build context from session memory and opponent models for injection into prompts."""
        parts = []

        # Session context (recent outcomes, streak, observations)
        if self.session_memory:
            session_ctx = self.session_memory.get_context_for_prompt(MEMORY_CONTEXT_TOKENS)
            if session_ctx:
                parts.append(f"=== Your Session ===\n{session_ctx}")

        # Opponent summaries
        if self.opponent_model_manager:
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
        "adding_to_pot": bet_amount,
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


def convert_game_to_hand_state(game_state, player: Player, phase, messages):
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

    persona_state = (
        f"Persona: {persona}\n"
        # f"Attitude: {attitude}\n"
        # f"Confidence: {confidence}\n"
        f"Your Cards: {hole_cards}\n"
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

    # Calculate pot odds for clearer decision making
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
