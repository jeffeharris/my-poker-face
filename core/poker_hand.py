from enum import Enum
from typing import List, Optional

from core.deck import CardSet
from core.hand_evaluator import HandEvaluator
from core.poker_action import PokerAction
from core.poker_hand_pot import PokerHandPot
from core.poker_player import PokerPlayer
from core.round_manager import RoundManager
from core.utils import shift_list_left, obj_to_dict


class PokerHandPhase(Enum):
    INITIALIZING = "initializing"
    PRE_FLOP = "pre-flop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"


class PokerHand:
    """
    PokerHand manages teh state of a hand within a game
    """
    poker_actions: List[PokerAction]
    community_cards: CardSet
    current_round: PokerHandPhase
    pots: List[PokerHandPot]
    round_manager: RoundManager

    def __init__(self):
        self.poker_actions = []
        self.community_cards = CardSet()
        self.current_round = PokerHandPhase.INITIALIZING
        self.pots = [PokerHandPot(self.round_manager.players)]
        self.round_manager = RoundManager()

    def to_dict(self):
        return obj_to_dict(self.hand_state)
        # TODO: clean up unnecessary code for PokerHand.to_dict()
        # hand_state_dict = self.hand_state
        # hand_state_dict['community_cards'] = Card.list_to_dict(self.community_cards)
        # hand_state_dict['current_pot'] = self.pots[0].to_dict()
        # hand_state_dict['players'] = players_to_dict(self.table_manager.players)
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
    def hand_state(self):
        hand_state = {
            "community_cards": self.community_cards.copy(),
            "current_bet": self.pots[0].current_bet,
            "current_pot": self.pots[0],
            "players": self.round_manager.players,
            "opponent_status": self.get_opponent_status(),
            "table_positions": self.get_table_positions(),
            "current_situation": f"The {self.current_round.value} cards have just been dealt",
            "current_round": self.current_round.value,
            "table_messages": self.round_manager.table_messages,
            "table_manager": self.round_manager,
            "poker_actions": self.poker_actions,
            "remaining_players": self.remaining_players,
        }
        return hand_state

    def set_current_round(self, current_round: PokerHandPhase):
        self.current_round = current_round

    def player_bet_this_hand(self, player: PokerPlayer) -> int:
        pot_contributions = []
        for pot in self.pots:
            pot_contributions.append(pot.get_player_pot_amount(player))
        return sum(pot_contributions)

    def determine_start_player(self):
        start_player = None
        if self.current_round == PokerHandPhase.PRE_FLOP:
            # Player after big blind starts
            start_player = self.round_manager.players[(self.dealer_position + 3) % len(self.round_manager.players)]
        else:
            # Find the first player after the dealer who hasn't folded
            for j in range(1, len(self.round_manager.players) + 1):
                index = (self.dealer_position + j) % len(self.round_manager.players)
                if not self.round_manager.players[index].folded:
                    start_player = self.round_manager.players[index]
                    break
        return start_player

    def summarize_actions(self, count) -> str:
        """
        Function should take in text descriptions of actions taken during a poker round and create a summary.
        """
        actions = self.poker_actions[-count:]

        if actions is str:
            action_summary = actions
        else:
            summary_request = f"Please summarize these actions for a poker game in the style of {self.name}: {actions}\n"
            message = [{"role": "user", "content": summary_request}]
            action_summary = self.round_manager.assistant.get_response(message)
        return action_summary

    def get_next_round_queue(self, round_queue, betting_player: Optional[PokerPlayer]):
        next_round_queue = round_queue.copy()
        if betting_player:
            index = round_queue.index(betting_player) + 1
        else:
            index = 1
        shift_list_left(next_round_queue, index)
        return next_round_queue

    def determine_winner(self):
        # initialize a list which will hold a Tuple of (PokerPlayer, HandEvaluator)
        hands = []

        for player in self.round_manager.players:
            if not player.folded:
                hands.append((player, HandEvaluator(player.cards + self.community_cards).evaluate_hand()))


        # TODO: remove all of the prints from determine_winner, replace with a different UX
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

        winner_name = hands[0][0]
        winning_hand = hands[0][1]["hand_values"]
        return winner_name, winning_hand

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
    #     self.table_manager.players = [player for player in self.starting_players if player.money > 0]
    #     if len(self.table_manager.players) == 1:
    #         self.interface.display_text(f"{self.table_manager.players[0].name} is the last player remaining and wins the game!")
    #         return
    #     elif len(self.table_manager.players) == 0:
    #         self.interface.display_text("You... you all lost. Somehow you all have no money.")
    #         return
    #
    #     # Reset players
    #     for player in self.table_manager.players:
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

        index = self.round_manager.players.index(start_player)  # Set index at the start_player
        round_queue = self.round_manager.players.copy()  # Copy list of all players that started the hand, could include folded
        shift_list_left(round_queue, index)  # Move to the start_player

        return round_queue

    # def summarize_poker_actions(self, count=None):
    #     """
    #     Get the last N actions for the hand summarized
    #     """
    #     summary = []
    #     for action in self.poker_actions[-count:]:
    #         s = summarize_action(action)
    #         summary.append(s)

