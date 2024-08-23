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
            "current_round": self.current_round,
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

#     def betting_round(self, player_queue: List['PokerPlayer'], is_initial_round: bool = True):
#         active_players = self.initialize_active_players(player_queue, is_initial_round)
#
#         if len(self.remaining_players) <= 1:
#             raise ValueError("No remaining players left in the hand")
#
#         for player in active_players:
#             if player.folded:
#                 continue
#
#             print_queue_status(player_queue)
#             self.set_player_options(player, PokerSettings())
#
#             poker_action = player.get_player_action(self.hand_state)
#             self.poker_actions.append(poker_action)
#
#             if self.process_player_action(player, poker_action):
#                 return
#
#     def initialize_active_players(self, player_queue: List['PokerPlayer'], is_initial_round: bool) -> List[
#         'PokerPlayer']:
#         return player_queue.copy() if is_initial_round else player_queue[:-1]
#
#     def process_player_action(self, player: 'PokerPlayer', poker_action: 'PokerAction') -> bool:
#         player_action = poker_action.player_action
#         amount = poker_action.amount
#
#         if player_action in {PokerPlayer.PlayerAction.BET, PokerPlayer.PlayerAction.RAISE}:
#             self.handle_bet_or_raise(player, amount, self.get_next_round_queue(self.remaining_players, player))
#             return True
#         elif player_action == PokerPlayer.PlayerAction.ALL_IN:
#             self.handle_all_in(player, amount, self.get_next_round_queue(self.remaining_players, player))
#             return True
#         elif player_action == PokerPlayer.PlayerAction.CALL:
#             self.handle_call(player, amount)
#         elif player_action == PokerPlayer.PlayerAction.FOLD:
#             self.handle_fold(player)
#         elif player_action == PokerPlayer.PlayerAction.CHECK:
#             return False
#         else:
#             raise ValueError("Invalid action selected: " + str(player_action))
#         return False
#
#     def reveal_cards(self, num_cards: int, round_name: PokerHandPhase):
#         """
#         Reveal the cards.
#
#         :param num_cards: Number of cards to reveal
#         :param round_name: Name of the current round
#         :return: string with text to output and revealed cards
#         """
#         self.deck.discard(1)
#         new_cards = self.deck.deal(num_cards)
#         self.community_cards += new_cards
#         self.current_round = round_name
#         output_text = f"""
#                     ---***{round_name}***---
#             {self.community_cards}
# """
#         output_text += render_cards(self.community_cards)
#
#         return output_text, new_cards
#
#     # TODO: update to not use interface
#     def reveal_flop(self):
#         output_text, new_cards = self.reveal_cards(3, PokerHandPhase.FLOP)
#         self.interface.display_text(output_text)
#
#     def reveal_turn(self):
#         output_text, new_cards = self.reveal_cards(1, PokerHandPhase.TURN)
#         self.interface.display_text(output_text)
#
#     def reveal_river(self):
#         output_text, new_cards = self.reveal_cards(1, PokerHandPhase.RIVER)
#         self.interface.display_text(output_text)

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

    def get_table_positions(self) -> Dict[str, PokerPlayer]:
        table_positions = {"dealer": self.dealer,
                           "small_blind_player": self.players[(self.dealer_position + 1) % len(self.players)],
                           "big_blind_player": self.players[(self.dealer_position + 2) % len(self.players)],
                           "under_the_gun": self.players[(self.dealer_position + 3) % len(self.players)]
                           }
        return table_positions
