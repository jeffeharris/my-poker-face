"""Tests for hand classification (made tier + draw modifier)."""

import pytest

from poker.strategy.hand_classification import (
    classify_hand,
    simplify_hand_class,
)


# ---------------------------------------------------------------------------
# Made-tier tests
# ---------------------------------------------------------------------------

class TestMadeTierClassification:
    """Test made-hand tier assignment."""

    def test_flush_is_nuts(self):
        made, _ = classify_hand(['Ah', 'Kh'], ['Qh', 'Jh', '2h'])
        assert made == 'nuts'

    def test_straight_is_nuts(self):
        made, _ = classify_hand(['9h', '8d'], ['7s', '6c', '5h'])
        assert made == 'nuts'

    def test_set_is_nuts(self):
        # Pocket pair + board match = set
        made, _ = classify_hand(['7h', '7d'], ['7s', '4c', '2h'])
        assert made == 'nuts'

    def test_trips_is_strong_made(self):
        # One hole card + board pair = trips (not a set)
        made, _ = classify_hand(['Ah', '7d'], ['7s', '7c', '2h'])
        assert made == 'strong_made'

    def test_two_pair_is_strong_made(self):
        made, _ = classify_hand(['Kh', 'Jd'], ['Ks', 'Jc', '5h'])
        assert made == 'strong_made'

    def test_overpair_is_strong_made(self):
        made, _ = classify_hand(['Qh', 'Qd'], ['Js', '8c', '3h'])
        assert made == 'strong_made'

    def test_tptk_is_strong_made(self):
        # Top pair (K matches board K) + A kicker
        made, _ = classify_hand(['Ah', 'Kd'], ['Ks', '7c', '2h'])
        assert made == 'strong_made'

    def test_top_pair_weak_kicker_is_medium_made(self):
        # Top pair (K matches board K) + 9 kicker (not A/K)
        made, _ = classify_hand(['Kh', '9d'], ['Ks', '7c', '2h'])
        assert made == 'medium_made'

    def test_second_pair_dry_board_is_medium_made(self):
        # 8 matches second-highest board rank (K > 8 > 2), dry/rainbow board
        made, _ = classify_hand(['8h', '7d'], ['Ks', '8c', '2h'])
        assert made == 'medium_made'

    def test_weak_made_bottom_pair(self):
        # 5 matches lowest board rank
        made, _ = classify_hand(['5h', '4d'], ['Ks', '8c', '5s'])
        assert made == 'weak_made'

    def test_air_no_pair(self):
        made, _ = classify_hand(['Ah', 'Qd'], ['Ks', '8c', '3h'])
        assert made == 'air'

    def test_full_house_is_nuts(self):
        made, _ = classify_hand(['Ah', 'Ad'], ['As', 'Kc', 'Kh'])
        assert made == 'nuts'

    def test_four_of_a_kind_is_nuts(self):
        made, _ = classify_hand(['Ah', 'Ad'], ['As', 'Ac', '2h'])
        assert made == 'nuts'


# ---------------------------------------------------------------------------
# Draw-modifier tests
# ---------------------------------------------------------------------------

class TestDrawModifierClassification:
    """Test draw modifier assignment."""

    def test_flush_draw_is_strong_draw(self):
        # 4 hearts: Ah, 5h, Kh, 7h
        _, draw = classify_hand(['Ah', '5h'], ['Kh', '7h', '2s'])
        assert draw == 'strong_draw'

    def test_oesd_is_strong_draw(self):
        # J-T-9-8 are 4 consecutive ranks
        _, draw = classify_hand(['Jh', 'Td'], ['9s', '8c', '2h'])
        assert draw == 'strong_draw'

    def test_gutshot_is_weak_draw(self):
        # J-T-8-7: needs 9, 4-in-5-window but not 4 consecutive → gutshot
        _, draw = classify_hand(['Jh', 'Td'], ['8s', '7c', '2h'])
        assert draw == 'weak_draw'

    def test_backdoor_flush(self):
        # Only 2 hearts in hole + 1 on board = 3 total (not 4)
        # But we need to make sure no straight draw overrides
        _, draw = classify_hand(['Ah', '5h'], ['Kh', '7s', '2d'])
        assert draw == 'backdoor'

    def test_no_draw(self):
        _, draw = classify_hand(['Ah', 'Kd'], ['Qs', '7c', '2h'])
        assert draw == 'no_draw'

    def test_made_flush_has_no_draw(self):
        # Already made a flush (5 hearts) → no_draw
        _, draw = classify_hand(['Ah', 'Kh'], ['Qh', 'Jh', '2h'])
        assert draw == 'no_draw'

    def test_made_straight_has_no_draw(self):
        _, draw = classify_hand(['9h', '8d'], ['7s', '6c', '5h'])
        assert draw == 'no_draw'


# ---------------------------------------------------------------------------
# simplify_hand_class tests
# ---------------------------------------------------------------------------

class TestSimplifyHandClass:
    """Test the simplified 6-class mapping."""

    def test_nuts_any_draw(self):
        assert simplify_hand_class('nuts', 'strong_draw') == 'nuts'
        assert simplify_hand_class('nuts', 'no_draw') == 'nuts'

    def test_strong_made_strong_draw_promotes_to_nuts(self):
        assert simplify_hand_class('strong_made', 'strong_draw') == 'nuts'

    def test_strong_made_other(self):
        assert simplify_hand_class('strong_made', 'no_draw') == 'strong_made'
        assert simplify_hand_class('strong_made', 'weak_draw') == 'strong_made'

    def test_medium_made_strong_draw_promotes(self):
        assert simplify_hand_class('medium_made', 'strong_draw') == 'strong_made'

    def test_medium_made_other(self):
        assert simplify_hand_class('medium_made', 'no_draw') == 'medium_made'
        assert simplify_hand_class('medium_made', 'backdoor') == 'medium_made'

    def test_weak_made_strong_draw_promotes(self):
        assert simplify_hand_class('weak_made', 'strong_draw') == 'medium_made'

    def test_weak_made_other(self):
        assert simplify_hand_class('weak_made', 'no_draw') == 'weak_made'
        assert simplify_hand_class('weak_made', 'weak_draw') == 'weak_made'

    def test_air_strong_draw(self):
        assert simplify_hand_class('air', 'strong_draw') == 'air_strong_draw'

    def test_air_no_draw(self):
        assert simplify_hand_class('air', 'no_draw') == 'air_no_draw'
        assert simplify_hand_class('air', 'weak_draw') == 'air_no_draw'
        assert simplify_hand_class('air', 'backdoor') == 'air_no_draw'


# ---------------------------------------------------------------------------
# Integration: classify_hand → simplify_hand_class
# ---------------------------------------------------------------------------

class TestEndToEnd:
    """Full pipeline from cards to simplified class."""

    def test_set_simplifies_to_nuts(self):
        made, draw = classify_hand(['7h', '7d'], ['7s', '4c', '2h'])
        assert simplify_hand_class(made, draw) == 'nuts'

    def test_tptk_simplifies_to_strong_made(self):
        made, draw = classify_hand(['Ah', 'Kd'], ['Ks', '7c', '2h'])
        assert simplify_hand_class(made, draw) == 'strong_made'

    def test_air_with_flush_draw_simplifies(self):
        # Air (no pair) + flush draw → air_strong_draw
        made, draw = classify_hand(['Ah', '5h'], ['Kh', '7h', '2s'])
        # Actually Ah-5h with Kh-7h is 4 hearts → flush draw
        # But Ah is high card only (no pair) → air + strong_draw
        assert simplify_hand_class(made, draw) == 'air_strong_draw'
