import random
from core.card import *


class CardSet:
    RANKS = list(Card.RANK_VALUES.keys())
    SUITS = list(Card.SUIT_TO_ASCII.keys())
    cards: List[Card]

    def __init__(self):
        self.cards = []

    def __len__(self):
        return len(self.cards)

    def __add__(self, other: 'CardSet'):
        return self.cards + other.cards

    def __getitem__(self, index):
        return self.cards[index]

    @classmethod
    def cards_from_dict(cls, deck_dict: Dict[str, List[Dict[str, str]]]) -> 'CardSet':
        """Create a Deck instance from a dictionary representation."""
        cards = cls()
        cards.cards = Card.list_from_dict_list(deck_dict['cards'])
        cards.discard_pile = Card.list_from_dict_list(deck_dict['discard_pile'])
        return cards

    def deal(self, deal_to: 'CardSet', num: int = 1) -> None:
        """Deal a number of cards from the Deck."""
        cards = [self.cards.pop() for _ in range(num)]
        deal_to.add_cards(cards)
        return None

    def add_cards(self, cards):
        for card in cards:
            self.cards.append(card)

    def copy(self):
        """ Returns a new object that is a copy of the current object."""
        instance = type(self)()
        instance.cards = self.cards.copy()
        return instance


class Deck:
    card_deck: CardSet
    discard_pile: CardSet

    def __init__(self):
        self.card_deck = self._init_deck()
        self.discard_pile = CardSet()

    def to_dict(self) -> Dict[str, List[Dict[str, str]]]:
        """Convert the Deck to a dictionary representation."""
        deck_dict = {
            'card_deck': [card.to_dict() for card in self.card_deck.cards],
            'discard_pile': [card.to_dict() for card in self.discard_pile]
        }
        return deck_dict

    @staticmethod
    def _init_deck() -> CardSet:
        """Initialize the deck with cards."""
        deck = CardSet()
        deck.add_cards([Card(rank, suit) for rank in deck.RANKS for suit in deck.SUITS])

        return deck

    def shuffle(self) -> None:
        """Shuffle the deck of cards."""
        random.shuffle(self.card_deck.cards)

    def discard(self, num=1) -> None:
        """Discard a number of cards from the Deck."""
        self.card_deck.deal(self.discard_pile, num)
        return None

    def _return_cards_to_deck(self, cards: List[Card], shuffle: bool = True) -> None:
        """Return cards to the Deck and optionally shuffle."""
        self.card_deck.add_cards(cards)
        if shuffle:
            self.shuffle()

    def return_cards_to_discard_pile(self, cards: List[Card]) -> None:
        """Return cards to the discard pile."""
        self.discard_pile.cards += cards

    def reset(self) -> None:
        """Reset the Deck by returning all cards from the discard pile."""
        if self._validate_deck():
            self._return_cards_to_deck(self.discard_pile.cards)
            self.shuffle()
        else:
            raise DeckError("Deck is missing cards")

    def _validate_deck(self) -> bool:
        return len(self.card_deck) + len(self.discard_pile) == 52

class DeckError(Exception):
    """Custom exception for Deck-related errors."""
    pass
