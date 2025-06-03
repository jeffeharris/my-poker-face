import json
from typing import List, Optional, Dict
import logging

from core.card import Card, CardRenderer
from .poker_game import Player
from .poker_state_machine import PokerStateMachine
from .poker_player import AIPokerPlayer
from .utils import prepare_ui_data
from .prompt_manager import PromptManager
from .ai_resilience import (
    with_ai_fallback, 
    expects_json,
    parse_json_response,
    validate_ai_response,
    get_fallback_chat_response,
    AIFallbackStrategy
)

logger = logging.getLogger(__name__)


class ConsolePlayerController:
    def __init__(self, player_name, state_machine: PokerStateMachine = None):
        self.player_name = player_name
        self.state_machine = state_machine

    def decide_action(self) -> Dict:
        ui_data, player_options = prepare_ui_data(self.state_machine.game_state)
        display_player_turn_update(ui_data, player_options)
        return human_player_action(ui_data, player_options)


def summarize_messages(messages: List[Dict[str, str]], name: str) -> List[str]:
    # Find the index of the last message from the Player with 'name'
    # Search the list of messages for the last message from the player
    # Change the message to a string from a dict
    last_message_index = -1
    for i in range(len(messages) - 1, -1, -1):  # Iterate backwards
        if messages[i]['sender'] == name:
            last_message_index = i
            break

    # Convert messages to strings with less text than the dict representation
    converted_messages = []
    for msg in messages:
        converted_messages.append(f"{msg['sender']}: {msg['content']}")

    # Return the messages since the player's last message
    if last_message_index >= 0:
        messages_since_last_message = converted_messages[last_message_index:]
        return messages_since_last_message
    else:
        return converted_messages



class AIPlayerController:
    def __init__(self, player_name, state_machine=None, ai_temp=0.9):
        self.player_name = player_name
        self.state_machine = state_machine
        self.ai_temp = ai_temp
        self.ai_player = AIPokerPlayer(player_name, ai_temp=ai_temp)
        self.assistant = self.ai_player.assistant
        self.prompt_manager = PromptManager()
        # Store personality traits for fallback behavior
        self.personality_traits = self.ai_player.personality_config.get('personality_traits', {})

    def decide_action(self, game_messages) -> Dict:
        game_state = self.state_machine.game_state
        game_messages = summarize_messages(
            game_messages,
            self.player_name)
        message = convert_game_to_hand_state(
            game_state,
            game_state.current_player,
            self.state_machine.phase,
            game_messages)
        print(message)
        
        # Get valid actions and context for fallback
        player_options = game_state.current_player_options
        cost_to_call = game_state.highest_bet - game_state.current_player.bet
        player_stack = game_state.current_player.stack
        
        # Use resilient AI call
        response_dict = self._get_ai_decision(
            message=message,
            valid_actions=player_options,
            call_amount=cost_to_call,
            min_raise=10,  # TODO: Calculate from game rules
            max_raise=min(player_stack, game_state.pot['total'] * 2)
        )
        
        print(json.dumps(response_dict, indent=4))
        return response_dict
    
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
        
        response_json = self.assistant.chat(decision_prompt)
        response_dict = parse_json_response(response_json)
        
        # Validate response has required keys
        required_keys = ('action', 'adding_to_pot', 'persona_response', 'physical')
        if not all(key in response_dict for key in required_keys):
            # Try to fix missing keys
            response_dict.setdefault('action', 'fold')
            response_dict.setdefault('adding_to_pot', 0)
            response_dict.setdefault('persona_response', get_fallback_chat_response(self.player_name))
            response_dict.setdefault('physical', "...")
            logger.warning(f"AI response was missing keys, filled with defaults: {response_dict}")
        
        # Validate action is valid
        valid_actions = context.get('valid_actions', [])
        if valid_actions and response_dict['action'] not in valid_actions:
            logger.warning(f"AI chose invalid action {response_dict['action']}, validating...")
            validated = validate_ai_response(response_dict, valid_actions)
            response_dict['action'] = validated['action']
            response_dict['adding_to_pot'] = validated['amount']
        
        return response_dict


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

    try:
        # Render the player's cards using the CardRenderer.
        rendered_hole_cards = CardRenderer().render_hole_cards(
            [Card(c['rank'], c['suit']) for c in player_hand])
    except:
        print(f"{player_name} has no cards.")
        raise ValueError('Missing cards. Please check your hand.')

    # Display information to the user
    if len(ui_data['community_cards']) > 0:
        rendered_community_cards = CardRenderer().render_cards(
            [Card(c['rank'], c['suit']) for c in ui_data['community_cards']])
        print(f"\nCommunity Cards:\n{rendered_community_cards}")

    print(f"Your Hand:\n{rendered_hole_cards}")
    print(f"Pot: {ui_data['pot_total']}")
    print(f"Your Stack: {ui_data['player_stack']}")
    print(f"Cost to Call: {ui_data['cost_to_call']}")
    print(f"Options: {player_options}\n")


def convert_game_to_hand_state(game_state, player: Player, phase, messages):
    # Currently used values
    persona = player.name
    # attitude = player.attitude
    # confidence = player.confidence
    table_positions = game_state.table_positions
    opponent_status = game_state.opponent_status
    current_round = phase
    community_cards = [str(card) for card in [Card(c['rank'], c['suit']) for c in game_state.community_cards]]
    # opponents = [p.name for p in game_state.players if p.name != player.name]
    # number_of_opponents = len(opponents)
    player_money = player.stack
    player_positions = [position for position, name in table_positions.items() if name == player.name]
    current_situation = f"The {current_round} cards have just been dealt"
    hole_cards = [str(card) for card in [Card(c['rank'], c['suit']) for c in player.hand]]
    current_pot = game_state.pot['total']
    current_bet = game_state.current_player.bet
    cost_to_call = game_state.highest_bet - game_state.current_player.bet
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
        f"Actions since your last turn: {action_summary}\n"
    )

    pot_state = (
        f"Pot Total: ${current_pot}\n"
        f"How much you've bet: ${current_bet}\n"
        f"Your cost to call: ${cost_to_call}\n"
    )

    hand_update_message = persona_state + hand_state + pot_state + (
        f"Consider your table position and the strength of your hand relative to the pot and the likelihood that your opponents might have stronger hands. "
        f"Preserve your chips for when the odds are in your favor, and remember that sometimes folding or checking is the best move. "
        f"You cannot bet more than you have, ${player_money}.\n"
        f"You must select from these options: {player_options}\n"
        f"Your table position: {player_positions}\n"
        f"What is your move, {persona}?\n\n"
    )

    return hand_update_message
