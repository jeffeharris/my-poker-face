from typing import List, Dict, Optional


class Card:
    """
    Represents a playing card.

    Attributes:
        SUIT_TO_ASCII (dict): A dictionary that maps the suit names to their corresponding ASCII symbols.
        RANK_VALUES (dict): A dictionary that maps the rank names to their corresponding values.

    Methods:
        __init__(self, rank: str, suit: str): Initializes a Card object with the given rank and suit.
        to_dict(self) -> Dict[str, str or int]: Returns a dictionary representation of the Card object.
        from_dict(cls, card_dict) -> 'Card': Creates a Card object from a dictionary representation.
        list_from_dict_list(cls, card_dict_list: List[Dict[str, str]]) -> List['Card']: Creates a list of Card objects from a list of dictionary representations.
        get_rank_value(self) -> int: Returns the value associated with the rank of the Card object.
        get_suit_symbol(self) -> str: Returns the ASCII symbol associated with the suit of the Card object.
        __repr__(self): Returns a string representation of the Card object.
        __str__(self): Returns a formatted string representation of the Card object.
        __eq__(self, other): Checks if two Card objects are equal.

    """
    SUIT_TO_ASCII = {'Hearts': '♥', 'Diamonds': '♦', 'Clubs': '♣', 'Spades': '♠'}
    ASCII_TO_SUIT = {v: k for k, v in SUIT_TO_ASCII.items()}
    RANK_VALUES = {'2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7,'8': 8,
                   '9': 9, '10': 10, 'J': 11, 'Q': 12, 'K': 13, 'A': 14}

    def __init__(self, rank, suit):
        self.rank = rank
        self.suit = suit
        self.value = Card.RANK_VALUES[rank]

    def to_dict(self) -> Dict[str, str or int]:
        return {
            'rank': self.rank,
            'suit': self.suit,
            'suit_symbol': self.get_suit_symbol(),
            # 'value': self.value
        }

    @staticmethod
    def list_to_dict(card_list: List['Card']) -> List[Dict[str, str or int]]:
        card_dict_list = []
        for card in card_list:
            card_dict_list.append(card.to_dict())
        return card_dict_list

    @classmethod
    def from_dict(cls, card_dict) -> 'Card':
        rank = card_dict['rank']
        suit_input = card_dict['suit']
        # Convert from ASCII symbol to full suit name if necessary
        suit = cls.ASCII_TO_SUIT.get(suit_input, suit_input)  # Default to suit_input if not an ASCII symbol
        return cls(rank, suit)

    @classmethod
    def list_from_dict_list(cls, card_dict_list: List[Dict[str, str]]) -> List['Card']:
        card_list = []
        for card_dict in card_dict_list:
            card_list.append(cls.from_dict(card_dict))
        return card_list

    def get_rank_value(self) -> int:
        return Card.RANK_VALUES[self.rank]

    def get_suit_symbol(self) -> str:
        return Card.SUIT_TO_ASCII[self.suit]

    def __repr__(self):
        return f"Card('{self.rank.ljust(2)}', '{self.suit}')"

    def __str__(self):
        return f"{self.rank}{Card.SUIT_TO_ASCII[self.suit]}"

    def __eq__(self, other):
        if isinstance(other, Card):
            return self.rank == other.rank and self.suit == other.suit
        return False


class CardRenderer:
    _CARD_TEMPLATE = '''
.---------.
|{}       |
| {}       |
|         |
|         |
|      {}  |
|       {}|
`---------'
'''
    _TWO_CARD_TEMPLATE = '''
.---.---------.
|{}  |{}        |
|  {}|  {}      |
|   |         |
|   |         |
|   |       {} |
|   |        {}|
`---`---------'
'''

    @staticmethod
    def render_card(card):
        """
            Render a card object for output to the console.

            :param card: (Card)
                The card object to render.
            :return: (str)
                A string representation of the card formatted for console output.
            :raises KeyError:
                If the card's suit is not found in the suit-to-ASCII map.
        """
        rank_left = card.rank.ljust(2)
        rank_right = card.rank.rjust(2)
        card = CardRenderer._CARD_TEMPLATE.format(rank_left, Card.SUIT_TO_ASCII[card.suit], Card.SUIT_TO_ASCII[card.suit], rank_right)
        return card

    @staticmethod
    def render_cards(cards: List[Card]) -> Optional[str]:
        """
        Renders a list of Cards for output to the console.

        :param cards: (List[Card])
            A list of Card objects to be rendered.
        :return: (Optional[str])
            A string containing the rendered ASCII representation of the cards,
            or None if the card list is empty.
        """
        card_lines = [CardRenderer.render_card(card).strip().split('\n') for card in cards]
        if not card_lines:
            return None
        ascii_card_lines = []
        for lines in zip(*card_lines):
            ascii_card_lines.append('  '.join(lines))
        card_ascii_string = '\n'.join(ascii_card_lines)
        return card_ascii_string

    @staticmethod
    def render_two_cards(card_1, card_2):
        """
        Renders two cards for output to the console. Meant to represent the cards as the players' hole cards.

        :param card_1: (Card)
            The first card to render.
        :param card_2: (Card)
            The second card to render.
        :return: (str)
            ASCII representation of the two cards.
        :raises KeyError:
            If the suit of either card is not found in the SUIT_TO_ASCII mapping.
        """
        two_card_ascii_string = CardRenderer._TWO_CARD_TEMPLATE.format(card_1.rank,
                                                         card_2.rank,
                                                         Card.SUIT_TO_ASCII[card_1.suit],
                                                         Card.SUIT_TO_ASCII[card_2.suit],
                                                         Card.SUIT_TO_ASCII[card_2.suit],
                                                         card_2.rank)
        return two_card_ascii_string

    @staticmethod
    def render_hole_cards(cards: List[Card]):
        sorted_cards = sorted(cards, key=lambda card: card.value)
        card_1 = sorted_cards[0]
        card_2 = sorted_cards[1]

        # Generate console output for the Cards
        hole_card_art = CardRenderer.render_two_cards(card_1, card_2)
        return hole_card_art
