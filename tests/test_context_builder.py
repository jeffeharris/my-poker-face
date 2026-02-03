"""Tests for context_builder helper functions."""

import pytest

from flask_app.services.context_builder import _has_real_draw


class TestHasRealDraw:
    """Test _has_real_draw() draw detection."""

    def test_flush_draw(self):
        """4 cards of same suit = flush draw."""
        data = {
            'hand_hole_cards': ['As', 'Ks'],
            'hand_community_cards': ['Qs', '2s', '7h'],
        }
        assert _has_real_draw(data) is True

    def test_straight_draw_open_ended(self):
        """4 consecutive ranks = straight draw."""
        data = {
            'hand_hole_cards': ['9h', 'Td'],
            'hand_community_cards': ['Jc', 'Qs', '2h'],
        }
        assert _has_real_draw(data) is True

    def test_gutshot_straight_draw(self):
        """4 ranks in 5-wide window = straight draw."""
        data = {
            'hand_hole_cards': ['9h', 'Jd'],
            'hand_community_cards': ['Qc', 'Ks', '2h'],  # 9-J-Q-K missing T
        }
        assert _has_real_draw(data) is True

    def test_ace_low_straight_draw(self):
        """A-2-3-4 wheel draw detection."""
        data = {
            'hand_hole_cards': ['Ah', '2d'],
            'hand_community_cards': ['3c', '4s', '9h'],
        }
        assert _has_real_draw(data) is True

    def test_no_draw_random_cards(self):
        """Random unconnected cards = no draw."""
        data = {
            'hand_hole_cards': ['Ah', '7d'],
            'hand_community_cards': ['2c', '9s', 'Kh'],
        }
        assert _has_real_draw(data) is False

    def test_no_draw_three_suited(self):
        """Only 3 suited cards = no flush draw."""
        data = {
            'hand_hole_cards': ['As', 'Ks'],
            'hand_community_cards': ['Qs', '2h', '7c'],  # Only 3 spades
        }
        assert _has_real_draw(data) is False

    def test_preflop_fallback_to_outs(self):
        """Pre-flop uses outs count fallback."""
        data = {
            'hand_hole_cards': ['As', 'Ks'],
            'hand_community_cards': [],
            'outs': 10,
        }
        assert _has_real_draw(data) is True

    def test_preflop_low_outs(self):
        """Pre-flop with low outs = no draw."""
        data = {
            'hand_hole_cards': ['As', 'Ks'],
            'hand_community_cards': [],
            'outs': 5,
        }
        assert _has_real_draw(data) is False

    def test_empty_data_fallback(self):
        """Missing cards uses outs fallback."""
        data = {'outs': 0}
        assert _has_real_draw(data) is False
