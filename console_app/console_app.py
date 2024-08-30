import json
import random
from typing import List, Optional, Any, Dict

from core.card import Card
from core.game import Interface
from core.poker_action import PokerAction, PlayerAction
from core.poker_game import PokerGame
from core.poker_hand import PokerHand, PokerHandPhase
from core.poker_player import PokerPlayer, AIPokerPlayer
from core.poker_settings import PokerSettings
from core.utils import get_players, shift_list_left

CARD_TEMPLATE = '''
.---------.
|{}       |
| {}       |
|         |
|         |
|    {}    |
|       {}|
`---------'
'''
TWO_CARD_TEMPLATE = '''
.---.---------.
|{}  |{}        |
|  {}|  {}      |
|   |         |
|   |         |
|   |       {} |
|   |        {}|
`---`---------'
'''

class ConsoleInterface(Interface):
    class TextFormat:
        CYAN = '\033[96m'
        RED = '\033[91m'
        GREEN = '\033[92m'
        YELLOW = '\033[93m'
        BLUE = '\033[94m'
        MAGENTA = '\033[95m'
        CYAN_BOLD = '\033[96m\033[1m'
        RED_BOLD = '\033[91m\033[1m'
        GREEN_BOLD = '\033[92m\033[1m'
        YELLOW_BOLD = '\033[93m\033[1m'
        BLUE_BOLD = '\033[94m\033[1m'
        MAGENTA_BOLD = '\033[95m\033[1m'
        RESET = '\033[0m'
        BOLD = '\033[1m'
        UNDERLINE = '\033[4m'

        def __add__(self, other: str or Dict or List) -> str:
            return str(self) + str(other)

    def request_action(self, options: List, request: str, default_option: Optional[int] = None) -> Optional[str]:
        self.display_text(f"{self.TextFormat.CYAN_BOLD}Actions: {self.TextFormat.CYAN}{options}{self.TextFormat.RESET}")
        return input(request)

    def display_text(self, text: str or Dict or List, style: TextFormat = None):
        if style is not None:
            self.print_pretty_json(style + text + style.RESET)
        self.print_pretty_json(text)

    def display_expander(self, label: str, body: Any):
        self.display_text(self.TextFormat.BLUE_BOLD + label + self.TextFormat.RESET)
        self.display_text(body)

    def print_pretty_json(self, input_value):
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
            print(self.TextFormat.BLUE + pretty_json + self.TextFormat.RESET)
        except (json.JSONDecodeError, ValueError) as e:
            # If parsing fails or input is invalid, print the original value
            print(input_value)

CONSOLE_INTERFACE = ConsoleInterface()

def display_hole_cards(cards: [Card, Card]):
    sorted_cards = sorted(cards, key=lambda card: card.value)
    card_1 = sorted_cards[0]
    card_2 = sorted_cards[1]

    # Generate and print each card
    hole_card_art = render_two_cards(card_1, card_2)
    return hole_card_art


def render_card(card):
    rank_left = card.rank.ljust(2)
    rank_right = card.rank.rjust(2)
    card = CARD_TEMPLATE.format(rank_left, Card.SUIT_TO_ASCII[card.suit], Card.SUIT_TO_ASCII[card.suit], rank_right)
    return card


def render_cards(cards: List[Card]) -> Optional[str]:
    card_lines = [render_card(card).strip().split('\n') for card in cards]
    if not card_lines:
        return None
    ascii_card_lines = []
    for lines in zip(*card_lines):
        ascii_card_lines.append('  '.join(lines))
    card_ascii_string = '\n'.join(ascii_card_lines)
    return card_ascii_string


def render_two_cards(card_1, card_2):
    # Generate and print each card
    two_card_ascii_string = TWO_CARD_TEMPLATE.format(card_1.rank,
                                                          card_2.rank,
                                                          Card.SUIT_TO_ASCII[card_1.suit],
                                                          Card.SUIT_TO_ASCII[card_2.suit],
                                                          Card.SUIT_TO_ASCII[card_2.suit],
                                                          card_2.rank)
    return two_card_ascii_string


def reveal_cards(poker_hand, num_cards: int, round_name: PokerHandPhase):
    """
    Reveal the cards.

    :param poker_hand: PokerHand object
    :param num_cards: Number of cards to reveal
    :param round_name: Name of the current round
    :return: string with text to output and revealed cards
    """
    poker_hand.deck.discard(1)
    new_cards = poker_hand.deck.deal(num_cards)
    poker_hand.community_cards += new_cards
    poker_hand.current_round = round_name
    output_text = f"""
                ---***{round_name}***---
"""
    output_text += render_cards(poker_hand.community_cards)

    return output_text, new_cards


def run_chat(hand_state):
    player_names = [player.name for player in hand_state["players"]]
    human_player_name = ""
    ai_player_names = []

    for player in hand_state["players"]:
        if isinstance(player, AIPokerPlayer):
            ai_player_names.append(player.name)
        else:
            human_player_name = player.name

    player_input = input(f"Who do you want to message? {ai_player_names}\n")
    while player_input not in ai_player_names:
        player_input = input(f"Please enter a name form the list: {ai_player_names}\n")
    player_to_message = hand_state["players"][player_names.index(player_input)]

    chat_message = input("Enter message: ")
    chat_message = player_to_message.build_hand_update_message(hand_state) + chat_message
    while chat_message != "quit":
        chat_message = (f"Message from {human_player_name}: "
                       f"{chat_message}")
        response_json = player_to_message.get_player_response(chat_message)
        player_to_message.attitude = response_json["new_attitude"]
        player_to_message.confidence = response_json["new_confidence"]
        CONSOLE_INTERFACE.print_pretty_json(response_json)
        chat_message = input("Enter response: ")


def get_player_action(player, hand_state) -> PokerAction:
    if isinstance(player, AIPokerPlayer):
        return get_ai_player_action(player, hand_state)

    current_pot = hand_state["current_pot"]
    cost_to_call = current_pot.get_player_cost_to_call(player)

    CONSOLE_INTERFACE.display_text(display_hole_cards(player.cards))
    display_hand_update_text(hand_state, player)

    action = CONSOLE_INTERFACE.request_action(player.options, "Enter action: \n")

    add_to_pot = 0
    if action is None:
        if "check" in player.options:
            action = "check"
        elif "call" in player.options:
            action = "call"
        else:
            action = "fold"
    if action in ["bet", "b", "be"]:
        add_to_pot = int(input("Enter amount: "))
        action = "bet"
    elif action in ["raise", "r", "ra", "rai", "rais"]:
        raise_amount = int(input(f"Calling {cost_to_call}.\nEnter amount to raise: "))
        # add_to_pot = raise_amount - current_pot.current_bet
        add_to_pot = raise_amount + cost_to_call
        action = "raise"
    elif action in ["all-in", "all in", "allin", "a", "al", "all", "all-", "all-i", "alli"]:
        add_to_pot = player.money
        action = "all-in"
    elif action in ["call", "ca", "cal"]:
        add_to_pot = cost_to_call
        action = "call"
    elif action in ["fold", "f", "fo", "fol"]:
        add_to_pot = 0
        action = "fold"
    elif action in ["check", "ch", "che", "chec"]:
        add_to_pot = 0
        action = "check"
    elif action in ["show", "sh", "sho"]:
        pass
    elif action in ["quit", "q", "qui"]:
        exit()
    elif action in ["chat"]:
        run_chat(hand_state)
        return get_player_action(player, hand_state)

    chat_message = input("Enter table comment (optional): ")
    if chat_message != "":
        hand_state["table_messages"].append({"name": player.name, "message": chat_message})

    action_detail = { "comment": chat_message }
    table_message = f"{player.name} chooses to {action} by {add_to_pot}."
    action_comment = (f"{player.name}:\t'{chat_message}'\n"
                      f"\t{table_message}\n")

    # TODO: return a dict that can be converted to a PokerAction so we can decouple the Classes
    poker_action = PokerAction(player, action, add_to_pot, hand_state, action_detail, action_comment)
    return poker_action

# TODO: determine if this is needed or can be deleted
def get_ai_player_action(player, hand_state):
    display_hand_update_text(hand_state, player)

    hand_update_message = player.build_hand_update_message(hand_state)
    # Show the update shared with the AI
    CONSOLE_INTERFACE.display_expander(label=f"{player.name}'s Hand Update",body=hand_update_message)
    response_json = player.get_player_response(hand_update_message)

    # Show the entire JSON response form the AI
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

    # TODO: return a dict that can be converted to a PokerAction so we can decouple the Classes
    # TODO: reduce what is sent from hand_state to just what is needed - unknown at this point what that will be
    poker_action = PokerAction(player.name, action, add_to_pot, hand_state, response_json, action_comment)
    return poker_action


def display_hand_update_text(hand_state, player):
    community_cards = hand_state["community_cards"]
    current_bet = hand_state["current_bet"]
    current_pot = hand_state["current_pot"]
    cost_to_call = current_pot.get_player_cost_to_call(player)
    total_to_pot = current_pot.get_player_pot_amount(player)
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


    # # create a list of the action comments and then send them to the table manager to summarize
    # action_comment_list = [action.action_comment for action in hand_state["poker_actions"]]
    # if len(action_comment_list) > 0:
    #     action_summary = hand_state["table_manager"].summarize_actions(action_comment_list[-3:])
    #     # display the summary to the console
    #     CONSOLE_INTERFACE.display_text("\n" + action_summary + "\n")

# Used to debug issues with folding and player_queue can likely be removed
def print_queue_status(player_queue: List[PokerPlayer]):
    for index, player in enumerate(player_queue):
        print(f"{index}: {player.name} - {player.folded}")

def process_pot_update(poker_hand, player: PokerPlayer, amount_to_add: int):
    poker_hand.pots[0].add_to_pot(player, amount_to_add)

def handle_bet_or_raise(poker_hand, player: PokerPlayer, add_to_pot: int, next_round_queue: List[PokerPlayer]):
    process_pot_update(poker_hand, player, add_to_pot)
    return betting_round(poker_hand, next_round_queue, is_initial_round=False)

def handle_all_in(poker_hand, player: PokerPlayer, add_to_pot: int, next_round_queue: List[PokerPlayer]):
    raising = add_to_pot > poker_hand.pots[0].current_bet
    process_pot_update(poker_hand, player, add_to_pot)
    if raising:
        return betting_round(poker_hand, next_round_queue, is_initial_round=False)
    else:
        # TODO: create a side pot
        pass

def handle_call(poker_hand, player: PokerPlayer, add_to_pot: int):
    process_pot_update(poker_hand, player, add_to_pot)

def handle_fold(poker_hand, player: PokerPlayer):
    player.folded = True
    poker_hand.set_remaining_players()


def betting_round(poker_hand, player_queue: List[PokerPlayer], is_initial_round: bool = True):
    # Check to see if remaining players are all-in

    active_player_queue = initialize_active_players(player_queue, is_initial_round)

    if len(poker_hand.remaining_players) <= 0:
        raise ValueError("No remaining players left in the hand")

    for player in active_player_queue:
        if player.folded:
            continue

        all_in_count = 0
        for p in poker_hand.remaining_players:
            if p.money <= 0:
                all_in_count += 1
        if all_in_count == len(poker_hand.remaining_players):
            return
        elif len(poker_hand.remaining_players) <= 1:
            return
        else:
            # print_queue_status(player_queue)
            poker_hand.set_player_options(player, PokerSettings())

            poker_action = get_player_action(player, poker_hand.hand_state)
            poker_hand.poker_actions.append(poker_action)

            if process_player_action(poker_hand, player, poker_action):
                return


def initialize_active_players(player_queue: List[PokerPlayer], is_initial_round: bool) -> List[
    PokerPlayer]:
    return player_queue.copy() if is_initial_round else player_queue[:-1]


def process_player_action(poker_hand, player: PokerPlayer, poker_action: PokerAction) -> bool:
    player_action = poker_action.player_action
    amount = poker_action.amount

    if player_action in {PlayerAction.BET, PlayerAction.RAISE}:
        handle_bet_or_raise(poker_hand, player, amount, poker_hand.get_next_round_queue(poker_hand.remaining_players, player))
        return True
    elif player_action == PlayerAction.ALL_IN:
        handle_all_in(poker_hand, player, amount, poker_hand.get_next_round_queue(poker_hand.remaining_players, player))
        return True
    elif player_action == PlayerAction.CALL:
        handle_call(poker_hand, player, amount)
    elif player_action == PlayerAction.FOLD:
        handle_fold(poker_hand, player)
    elif player_action == PlayerAction.CHECK:
        return False
    elif player_action == PlayerAction.CHAT:
        # TODO: implement handle_chat to open up  ability for AIs to chat with each other or the player.
        pass
    else:
        raise ValueError("Invalid action selected: " + str(player_action))
    return False

# TODO: update to not use interface
def reveal_flop(poker_hand):
    output_text, new_cards = reveal_cards(poker_hand,3, PokerHandPhase.FLOP)
    CONSOLE_INTERFACE.display_text(output_text)

def reveal_turn(poker_hand):
    output_text, new_cards = reveal_cards(poker_hand,1, PokerHandPhase.TURN)
    CONSOLE_INTERFACE.display_text(output_text)

def reveal_river(poker_hand):
    output_text, new_cards = reveal_cards(poker_hand,1, PokerHandPhase.RIVER)
    CONSOLE_INTERFACE.display_text(output_text)


def build_hand_complete_update_message(player_name, winning_player_name, total_pot, amount_lost, winning_hand, shown_cards=None):
    message = (f"The winner is {winning_player_name}! They win the pot of ${total_pot}.\n"
               f"Winners cards: {shown_cards}\n"
               f"Winning hand: {winning_hand}\n")
    if winning_player_name != player_name:
        message += f"You lost ${amount_lost} this hand, better luck next time!\n"
    return message


def play_hand(poker_hand):
    round_queue = poker_hand.setup_hand()
    CONSOLE_INTERFACE.display_text(f"{poker_hand.dealer.name}'s deal.\n")
    CONSOLE_INTERFACE.display_text(
        f"Small blind: {poker_hand.small_blind_player.name}\n Big blind: {poker_hand.big_blind_player.name}\n")

    betting_round(poker_hand, round_queue)

    reveal_flop(poker_hand)
    start_player = poker_hand.determine_start_player()
    index = poker_hand.players.index(start_player)
    round_queue = poker_hand.players.copy()  # Copy list of all players that started the hand, could include folded
    shift_list_left(round_queue, index)  # Move to the start_player
    betting_round(poker_hand, round_queue)

    reveal_turn(poker_hand)
    betting_round(poker_hand, round_queue)

    reveal_river(poker_hand)
    betting_round(poker_hand, round_queue)

    # Evaluate and announce the winner
    winning_player, winning_hand = poker_hand.determine_winner()
    CONSOLE_INTERFACE.display_text(f"The winner is {winning_player.name}! They win the pot of {poker_hand.pots[0].total}")

    # Get end fo hand reactions from AI players
    player_reactions = []
    winner_shows_cards = True  # TODO: let the winner decide if they want to show their cards in cases where they don't need to
    winners_cards_string = f"{winning_player.name} didn't show their cards!"  # Initialize the message in the case that the winner did not show their cards
    if winner_shows_cards:
        winning_hand_string = [str(card) for card in winning_hand]
        winners_cards_string = [str(card) for card in winning_player.cards]
        winners_cards_string = "|".join(winners_cards_string)

    for player in poker_hand.players:
        if isinstance(player, AIPokerPlayer):
            message = build_hand_complete_update_message(player_name=player.name,
                                                         winning_player_name=winning_player.name,
                                                         total_pot=poker_hand.pots[0].total,
                                                         amount_lost=poker_hand.pots[0].player_pot_amounts[player],
                                                         winning_hand=winning_hand_string,
                                                         shown_cards=winners_cards_string
                                                         )
            response_json = player.get_player_response(message)

            reaction = {
                "name": player.name,
                "response_json": response_json
                }
            player_reactions.append(reaction)
            if reaction is not None:
                name = reaction["name"]
                message = reaction["response_json"]
                CONSOLE_INTERFACE.display_text(name)
                CONSOLE_INTERFACE.display_text(message)

    # for r in player_reactions:
    #     if r is not None:
    #         name = r["name"]
    #         message = r["response_json"]
    #         CONSOLE_INTERFACE.display_text(name)
    #         CONSOLE_INTERFACE.display_text(message)

    game_actions = [action.action_comment for action in poker_hand.poker_actions]
    game_actions.append(build_hand_complete_update_message(player_name=winning_player.name,
                                                         winning_player_name=winning_player.name,
                                                         total_pot=poker_hand.pots[0].total,
                                                         amount_lost=None,
                                                         winning_hand=winning_hand_string,
                                                         shown_cards=winners_cards_string))

    game_summary = poker_hand.round_manager.summarize_actions(game_actions)
    CONSOLE_INTERFACE.display_text(game_summary)

    # Reset game for next round
    poker_hand.pots[0].resolve_pot(winning_player)  # TODO: implement support for side-pots (multiple pots)
    poker_hand.rotate_dealer()
    # Return community cards to Deck
    poker_hand.deck.return_cards_to_discard_pile(poker_hand.community_cards)
    # Reset players
    for player in poker_hand.players:
        poker_hand.deck.return_cards_to_discard_pile(player.cards)
        player.folded = False

    # TODO: move to the play_game function to handle there
    # Check if the game should continue
    # Remove players from the hand if they are out of money
    poker_hand.remaining_players = [player for player in poker_hand.starting_players if player.money > 0]
    if len(poker_hand.remaining_players) == 1:      # When all other players have lost
        CONSOLE_INTERFACE.display_text(f"{poker_hand.players[0].name} is the last player remaining and wins the game!")
        return  # This causes an error when there is only 1 player eft in the game. Later, the player should be given the option to enter another tournament.
    elif len(poker_hand.remaining_players) == 0:    # This case should never happen
        CONSOLE_INTERFACE.display_text("You... you all lost. Somehow you all have no money.")
        return

    poker_hand.deck.reset()

    return poker_hand.remaining_players, poker_hand.dealer

def play_game(poker_game: PokerGame):
    poker_hand = PokerHand(players=poker_game.players,
                           dealer=poker_game.players[random.randint(0, len(poker_game.players) - 1)],
                           deck=poker_game.deck)
    while len(poker_game.remaining_players) > 1:
        poker_game.hands.append(poker_hand)
        poker_game.remaining_players, dealer = play_hand(poker_hand)
        play_again = CONSOLE_INTERFACE.request_action(
            ["yes", "no"],
            "Would you like to play another hand? ",
            0)
        if play_again != "yes":
            break
        else:
            poker_hand = PokerHand(players=poker_game.remaining_players,
                                   dealer=dealer,
                                   deck=poker_game.deck)

    CONSOLE_INTERFACE.display_text("Game over!")


def main(test=False, num_players=4):
    players = get_players(test=test, num_players=num_players)
    poker_game = PokerGame(players)
    play_game(poker_game)


if __name__ == "__main__":
    main()