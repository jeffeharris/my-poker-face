import random
from typing import List, Optional, Any

from core.card import Card
from core.game import Interface
from core.poker_action import PokerAction, PlayerAction
from core.poker_game import PokerGame
from core.poker_hand import PokerHand, PokerHandPhase
from core.poker_player import PokerPlayer
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
    def request_action(self, options: List, request: str, default_option: Optional[int] = None) -> Optional[str]:
        print(options)
        return input(request)

    def display_text(self, text: str):
        print(text)

    def display_expander(self, label: str, body: Any):
        self.display_text(body)

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


def get_player_action(player, hand_state):
    community_cards = hand_state['community_cards']
    current_bet = hand_state['current_bet']
    current_pot = hand_state['current_pot']
    cost_to_call = current_pot.get_player_cost_to_call(player)
    total_to_pot = current_pot.get_player_pot_amount(player)

    CONSOLE_INTERFACE.display_text(display_hole_cards(player.cards))
    text_lines = [
        f"{player.name}'s turn. Current cards: {player.cards} Current money: {player.money}",
        f"Community cards: {community_cards}",
        f"Current bet: {current_bet}",
        f"Current pot: {current_pot.total}",
        f"Cost to call: {cost_to_call}",
        f"Total to pot: {total_to_pot}"
    ]

    text = "\n".join(text_lines)

    CONSOLE_INTERFACE.display_text(text)
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
    # self.chat_message = input("Enter chat message (optional): ")
    # if not self.chat_message:
    #     f"{self.name} chooses to {action}."
    # TODO: return a dict that can be converted to a PokerAction on the other end
    poker_action = PokerAction(player, action, add_to_pot, hand_state)
    return poker_action

# TODO: determine if this is needed or can be deleted
def print_queue_status(player_queue: List[PokerPlayer]):
    for index, player in enumerate(player_queue):
        print(f"{index}: {player.name} - {player.folded}")

def process_pot_update(poker_hand, player: PokerPlayer, amount_to_add: int):
    poker_hand.pots[0].add_to_pot(player, amount_to_add)

def handle_bet_or_raise(poker_hand, player: PokerPlayer, add_to_pot: int, next_round_queue: List['PokerPlayer']):
    process_pot_update(poker_hand, player, add_to_pot)
    return betting_round(poker_hand, next_round_queue, is_initial_round=False)

def handle_all_in(poker_hand, player: PokerPlayer, add_to_pot: int, next_round_queue: List['PokerPlayer']):
    process_pot_update(poker_hand, player, add_to_pot)
    raising = add_to_pot > poker_hand.pots[0].current_bet
    if raising:
        return poker_hand.betting_round(next_round_queue, is_initial_round=False)

def handle_call(poker_hand, player: PokerPlayer, add_to_pot: int):
    process_pot_update(poker_hand, player, add_to_pot)

def handle_fold(poker_hand, player: PokerPlayer):
    player.folded = True
    poker_hand.set_remaining_players()

def betting_round(poker_hand, player_queue: List[PokerPlayer], is_initial_round: bool = True):
    active_players = initialize_active_players(player_queue, is_initial_round)

    if len(poker_hand.remaining_players) <= 0:
        raise ValueError("No remaining players left in the hand")

    for player in active_players:
        if player.folded:
            continue

        print_queue_status(player_queue)
        poker_hand.set_player_options(player, PokerSettings())

        poker_action = get_player_action(player, poker_hand.hand_state)
        poker_hand.poker_actions.append(poker_action)

        if process_player_action(poker_hand, player, poker_action):
            return

def initialize_active_players(player_queue: List['PokerPlayer'], is_initial_round: bool) -> List[
    'PokerPlayer']:
    return player_queue.copy() if is_initial_round else player_queue[:-1]

def process_player_action(poker_hand, player: 'PokerPlayer', poker_action: 'PokerAction') -> bool:
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
    winning_player = poker_hand.determine_winner()
    CONSOLE_INTERFACE.display_text(f"The winner is {winning_player.name}! They win the pot of {poker_hand.pots[0].total}")

    # Reset game for next round
    poker_hand.pots[0].resolve_pot(winning_player)
    poker_hand.rotate_dealer()

    # Check if the game should continue
    poker_hand.players = [player for player in poker_hand.starting_players if player.money > 0]
    if len(poker_hand.players) == 1:
        CONSOLE_INTERFACE.display_text(f"{poker_hand.players[0].name} is the last player remaining and wins the game!")
        return
    elif len(poker_hand.players) == 0:
        CONSOLE_INTERFACE.display_text("You... you all lost. Somehow you all have no money.")
        return

    # Return community cards to Deck
    poker_hand.deck.return_cards_to_discard_pile(poker_hand.community_cards)
    # Reset players
    for player in poker_hand.players:
        poker_hand.deck.return_cards_to_discard_pile(player.cards)
        player.folded = False

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


def main(test=False, num_players=3):
    players = get_players(test=test, num_players=num_players)
    poker_game = PokerGame(players)
    play_game(poker_game)


if __name__ == "__main__":
    main()