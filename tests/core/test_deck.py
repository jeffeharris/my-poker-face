import unittest
from core.deck import Deck


class DeckTestCase(unittest.TestCase):
    def setUp(self):
        self.maxDiff = None
        self.deck = Deck()

    def test_init_cards(self):
        self.assertEqual(len(self.deck.cards), 52)

    def test_to_dict(self):
        deck_dict = self.deck.to_dict()
        # Here, convert self.deck.cards and self.deck.discard_pile to dictionaries
        deck_cards_dicts = [card.to_dict() for card in self.deck.cards]
        discard_pile_dicts = [card.to_dict() for card in self.deck.discard_pile]

        # Now compare the two lists of dictionaries
        self.assertCountEqual(deck_dict['cards'] + deck_dict['discard_pile'], deck_cards_dicts + discard_pile_dicts)

    def test_from_dict(self):
        deck_dict = self.deck.to_dict()
        new_deck = Deck.from_dict(deck_dict)
        self.assertCountEqual(new_deck.cards + new_deck.discard_pile, self.deck.cards + self.deck.discard_pile)

    def test_shuffle(self):
        old_cards_order = self.deck.cards[:]
        self.deck.shuffle()
        self.assertNotEqual(old_cards_order, self.deck.cards)

    def test_deal(self):
        dealt_cards = self.deck.deal(5)
        self.assertEqual(len(dealt_cards), 5)

    def test_discard(self):
        discarded_cards = self.deck.discard(3)
        self.assertEqual(len(discarded_cards), 3)
        self.assertEqual(len(self.deck.discard_pile), 3)

    def test_return_cards_to_deck(self):
        discarded_cards = self.deck.discard(3)
        self.deck._return_cards_to_deck(discarded_cards)
        self.assertEqual(len(self.deck.cards), 52)

    def test_return_cards_to_discard_pile(self):
        discarded_cards = self.deck.discard(3)
        self.deck.return_cards_to_discard_pile(discarded_cards)
        self.assertEqual(len(self.deck.discard_pile), 6)

    def test_reset(self):
        # Discard some cards to ensure the deck is in a modified state before resetting
        self.deck.discard(3)
        # Reset the deck to its initial state
        self.deck.reset()
        # Assert that the deck has 52 cards after reset
        self.assertEqual(len(self.deck.cards), 52)
        # Assert that the discard pile is empty after reset
        self.assertEqual(len(self.deck.discard_pile), 0)

    def test_validate_deck(self):
        valid = self.deck._validate_deck()
        self.assertTrue(valid)

        self.deck.deal(1)
        valid = self.deck._validate_deck()
        self.assertFalse(valid)


if __name__ == '__main__':
    unittest.main()
