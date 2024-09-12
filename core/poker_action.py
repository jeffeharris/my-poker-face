from enum import Enum
from typing import Optional, Dict


class PlayerAction(Enum):
    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    BET = "bet"
    RAISE = "raise"
    ALL_IN = "all-in"
    CHAT = "chat"
    NONE = None  # TODO: remove None as an option


# TODO: write unit tests for PokerAction
class PokerAction:
    player: str
    player_action: PlayerAction
    amount: Optional[int]
    hand_state: Optional[Dict]
    action_detail: Optional[str]
    action_comment: Optional[str]

    def __init__(self,
                 player: str,
                 action: str,
                 amount: int or None = None,
                 hand_state: Dict or None = None,
                 action_detail: str or None = None,
                 action_comment: str or None = None):
        self.player = player
        self.player_action = PlayerAction(action)
        self.amount = amount
        self.hand_state = hand_state.copy()
        self.action_detail = action_detail
        self.action_comment = action_comment

    def to_dict(self):
        return {
            'player': self.player,
            'player_action': self.player_action,
            'amount': self.amount,
            'hand_state': self.hand_state,
            'action_detail': self.action_detail
        }

    @classmethod
    def from_dict(cls, dict_data):
        return cls(
            dict_data['player_action'],
            dict_data['player_action'],
            dict_data['amount'],
            dict_data['hand_state'],
            dict_data['action_detail']
        )
