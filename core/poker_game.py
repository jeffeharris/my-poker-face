import logging
from typing import List

from core.poker_hand import PokerHand
from core.poker_player import PokerPlayer
from core.poker_settings import PokerSettings
# from core.serialization import cards_to_dict, players_to_dict, hands_to_dict
from core.deck import Deck
from core.game import Game, OpenAILLMAssistant, LLMAssistant  # , Interface, ConsoleInterface

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)     # DEBUG, INFO, WARNING, ERROR, CRITICAL


class PokerGame(Game):
    # Class-level type hints
    settings: PokerSettings
    players: List[PokerPlayer]
    starting_players: List[PokerPlayer]
    remaining_players: List[PokerPlayer]
    deck: Deck
    hands: List[PokerHand]
    assistant: OpenAILLMAssistant

    def __init__(self, players: List[PokerPlayer]):
        super().__init__(players)
        self.settings = PokerSettings()
        self.starting_players = list(players)
        self.remaining_players = list(players)
        self.deck = Deck()
        self.hands = []
        self.assistant = OpenAILLMAssistant()

    def to_dict(self):
        poker_game_dict = {
            "players": PokerPlayer.players_to_dict(self.players),
            "settings": self.settings.to_dict(),
            "starting_players": PokerPlayer.players_to_dict(self.starting_players),
            "remaining_players": PokerPlayer.players_to_dict(self.remaining_players),
            "deck": self.deck.to_dict(),
            "hands": PokerHand.list_to_dict(self.hands),
            "assistant": self.assistant.to_dict(),
        }
        return poker_game_dict

    @classmethod
    def from_dict(cls, poker_game_dict: dict):
        pg = PokerGame(players=PokerPlayer.list_from_dict_list(poker_game_dict["players"]))
        pg.settings=PokerSettings.from_dict(poker_game_dict["settings"])
        pg.starting_players=PokerPlayer.list_from_dict_list(poker_game_dict["starting_players"])
        pg.remaining_players=PokerPlayer.list_from_dict_list(poker_game_dict["remaining_players"])
        pg.deck=Deck.from_dict(poker_game_dict["deck"])
        pg.hands = [PokerHand.from_dict(hand_dict) for hand_dict in poker_game_dict["hands"]]
        # TODO: split the below into assistant.from_dict
        pg.assistant.ai_temp = poker_game_dict["assistant"]["ai_temp"]
        pg.assistant.ai_model = poker_game_dict["assistant"]["ai_model"]
        pg.assistant.system_message = poker_game_dict["assistant"]["system_message"]
        pg.assistant.memory = poker_game_dict["assistant"]["memory"]
        pg.assistant.functions = poker_game_dict["assistant"]["functions"]
        return pg