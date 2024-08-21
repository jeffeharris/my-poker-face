import logging
import random
from enum import Enum
from typing import List, Dict, Optional

from console_app.console_app import render_cards
from core.hand_evaluator import HandEvaluator
from core.poker_player import PokerPlayer
# from core.serialization import cards_to_dict, players_to_dict, hands_to_dict
from core.utils import get_players, shift_list_left
from core.deck import Deck
from core.card import Card
from .game import Game, Interface, OpenAILLMAssistant, ConsoleInterface

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)     # DEBUG, INFO, WARNING, ERROR, CRITICAL


class PokerSettings:
    all_in_allowed: bool
    starting_small_blind: int
    player_starting_money: int
    ai_player_starting_money: int or None

    def __init__(self,
                 all_in_allowed: bool = True,
                 starting_small_blind: int = 50,
                 player_starting_money: int = 10000,
                 ai_player_starting_money: int = None
                 ):
        self.all_in_allowed = all_in_allowed
        self.starting_small_blind = starting_small_blind
        self.player_starting_money = player_starting_money

        if ai_player_starting_money is None:
            self.ai_player_starting_money = self.player_starting_money
        else:
            self.ai_player_starting_money = ai_player_starting_money

    def to_dict(self):
        return self.__dict__


class PokerAction:
    player: PokerPlayer
    player_action: PokerPlayer.PlayerAction
    amount: Optional[int]
    hand_state: Optional[dict]
    action_detail: Optional[str]

    def __init__(self,
                 player: PokerPlayer,
                 action: str,
                 amount: int or None = None,
                 hand_state: dict or None = None,
                 action_detail: str or None = None):
        self.player = player
        self.player_action = PokerPlayer.PlayerAction(action)
        self.amount = amount
        self.hand_state = hand_state.copy()
        self.action_detail = action_detail

    def __str__(self):
        return (f"PokerAction("
                f" player={self.player}, "
                f" action={self.player_action}, "
                f" amount={self.amount}, "
                f" hand_state={self.hand_state}, "
                f" detail={self.action_detail}"
                f")")

    def to_dict(self):
        return self.__dict__

    @classmethod
    def from_dict(cls, data: dict):
        return cls(**data)


class PokerHandPot:
    player_pot_amounts: Dict['PokerPlayer', int]
    pot_winner: PokerPlayer or None

    @property
    def total(self) -> int:
        return sum(self.player_pot_amounts.values())

    @property
    def current_bet(self) -> int:
        return max(self.player_pot_amounts.values())

    def __init__(self, poker_players: List[PokerPlayer]):
        self.player_pot_amounts = {}
        self.pot_winner = None

        for player in poker_players:
            self.player_pot_amounts[player] = 0

    def to_dict(self):
        pot_dict = {'player_pot_amounts': {}}
        for player in self.player_pot_amounts:
            pot_dict['player_pot_amounts'][player.name] = self.player_pot_amounts[player]
        pot_dict['pot_winner'] = self.pot_winner
        return pot_dict

    def get_player_pot_amount(self, player: PokerPlayer) -> int:
        return self.player_pot_amounts[player]

    def get_player_cost_to_call(self, player: PokerPlayer) -> int:
        player_contributed = self.get_player_pot_amount(player)
        return self.current_bet - player_contributed

    def add_to_pot(self, player: PokerPlayer, amount: int) -> None:
        player.get_for_pot(amount)
        self.player_pot_amounts[player] += amount

    def resolve_pot(self, pot_winner: PokerPlayer) -> None:
        pot_winner.money += self.total
        self.pot_winner = pot_winner


def print_queue_status(player_queue: List['PokerPlayer']):
    for index, player in enumerate(player_queue):
        print(f"{index}: {player.name} - {player.folded}")


class PokerHand:
    class PokerHandPhase(Enum):
        INITIALIZING = "initializing"
        PRE_FLOP = "pre-flop"
        FLOP = "flop"
        TURN = "turn"
        RIVER = "river"

    interface: Interface
    players: List[PokerPlayer]
    starting_players: List[PokerPlayer]
    remaining_players: List[PokerPlayer]
    deck: Deck
    table_positions: Dict[str, PokerPlayer]
    dealer: PokerPlayer
    small_blind_player: PokerPlayer
    big_blind_player: PokerPlayer
    under_the_gun: PokerPlayer
    poker_actions: List[PokerAction]
    community_cards: List[Card]
    current_round: PokerHandPhase
    pots: List[PokerHandPot]
    small_blind: int
    min_bet: int

    @property
    def dealer_position(self):
        return self.players.index(self.dealer)

    @property
    def hand_state(self):
        hand_state = {
            "game_interface": self.interface,
            "community_cards": self.community_cards.copy(),
            "current_bet": self.pots[0].current_bet,
            "current_pot": self.pots[0],
            "players": self.players,
            "opponent_positions": self.get_table_positions(),
            "current_situation": f"The {self.current_round.value} cards have just been dealt",
            "current_round": self.current_round,
        }
        return hand_state

    def __init__(self,
                 interface: Interface,
                 players: List['PokerPlayer'],
                 dealer: PokerPlayer,
                 deck: Deck):
        self.interface = interface
        self.players = players
        self.starting_players = list(players)
        self.remaining_players = list(players)
        self.dealer = dealer
        self.deck = deck
        self.poker_actions = []
        self.community_cards = []
        self.current_round = PokerHand.PokerHandPhase.INITIALIZING
        self.pots = [PokerHandPot(self.players)]
        self.small_blind = PokerSettings().starting_small_blind
        self.small_blind_player = self.players[(self.dealer_position + 1) % len(self.players)]
        self.big_blind_player = self.players[(self.dealer_position + 2) % len(self.players)]
        self.under_the_gun = self.players[(self.dealer_position + 3) % len(self.players)]

    def to_dict(self):
        hand_state_dict = self.hand_state
        hand_state_dict['community_cards'] = Card.list_to_dict(self.community_cards)
        hand_state_dict['current_pot'] = self.pots[0].to_dict()
        hand_state_dict['players'] = players_to_dict(self.players)
        del hand_state_dict['game_interface']
        return hand_state_dict

    @classmethod
    def from_dict(cls, data: dict, interface: Interface):
        interface = data['game_interface']
        del data['game_interface']
        hand = cls(interface, **data)
        return hand

    def get_opponent_positions(self, requesting_player=None) -> List[str]:
        opponent_positions = []
        for player in self.players:
            if player != requesting_player:
                position = f'{player.name} has ${player.money}'
                position += ' and they have folded' if player.folded else ''
                position += '.\n'
                opponent_positions.append(position)
        return opponent_positions

    def set_current_round(self, current_round: PokerHandPhase):
        self.current_round = current_round

    def set_remaining_players(self):
        self.remaining_players = [player for player in self.players if not player.folded]

    def player_bet_this_hand(self, player: PokerPlayer) -> int:
        pot_contributions = []
        for pot in self.pots:
            pot_contributions.append(pot.get_player_pot_amount(player))
        return sum(pot_contributions)

    def post_blinds(self):
        small_blind = self.small_blind
        big_blind = small_blind * 2
        self.pots[0].add_to_pot(self.small_blind_player, small_blind)
        self.pots[0].add_to_pot(self.big_blind_player, big_blind)

    def deal_hole_cards(self):
        for player in self.players:
            player.cards = self.deck.deal(2)

    def determine_start_player(self):
        start_player = None
        if self.current_round == PokerHand.PokerHandPhase.PRE_FLOP:
            # Player after big blind starts
            start_player = self.players[(self.dealer_position + 3) % len(self.players)]
        else:
            # Find the first player after the dealer who hasn't folded
            for j in range(1, len(self.players) + 1):
                index = (self.dealer_position + j) % len(self.players)
                if not self.players[index].folded:
                    start_player = self.players[index]
                    break
        return start_player

    def process_pot_update(self, player: PokerPlayer, amount_to_add: int):
        self.pots[0].add_to_pot(player, amount_to_add)

    def handle_bet_or_raise(self, player: PokerPlayer, add_to_pot: int, next_round_queue: List['PokerPlayer']):
        self.process_pot_update(player, add_to_pot)
        return self.betting_round(next_round_queue, is_initial_round=False)

    def handle_all_in(self, player: PokerPlayer, add_to_pot: int, next_round_queue: List['PokerPlayer']):
        self.process_pot_update(player, add_to_pot)
        raising = add_to_pot > self.pots[0].current_bet
        if raising:
            return self.betting_round(next_round_queue, is_initial_round=False)

    def handle_call(self, player: PokerPlayer, add_to_pot: int):
        self.process_pot_update(player, add_to_pot)

    def handle_fold(self, player: PokerPlayer):
        player.folded = True
        self.set_remaining_players()

    # TODO: change this to return the options as a PlayerAction enum
    def set_player_options(self, poker_player: PokerPlayer, settings: PokerSettings):
        # How much is it to call the bet for the player?
        player_cost_to_call = self.pots[0].get_player_cost_to_call(poker_player)
        # Does the player have enough to call
        player_has_enough_to_call = poker_player.money > player_cost_to_call
        # Is the current player also the big_blind TODO: add "and have they played this hand yet"
        current_player_is_big_blind = poker_player is self.big_blind_player

        # If the current player is last to act (aka big blind), and we're still in the pre-flop round
        if (current_player_is_big_blind
                and self.current_round == PokerHand.PokerHandPhase.PRE_FLOP
                and self.pots[0].current_bet == self.small_blind * 2):
            player_options = ['check', 'raise', 'all-in']
        else:
            player_options = ['fold', 'check', 'call', 'bet', 'raise', 'all-in']
            if player_cost_to_call == 0:
                player_options.remove('fold')
            if player_cost_to_call > 0:
                player_options.remove('check')
            if not player_has_enough_to_call or player_cost_to_call == 0:
                player_options.remove('call')
            if self.pots[0].current_bet > 0 or player_cost_to_call > 0:
                player_options.remove('bet')
            if poker_player.money - self.pots[0].current_bet <= 0 or 'bet' in player_options:
                player_options.remove('raise')
            if not settings.all_in_allowed or poker_player.money == 0:
                player_options.remove('all-in')

        poker_player.options = player_options.copy()

    def get_next_round_queue(self, round_queue, betting_player: Optional['PokerPlayer']):
        next_round_queue = round_queue.copy()
        if betting_player:
            index = round_queue.index(betting_player) + 1
        else:
            index = 1
        shift_list_left(next_round_queue, index)
        return next_round_queue

    def betting_round(self, player_queue: List['PokerPlayer'], is_initial_round: bool = True):
        active_players = self.initialize_active_players(player_queue, is_initial_round)

        if len(self.remaining_players) <= 1:
            raise ValueError("No remaining players left in the hand")

        for player in active_players:
            if player.folded:
                continue

            print_queue_status(player_queue)
            self.set_player_options(player, PokerSettings())

            poker_action = player.get_player_action(self.hand_state)
            self.poker_actions.append(poker_action)

            if self.process_player_action(player, poker_action):
                return

    def initialize_active_players(self, player_queue: List['PokerPlayer'], is_initial_round: bool) -> List[
        'PokerPlayer']:
        return player_queue.copy() if is_initial_round else player_queue[:-1]

    def process_player_action(self, player: 'PokerPlayer', poker_action: 'PokerAction') -> bool:
        player_action = poker_action.player_action
        amount = poker_action.amount

        if player_action in {PokerPlayer.PlayerAction.BET, PokerPlayer.PlayerAction.RAISE}:
            self.handle_bet_or_raise(player, amount, self.get_next_round_queue(self.remaining_players, player))
            return True
        elif player_action == PokerPlayer.PlayerAction.ALL_IN:
            self.handle_all_in(player, amount, self.get_next_round_queue(self.remaining_players, player))
            return True
        elif player_action == PokerPlayer.PlayerAction.CALL:
            self.handle_call(player, amount)
        elif player_action == PokerPlayer.PlayerAction.FOLD:
            self.handle_fold(player)
        elif player_action == PokerPlayer.PlayerAction.CHECK:
            return False
        else:
            raise ValueError("Invalid action selected: " + str(player_action))
        return False

    def reveal_cards(self, num_cards: int, round_name: PokerHandPhase):
        """
        Reveal the cards.

        :param num_cards: Number of cards to reveal
        :param round_name: Name of the current round
        :return: string with text to output and revealed cards
        """
        self.deck.discard(1)
        new_cards = self.deck.deal(num_cards)
        self.community_cards += new_cards
        self.current_round = round_name
        output_text = f"""
                    ---***{round_name}***---
            {self.community_cards}
"""
        output_text += render_cards(self.community_cards)

        return output_text, new_cards

    def reveal_flop(self):
        output_text, new_cards = self.reveal_cards(3, PokerHand.PokerHandPhase.FLOP)
        self.interface.display_text(output_text)

    def reveal_turn(self):
        output_text, new_cards = self.reveal_cards(1, PokerHand.PokerHandPhase.TURN)
        self.interface.display_text(output_text)

    def reveal_river(self):
        output_text, new_cards = self.reveal_cards(1, PokerHand.PokerHandPhase.RIVER)
        self.interface.display_text(output_text)

    def determine_winner(self):
        hands = []

        for player in self.players:
            if not player.folded:
                hands.append((player, HandEvaluator(player.cards + self.community_cards).evaluate_hand()))

        print("Before sorting:")
        for player, hand_info in hands:
            print(f"{player.name}'s hand: {hand_info}")

        hands.sort(key=lambda x: sorted(x[1]["kicker_values"]), reverse=True)

        print("After sorting by kicker values:")
        for player, hand_info in hands:
            print(f"{player.name}'s hand: {hand_info}")

        hands.sort(key=lambda x: sorted(x[1]["hand_values"]), reverse=True)

        print("After sorting by hand values:")
        for player, hand_info in hands:
            print(f"{player.name}'s hand: {hand_info}")

        hands.sort(key=lambda x: x[1]["hand_rank"])

        print("After sorting by hand rank:")
        for player, hand_info in hands:
            print(f"{player.name}'s hand: {hand_info}")

        winner = hands[0][0]
        return winner

    def rotate_dealer(self):
        current_dealer_starting_player_index = self.starting_players.index(self.dealer)
        new_dealer_starting_player_index = (current_dealer_starting_player_index + 1) % len(self.starting_players)
        self.dealer = self.starting_players[new_dealer_starting_player_index]
        if self.dealer.money <= 0:
            self.rotate_dealer()

    def end_hand(self):
        # Evaluate and announce the winner
        winning_player = self.determine_winner()
        self.interface.display_text(f"The winner is {winning_player.name}! They win the pot of {self.pots[0].total}")

        # Reset game for next round
        self.pots[0].resolve_pot(winning_player)
        self.rotate_dealer()

        # Check if the game should continue
        self.players = [player for player in self.starting_players if player.money > 0]
        if len(self.players) == 1:
            self.interface.display_text(f"{self.players[0].name} is the last player remaining and wins the game!")
            return
        elif len(self.players) == 0:
            self.interface.display_text("You... you all lost. Somehow you all have no money.")
            return

        # Reset players
        for player in self.players:
            self.deck.return_cards_to_deck(player.cards)
            player.folded = False

        self.deck.reset()

    def play_hand(self):
        round_queue = self.setup_hand()

        self.betting_round(round_queue)

        self.reveal_flop()
        start_player = self.determine_start_player()
        index = self.players.index(start_player)
        round_queue = self.players.copy()  # Copy list of all players that started the hand, could include folded
        shift_list_left(round_queue, index)  # Move to the start_player
        self.betting_round(round_queue)

        self.reveal_turn()
        self.betting_round(round_queue)

        self.reveal_river()
        self.betting_round(round_queue)

        self.end_hand()

        return self.remaining_players, self.dealer

    def setup_hand(self):
        self.set_remaining_players()
        self.set_current_round(PokerHand.PokerHandPhase.PRE_FLOP)
        self.post_blinds()
        self.interface.display_text(f"{self.dealer.name}'s deal.\n")
        self.interface.display_text(
            f"Small blind: {self.small_blind_player.name}\n Big blind: {self.big_blind_player.name}\n")
        self.deal_hole_cards()

        start_player = self.determine_start_player()

        index = self.players.index(start_player)  # Set index at the start_player
        round_queue = self.players.copy()  # Copy list of all players that started the hand, could include folded
        shift_list_left(round_queue, index)  # Move to the start_player

        return round_queue

    def get_table_positions(self) -> Dict[str, PokerPlayer]:
        table_positions = {"dealer": self.dealer,
                           "small_blind_player": self.players[(self.dealer_position + 1) % len(self.players)],
                           "big_blind_player": self.players[(self.dealer_position + 2) % len(self.players)],
                           "under_the_gun": self.players[(self.dealer_position + 3) % len(self.players)]
                           }
        return table_positions


class PokerGame(Game):
    # Class-level type hints
    settings: PokerSettings
    players: List[PokerPlayer]
    starting_players: List[PokerPlayer]
    remaining_players: List[PokerPlayer]
    deck: Deck
    hands: List[PokerHand]
    assistant: OpenAILLMAssistant

    def __init__(self, players: List[PokerPlayer], interface: Interface):
        super().__init__(players, interface)
        self.settings = PokerSettings()
        self.starting_players = list(players)
        self.remaining_players = list(players)
        self.deck = Deck()
        self.hands = []
        self.assistant = OpenAILLMAssistant()

    def to_dict(self):
        poker_game_dict = {
            "players": players_to_dict(self.players),
            "interface": self.interface.to_dict(),
            "settings": self.settings.to_dict(),
            "starting_players": players_to_dict(self.starting_players),
            "remaining_players": players_to_dict(self.remaining_players),
            "deck": self.deck.to_dict(),
            "hands": hands_to_dict(self.hands),
            "assistant": self.assistant.to_dict(),
        }
        return poker_game_dict

    # TODO: pull this out to the console app
    def play_game(self):
        poker_hand = PokerHand(interface=self.interface,
                               players=self.players,
                               dealer=self.players[random.randint(0, len(self.players) - 1)],
                               deck=self.deck)
        while len(self.remaining_players) > 1:
            self.hands.append(poker_hand)
            self.remaining_players, dealer = poker_hand.play_hand()
            play_again = self.interface.request_action(
                ["yes", "no"],
                "Would you like to play another hand? ",
                0)
            if play_again != "yes":
                break
            else:
                poker_hand = PokerHand(interface=self.interface,
                                       players=self.remaining_players,
                                       dealer=dealer,
                                       deck=self.deck)

        self.display_text("Game over!")


def main(test=False, num_players=3):
    players = get_players(test=test, num_players=num_players)
    poker_game = PokerGame(players, ConsoleInterface())
    poker_game.play_game()


if __name__ == "__main__":
    main()
