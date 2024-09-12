import json
import random
from typing import List, Optional, Any, Dict

from core.card import Card
from core.deck import Deck
from core.interface import Interface
from core.poker_action import PokerAction, PlayerAction
from core.poker_game import PokerGame
from core.poker_hand import PokerHand
from core.poker_player import PokerPlayer, AIPokerPlayer
from core.poker_settings import PokerSettings
from core.round_manager import RoundManager
from core.utils import get_ai_players, shift_list_left, PokerHandPhase


VIEW_AI_HAND_UPDATES = True
VIEW_AI_ACTION_INSIGHTS = True
VIEW_AI_HAND_SUMMARY = True
NUM_PLAYERS = 4
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
        # Prints a Card to the console
        rank_left = card.rank.ljust(2)
        rank_right = card.rank.rjust(2)
        card = CardRenderer._CARD_TEMPLATE.format(rank_left, Card.SUIT_TO_ASCII[card.suit], Card.SUIT_TO_ASCII[card.suit], rank_right)
        return card

    @staticmethod
    def render_cards(cards: List[Card]) -> Optional[str]:
        # Prints a list of Cards to the console
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
        # Prints two cards to the console. Meant to represent the cards as the players hole cards
        two_card_ascii_string = CardRenderer._TWO_CARD_TEMPLATE.format(card_1.rank,
                                                         card_2.rank,
                                                         Card.SUIT_TO_ASCII[card_1.suit],
                                                         Card.SUIT_TO_ASCII[card_2.suit],
                                                         Card.SUIT_TO_ASCII[card_2.suit],
                                                         card_2.rank)
        return two_card_ascii_string


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


class ConsoleInterface(Interface):
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

CONSOLE_INTERFACE = ConsoleInterface()

def display_hole_cards(cards: [Card, Card]):
    sorted_cards = sorted(cards, key=lambda card: card.value)
    card_1 = sorted_cards[0]
    card_2 = sorted_cards[1]

    # Generate and print each card
    hole_card_art = CardRenderer.render_two_cards(card_1, card_2)
    return hole_card_art


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
    poker_hand.current_phase = new_phase

    output_text = f"""
                ---***{new_phase}***---
"""
    output_text += CardRenderer.render_cards(poker_hand.community_cards)

    return output_text


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


# TODO: change this to return the options as a PlayerAction enum
def set_player_options(poker_hand, poker_player: PokerPlayer, settings: PokerSettings, big_blind_player_name: str, small_blind: int):
    # How much is it to call the bet for the player?
    player_cost_to_call = poker_hand.pots[0].get_player_cost_to_call(poker_player.name)
    # Does the player have enough to call
    player_has_enough_to_call = poker_player.money > player_cost_to_call
    # Is the current player also the big_blind TODO: add "and have they played this hand yet"
    current_player_is_big_blind = (poker_player.name == big_blind_player_name)

    # If the current player is last to act (aka big blind), and we're still in the pre-flop round
    if (current_player_is_big_blind
            and poker_hand.current_phase == PokerHandPhase.PRE_FLOP
            and poker_hand.pots[0].current_bet == small_blind * 2):
        player_options = ['check', 'raise', 'all-in', 'chat']
    else:
        player_options = ['fold', 'check', 'call', 'bet', 'raise', 'all-in', 'chat']
        if player_cost_to_call == 0:
            player_options.remove('fold')
        if player_cost_to_call > 0:
            player_options.remove('check')
        if not player_has_enough_to_call or player_cost_to_call == 0:
            player_options.remove('call')
        if poker_hand.pots[0].current_bet > 0 or player_cost_to_call > 0:
            player_options.remove('bet')
        if poker_player.money - poker_hand.pots[0].current_bet <= 0 or 'bet' in player_options:
            player_options.remove('raise')
        if not settings.all_in_allowed or poker_player.money == 0:
            player_options.remove('all-in')

    poker_player.options = player_options.copy()


def get_player_action(player, hand_state) -> PokerAction:
    if isinstance(player, AIPokerPlayer):
        return get_ai_player_action(player, hand_state)

    current_pot = hand_state["current_pot"]
    cost_to_call = current_pot.get_player_cost_to_call(player.name)

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
        add_to_pot = int(CONSOLE_INTERFACE.get_user_input("Enter amount: "))
        action = "bet"
    elif action in ["raise", "r", "ra", "rai", "rais"]:
        raise_amount = int(CONSOLE_INTERFACE.get_user_input(f"Calling {cost_to_call}.\nEnter amount to raise: "))
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

    chat_message = CONSOLE_INTERFACE.get_user_input("Enter table comment (optional): ")
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

    # Create the update message to be shared with the AI before they take their action
    hand_update_message = player.build_hand_update_message(hand_state)
    # Show the update shared with the AI
    if VIEW_AI_HAND_UPDATES:
        CONSOLE_INTERFACE.display_expander(label=f"{player.name}'s Hand Update",body=hand_update_message)

    # Get the response from the AI player
    response_json = player.get_player_response(hand_update_message)

    # Show the entire JSON response form the AI
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

    # TODO: return a dict that can be converted to a PokerAction so we can decouple the Classes
    # TODO: reduce what is sent from hand_state to just what is needed - unknown at this point what that will be
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

# Used to debug issues with folding and player_queue can likely be removed
def print_queue_status(player_queue: List[PokerPlayer]):
    for index, player in enumerate(player_queue):
        print(f"{index}: {player.name} - {player.folded}")

def process_pot_update(poker_hand, player: PokerPlayer, amount_to_add: int):
    poker_hand.pots[0].add_to_pot(player.name, player.get_for_pot, amount_to_add)

def handle_bet_or_raise(poker_hand, round_manager, player: PokerPlayer, add_to_pot: int, next_round_queue: List[PokerPlayer]):
    process_pot_update(poker_hand, player, add_to_pot)
    return betting_round(poker_hand, next_round_queue, round_manager, is_initial_round=False)

def handle_all_in(poker_hand, round_manager, player: PokerPlayer, add_to_pot: int, next_round_queue: List[PokerPlayer]):
    raising = add_to_pot > poker_hand.pots[0].current_bet
    process_pot_update(poker_hand, player, add_to_pot)
    if raising:
        return betting_round(poker_hand, next_round_queue, round_manager, is_initial_round=False)
    else:
        # TODO: create a side pot
        pass

def handle_call(poker_hand, player: PokerPlayer, add_to_pot: int):
    process_pot_update(poker_hand, player, add_to_pot)

def handle_fold(round_manager, player: PokerPlayer):
    player.folded = True
    round_manager.set_remaining_players()


def poker_game_state(rm: RoundManager, hand: PokerHand):
    # Assuming rm.round_manager_state and hand.hand_state are both dictionaries
    return {**rm.round_manager_state, **hand.hand_state}


def betting_round(poker_hand, player_queue: List[PokerPlayer], round_manager, is_initial_round: bool = True):
    # Check to see if remaining players are all-in

    active_player_queue = initialize_active_players(player_queue, is_initial_round)

    if len(round_manager.remaining_players) <= 0:
        raise ValueError("No remaining players left in the hand")

    for player in active_player_queue:
        if player.folded:
            continue

        all_in_count = 0
        for p in round_manager.remaining_players:
            if p.money <= 0:
                all_in_count += 1
        if all_in_count == len(round_manager.remaining_players):
            return
        elif len(round_manager.remaining_players) <= 1:
            return
        else:
            # print_queue_status(player_queue)
            set_player_options(poker_hand,player, PokerSettings(),
                               round_manager.big_blind_player, round_manager.small_blind)

            poker_action = get_player_action(player, poker_game_state(round_manager, poker_hand))
            poker_hand.poker_actions.append(poker_action)

            if process_player_action(poker_hand, round_manager, player, poker_action):
                return


def initialize_active_players(player_queue: List[PokerPlayer], is_initial_round: bool) -> List[
    PokerPlayer]:
    return player_queue.copy() if is_initial_round else player_queue[:-1]


def process_player_action(poker_hand, round_manager, player: PokerPlayer, poker_action: PokerAction) -> bool:
    player_action = poker_action.player_action
    amount = poker_action.amount

    if player_action in {PlayerAction.BET, PlayerAction.RAISE}:
        handle_bet_or_raise(poker_hand, round_manager, player, amount, round_manager.get_next_round_queue(round_manager.remaining_players, player))
        return True
    elif player_action == PlayerAction.ALL_IN:
        handle_all_in(poker_hand, round_manager, player, amount, round_manager.get_next_round_queue(round_manager.remaining_players, player))
        return True
    elif player_action == PlayerAction.CALL:
        handle_call(poker_hand, player, amount)
    elif player_action == PlayerAction.FOLD:
        handle_fold(round_manager, player)
    elif player_action == PlayerAction.CHECK:
        return False
    elif player_action == PlayerAction.CHAT:
        # TODO: implement handle_chat to open up  ability for AIs to chat with each other or the player.
        pass
    else:
        raise ValueError("Invalid action selected: " + str(player_action))
    return False

# TODO: update to not use interface
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


def play_hand(poker_game):
    ph = poker_game.hands[-1]
    rm = poker_game.round_manager

    round_queue = rm.setup_hand(ph.pots[0], ph.current_phase)
    CONSOLE_INTERFACE.display_text(f"{rm.dealer.name}'s deal.\n")
    CONSOLE_INTERFACE.display_text(
        f"Small blind: {rm.small_blind_player.name}\n Big blind: {rm.big_blind_player.name}\n")

    betting_round(ph, round_queue, rm, True)

    reveal_flop(ph, rm.deck)
    start_player = rm.determine_start_player(ph.current_phase)
    index = rm.players.index(start_player)
    round_queue = rm.players.copy()  # Copy list of all players that started the hand, could include folded
    shift_list_left(round_queue, index)  # Move to the start_player
    betting_round(ph, round_queue, rm)

    reveal_turn(ph, rm.deck)
    betting_round(ph, round_queue, rm)

    reveal_river(ph, rm.deck)
    betting_round(ph, round_queue, rm)

    # Evaluate and announce the winner
    winning_player, winning_hand = poker_game.determine_winner(ph)
    winning_player_name = winning_player.name
    CONSOLE_INTERFACE.display_text(f"The winner is {winning_player_name}! They win the pot of {ph.pots[0].total}")

    # Get end of hand reactions from AI players
    player_reactions = []
    winner_shows_cards = True  # TODO: let the winner decide if they want to show their cards in cases where they don't need to
    winners_cards_string = f"{winning_player_name} didn't show their cards!"  # Initialize the message in the case that the winner did not show their cards

    winning_hand_string = ""
    if winner_shows_cards:
        winning_hand_string = [str(card) for card in winning_hand]
        winners_cards_string = "|".join(winners_cards_string)

    for player in rm.players:
        if isinstance(player, AIPokerPlayer):
            message = build_hand_complete_update_message(player_name=player.name,
                                                         winning_player_name=winning_player_name,
                                                         total_pot=ph.pots[0].total,
                                                         amount_lost=ph.pots[0].player_pot_amounts[player.name],
                                                         winning_hand=winning_hand_string,
                                                         shown_cards=winners_cards_string
                                                         )
            response_json = player.get_player_response(message)

            reaction = {
                "name": player.name,
                "response_json": response_json
                }
            player_reactions.append(reaction)

    if VIEW_AI_ACTION_INSIGHTS:
        for r in player_reactions:
            if r is not None:
                name = r["name"]
                message = r["response_json"]
                CONSOLE_INTERFACE.display_text(name)
                CONSOLE_INTERFACE.display_text(message)

    game_summary = poker_game.round_manager.summarize_actions([action.action_comment for action in ph.poker_actions])
    CONSOLE_INTERFACE.display_text(game_summary)

    # Reset game for next round
    ph.pots[0].resolve_pot(winning_player_name, winning_player.collect_winnings)  # TODO: implement support for side-pots (multiple pots)
    rm.rotate_dealer()
    # Return community cards to Deck discard pile
    rm.deck.return_cards_to_discard_pile(ph.community_cards)
    # Reset players
    for player in rm.players:
        # Return players cards to Deck discard pile
        rm.deck.return_cards_to_discard_pile(player.cards)
        player.folded = False

    return rm.dealer

def play_game(poker_game: PokerGame):
    # ph = PokerHand(players=poker_game.players,
    #                        dealer=poker_game.players[random.randint(0, len(poker_game.players) - 1)],
    #                        deck=poker_game.deck)
    poker_hand = PokerHand()
    poker_hand.pots[-1].initialize_pot([p.name for p in poker_game.round_manager.remaining_players])
    while len(poker_game.round_manager.remaining_players) > 1:
        poker_game.hands.append(poker_hand)
        play_hand(poker_game)      # TODO: why are we returning a dealer?

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
            # poker_hand = PokerHand(players=poker_game.round_manager.remaining_players,
            #                        dealer=poker_game.round_manager.dealer,
            #                        deck=poker_game.round_manager.deck)

    CONSOLE_INTERFACE.display_text("Game over!")


def main(num_ai_players: int = 1, num_human_players: Optional[int] = 1):
    human_player_names = ["Jeff"]
    ai_player_names = get_ai_players(num_players=num_ai_players)

    poker_game = PokerGame()
    poker_game.round_manager.add_players(human_player_names, ai=False)
    poker_game.round_manager.add_players(ai_player_names, ai=True)
    poker_game.round_manager.initialize_players()
    poker_game.round_manager.deck.shuffle()
    play_game(poker_game)


if __name__ == "__main__":
    main()