# ui_console.py
from typing import Optional

from functional_poker import *
from utils import get_celebrities


class CardRenderer:
    _CARD_TEMPLATE = '''
.---------.
|{}       |
| {}       |
|         |
|         |
|      {}  |
|       {}|
`---------'
'''
    _TWO_CARD_TEMPLATE = '''
.---.---------.
|{}  |{}        |
|  {}|  {}      |
|   |         |
|   |         |
|   |       {} |
|   |        {}|
`---`---------'
'''

    @staticmethod
    def render_card(card):
        # Renders a Card for output to the console
        rank_left = card.rank.ljust(2)
        rank_right = card.rank.rjust(2)
        card = CardRenderer._CARD_TEMPLATE.format(rank_left, Card.SUIT_TO_ASCII[card.suit], Card.SUIT_TO_ASCII[card.suit], rank_right)
        return card

    @staticmethod
    def render_cards(cards: List[Card]) -> Optional[str]:
        # Renders a list of Cards for output to the console
        card_lines = [CardRenderer.render_card(card).strip().split('\n') for card in cards]
        if not card_lines:
            return None
        ascii_card_lines = []
        for lines in zip(*card_lines):
            ascii_card_lines.append('  '.join(lines))
        card_ascii_string = '\n'.join(ascii_card_lines)
        return card_ascii_string

    @staticmethod
    def render_two_cards(card_1, card_2):
        # Renders two cards for output to the console. Meant to represent the cards as the players hole cards
        two_card_ascii_string = CardRenderer._TWO_CARD_TEMPLATE.format(card_1.rank,
                                                         card_2.rank,
                                                         Card.SUIT_TO_ASCII[card_1.suit],
                                                         Card.SUIT_TO_ASCII[card_2.suit],
                                                         Card.SUIT_TO_ASCII[card_2.suit],
                                                         card_2.rank)
        return two_card_ascii_string

    @staticmethod
    def render_hole_cards(cards: List[Card]):
        sorted_cards = sorted(cards, key=lambda card: card.value)
        card_1 = sorted_cards[0]
        card_2 = sorted_cards[1]

        # Generate console output for the Cards
        hole_card_art = CardRenderer.render_two_cards(card_1, card_2)
        return hole_card_art


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


def human_player_action(ui_data: dict, player_options: List[str]) -> Tuple[str, int]:
    # Render the player's cards using the CardRenderer.
    players_rendered_cards = CardRenderer().render_hole_cards(
        [Card(c['rank'], c['suit']) for c in ui_data['player_hand']])

    # Display information to the user
    # print(f"\nCommunity Cards: {ui_data['community_cards']}")
    print(f"Your Hand:\n{players_rendered_cards}")
    print(f"Pot: {ui_data['pot_total']}")
    print(f"Your Stack: {ui_data['player_stack']}")
    print(f"Cost to Call: {ui_data['cost_to_call']}")
    print(f"Options: {player_options}\n")

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

    return player_choice, bet_amount


def display_game_state(game_state):
    # Convert game_state to JSON and pretty print to console
    game_state_json = json.loads(json.dumps(game_state, default=lambda o: o.__dict__))
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


def play_hand(game_state):
    # Pre-flop actions
    game_state = advance_to_next_active_player(game_state)
    game_state = place_bet(game_state, ANTE)
    game_state = advance_to_next_active_player(game_state)
    game_state = place_bet(game_state, ANTE * 2)
    game_state = advance_to_next_active_player(game_state)
    game_state = deal_hole_cards(game_state)

    # Betting rounds
    betting_rounds = [
        ('Pre-flop', None),
        ('Flop', 3),
        ('Turn', 1),
        ('River', 1)
    ]

    for round_name, num_cards in betting_rounds:
        # Deal community cards if the round calls for it
        if num_cards:
            game_state = deal_community_cards(game_state, num_cards=num_cards)
            # Display the cards after they've been dealt
            display_cards(game_state.community_cards, round_name)
        # Play the betting round
        game_state = play_betting_round(game_state, get_player_action)

    # Determine the winner
    game_state, winner_info = determine_winner(game_state)
    display_hand_winner(winner_info)

    return game_state


if __name__ == '__main__':
    ai_player_names = get_celebrities(shuffled=True)[:NUM_AI_PLAYERS]
    game_instance = initialize_game_state(player_names=ai_player_names)

    while len(game_instance.players) > 1:
        game_instance = play_hand(game_state=game_instance)
        game_instance = reset_game_state_for_new_hand(game_state=game_instance)

        # display_game_state(game_instance)

    end_game_info = end_game(game_state=game_instance)
    display_end_game(end_game_info)