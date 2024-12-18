from typing import List

from old_files.deck import CardSet
from poker_action import PokerAction
from old_files.poker_hand_pot import PokerHandPot
from poker.utils import obj_to_dict, PokerHandPhase

class PokerHand:
    """
    PokerHand manages the state of a hand within a game
    """
    poker_actions: List[PokerAction]
    community_cards: CardSet
    current_phase: PokerHandPhase
    pots: List[PokerHandPot]

    def __init__(self):
        self.poker_actions = []
        self.community_cards = CardSet()
        self.current_phase = PokerHandPhase.PRE_FLOP
        self.pots = [PokerHandPot()]

    def to_dict(self):
        return obj_to_dict(self)

    @staticmethod
    def list_to_dict(hands: List['PokerHand']):
        hand_dict_list = []
        for hand in hands:
            hand_dict = hand.to_dict()
            hand_dict_list.append(hand_dict)
        return hand_dict_list

    @classmethod
    def from_dict(cls, data: dict):
        # hand = cls(**data) # TODO: <BUG> implement a from_dict function to deserialize a PokerHand
        instance = cls()
        instance.poker_actions = PokerAction.list_from_dict_list(data["poker_actions"])
        instance.community_cards = CardSet.from_dict(data["community_cards"])
        phase_str = data["current_phase"].replace('PokerHandPhase.', '')
        instance.current_phase = PokerHandPhase[phase_str]
        instance.pots = [PokerHandPot.from_dict(data["pots"][0])]
        return instance

    @property
    def hand_state(self):
        hand_state = {
            "community_cards": self.community_cards.to_dict(),
            "current_bet": self.pots[0].current_bet,
            "current_pot": self.pots[0].to_dict(),
            "current_situation": f"The {self.current_phase.value} cards have just been dealt",
            "current_phase": self.current_phase.value,
            "poker_actions": self.poker_actions,
        }
        return hand_state

    def set_current_round(self, current_round: PokerHandPhase):
        self.current_phase = current_round

    # # TODO: <REFACTOR> bring back end_hand - maybe here or maybe the round_manager
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

    # def summarize_poker_actions(self, count=None):
    #     """
    #     Get the last N actions for the hand summarized
    #     """
    #     summary = []
    #     for action in self.poker_actions[-count:]:
    #         s = summarize_action(action)
    #         summary.append(s)

