import random
from typing import List, Optional


class Card:
    SUIT_TO_ASCII = {'Hearts': '♥', 'Diamonds': '♦', 'Clubs': '♣', 'Spades': '♠'}
    RANK_VALUES = {'2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9, '10': 10, 'J': 11, 'Q': 12, 'K': 13,
                   'A': 14}
    CARD_TEMPLATE = '''
.---------.
|{}       |
| {}       |
|         |
|         |
|    {}    |
|       {}|
`---------'
'''
    TWO_CARD_TEMPLATE = '''
.---.---------.
|{}  |{}        |
|  {}|  {}      |
|   |         |
|   |         |
|   |       {} |
|   |        {}|
`---`---------'
'''

    def __init__(self, rank, suit):
        self.rank = rank
        self.suit = suit
        self.value = Card.RANK_VALUES[rank]

    def __repr__(self):
        return f"Card('{self.rank.ljust(2)}', '{self.suit}')"

    def __str__(self):
        return f"{self.rank}{Card.SUIT_TO_ASCII[self.suit]}"

    def __dict__(self):
        return card_to_dict(self)

    def render_card(self):
        rank_left = self.rank.ljust(2)
        rank_right = self.rank.rjust(2)
        card = Card.CARD_TEMPLATE.format(rank_left, Card.SUIT_TO_ASCII[self.suit], Card.SUIT_TO_ASCII[self.suit], rank_right)
        return card

    def to_dict(self):
        return card_to_dict(self)


class Deck:
    RANKS = list(Card.RANK_VALUES.keys())
    SUITS = list(Card.SUIT_TO_ASCII.keys())
    cards: List['Card']
    discarded_cards: List['Card']

    def __init__(self):
        self.cards = [Card(rank, suit) for rank in Deck.RANKS for suit in Deck.SUITS]
        self.discard_pile = []
        self.shuffle()

    def __dict__(self):
        return deck_to_dict(self)

    def to_dict(self):
        return deck_to_dict(self)

    def shuffle(self) -> None:
        random.shuffle(self.cards)

    def deal(self, num=1) -> List['Card']:
        return [self.cards.pop() for _ in range(num)]

    def discard(self, num=1) -> List['Card']:
        discarded_cards = self.deal(num)
        self.discard_pile += discarded_cards
        return discarded_cards

    def return_cards_to_deck(self, cards: List['Card']) -> None:
        self.cards += cards

    def reset(self) -> None:
        if self._validate_deck():
            self.return_cards_to_deck(self.discard_pile)
            self.shuffle()
        else:
            SystemError("Deck is missing cards")

    def _validate_deck(self) -> bool:
        card_count = len(self.cards) + len(self.discard_pile)
        if card_count != 52:
            return False
        return True


def render_cards(cards: List['Card']) -> Optional[str]:
    card_lines = [card.render_card().strip().split('\n') for card in cards]
    if not card_lines:
        return None
    ascii_card_lines = []
    for lines in zip(*card_lines):
        ascii_card_lines.append('  '.join(lines))
    card_ascii_string = '\n'.join(ascii_card_lines)
    return card_ascii_string


def render_two_cards(card_1, card_2):
    # Generate and print each card
    two_card_ascii_string = Card.TWO_CARD_TEMPLATE.format(card_1.rank,
                                                          card_2.rank,
                                                          Card.SUIT_TO_ASCII[card_1.suit],
                                                          Card.SUIT_TO_ASCII[card_2.suit],
                                                          Card.SUIT_TO_ASCII[card_2.suit],
                                                          card_2.rank)
    return two_card_ascii_string


def card_to_dict(card: Card) -> dict:
    return {'rank': card.rank, 'suit': card.suit, 'value': card.value}


def deck_to_dict(deck: Deck) -> dict:
    deck_dict = {
        'cards': [card_to_dict(card) for card in deck.cards],
        'discard_pile': [card_to_dict(card) for card in deck.discard_pile]
    }
    return deck_dict

def cards_from_dict(card_dict_list: List[dict]) -> List[Card]:
    cards_list = []
    for card_dict in card_dict_list:
        card = Card(card_dict["rank"], card_dict["suit"])
        cards_list.append(card)
    return cards_list

def deck_from_dict(deck_dict: dict) -> Deck:
    deck = Deck()
    deck.cards = cards_from_dict(deck_dict["cards"])
    deck.discard_pile = cards_from_dict(deck_dict["discard_pile"])
    return deck