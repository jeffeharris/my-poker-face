from enum import Enum
from typing import List, Dict, Optional

from core.card import Card
from core.deck import Deck
from core.hand_evaluator import HandEvaluator
from core.poker_action import PokerAction
from core.poker_hand_pot import PokerHandPot
from core.poker_player import PokerPlayer
from core.poker_settings import PokerSettings
from core.utils import shift_list_left, obj_to_dict


class PokerHandPhase(Enum):
    INITIALIZING = "initializing"
    PRE_FLOP = "pre-flop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"


class PokerHand:
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

    def __init__(self,
                 players: List['PokerPlayer'],
                 dealer: PokerPlayer,
                 deck: Deck):
        self.players = players
        self.starting_players = list(players)
        self.remaining_players = list(players)
        self.dealer = dealer
        self.deck = deck
        self.poker_actions = []
        self.community_cards = []
        self.current_round = PokerHandPhase.INITIALIZING
        self.pots = [PokerHandPot(self.players)]
        self.small_blind = PokerSettings().starting_small_blind
        self.small_blind_player = self.players[(self.dealer_position + 1) % len(self.players)]
        self.big_blind_player = self.players[(self.dealer_position + 2) % len(self.players)]
        self.under_the_gun = self.players[(self.dealer_position + 3) % len(self.players)]

    def to_dict(self):
        return obj_to_dict(self.hand_state)
        # TODO: clean up unnecessary code for PokerHand.to_dict()
        # hand_state_dict = self.hand_state
        # hand_state_dict['community_cards'] = Card.list_to_dict(self.community_cards)
        # hand_state_dict['current_pot'] = self.pots[0].to_dict()
        # hand_state_dict['players'] = players_to_dict(self.players)
        # return hand_state_dict

    @staticmethod
    def list_to_dict(hands):
        hand_dict_list = []
        for hand in hands:
            hand_dict = hand.to_dict()
            hand_dict_list.append(hand_dict)
        return hand_dict_list

    @classmethod
    def from_dict(cls, data: dict):
        hand = cls(**data)
        return hand

    @property
    def dealer_position(self):
        return self.players.index(self.dealer)

    @property
    def hand_state(self):
        hand_state = {
            "community_cards": self.community_cards.copy(),
            "current_bet": self.pots[0].current_bet,
            "current_pot": self.pots[0],
            "players": self.players,
            "opponent_positions": self.get_table_positions(),
            "current_situation": f"The {self.current_round.value} cards have just been dealt",
            "current_round": self.current_round.value,
        }
        return hand_state

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
        if self.current_round == PokerHandPhase.PRE_FLOP:
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
                and self.current_round == PokerHandPhase.PRE_FLOP
                and self.pots[0].current_bet == self.small_blind * 2):
            player_options = ['check', 'raise', 'all-in']
        else:
            player_options = ['fold', 'check', 'call', 'bet', 'raise', 'all-in', 'chat']
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

    # # TODO: update to not use interface
    # def end_hand(self):
    #     # Evaluate and announce the winner
    #     winning_player = self.determine_winner()
    #     self.interface.display_text(f"The winner is {winning_player.name}! They win the pot of {self.pots[0].total}")
    #
    #     # Reset game for next round
    #     self.pots[0].resolve_pot(winning_player)
    #     self.rotate_dealer()
    #
    #     # Check if the game should continue
    #     self.players = [player for player in self.starting_players if player.money > 0]
    #     if len(self.players) == 1:
    #         self.interface.display_text(f"{self.players[0].name} is the last player remaining and wins the game!")
    #         return
    #     elif len(self.players) == 0:
    #         self.interface.display_text("You... you all lost. Somehow you all have no money.")
    #         return
    #
    #     # Reset players
    #     for player in self.players:
    #         self.deck.return_cards_to_deck(player.cards)
    #         player.folded = False
    #
    #     self.deck.reset()

    def rotate_dealer(self):
        """
        Rotates the dealer to the next player in the starting players list.
        If the new dealer has no money, recursively finds the next eligible dealer.

        Parameters:
        - None

        Returns:
        - None

        Usage example:

          game = Game()  # create an instance of the Game class
          game.rotate_dealer()  # rotate the dealer
        """

        # Find the current dealer's position in the starting players list
        current_index = self.starting_players.index(self.dealer)

        # Calculate the new dealer's index using modulo for wrap-around
        new_index = (current_index + 1) % len(self.starting_players)

        # Update the dealer to the new player at the calculated index
        self.dealer = self.starting_players[new_index]

        # Check if the new dealer has no money left
        if self.dealer.money <= 0:
            # Recursively find the next eligible dealer
            self.rotate_dealer()

    def setup_hand(self):
        self.set_remaining_players()
        self.set_current_round(PokerHandPhase.PRE_FLOP)
        self.post_blinds()
        self.deal_hole_cards()

        start_player = self.determine_start_player()

        index = self.players.index(start_player)  # Set index at the start_player
        round_queue = self.players.copy()  # Copy list of all players that started the hand, could include folded
        shift_list_left(round_queue, index)  # Move to the start_player

        return round_queue

    def get_table_positions(self) -> Dict[str, str]:
        table_positions = {"dealer": self.dealer.name,
                           "small_blind_player": self.players[(self.dealer_position + 1) % len(self.players)].name,
                           "big_blind_player": self.players[(self.dealer_position + 2) % len(self.players)].name,
                           "under_the_gun": self.players[(self.dealer_position + 3) % len(self.players)].name
                           }
        return table_positions
