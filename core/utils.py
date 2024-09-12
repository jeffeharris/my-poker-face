import random
from enum import Enum

# Constants for configuration
DEFAULT_NUM_PLAYERS = 2
ATTRIBUTE_CONSTRAINT = "Use less than 20 words"
CELEBRITIES_LIST = [
    "Ace Ventura", "Khloe and Kim Khardashian", "Fred Durst", "Tom Cruise",
    "James Bond", "Jon Stewart", "Jim Cramer", "Marjorie Taylor Greene",
    "Lizzo", "Bill Clinton", "Barack Obama", "Jesus Christ",
    "Triumph the Insult Dog", "Donald Trump", "Batman", "Deadpool",
    "Lance Armstrong", "A Mime", "Jay Gatsby", "Whoopi Goldberg",
    "Dave Chappelle", "Chris Rock", "Sarah Silverman", "Kathy Griffin",
    "Dr. Seuss", "Dr. Oz", "A guy who tells too many dad jokes",
    "Someone who is very, very mean to people", "Socrates", "Shakespeare",
    "C3PO", "R2-D2", "Winston Churchill", "Abraham Lincoln", "Buddha",
    "Crocodile Dundee", "Tyler Durden", "Hulk Hogan", "The Rock", "The Hulk",
    "King Henry VIII", "Louis XIV", "Kim Jong Un", "Scarlett Johansson",
    "Joan of Ark", "John Wayne", "Doc Holiday", "Captain Jack Sparrow",
    "Terry Tate, Office Linebacker", "Bob Dylan", "Captain Spock", "Scarlett Johansson",
    "Howard Stern", "Elmo", "Captain Ahab", "Dracula", "Ludacris", "Lil John",
]


def get_celebrities():
    """Retrieve the list of celebrities."""
    return CELEBRITIES_LIST


def initialize_test_players():
    """Set up test players for simplified testing scenario."""
    return ["Player1", "Player2"]


def get_ai_players(num_players=DEFAULT_NUM_PLAYERS, celebrities=None, random_seed=None):
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


def shift_list_left(my_list: list, count: int = 1):
    """
    Shifts the elements of the given list to the left by a specified count.

    :param my_list: The list to manipulate.
    :param count: The number of positions to shift the list to the left. Default is 1.
    """
    if not my_list:  # Handle empty list
        return
    count = count % len(my_list)  # Ensure count is within valid bounds
    my_list[:] = my_list[count:] + my_list[:count]


def shift_list_right(my_list: list, count: int = 1):
    """
    Shifts the elements of the given list to the right by a specified count.

    :param my_list: The list to manipulate.
    :param count: The number of positions to shift the list to the right. Default is 1.
    """
    if not my_list:  # Handle empty list
        return
    count = -count % len(my_list)
    my_list[:] = my_list[count:] + my_list[:count]


def obj_to_dict(self):
    result = {}
    for key, value in self.__dict__.items():
        try:
            result[key] = serialize(value)
        except Exception as e:
            result[key] = f"Error serializing {key}: {str(e)}"
    return result


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
        # Provide a placeholder or convert to string
        return str(converted_object)


# TODO: <REFACTOR> move bake to PokerHand or RoundManager
class PokerHandPhase(Enum):
    INITIALIZING = "initializing"
    PRE_FLOP = "pre-flop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"   # TODO: implement showdown gameplay
