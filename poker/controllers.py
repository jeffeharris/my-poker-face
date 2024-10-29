import json
from typing import List, Optional, Dict

from card import Card, CardRenderer
from functional_poker import PokerStateMachine
from old_files.poker_player import AIPokerPlayer
from utils import prepare_ui_data


class ConsolePlayerController:
    def __init__(self, player_name, state_machine: PokerStateMachine = None):
        self.player_name = player_name
        self.state_machine = state_machine

    def decide_action(self) -> Dict:
        ui_data, player_options = prepare_ui_data(self.state_machine.game_state)
        display_player_turn_update(ui_data, player_options)
        return human_player_action(ui_data, player_options)


class AIPlayerController:
    def __init__(self, player_name, state_machine=None, ai_temp=0.9):
        self.player_name = player_name
        self.state_machine = state_machine
        self.ai_temp = ai_temp
        self.assistant = AIPokerPlayer(player_name, ai_temp=ai_temp).assistant

    def decide_action(self) -> Dict:
        message = json.dumps(prepare_ui_data(self.state_machine.game_state))
        response_json = self.assistant.chat(
            message + "\nPlease only respond with the JSON, not the text with back quotes.")
        try:
            response_dict = json.loads(response_json)
            if not all(key in response_dict for key in ('action', 'adding_to_pot', 'persona_response', 'physical')):
                raise ValueError("AI response is missing required keys.")
        except json.JSONDecodeError:
            raise ValueError(f"Error decoding AI response: {response_json}")
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


# def convert_game_to_hand_state(game_state, player: AIPokerPlayer):
#     # Currently used values
#     persona = player.name
#     attitude = player.attitude
#     confidence = player.confidence
#     table_positions = hand_state["table_positions"]                         # TODO: create table positions
#     opponent_status = hand_state["opponent_status"]                         # TODO: create opponent status
#     current_round = hand_state["current_phase"]                             # TODO: assign current round here
#     community_cards = [str(card) for card in hand_state["community_cards"]] # TODO: create community cards as Card objects
#     opponents = game_state.players
#     number_of_opponents = len(opponents) - 1
#     player_money = player.money
#     # TODO: <FEATURE> decide what to do with this position idea
#     # position = hand_state["positions"][self]
#     current_situation = hand_state["current_situation"]
#     hole_cards = [str(card) for card in player.cards]
#     current_pot = hand_state["current_pot"]
#     # current_bet = current_pot.current_bet     # removed this because i wasn't able to get the ai player to understand how to bet when i included this, the pot, the cost to call etc.
#     cost_to_call = game_state.highest_bet - game_state.current_player['bet']
#     player_options = game_state.current_player_options
#
#     # create a list of the action comments and then send them to the table manager to summarize
#     action_comment_list = [action.action_comment for action in hand_state["poker_actions"]]
#     action_summary = "We're just getting started! You're first to go."
#     if len(action_comment_list) > 0:
#         action_summary = hand_state["table_manager"].summarize_actions_for_player(
#             action_comment_list[-number_of_opponents:], self.name)
#
#     persona_state = (
#         f"Persona: {persona}\n"
#         f"Attitude: {attitude}\n"
#         f"Confidence: {confidence}\n"
#         f"Your Cards: {hole_cards}\n"
#         f"Your Money: {player_money}\n"
#     )
#
#     hand_state = (
#         f"{current_situation}\n"
#         f"Current Round: {current_round}\n"
#         f"Community Cards: {community_cards}\n"
#         f"Table Positions: {table_positions}\n"
#         f"Opponent Status:\n{opponent_status}\n"
#         f"Actions since your last turn: {action_summary}\n"
#     )
#
#     pot_state = (
#         f"Pot Total: ${current_pot.total}\n"
#         f"How much you've bet: ${current_pot.get_player_pot_amount(self.name)}\n"
#         f"Your cost to call: ${cost_to_call}\n"
#     )
#
#     hand_update_message = persona_state + hand_state + pot_state + (
#         # f"You have {hole_cards} in your hand.\n"  # The current bet is ${current_bet} and
#         # f"Remember, you're feeling {attitude} and {confidence}.\n"
#         f"Consider the strength of your hand relative to the pot and the likelihood that your opponents might have stronger hands. "
#         f"Preserve your chips for when the odds are in your favor, and remember that sometimes folding or checking is the best move. "
#         f"You cannot bet more than you have, ${player_money}.\n"
#         f"You must select from these options: {player_options}\n"
#         f"What is your move, {persona}?\n\n"
#     )
#
#     return hand_update_message
