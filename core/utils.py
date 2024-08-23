import random

from core.poker_player import PokerPlayer, AIPokerPlayer


def get_players(test=False, num_players=2):
    definites = [
        PokerPlayer("Jeff")
    ]

    if test:
        basic_test_players = [
            PokerPlayer("Player1"),
            PokerPlayer("Player2"),
            PokerPlayer("Player3"),
            PokerPlayer("Player4")
        ]

        players = basic_test_players

    else:
        celebrities = [
            AIPokerPlayer("Ace Ventura", ai_temp=.9),
            AIPokerPlayer("Khloe and Kim Khardashian"),
            AIPokerPlayer("Fred Durst"),
            AIPokerPlayer("Tom Cruise"),
            AIPokerPlayer("James Bond"),
            AIPokerPlayer("Jon Stewart"),
            AIPokerPlayer("Jim Cramer", ai_temp=.7),
            AIPokerPlayer("Marjorie Taylor Greene", ai_temp=.7),
            AIPokerPlayer("Lizzo"),
            AIPokerPlayer("Bill Clinton"),
            AIPokerPlayer("Barack Obama"),
            AIPokerPlayer("Jesus Christ"),
            AIPokerPlayer("Triumph the Insult Dog", ai_temp=.7),
            AIPokerPlayer("Donald Trump", ai_temp=.7),
            AIPokerPlayer("Batman"),
            AIPokerPlayer("Deadpool"),
            AIPokerPlayer("Lance Armstrong"),
            AIPokerPlayer("A Mime", ai_temp=.8),
            AIPokerPlayer("Jay Gatsby"),
            AIPokerPlayer("Whoopi Goldberg"),
            AIPokerPlayer("Dave Chappelle"),
            AIPokerPlayer("Chris Rock"),
            AIPokerPlayer("Sarah Silverman"),
            AIPokerPlayer("Kathy Griffin"),
            AIPokerPlayer("Dr. Seuss", ai_temp=.7),
            AIPokerPlayer("Dr. Oz"),
            AIPokerPlayer("A guy who tells too many dad jokes")
        ]

        random.shuffle(celebrities)
        randos = celebrities[0:(num_players - len(definites))]
        players = definites + randos
        for player in players:
            if isinstance(player, AIPokerPlayer):
                i = random.randint(0, 2)
                player.confidence = player.initialize_attribute("confidence",
                                                                "Use less than 20 words",
                                                                "other players",
                                                                mood=i)
                player.attitude = player.initialize_attribute("attitude",
                                                              "Use less than 20 words",
                                                              "other players",
                                                              mood=i)
    return players


def shift_list_left(my_list: list, count: int = 1):
    """
    :param my_list: list that you want to manipulate
    :param count: how many shifts you want to make
    """
    for i in range(1, count + 1):
        # Pop from the beginning of the list and append to the end
        my_list.append(my_list.pop(0))


def shift_list_right(my_list: list, count: int = 1):
    """
    :param my_list: list that you want to manipulate
    :param count: how many shifts you want to make
    """
    for i in range(1, count + 1):
        # Pop from the end of the list and insert it at the beginning
        my_list.insert(0, my_list.pop())


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
