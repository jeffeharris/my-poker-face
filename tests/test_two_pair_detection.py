"""
Tests for T1-04: _check_two_pair and _check_one_pair should use count == 2, not >= 2.

Verifies that hands with trips are not falsely detected as two-pair or one-pair
when the check methods are called directly.
"""

from poker.hand_evaluator import HandEvaluator
from core.card import Card


def make_cards(specs):
    """Create cards from a list of (rank, suit) tuples."""
    return [Card(rank, suit) for rank, suit in specs]


class TestTwoPairDoesNotMatchTrips:
    """_check_two_pair should only match exactly count == 2, not trips."""

    def test_trips_not_detected_as_two_pair(self):
        """A hand with three-of-a-kind (no second pair) should NOT be two-pair."""
        # Three Kings + unrelated cards (no pair among the rest)
        cards = make_cards([
            ('K', 'Hearts'), ('K', 'Diamonds'), ('K', 'Clubs'),
            ('7', 'Spades'), ('3', 'Hearts'),
        ])
        evaluator = HandEvaluator(cards)
        result = evaluator._check_two_pair()
        assert result[0] is False, "Trips should not be detected as two-pair"

    def test_trips_plus_pair_not_detected_as_two_pair(self):
        """A hand with trips + a pair (full house material) should NOT have trips counted as a pair."""
        # Three Kings + pair of 5s — the trips should not count as a pair
        cards = make_cards([
            ('K', 'Hearts'), ('K', 'Diamonds'), ('K', 'Clubs'),
            ('5', 'Spades'), ('5', 'Hearts'),
        ])
        evaluator = HandEvaluator(cards)
        result = evaluator._check_two_pair()
        # Only the 5s are a pair (count == 2). Kings have count == 3.
        # So there's only one pair, not two — should return False.
        assert result[0] is False, "Trips + one pair should not be detected as two-pair"

    def test_normal_two_pair_still_works(self):
        """Standard two-pair hand should still be detected."""
        cards = make_cards([
            ('K', 'Hearts'), ('K', 'Diamonds'),
            ('7', 'Clubs'), ('7', 'Spades'),
            ('3', 'Hearts'),
        ])
        evaluator = HandEvaluator(cards)
        result = evaluator._check_two_pair()
        assert result[0] is True, "Normal two-pair should be detected"


class TestOnePairDoesNotMatchTrips:
    """_check_one_pair should only match exactly count == 2, not trips."""

    def test_trips_not_detected_as_one_pair(self):
        """A hand with three-of-a-kind should NOT be detected as one-pair."""
        cards = make_cards([
            ('K', 'Hearts'), ('K', 'Diamonds'), ('K', 'Clubs'),
            ('7', 'Spades'), ('3', 'Hearts'),
        ])
        evaluator = HandEvaluator(cards)
        result = evaluator._check_one_pair()
        assert result[0] is False, "Trips should not be detected as one-pair"

    def test_normal_one_pair_still_works(self):
        """Standard one-pair hand should still be detected."""
        cards = make_cards([
            ('K', 'Hearts'), ('K', 'Diamonds'),
            ('7', 'Clubs'), ('5', 'Spades'),
            ('3', 'Hearts'),
        ])
        evaluator = HandEvaluator(cards)
        result = evaluator._check_one_pair()
        assert result[0] is True, "Normal one-pair should be detected"


class TestEvaluateHandStillCorrect:
    """Full evaluate_hand flow should still correctly identify hand types."""

    def test_trips_evaluated_as_three_of_a_kind(self):
        """Three-of-a-kind should be ranked correctly via evaluate_hand."""
        cards = make_cards([
            ('K', 'Hearts'), ('K', 'Diamonds'), ('K', 'Clubs'),
            ('7', 'Spades'), ('3', 'Hearts'),
        ])
        result = HandEvaluator(cards).evaluate_hand()
        assert result['hand_rank'] == 7, "Trips should be hand_rank 7 (three of a kind)"
        assert 'Three of a kind' in result['hand_name']

    def test_two_pair_evaluated_correctly(self):
        """Two-pair should be ranked correctly via evaluate_hand."""
        cards = make_cards([
            ('K', 'Hearts'), ('K', 'Diamonds'),
            ('7', 'Clubs'), ('7', 'Spades'),
            ('3', 'Hearts'),
        ])
        result = HandEvaluator(cards).evaluate_hand()
        assert result['hand_rank'] == 8, "Two-pair should be hand_rank 8"
        assert 'Two Pair' in result['hand_name']

    def test_one_pair_evaluated_correctly(self):
        """One-pair should be ranked correctly via evaluate_hand."""
        cards = make_cards([
            ('K', 'Hearts'), ('K', 'Diamonds'),
            ('7', 'Clubs'), ('5', 'Spades'),
            ('3', 'Hearts'),
        ])
        result = HandEvaluator(cards).evaluate_hand()
        assert result['hand_rank'] == 9, "One-pair should be hand_rank 9"
        assert 'One Pair' in result['hand_name']

    def test_full_house_not_affected(self):
        """Full house (trips + pair) should still evaluate correctly."""
        cards = make_cards([
            ('K', 'Hearts'), ('K', 'Diamonds'), ('K', 'Clubs'),
            ('5', 'Spades'), ('5', 'Hearts'),
        ])
        result = HandEvaluator(cards).evaluate_hand()
        assert result['hand_rank'] == 4, "Full house should be hand_rank 4"
        assert 'Full House' in result['hand_name']
