# ui_console.py

from functional_poker import *
from utils import get_celebrities


def human_player_action(ui_data: dict, player_options: List[str]) -> Tuple[str, int]:
    # Display information to the user
    print(f"Community Cards: {ui_data['community_cards']}")
    print(f"Your Hand: {ui_data['player_hand']}")
    print(f"Pot: {ui_data['pot_total']}")
    print(f"Your Stack: {ui_data['player_stack']}")
    print(f"Cost to Call: {ui_data['cost_to_call']}")
    print(f"Options: {player_options}")

    # Get user choice
    player_choice = None
    while player_choice not in player_options:
        player_choice = input(f"{ui_data['player_name']}, what would you like to do? ").lower().replace("-","_")
        if player_choice in ["all-in", "allin", "all in"]:
            player_choice = "all_in"
        if player_choice not in player_options:
            print("Invalid choice. Please select from the available options.\n\t")

    # Get bet amount if necessary
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

    return player_choice, bet_amount

def prepare_ui_data(game_state):
    player_options = game_state.current_player_options
    cost_to_call_bet = game_state.highest_bet - game_state.current_player['bet']
    current_player = game_state.current_player

    ui_data = {
        'community_cards': game_state.community_cards,
        'player_hand': current_player['hand'],
        'pot_total': game_state.pot['total'],
        'player_stack': current_player['stack'],
        'cost_to_call': cost_to_call_bet,
        'player_name': current_player['name']
    }

    return ui_data, player_options


# def ai_player_action(ui_data, player_options):
#     """
#     TODO: implement AI action
#     """
#     return human_player_action(ui_data, player_options)


def ai_player_action(game_state):
    player_options = game_state.current_player_options
    cost_to_call_bet = game_state.highest_bet - game_state.current_player['bet']
    player = game_state.current_player

    # Simple AI logic (can be enhanced)
    if 'call' in player_options and cost_to_call_bet <= (player['stack'] * 0.2):
        action = 'call'
        amount = cost_to_call_bet
    elif 'check' in player_options:
        action = 'check'
        amount = 0
    elif 'fold' in player_options:
        action = 'fold'
        amount = 0
    else:
        action = 'all_in'
        amount = player['stack']

    print(f"{player['name']} has chosen {action} ({amount})")
    return action, amount


def get_player_action(game_state):
    current_player = game_state.current_player

    # Prepare data for the UI
    ui_data, player_options = prepare_ui_data(game_state)

    if current_player['is_human']:
        # Get decision from human player
        player_choice, amount = human_player_action(ui_data, player_options)
    else:
        # Get decision from AI player
        player_choice, amount = ai_player_action(game_state)

    return player_choice, amount


def display_winner(info):
    print(f"{info['winning_player_name']} wins the pot of {info['pot_total']} with {info['winning_hand']}!")


def display_end_game(info):
    print(f"\n{info['message']}\n")


def display_game_state(game_state):
    # Convert game_state to JSON and pretty print to console
    game_state_json = json.loads(json.dumps(game_state, default=lambda o: o.__dict__))
    print(json.dumps(game_state_json, indent=4))


def ui_play_hand(game_state):
    # Pre-flop actions
    game_state = advance_to_next_active_player(game_state)
    game_state = place_bet(game_state, ANTE)
    game_state = advance_to_next_active_player(game_state)
    game_state = place_bet(game_state, ANTE * 2)
    game_state = advance_to_next_active_player(game_state)
    game_state = deal_hole_cards(game_state)

    # Betting rounds
    betting_rounds = [
        ('Flop', 3),
        ('Turn', 1),
        ('River', 1)
    ]

    for round_name, num_cards in betting_rounds:
        # Play the betting round
        game_state = play_betting_round(game_state, get_player_action)
        # Deal community cards
        game_state = deal_community_cards(game_state, num_cards=num_cards)
        # Optionally, display the game state after each round
        # display_game_state(game_state)

    # Final betting round without dealing new cards
    game_state = play_betting_round(game_state, get_player_action)

    # Determine the winner
    game_state, winner_info = determine_winner(game_state)
    display_winner(winner_info)

    return game_state

def play_hand(game_state: PokerGameState):
    """
    Progress the game through the phases to play a hand and determine the winner.
    """
    phases = [
        lambda state: advance_to_next_active_player(state),
        lambda state: place_bet(state, ANTE),
        lambda state: advance_to_next_active_player(state),
        lambda state: place_bet(state, ANTE*2),
        lambda state: advance_to_next_active_player(state),
        lambda state: deal_hole_cards(state),
        lambda state: play_betting_round(state, get_player_action),
        lambda state: deal_community_cards(state, num_cards=3),
        lambda state: play_betting_round(state, get_player_action),
        lambda state: deal_community_cards(state, num_cards=1),
        lambda state: play_betting_round(state, get_player_action),
        lambda state: deal_community_cards(state, num_cards=1),
        lambda state: play_betting_round(state, get_player_action),
        lambda state: determine_winner(state)
    ]

    for phase in phases:
        game_state = phase(game_state)
    return game_state


if __name__ == '__main__':
    ai_player_names = get_celebrities(shuffled=True)[:NUM_AI_PLAYERS]
    game_instance = initialize_game_state(player_names=ai_player_names)

    while len(game_instance.players) > 1:
        game_instance = ui_play_hand(game_state=game_instance)
        game_instance = reset_game_state_for_new_hand(game_state=game_instance)

        # display_game_state(game_instance)

    end_game_info = end_game(game_state=game_instance)
    display_end_game(end_game_info)