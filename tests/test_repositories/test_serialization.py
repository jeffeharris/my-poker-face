"""Tests for serialization utilities."""
import os
import sys
import unittest

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from core.card import Card
from poker.poker_game import initialize_game_state
from poker.repositories.serialization import (
    serialize_card, deserialize_card,
    serialize_cards, deserialize_cards,
    restore_state_from_dict,
)


class TestCardSerialization(unittest.TestCase):
    """Test card serialization round-trips."""

    def test_serialize_card_object(self):
        card = Card('A', 'Hearts')
        result = serialize_card(card)
        self.assertEqual(result['rank'], 'A')
        self.assertEqual(result['suit'], 'Hearts')

    def test_serialize_card_dict_passthrough(self):
        card_dict = {'rank': 'K', 'suit': 'Spades'}
        result = serialize_card(card_dict)
        self.assertEqual(result, card_dict)

    def test_serialize_card_invalid_dict_raises(self):
        with self.assertRaises(ValueError):
            serialize_card({'color': 'red'})

    def test_serialize_card_invalid_type_raises(self):
        with self.assertRaises(ValueError):
            serialize_card(42)

    def test_deserialize_card_from_dict(self):
        card = deserialize_card({'rank': 'Q', 'suit': 'Diamonds'})
        self.assertIsInstance(card, Card)
        self.assertEqual(card.rank, 'Q')
        self.assertEqual(card.suit, 'Diamonds')

    def test_deserialize_card_already_card(self):
        original = Card('J', 'Clubs')
        result = deserialize_card(original)
        self.assertIs(result, original)

    def test_deserialize_card_invalid_raises(self):
        with self.assertRaises(ValueError):
            deserialize_card("not a card")

    def test_round_trip_single_card(self):
        original = Card('10', 'Hearts')
        serialized = serialize_card(original)
        restored = deserialize_card(serialized)
        self.assertEqual(restored.rank, original.rank)
        self.assertEqual(restored.suit, original.suit)

    def test_serialize_cards_empty(self):
        self.assertEqual(serialize_cards([]), [])
        self.assertEqual(serialize_cards(None), [])

    def test_deserialize_cards_empty(self):
        self.assertEqual(deserialize_cards([]), tuple())
        self.assertEqual(deserialize_cards(None), tuple())

    def test_round_trip_card_collection(self):
        originals = [Card('A', 'Spades'), Card('K', 'Hearts'), Card('Q', 'Diamonds')]
        serialized = serialize_cards(originals)
        restored = deserialize_cards(serialized)
        self.assertEqual(len(restored), 3)
        for orig, rest in zip(originals, restored):
            self.assertEqual(orig.rank, rest.rank)
            self.assertEqual(orig.suit, rest.suit)


class TestGameStateSerialization(unittest.TestCase):
    """Test game state serialization round-trips."""

    def test_prepare_state_returns_dict(self):
        game_state = initialize_game_state(player_names=['P1', 'P2'])
        result = game_state.to_dict()
        self.assertIsInstance(result, dict)
        self.assertIn('players', result)
        self.assertIn('pot', result)

    def test_round_trip_game_state(self):
        original = initialize_game_state(player_names=['Alice', 'Bob', 'Charlie'])
        state_dict = original.to_dict()
        restored = restore_state_from_dict(state_dict)

        self.assertEqual(len(restored.players), len(original.players))
        for orig_p, rest_p in zip(original.players, restored.players):
            self.assertEqual(orig_p.name, rest_p.name)
            self.assertEqual(orig_p.stack, rest_p.stack)
            self.assertEqual(orig_p.is_human, rest_p.is_human)

        self.assertEqual(restored.pot['total'], original.pot['total'])
        self.assertEqual(restored.current_dealer_idx, original.current_dealer_idx)
        self.assertEqual(restored.current_player_idx, original.current_player_idx)

    def test_restore_handles_missing_optional_fields(self):
        """State dicts from older versions may lack run_it_out."""
        game_state = initialize_game_state(player_names=['P1', 'P2'])
        state_dict = game_state.to_dict()
        # Remove optional field
        state_dict.pop('run_it_out', None)
        restored = restore_state_from_dict(state_dict)
        self.assertFalse(restored.run_it_out)

    def test_restore_handles_empty_hand(self):
        game_state = initialize_game_state(player_names=['P1', 'P2'])
        state_dict = game_state.to_dict()
        # Ensure hand is None/empty
        for p in state_dict['players']:
            p['hand'] = None
        restored = restore_state_from_dict(state_dict)
        for p in restored.players:
            self.assertIsNone(p.hand)


if __name__ == '__main__':
    unittest.main()
