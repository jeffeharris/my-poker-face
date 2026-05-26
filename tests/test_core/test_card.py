import unittest

from core.card import Card


class TestCard(unittest.TestCase):
    def setUp(self):
        self.test_card = Card('A', 'Hearts')

    def test_card_init(self):
        self.assertEqual(self.test_card.rank, 'A')
        self.assertEqual(self.test_card.suit, 'Hearts')
        self.assertEqual(self.test_card.value, 14)

    def test_card_to_dict(self):
        card_dict = self.test_card.to_dict()
        self.assertDictEqual(
            card_dict, {'rank': 'A', 'suit': 'Hearts', 'suit_symbol': '♥', 'value': 14}
        )

    def test_card_from_dict(self):
        card_dict = {'rank': 'A', 'suit': 'Hearts'}
        new_card = Card.from_dict(card_dict)
        self.assertEqual(new_card, self.test_card)

    def test_card_list_from_dict_list(self):
        card_dict_list = [{'rank': 'A', 'suit': 'Hearts'}, {'rank': 'K', 'suit': 'Spades'}]
        card_list = Card.list_from_dict_list(card_dict_list)
        self.assertEqual(card_list[0], Card('A', 'Hearts'))
        self.assertEqual(card_list[1], Card('K', 'Spades'))

    def test_get_rank_value(self):
        self.assertEqual(self.test_card.get_rank_value(), 14)

    def test_get_suit_symbol(self):
        self.assertEqual(self.test_card.get_suit_symbol(), '♥')

    def test_card_repr(self):
        self.assertEqual(repr(self.test_card), "Card('A ', 'Hearts')")

    def test_card_str(self):
        self.assertEqual(str(self.test_card), 'A♥')

    def test_card_eq(self):
        other_card = Card('A', 'Hearts')
        self.assertEqual(self.test_card, other_card)

    def test_from_short_ascii_suits(self):
        self.assertEqual(Card.from_short('As'), Card('A', 'Spades'))
        self.assertEqual(Card.from_short('Td'), Card('10', 'Diamonds'))
        self.assertEqual(Card.from_short('10h'), Card('10', 'Hearts'))
        self.assertEqual(Card.from_short('2c'), Card('2', 'Clubs'))

    def test_from_short_unicode_suits(self):
        # str(Card) emits Unicode-suited shorthand; from_short must round-trip it
        # so that hand-recorder strings parse back to Card objects.
        for card in [
            Card('A', 'Spades'),
            Card('10', 'Diamonds'),
            Card('K', 'Hearts'),
            Card('2', 'Clubs'),
        ]:
            self.assertEqual(Card.from_short(str(card)), card)


if __name__ == "__main__":
    unittest.main()
