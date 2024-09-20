import logging
from typing import List

from poker.hand_evaluator import HandEvaluator
from poker.poker_hand import PokerHand
from poker.poker_settings import PokerSettings
from poker.round_manager import RoundManager

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.WARNING)     # DEBUG, INFO, WARNING, ERROR, CRITICAL


class PokerGame:
    # Class-level type hints
    round_manager: RoundManager
    hands: List[PokerHand]
    settings: PokerSettings

    def __init__(self):
        self.round_manager = RoundManager()
        self.hands = []
        self.settings = PokerSettings()

    @classmethod
    def from_dict(cls, poker_game_dict: dict):
        pg = cls()
        pg.round_manager=RoundManager.from_dict(poker_game_dict["rm"])
        pg.hands = [PokerHand.from_dict(hand_dict) for hand_dict in poker_game_dict["hands"]]
        pg.settings=PokerSettings.from_dict(poker_game_dict["settings"])
        return pg

    @property
    def game_state(self):
        rm = self.round_manager
        hand = self.hands[-1] if self.hands else None
        state = {**rm.round_manager_state, **hand.hand_state}
        return state

    def to_dict(self):
        poker_game_dict = {
            "rm": self.round_manager.to_dict(),
            "hands": PokerHand.list_to_dict(self.hands),
            "settings": self.settings.to_dict(),
        }
        return poker_game_dict

    def summarize_hands(self, count=1):
        hand_summaries = []
        for hand in self.hands[-count:]:
            summary = summarize_hand(hand)
            hand_summaries.append(summary)

        response = self.round_manager.assistant.chat(f"Please review these poker hands and provide a brief summary:\n"
                                         f"{hand_summaries}")
        return response

    def determine_winner(self, hand):
        # initialize a list which will hold a Tuple of (PokerPlayer, HandEvaluator)
        hands = []

        for player in self.round_manager.players:
            if not player.folded:
                hands.append((player, HandEvaluator(player.cards + self.hands[-1].community_cards).evaluate_hand()))


        # TODO: <REFACTOR> remove all of the prints from determine_winner, replace with a different UX
        print(f"Before sorting:\n"
              f"Community Cards: {hand.community_cards.cards}\n")
        for player, hand_info in hands:
            print(f"{player.name}'s hand: {player.cards} | {hand_info}")

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

        winning_player = hands[0][0]
        winning_hand = hands[0][1]["hand_values"]
        return winning_player, winning_hand