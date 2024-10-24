# ui_console.py
import os
from dotenv import load_dotenv

from card import CardRenderer
from controllers import ConsolePlayerController, AIPlayerController, human_player_action, display_player_turn_update

from functional_poker import *
from old_files.poker_player import AIPokerPlayer
from utils import get_celebrities, prepare_ui_data

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

def display_ai_player_action(player_name: str, response_dict: dict):
    player_choice = response_dict['action']
    amount = response_dict['adding_to_pot']
    player_message = response_dict['persona_response']
    player_physical_description = response_dict['physical']
    print(f"\n{'-'*20}\n")
    print(f"{player_name} chose to {player_choice} by {amount}")
    print(f"\"{player_message}\"")
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


def handle_player_action(game_state):
    ui_data, player_options = prepare_ui_data(game_state)

    if game_state.current_player.is_human:
        display_player_turn_update(ui_data=ui_data, player_options=player_options)
        action, amount = human_player_action(ui_data, player_options)
    else:
        # ai_controller = AIPlayerController(game_state.current_player.name)
        # action, amount, response_dict = ai_player_action(game_state, ai_controller.assistant)
        ai_assistant = AIPokerPlayer(name=game_state.current_player.name,
                                     starting_money=game_state.current_player.stack).assistant
        ai_assistant.api_key = OPENAI_API_KEY
        response_dict = ai_player_action(game_state=game_state, ai_assistant=ai_assistant)
        display_ai_player_action(game_state.current_player.name, response_dict)
        action, amount = (response_dict['action'], response_dict['amount'])

    game_state = play_turn(game_state, action, amount)
    game_state = advance_to_next_active_player(game_state)

    return game_state


if __name__ == '__main__':
    # Get AI player names and initialize the game instance
    ai_player_names = get_celebrities(shuffled=True)[:NUM_AI_PLAYERS]
    game_instance = initialize_game_state(player_names=ai_player_names)

    # Create a controller for each player in the game.
    # Could consider a single controller for the AI
    controllers = []
    for player in game_instance.players:
        if player.is_human:
            controllers.append(ConsolePlayerController(player.name))
        else:
            controllers.append(AIPlayerController(player.name))

    state_machine = PokerStateMachine(game_instance, controllers)

    try:
        state_machine.run()
        display_game_state(state_machine.game_state, include_deck=True)
        print(f"\n{state_machine.game_state.players[0].name} Won! Thanks for playing!")

    except KeyboardInterrupt:
        display_game_state(state_machine.game_state, include_deck=True)
        print("\nGame interrupted. Thanks for playing!")
