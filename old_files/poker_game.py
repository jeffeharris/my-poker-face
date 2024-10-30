import logging
import random
from typing import List

from poker.utils import get_celebrities

from hand_evaluator import HandEvaluator
from old_files.poker_hand import PokerHand
from old_files.poker_settings import PokerSettings
from old_files.round_manager import RoundManager

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

    def to_dict(self):
        poker_game_dict = {
            "round_manager": self.round_manager.to_dict(),
            "hands": PokerHand.list_to_dict(self.hands),
            "settings": self.settings.to_dict(),
        }
        return poker_game_dict

    @classmethod
    def from_dict(cls, poker_game_dict: dict):
        instance = cls()
        round_manager = RoundManager.from_dict(poker_game_dict["round_manager"])
        instance.round_manager = round_manager
        # instance.hands = poker_game_dict["hands"]
        instance.hands = [PokerHand.from_dict(hand_dict) for hand_dict in poker_game_dict["hands"]]
        instance.settings=PokerSettings.from_dict(poker_game_dict["settings"])
        return instance

    @property
    def game_state(self):
        round_manager = self.round_manager
        state = {"round_manager": round_manager.to_dict(), "hands": PokerHand.list_to_dict(self.hands), "settings": self.settings.to_dict()}
        return state

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

    def initialize_game(self, num_ai_players):
        human_player_names = self.get_human_player_names()
        ai_player_names = self.get_ai_players(num_players=num_ai_players)

        self.round_manager.add_players(human_player_names, ai=False)
        self.round_manager.add_players(ai_player_names, ai=True)
        self.round_manager.initialize_players()
        self.round_manager.deck.shuffle()

    @staticmethod
    def get_ai_players(num_players: int, celebrities=None, random_seed=None):
        """
        Retrieve a list of players, either for testing or actual gameplay.

        Parameters:
            test (bool): Flag to indicate if test players should be used.
            num_players (int): Total number of players required.
            humans (list): List of definite players.
            celebrities (list): List of celebrity names.
            random_seed (int): Seed for random number generator (optional).

        Returns:
            Dict: 2 lists of player names.
        """
        celebrities = celebrities if celebrities else get_celebrities()

        if random_seed is not None:
            random.seed(random_seed)

        random.shuffle(celebrities)
        randos = celebrities[:num_players]
        player_names = randos

        return player_names

    def play_continues(self):
        continue_game = False
        for player in self.round_manager.players:
            if not player.folded and player.money > 0:
                continue_game = True
                break
        return continue_game