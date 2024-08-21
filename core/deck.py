import random
from core.card import *


class Deck:
    RANKS = list(Card.RANK_VALUES.keys())
    SUITS = list(Card.SUIT_TO_ASCII.keys())
    cards: List[Card]
    discarded_cards: List[Card]

    def __init__(self):
        self.cards = self._init_cards()
        self.discard_pile = []
        self.shuffle()

    @staticmethod
    def _init_cards() -> List[Card]:
        """Initialize the deck with cards."""
        return [Card(rank, suit) for rank in Deck.RANKS for suit in Deck.SUITS]

    def to_dict(self) -> Dict[str, List[Dict[str, str]]]:
        """Convert the Deck to a dictionary representation."""
        deck_dict = {
            'cards': [card.to_dict() for card in self.cards],
            'discard_pile': [card.to_dict() for card in self.discard_pile]
        }
        return deck_dict

    @classmethod
    def from_dict(cls, deck_dict: Dict[str, List[Dict[str, str]]]) -> 'Deck':
        """Create a Deck instance from a dictionary representation."""
        deck = cls()
        deck.cards = Card.list_from_dict_list(deck_dict['cards'])
        deck.discard_pile = Card.list_from_dict_list(deck_dict['discard_pile'])
        return deck

    def shuffle(self) -> None:
        """Shuffle the deck of cards."""
        random.shuffle(self.cards)

    def deal(self, num=1) -> List[Card]:
        """Deal a number of cards from the Deck."""
        return [self.cards.pop() for _ in range(num)]

    def discard(self, num=1) -> List[Card]:
        """Discard a number of cards from the Deck."""
        discarded_cards = self.deal(num)
        self.discard_pile += discarded_cards
        return discarded_cards

    def _return_cards_to_deck(self, cards: List[Card], shuffle: bool = True) -> None:
        """Return cards to the Deck and optionally shuffle."""
        self.cards += cards
        if shuffle:
            self.shuffle()

    def return_cards_to_discard_pile(self, cards: List[Card]) -> None:
        """Return cards to the discard pile."""
        self.discard_pile += cards

    def reset(self) -> None:
        """Reset the Deck by returning all cards from the discard pile."""
        if self._validate_deck():
            self._return_cards_to_deck(self.discard_pile)
            self.discard_pile = []
            self.shuffle()
        else:
            raise DeckError("Deck is missing cards")

    def _validate_deck(self) -> bool:
        return len(self.cards) + len(self.discard_pile) == 52

class DeckError(Exception):
    """Custom exception for Deck-related errors."""
    pass
