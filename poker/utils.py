import random
from enum import Enum
from typing import Tuple, Dict, List

# Constants for configuration
ATTRIBUTE_CONSTRAINT = "Use less than 20 words"
# Only include celebrities that have personality configurations
# See poker/personalities.json for the personality definitions
# This list is automatically updated when personalities are generated
CELEBRITIES_LIST = [
    "A Mime",
    "A guy who tells too many dad jokes",
    "Abraham Lincoln",
    "Ace Ventura",
    "Barack Obama",
    "Batman",
    "Bill Clinton",
    "Bob Ross",
    "Buddha",
    "C3PO",
    "Chris Rock",
    "Crocodile Dundee",
    "Dave Chappelle",
    "Deadpool",
    "Donald Trump",
    "Dr. Oz",
    "Dr. Seuss",
    "Eeyore",
    "Fred Durst",
    "Gordon Ramsay",
    "Hulk Hogan",
    "James Bond",
    "Jay Gatsby",
    "Jesus Christ",
    "Jim Cramer",
    "Jon Stewart",
    "Khloe and Kim Khardashian",
    "King Henry VIII",
    "Lance Armstrong",
    "Lizzo",
    "Louis XIV",
    "Marjorie Taylor Greene",
    "Napoleon",
    "R2-D2",
    "Sarah Silverman",
    "Shakespeare",
    "Sherlock Holmes",
    "Socrates",
    "Someone who is very, very mean to people",
    "The Hulk",
    "The Rock",
    "Tom Cruise",
    "Triumph the Insult Comic Dog",
    "Tyler Durden",
    "Whoopi Goldberg",
    "Winston Churchill"
]

# Full list of potential celebrities for future expansion
ALL_CELEBRITIES_LIST = [
    "Ace Ventura", "Khloe and Kim Khardashian", "Fred Durst", "Tom Cruise",
    "James Bond", "Jon Stewart", "Jim Cramer", "Marjorie Taylor Greene",
    "Lizzo", "Bill Clinton", "Barack Obama", "Jesus Christ",
    "Triumph the Insult Comic Dog", "Donald Trump", "Batman", "Deadpool",
    "Lance Armstrong", "A Mime", "Jay Gatsby", "Whoopi Goldberg",
    "Dave Chappelle", "Chris Rock", "Sarah Silverman", "Napoleon",
    "Dr. Seuss", "Dr. Oz", "A guy who tells too many dad jokes",
    "Someone who is very, very mean to people", "Socrates", "Shakespeare",
    "C3PO", "R2-D2", "Winston Churchill", "Abraham Lincoln", "Buddha",
    "Crocodile Dundee", "Tyler Durden", "Hulk Hogan", "The Rock", "The Hulk",
    "King Henry VIII", "Louis XIV", "Kim Jong Un", "Scarlett Johansson",
    "Joan of Ark", "John Wayne", "Doc Holiday", "Captain Jack Sparrow",
    "Terry Tate, Office Linebacker", "Bob Dylan", "Captain Spock", "Scarlett Johansson",
    "Howard Stern", "Elmo", "Captain Ahab", "Dracula", "Ludacris", "Lil John",
    "A cat", "A dog", "A horse", "A fish", "A pig", "A chicken", "A turtle",
    "Bart Simpson", "Homer Simpson", "An alien who has never played poker",
    "Phil Helmuth", "Doyle Brunson", "Bilbo Baggins", "Fox Mulder", "Al Pacino",
    "Gallagher", "Lloyd Christmas", "Yogi Bear", "Scooby Doo", "Shaggy", "The Grinch",
    "Santa Claus", "Satan", "Hercules", "Micky Mouse", "Rocky", "Benjamin Franklin",
]


def get_celebrities(shuffled: bool = False):
    """Retrieve the list of celebrities."""
    celebrities_list = CELEBRITIES_LIST
    random.shuffle(celebrities_list) if shuffled else None
    return celebrities_list


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


def prepare_ui_data(game_state) -> Tuple[Dict, List]:
    """
    Prepare the data needed for the UI to display the current game state and actions available to the player.

    :param game_state: (GameState)
        The current state of the game containing all relevant information.
    :return: (tuple)
        A tuple containing two elements:
        - A dictionary with UI data including community cards, player's hand, pot total, player's stack, cost to call, and player's name.
        - A list of player options available for the current player.
    """
    player_options = game_state.current_player_options
    cost_to_call_bet = game_state.highest_bet - game_state.current_player.bet
    current_player = game_state.current_player
    opponents = [p.name for p in game_state.players if p != current_player]

    ui_data = {
        'community_cards': game_state.community_cards,
        'player_hand': current_player.hand,
        'pot_total': game_state.pot['total'],
        'player_stack': current_player.stack,
        'cost_to_call': cost_to_call_bet,
        'player_name': current_player.name,
        'opponents': opponents,
    }

    return ui_data, player_options
