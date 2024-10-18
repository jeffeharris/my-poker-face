# ui_console.py
from typing import Optional

from old_files.poker_player import AIPokerPlayer

from assistants import OpenAILLMAssistant
from functional_poker import *
from spades_game import assistant
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
        """
            Render a card object for output to the console.

            :param card: (Card)
                The card object to render.
            :return: (str)
                A string representation of the card formatted for console output.
            :raises KeyError:
                If the card's suit is not found in the suit-to-ASCII map.
        """
        rank_left = card.rank.ljust(2)
        rank_right = card.rank.rjust(2)
        card = CardRenderer._CARD_TEMPLATE.format(rank_left, Card.SUIT_TO_ASCII[card.suit], Card.SUIT_TO_ASCII[card.suit], rank_right)
        return card

    @staticmethod
    def render_cards(cards: List[Card]) -> Optional[str]:
        """
        Renders a list of Cards for output to the console.

        :param cards: (List[Card])
            A list of Card objects to be rendered.
        :return: (Optional[str])
            A string containing the rendered ASCII representation of the cards,
            or None if the card list is empty.
        """
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
        """
        Renders two cards for output to the console. Meant to represent the cards as the players' hole cards.

        :param card_1: (Card)
            The first card to render.
        :param card_2: (Card)
            The second card to render.
        :return: (str)
            ASCII representation of the two cards.
        :raises KeyError:
            If the suit of either card is not found in the SUIT_TO_ASCII mapping.
        """
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
    """
    Prepare the data needed for the UI to display the current game state and actions available to the player.

    :param game_state: (GameState)
        The current state of the game containing all relevant information.
    :return: (tuple)
        A tuple containing two elements:
        - A dictionary with UI data including community cards, player's hand, pot total, player's stack, cost to call, and player's name.
        - A list of player options available for the current player.
    """
    player_options = game_state.current_player_options
    cost_to_call_bet = game_state.highest_bet - game_state.current_player['bet']
    current_player = game_state.current_player
    opponents = [p['name'] for p in game_state.players if p != current_player]

    ui_data = {
        'community_cards': game_state.community_cards,
        'player_hand': current_player['hand'],
        'pot_total': game_state.pot['total'],
        'player_stack': current_player['stack'],
        'cost_to_call': cost_to_call_bet,
        'player_name': current_player['name'],
        'opponents': opponents,
    }

    return ui_data, player_options


# def ai_player_action(ui_data, player_options):
#     """
#     TODO: implement AI action
#     """
#     return human_player_action(ui_data, player_options)


def convert_game_to_hand_state(game_state, player: AIPokerPlayer):
    # Currently used values
    persona = player.name
    attitude = player.attitude
    confidence = player.confidence
    table_positions = hand_state["table_positions"]                         # TODO: create table positions
    opponent_status = hand_state["opponent_status"]                         # TODO: create opponent status
    current_round = hand_state["current_phase"]                             # TODO: assign current round here
    community_cards = [str(card) for card in hand_state["community_cards"]] # TODO: create community cards as Card objects
    opponents = game_state.players
    number_of_opponents = len(opponents) - 1
    player_money = player.money
    # TODO: <FEATURE> decide what to do with this position idea
    # position = hand_state["positions"][self]
    current_situation = hand_state["current_situation"]
    hole_cards = [str(card) for card in player.cards]
    current_pot = hand_state["current_pot"]
    # current_bet = current_pot.current_bet     # removed this because i wasn't able to get the ai player to understand how to bet when i included this, the pot, the cost to call etc.
    cost_to_call = game_state.highest_bet - game_state.current_player['bet']
    player_options = game_state.current_player_options

    # create a list of the action comments and then send them to the table manager to summarize
    action_comment_list = [action.action_comment for action in hand_state["poker_actions"]]
    action_summary = "We're just getting started! You're first to go."
    if len(action_comment_list) > 0:
        action_summary = hand_state["table_manager"].summarize_actions_for_player(
            action_comment_list[-number_of_opponents:], self.name)

    persona_state = (
        f"Persona: {persona}\n"
        f"Attitude: {attitude}\n"
        f"Confidence: {confidence}\n"
        f"Your Cards: {hole_cards}\n"
        f"Your Money: {player_money}\n"
    )

    hand_state = (
        f"{current_situation}\n"
        f"Current Round: {current_round}\n"
        f"Community Cards: {community_cards}\n"
        f"Table Positions: {table_positions}\n"
        f"Opponent Status:\n{opponent_status}\n"
        f"Actions since your last turn: {action_summary}\n"
    )

    pot_state = (
        f"Pot Total: ${current_pot.total}\n"
        f"How much you've bet: ${current_pot.get_player_pot_amount(self.name)}\n"
        f"Your cost to call: ${cost_to_call}\n"
    )

    hand_update_message = persona_state + hand_state + pot_state + (
        # f"You have {hole_cards} in your hand.\n"  # The current bet is ${current_bet} and
        # f"Remember, you're feeling {attitude} and {confidence}.\n"
        f"Consider the strength of your hand relative to the pot and the likelihood that your opponents might have stronger hands. "
        f"Preserve your chips for when the odds are in your favor, and remember that sometimes folding or checking is the best move. "
        f"You cannot bet more than you have, ${player_money}.\n"
        f"You must select from these options: {player_options}\n"
        f"What is your move, {persona}?\n\n"
    )

    return hand_update_message


def ai_player_action(game_state):
    current_player = game_state.current_player
    poker_player = AIPokerPlayer(current_player['name'],starting_money=current_player['stack'],ai_temp=0.9)
    ai = poker_player.assistant
    # for message in player_messages:
    #     ai_assistant.assistant.add_to_memory(message)
    message = json.dumps(prepare_ui_data(game_state))
    # print(message)
    response_json = ai.chat(message + "\nPlease only respond with the JSON, not the text with back quotes.")
    try:
        response_dict = json.loads(response_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Error decoding JSON response: {e}")

    # print(response_json)
    player_choice = response_dict['action']
    amount = response_dict['adding_to_pot']
    player_message = response_dict['persona_response']
    player_physical_description = response_dict['physical']
    print(f"\n{'-'*20}\n")
    print(f"{current_player['name']} chose to {player_choice} by {amount}")
    print(f"\"{player_message}\"")
    print(f"{player_physical_description}")
    print(f"\n{'-'*20}\n")

    return player_choice, amount


def human_player_action(ui_data: dict, player_options: List[str]) -> Tuple[str, int]:
    """
    Console UI is used to update the player with the relevant game state info and receives input.
    This will return a tuple as ( action, amount ) for the players bet.
    """
    # Render the player's cards using the CardRenderer.
    rendered_hole_cards = CardRenderer().render_hole_cards(
        [Card(c['rank'], c['suit']) for c in ui_data['player_hand']])

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


# def play_betting_round(game_state):
#     while (not are_pot_contributions_valid(game_state)
#            # number of players still able to bet is greater than 1
#            and len([p['name'] for p in game_state.players if not p['is_folded'] or not p['is_all_in']]) > 1):
#         player_choice, amount = get_player_action(game_state)
#         # Play the turn with the provided decision
#         game_state = play_turn(game_state, player_choice, amount)
#         game_state = advance_to_next_active_player(game_state)
#     return game_state


def handle_player_action(game_state):
    ui_data, player_options = prepare_ui_data(game_state)

    if game_state.current_player['is_human']:
        action, amount = human_player_action(ui_data, player_options)
    else:
        action, amount = ai_player_action(game_state)

    game_state = play_turn(game_state, action, amount)
    game_state = advance_to_next_active_player(game_state)

    return game_state


if __name__ == '__main__':
    # Get AI player names and initialize the game instance
    ai_player_names = get_celebrities(shuffled=True)[:NUM_AI_PLAYERS]
    game_instance = initialize_game_state(player_names=ai_player_names)

    try:
        # Loop playing a hand until there is 1 player remaining
        while len(game_instance.players) > 1:
            game_instance = run_hand_until_player_turn(game_state=game_instance)
            if game_instance.current_phase == 'determining-winner':
                # The hand will reset when it loops back
                # Determine the winner
                game_instance, winner_info = determine_winner(game_instance)
                game_instance = update_poker_game_state(game_instance, current_phase='hand-over')
                print(10, game_instance.current_phase, "hand has ended!")
                # Reset the game for a new hand
                game_instance = reset_game_state_for_new_hand(game_state=game_instance)
            # Get action from player and update the game state
            elif game_instance.awaiting_action:
                game_instance = handle_player_action(game_state=game_instance)
                game_instance = update_poker_game_state(game_instance, awaiting_action=False)

        display_game_state(game_instance, include_deck=True)
        print(f"\n{game_instance.players[0]['name']} Won! Thanks for playing!")

    except KeyboardInterrupt:
        display_game_state(game_instance, include_deck=True)
        print("\nGame interrupted. Thanks for playing!")
