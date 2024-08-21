from typing import Dict, List

from core.poker import PokerHand
from core.poker_player import PokerPlayer, AIPokerPlayer


def obj_to_dict(self):
        def serialize(converted_object):
            """
            Helper function to serialize a value.
            Recursively handles lists and dictionaries.
            """
            if hasattr(converted_object, 'to_dict'):
                return converted_object.to_dict()
            elif isinstance(converted_object, dict):
                return {k: serialize(v) for k, v in converted_object.items()}
            elif isinstance(converted_object, list):
                return [serialize(v) for v in converted_object]
            elif isinstance(converted_object, (str, int, float, bool, type(None))):
                return converted_object
            else:
                return str(converted_object)  # Convert to string or use a placeholder

        result = {}
        for key, value in self.__dict__.items():
            try:
                result[key] = serialize(value)
            except Exception as e:
                result[key] = f"Error serializing {key}: {str(e)}"
        return result


def poker_player_from_dict(player_dict: Dict) -> PokerPlayer:
    if player_dict["type"] == "PokerPlayer":
        player = PokerPlayer(name=player_dict["name"])
    elif player_dict["type"] == "AIPokerPlayer":
        player = AIPokerPlayer(name=player_dict["name"], ai_temp=player_dict["ai_temp"])
    else:
        ValueError("Invalid player type")

    player.from_dict(player_dict)
    return player


def hand_from_dict(hand_dict):
    pass


def hand_list_from_dict(hand_dict_list):
    hand_list = []
    for hand_dict in hand_dict_list:
        hand = hand_from_dict(hand_dict)
        hand_list.append(hand)
    return hand_list


def players_to_dict(players: List[PokerPlayer]) -> List[Dict]:
    player_dict_list = []
    for player in players:
        player_dict = player.to_dict()
        player_dict_list.append(player_dict)
    return player_dict_list


def hands_to_dict(hands: List[PokerHand]) -> List[Dict]:
    hand_dict_list = []
    for hand in hands:
        hand_dict = hand.to_dict()
        hand_dict_list.append(hand_dict)
    return hand_dict_list
