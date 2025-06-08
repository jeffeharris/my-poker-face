# ui_console.py
import os
from dotenv import load_dotenv

from core.card import CardRenderer
from poker.controllers import ConsolePlayerController, AIPlayerController
from poker.poker_game import *
from poker.poker_state_machine import PokerStateMachine
from poker.utils import get_celebrities

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

def display_ai_player_action(player_name: str, response_dict: dict):
    player_choice = response_dict['action']
    amount = response_dict.get('adding_to_pot', 0)
    player_message = response_dict.get('persona_response', '')
    player_physical_description = response_dict.get('physical', '')
    
    print(f"\n{'-'*20}\n")
    print(f"{player_name} chose to {player_choice}{' by ' + str(amount) if amount > 0 else ''}")
    
    # Only show message if AI actually spoke
    if player_message and player_message != '...':
        print(f"\"{player_message}\"")
    if player_physical_description and player_physical_description != '...':
        print(f"{player_physical_description}")
    
    print(f"\n{'-'*20}\n")


def display_game_state(game_state, include_deck: bool = False):
    # Convert game_state to JSON and pretty print to console
    game_state_json = json.loads(json.dumps(game_state, default=lambda o: o.__dict__))
    if not include_deck:
        del game_state_json['deck']
    print(json.dumps(game_state_json, indent=4))


def display_hand_winner(info):
    print(f"{info['winning_player_names']} wins the pot of {info['pot_total']} with {info['winning_hand']}!\n")


def display_end_game(info):
    print(f"\n{info['message']}\n")


def display_cards(cards, display_text: Optional[str] = None):
    """
    Prints the rendered cards to the console. Accepts a tuple of cards from the game_state.
    Converts the card tuple to Card class objects and prints to the console
    """
    rendered_cards = CardRenderer().render_cards([Card(c['rank'], c['suit']) for c in cards])

    if display_text is not None:
        print(f"\n{display_text}:")
    print(f"\n{rendered_cards}\n")


if __name__ == '__main__':
    # Get AI player names and initialize the game instance and state machine
    ai_player_names = get_celebrities(shuffled=True)[:NUM_AI_PLAYERS]
    state_machine = PokerStateMachine(game_state=initialize_game_state(player_names=ai_player_names))

    # Create a controller for each player in the game and add to a map of name -> controller
    controllers = {}
    for player in state_machine.game_state.players:
        if player.is_human:
            new_controller = ConsolePlayerController(player.name, state_machine)
        else:
            new_controller = AIPlayerController(player.name, state_machine)
        controllers[player.name] = new_controller

    while len(state_machine.game_state.players) > 1:
        # Run the game
        state_machine.run_until_player_action()
        controller = controllers[state_machine.game_state.current_player.name]
        player_response_dict = controller.decide_action()
        action, amount = (player_response_dict['action'], player_response_dict['adding_to_pot'])
        current_player = state_machine.game_state.current_player
        if not current_player.is_human:
            display_ai_player_action(state_machine.game_state.current_player.name, player_response_dict)
        state_machine.game_state = play_turn(state_machine.game_state, action, amount)
        state_machine.game_state = advance_to_next_active_player(state_machine.game_state)
