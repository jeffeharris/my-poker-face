import json
from typing import List, Optional, Any, Dict

from core.card import Card
from core.deck import Deck
from core.user_interface import UserInterface
from poker.poker_action import PokerAction
from poker.poker_game import PokerGame
from poker.poker_hand import PokerHand
from poker.poker_player import AIPokerPlayer
from poker.utils import get_ai_players, shift_list_left, PokerHandPhase


VIEW_AI_HAND_UPDATES = True
VIEW_AI_ACTION_INSIGHTS = True
VIEW_AI_HAND_SUMMARY = True
NUM_AI_PLAYERS = 3
TEST_MODE = False

class CardRenderer:
    _CARD_TEMPLATE = '''
.---------.
|{}       |
| {}       |
|         |
|         |
|    {}    |
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


class TextFormat:
    COLOR_CODES = {
        "CYAN": '\033[96m',
        "RED": '\033[91m',
        "GREEN": '\033[92m',
        "YELLOW": '\033[93m',
        "BLUE": '\033[94m',
        "MAGENTA": '\033[95m',
        "RESET": '\033[0m',
        "BOLD": '\033[1m',
        "UNDERLINE": '\033[4m',
    }

    @staticmethod
    def format_text(text, *styles):
        return ''.join(styles) + text + TextFormat.COLOR_CODES["RESET"]


class ConsoleUserInterface(UserInterface):
    @staticmethod
    def get_user_input(request):
        return input(request)

    def request_action(self, options: List, request: str, default_option: Optional[int] = None) -> Optional[str]:
        self.display_text(TextFormat.format_text(f"Actions: {options}",
                                                 TextFormat.COLOR_CODES["CYAN"], TextFormat.COLOR_CODES["BOLD"]))
        return self.get_user_input(request)

    def display_text(self, text: str or Dict or List, style: str = None):
        if style is not None:
            self.print_pretty_json(style + text + TextFormat.COLOR_CODES["RESET"])
        self.print_pretty_json(text)

    def display_expander(self, label: str, body: Any):
        self.display_text(TextFormat.format_text(label,
                                                 TextFormat.COLOR_CODES["BLUE"], TextFormat.COLOR_CODES["BOLD"]))
        self.display_text(body)

    @staticmethod
    def print_pretty_json(input_value):
        try:
            # If the input is a string, attempt to parse it as JSON
            if isinstance(input_value, str):
                parsed_json = json.loads(input_value)
            elif isinstance(input_value, dict):
                parsed_json = input_value
            else:
                raise ValueError("Input must be a JSON string or a dictionary")

            # Convert the parsed JSON or dictionary to a pretty-printed JSON string
            pretty_json = json.dumps(parsed_json, indent=4)
            print(TextFormat.format_text(pretty_json,
                                         TextFormat.COLOR_CODES["BLUE"]))
        except (json.JSONDecodeError, ValueError) as e:
            # If parsing fails or input is invalid, print the original value
            print(input_value)

CONSOLE_INTERFACE = ConsoleUserInterface()


###########################################################################################################
#####                                    PLAYER CHAT INTERFACE                                        #####
###########################################################################################################
# TODO: <REFACTOR> Refactor the chat interface to it's own class [get_player_names, select_ai_player, chat_with_ai]
def get_player_names(hand_state):
    player_names = [player.name for player in hand_state["players"]]
    ai_player_names = [player.name for player in hand_state["players"] if isinstance(player, AIPokerPlayer)]
    human_player_name = next(
        player.name for player in hand_state["players"] if not isinstance(player, AIPokerPlayer))
    return player_names, ai_player_names, human_player_name


def select_ai_player(ai_player_names, player_names, hand_state):
    # Prompt the user to select an AI player to message from the given list
    player_input = CONSOLE_INTERFACE.get_user_input(f"Who do you want to message? {ai_player_names}\n")
    while player_input not in ai_player_names:
        player_input = CONSOLE_INTERFACE.get_user_input(f"Please enter a name from the list: {ai_player_names}\n")
    # Return the selected AI player object from the hand_state
    return hand_state["players"][player_names.index(player_input)]


def chat_with_ai(hand_state, human_player_name, player_to_message):
    chat_message = CONSOLE_INTERFACE.get_user_input("Enter message: ")
    while chat_message != "quit":
        formatted_message = (
            f"Message from {human_player_name}: "
            f"{player_to_message.build_hand_update_message(hand_state) + chat_message}"
        )
        response_json = player_to_message.get_player_response(formatted_message)
        CONSOLE_INTERFACE.print_pretty_json(response_json)
        chat_message = CONSOLE_INTERFACE.get_user_input("Enter response: ")


def run_chat(hand_state):
    player_names, ai_player_names, human_player_name = get_player_names(hand_state)
    player_to_message = select_ai_player(ai_player_names, player_names, hand_state)
    chat_with_ai(hand_state, human_player_name, player_to_message)


###########################################################################################################
#####                           PLAYER INTERACTIONS AND OPTION SETTING                                #####
###########################################################################################################
def get_player_action(player, hand_state, player_options) -> PokerAction:
    if isinstance(player, AIPokerPlayer):
        return get_ai_player_action(player, hand_state)

    current_pot = hand_state["current_pot"]
    cost_to_call = current_pot.get_player_cost_to_call(player.name)

    CONSOLE_INTERFACE.display_text(CardRenderer.render_hole_cards(cards=player.cards))
    # display_hand_update_text(hand_state, player)

    action = CONSOLE_INTERFACE.request_action(player_options, "Enter action: \n")

    add_to_pot = 0
    if action is None:
        if "check" in player_options:
            action = "check"
        elif "call" in player_options:
            action = "call"
        else:
            action = "fold"
    if action in ["bet"]:
        add_to_pot = int(CONSOLE_INTERFACE.get_user_input("Enter amount: "))
    elif action in ["raise"]:
        raise_amount = int(CONSOLE_INTERFACE.get_user_input(f"Calling {cost_to_call}.\nEnter amount to raise: "))
        add_to_pot = raise_amount + cost_to_call
    elif action in ["all-in"]:
        add_to_pot = player.money
    elif action in ["call"]:
        add_to_pot = cost_to_call
    elif action in ["fold"]:
        add_to_pot = 0
    elif action in ["check"]:
        add_to_pot = 0
    elif action in ["quit"]:
        exit()
    else:
        return get_player_action(player, hand_state, player_options)

    chat_message = CONSOLE_INTERFACE.get_user_input("Enter table comment (optional): ")
    if chat_message != "":
        hand_state["table_messages"].append({"name": player.name, "message": chat_message})

    action_detail = { "comment": chat_message }
    table_message = f"{player.name} chooses to {action} by {add_to_pot}."
    action_comment = (f"{player.name}:\t'{chat_message}'\n"
                      f"\t{table_message}\n")

    poker_action = PokerAction(player, action, add_to_pot, hand_state, action_detail, action_comment)
    return poker_action


def get_ai_player_action(player, hand_state):
    # display_hand_update_text(hand_state, player)

    # Create the update message to be shared with the AI before they take their action
    hand_update_message = player.build_hand_update_message(hand_state)
    # Show the update shared with the AI
    if VIEW_AI_HAND_UPDATES:
        CONSOLE_INTERFACE.display_expander(label=f"{player.name}'s Hand Update",body=hand_update_message)

    # Get the response from the AI player
    response_json = player.get_player_response(hand_update_message)

    # Show the entire JSON response from the AI
    if VIEW_AI_ACTION_INSIGHTS:
        CONSOLE_INTERFACE.display_expander(label=f"{player.name}'s Insights", body=response_json)

    action = response_json["action"]
    add_to_pot = response_json["adding_to_pot"]
    chat_message = response_json["persona_response"]
    player.attitude = response_json["new_attitude"]
    player.confidence = response_json["new_confidence"]
    physical_actions = response_json["physical"]

    table_message = f"{player.name} chooses to {action} by {add_to_pot}."
    action_comment = (f"{player.name}:\t'{chat_message}'\n"
                      f"      actions:\t{physical_actions}\n"
                      f"\t{table_message}\n")

    CONSOLE_INTERFACE.display_text(action_comment)

    # TODO: <REFACTOR> reduce what is sent from hand_state to just what is needed - unknown at this point what that will be
    poker_action = PokerAction(player.name, action, add_to_pot, hand_state, response_json, action_comment)
    return poker_action


def display_hand_update_text(hand_state, player):
    community_cards = hand_state["community_cards"]
    current_bet = hand_state["current_bet"]
    current_pot = hand_state["current_pot"]
    cost_to_call = current_pot.get_player_cost_to_call(player.name)
    total_to_pot = current_pot.get_player_pot_amount(player.name)
    game_update_text_lines = [
        f"\n{player.name}'s turn. Current money: {player.money}",
        f"Community cards: {[str(card) for card in community_cards]}",
        f"Current bet: ${current_bet}",
        f"Current pot: ${current_pot.total}",
        f"Cost to call: ${cost_to_call}",
        f"Total to pot: ${total_to_pot}\n"
    ]
    game_update_text = "\n".join(game_update_text_lines)
    CONSOLE_INTERFACE.display_text(game_update_text)


    # create a list of the action comments and then send them to the table manager to summarize
    if VIEW_AI_HAND_SUMMARY:
        action_comment_list = [action.action_comment for action in hand_state["poker_actions"]]
        if len(action_comment_list) > 0:
            action_summary = hand_state["table_manager"].summarize_actions(action_comment_list[-3:])
            # display the summary to the console
            CONSOLE_INTERFACE.display_text("\n" + action_summary + "\n")


###########################################################################################################
#####                                    PROCESS POKER GAME FLOW                                      #####
###########################################################################################################
def reveal_cards(poker_hand, deck: Deck, num_cards: int, new_phase: PokerHandPhase):
    """
    Reveals a specified number of cards from the deck and adds them to the community cards in a poker game.

    Args:
        poker_hand: The current state of the poker hand.
        deck (Deck): The deck from which to deal the cards.
        num_cards (int): The number of cards to reveal.
        new_phase (PokerHandPhase): The name of the current phase of the poker hand.

    Returns:
        A tuple containing:
        - A text representation of the current phase and the rendered community cards.
        - The new cards dealt.

    """
    deck.discard(1)
    deck.card_deck.deal(poker_hand.community_cards, num_cards)
    poker_hand.set_current_round(new_phase)

    output_text = f"""
                ---***{new_phase}***---
"""
    output_text += CardRenderer.render_cards(poker_hand.community_cards)

    return output_text


# TODO: <REFACTOR> update to separate the game action from the interface output
def reveal_flop(poker_hand, deck):
    output_text = reveal_cards(poker_hand, deck, 3, PokerHandPhase.FLOP)
    CONSOLE_INTERFACE.display_text(output_text)


def reveal_turn(poker_hand, deck):
    output_text = reveal_cards(poker_hand, deck, 1, PokerHandPhase.TURN)
    CONSOLE_INTERFACE.display_text(output_text)


def reveal_river(poker_hand, deck):
    output_text = reveal_cards(poker_hand, deck, 1, PokerHandPhase.RIVER)
    CONSOLE_INTERFACE.display_text(output_text)


def build_hand_complete_update_message(player_name, winning_player_name, total_pot, amount_lost, winning_hand, shown_cards=None):
    message = (f"The winner is {winning_player_name}! They win the pot of ${total_pot}.\n"
               f"Winners cards: {shown_cards}\n"
               f"Winning hand: {winning_hand}\n")
    if winning_player_name != player_name:
        message += f"You lost ${amount_lost} this hand, better luck next time!\n"
    return message

def play_game(poker_game: PokerGame):
    # TODO: <REFACTOR> ensure the poker hand is being set up as expected
    # ph = PokerHand(players=poker_game.players,
    #                        dealer=poker_game.players[random.randint(0, len(poker_game.players) - 1)],
    #                        deck=poker_game.deck)
    poker_hand = PokerHand()
    poker_hand.pots[0].initialize_pot([p.name for p in poker_game.round_manager.remaining_players])
    while len(poker_game.round_manager.remaining_players) > 1:
        poker_game.hands.append(poker_hand)
        play_hand(poker_game)

        # Check if the game should continue
        # Remove players from the hand if they are out of money
        poker_game.round_manager.remaining_players = [player for player in poker_game.round_manager.starting_players if player.money > 0]
        if len(poker_game.round_manager.remaining_players) == 1:      # When all other players have lost
            CONSOLE_INTERFACE.display_text(f"{poker_game.round_manager.players[0].name} is the last player remaining and wins the hand!")
            return  # This causes an error when there is only 1 player eft in the game. Later, the player should be given the option to enter another tournament.
        elif len(poker_game.round_manager.remaining_players) == 0:    # This case should never happen
            CONSOLE_INTERFACE.display_text("You... you all lost. Somehow you all have no money.")
            return
        poker_game.round_manager.deck.reset()

        play_again = CONSOLE_INTERFACE.request_action(
            ["yes", "no"],
            "Would you like to play another hand? ",
            0)
        if play_again != "yes":
            break
        else:
            new_hand = PokerHand()
            new_hand.pots[0].initialize_pot([p.name for p in poker_game.round_manager.remaining_players])
            poker_hand = new_hand
            # TODO: <REFACTOR> ensure the poker hand is being set up as expected
            # poker_hand = PokerHand(players=poker_game.round_manager.remaining_players,
            #                        dealer=poker_game.round_manager.dealer,
            #                        deck=poker_game.round_manager.deck)

    CONSOLE_INTERFACE.display_text("Game over!")


def main(num_ai_players: int = NUM_AI_PLAYERS):
    poker_game = PokerGame()
    poker_game.initialize_game(num_ai_players=num_ai_players)
    while poker_game.play_continues():
        poker_game.play_hand()
    # play_game(poker_game)


if __name__ == "__main__":
    main()