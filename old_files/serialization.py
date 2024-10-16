# def poker_player_from_dict(player_dict: Dict) -> PokerPlayer:
#     if player_dict["type"] == "PokerPlayer":
#         player = PokerPlayer(name=player_dict["name"])
#     elif player_dict["type"] == "AIPokerPlayer":
#         player = AIPokerPlayer(name=player_dict["name"], ai_temp=player_dict["ai_temp"])
#     else:
#         ValueError("Invalid player type")
#
#     player.from_dict(player_dict)
#     return player
#
#
# def hand_from_dict(hand_dict):
#     pass
#

# def hand_list_from_dict(hand_dict_list):
#     hand_list = []
#     for hand_dict in hand_dict_list:
#         hand = hand_from_dict(hand_dict)
#         hand_list.append(hand)
#     return hand_list
#
#
# def players_to_dict(players: List[PokerPlayer]) -> List[Dict]:
#     player_dict_list = []
#     for player in players:
#         player_dict = player.to_dict()
#         player_dict_list.append(player_dict)
#     return player_dict_list
#
#
# def hands_to_dict(hands: List[PokerHand]) -> List[Dict]:
#     hand_dict_list = []
#     for hand in hands:
#         hand_dict = hand.to_dict()
#         hand_dict_list.append(hand_dict)
#     return hand_dict_list
