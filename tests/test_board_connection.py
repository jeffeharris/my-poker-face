"""
Tests for board connection analysis in HandEvaluator.

Validates that board connection detection correctly identifies:
- Pairs (hole card rank matches board rank)
- Overpairs (pocket pair > all board ranks)
- Underpairs (pocket pair < max board rank)
- Flush draws (suited hole cards + 2 of same suit on board)
- Straight draws (OESD or gutshot)
- Weight assignment (2.0 for pair/overpair, 1.5 for draws/underpair, 0.5 for air)
"""

import unittest
from poker.hand_evaluator import HandEvaluator, _has_straight_draw


class TestHasStraightDraw(unittest.TestCase):
    """Test the _has_straight_draw helper function (detects OESD and gutshots)."""

    def test_oesd_four_consecutive(self):
        """4-5-6-7 should be detected as straight draw (OESD)."""
        self.assertTrue(_has_straight_draw([4, 5, 6, 7]))

    def test_oesd_in_larger_set(self):
        """5-6-7-8 in a larger set should be detected."""
        self.assertTrue(_has_straight_draw([2, 5, 6, 7, 8]))

    def test_gutshot_one_gap(self):
        """7-9-10-11 should be detected as gutshot (needs 8)."""
        self.assertTrue(_has_straight_draw([7, 9, 10, 11]))

    def test_gutshot_broadway(self):
        """11-12-13-14 with gap at 10 should be detected (J-Q-K-A needs T)."""
        # Actually A-K-Q-J is consecutive [11,12,13,14] - that's OESD
        # Let me use a real gutshot: A-K-Q-T (needs J)
        self.assertTrue(_has_straight_draw([10, 12, 13, 14]))  # T-Q-K-A needs J

    def test_wheel_draw_oesd(self):
        """A-2-3-4 should be detected as wheel draw."""
        self.assertTrue(_has_straight_draw([2, 3, 4, 14]))  # Ace is 14

    def test_wheel_draw_gutshot(self):
        """A-2-4-5 should be detected as wheel gutshot (needs 3)."""
        self.assertTrue(_has_straight_draw([2, 4, 5, 14]))

    def test_no_draw_wide_gaps(self):
        """2-4-6-8 has no 4 ranks in any 5-rank window."""
        self.assertFalse(_has_straight_draw([2, 4, 6, 8]))

    def test_no_draw_three_only(self):
        """Only 3 cards cannot be a draw."""
        self.assertFalse(_has_straight_draw([5, 6, 7]))

    def test_no_draw_too_few_cards(self):
        """Fewer than 4 cards cannot have 4 to a straight."""
        self.assertFalse(_has_straight_draw([5, 6, 7]))
        self.assertFalse(_has_straight_draw([5, 6]))
        self.assertFalse(_has_straight_draw([5]))
        self.assertFalse(_has_straight_draw([]))


class TestGetBoardConnectionPair(unittest.TestCase):
    """Test pair detection in board connection."""

    def test_top_pair(self):
        """Ace on an Ace-high board should be detected as pair."""
        result = HandEvaluator.get_board_connection(['Ah', 'Kd'], ['As', '7h', '2c'])
        self.assertTrue(result['has_pair'])
        self.assertTrue(result['connects'])
        self.assertEqual(result['weight'], 2.0)

    def test_middle_pair(self):
        """7 on a board with 7 should be detected as pair."""
        result = HandEvaluator.get_board_connection(['7h', 'Kd'], ['Qs', '7c', '2h'])
        self.assertTrue(result['has_pair'])
        self.assertTrue(result['connects'])
        self.assertEqual(result['weight'], 2.0)

    def test_bottom_pair(self):
        """2 on a board with 2 should be detected as pair."""
        result = HandEvaluator.get_board_connection(['2h', 'Kd'], ['Qs', '7h', '2c'])
        self.assertTrue(result['has_pair'])
        self.assertTrue(result['connects'])
        self.assertEqual(result['weight'], 2.0)

    def test_no_pair(self):
        """AK on a low board should not be a pair."""
        result = HandEvaluator.get_board_connection(['Ah', 'Kd'], ['7s', '5h', '2c'])
        self.assertFalse(result['has_pair'])


class TestGetBoardConnectionOverpair(unittest.TestCase):
    """Test overpair detection in board connection."""

    def test_overpair_queens(self):
        """QQ on J-high board should be overpair."""
        result = HandEvaluator.get_board_connection(['Qh', 'Qd'], ['Js', '7h', '2c'])
        self.assertTrue(result['has_overpair'])
        self.assertTrue(result['connects'])
        self.assertEqual(result['weight'], 2.0)

    def test_overpair_aces(self):
        """AA on K-high board should be overpair."""
        result = HandEvaluator.get_board_connection(['Ah', 'Ad'], ['Ks', '7h', '2c'])
        self.assertTrue(result['has_overpair'])
        self.assertTrue(result['connects'])
        self.assertEqual(result['weight'], 2.0)

    def test_not_overpair_underpair(self):
        """TT on A-high board should not be overpair (it's underpair)."""
        result = HandEvaluator.get_board_connection(['Th', 'Td'], ['As', '7h', '2c'])
        self.assertFalse(result['has_overpair'])
        self.assertTrue(result['has_underpair'])

    def test_not_overpair_non_pair(self):
        """AK (not a pair) cannot be overpair."""
        result = HandEvaluator.get_board_connection(['Ah', 'Kd'], ['7s', '5h', '2c'])
        self.assertFalse(result['has_overpair'])

    def test_pair_not_overpair(self):
        """QQ hitting a Q on board is pair, not overpair."""
        result = HandEvaluator.get_board_connection(['Qh', 'Qd'], ['Qs', '7h', '2c'])
        self.assertTrue(result['has_pair'])
        self.assertFalse(result['has_overpair'])


class TestGetBoardConnectionUnderpair(unittest.TestCase):
    """Test underpair detection in board connection."""

    def test_underpair_deuces(self):
        """22 on AKQ board should be underpair."""
        result = HandEvaluator.get_board_connection(['2h', '2d'], ['As', 'Kh', 'Qc'])
        self.assertTrue(result['has_underpair'])
        self.assertTrue(result['connects'])
        self.assertEqual(result['weight'], 1.5)

    def test_underpair_tens(self):
        """TT on A-high board should be underpair."""
        result = HandEvaluator.get_board_connection(['Th', 'Td'], ['As', '7h', '2c'])
        self.assertTrue(result['has_underpair'])
        self.assertTrue(result['connects'])
        self.assertEqual(result['weight'], 1.5)

    def test_underpair_jacks(self):
        """JJ on AKQ board should be underpair."""
        result = HandEvaluator.get_board_connection(['Jh', 'Jd'], ['As', 'Kh', 'Qc'])
        self.assertTrue(result['has_underpair'])
        self.assertTrue(result['connects'])
        self.assertEqual(result['weight'], 1.5)

    def test_set_not_underpair(self):
        """77 on 7-high board is a set (pair with board), not underpair."""
        result = HandEvaluator.get_board_connection(['7h', '7d'], ['7s', '5h', '2c'])
        self.assertTrue(result['has_pair'])
        self.assertFalse(result['has_underpair'])
        self.assertEqual(result['weight'], 2.0)

    def test_non_pair_not_underpair(self):
        """AK (not a pair) cannot be underpair."""
        result = HandEvaluator.get_board_connection(['Ah', 'Kd'], ['Qs', '7h', '2c'])
        self.assertFalse(result['has_underpair'])


class TestGetBoardConnectionFlushDraw(unittest.TestCase):
    """Test flush draw detection in board connection."""

    def test_flush_draw_two_hearts(self):
        """AKhh with 2 hearts on board should be flush draw."""
        result = HandEvaluator.get_board_connection(['Ah', 'Kh'], ['Qh', '7h', '2c'])
        self.assertTrue(result['has_flush_draw'])
        self.assertTrue(result['connects'])
        self.assertGreaterEqual(result['weight'], 1.5)

    def test_flush_draw_exact_two(self):
        """Suited hand with exactly 2 of suit on board."""
        result = HandEvaluator.get_board_connection(['6h', '5h'], ['Qh', '7h', '2c'])
        self.assertTrue(result['has_flush_draw'])
        self.assertTrue(result['connects'])
        self.assertEqual(result['weight'], 1.5)

    def test_no_flush_draw_offsuit(self):
        """Offsuit hand cannot have flush draw."""
        result = HandEvaluator.get_board_connection(['Ah', 'Kd'], ['Qh', '7h', '2c'])
        self.assertFalse(result['has_flush_draw'])

    def test_no_flush_draw_one_on_board(self):
        """Suited hand with only 1 of suit on board is not a flush draw."""
        result = HandEvaluator.get_board_connection(['Ah', 'Kh'], ['Qh', '7d', '2c'])
        self.assertFalse(result['has_flush_draw'])


class TestGetBoardConnectionStraightDraw(unittest.TestCase):
    """Test straight draw detection in board connection (OESD and gutshots)."""

    def test_oesd(self):
        """9-8 on 7-6-2 board should be OESD."""
        result = HandEvaluator.get_board_connection(['9h', '8d'], ['7s', '6h', '2c'])
        self.assertTrue(result['has_straight_draw'])
        self.assertTrue(result['connects'])
        self.assertEqual(result['weight'], 1.5)

    def test_gutshot_middle(self):
        """J-T on 9-7-2 board should be gutshot (needs 8)."""
        result = HandEvaluator.get_board_connection(['Jh', 'Td'], ['9s', '7h', '2c'])
        # J=11, T=10, 9=9, 7=7 -> window 7-11 has 4 ranks (7,9,10,11)
        self.assertTrue(result['has_straight_draw'])
        self.assertTrue(result['connects'])
        self.assertEqual(result['weight'], 1.5)

    def test_gutshot_broadway(self):
        """K-Q on A-J-2 board should be gutshot (needs T)."""
        result = HandEvaluator.get_board_connection(['Kh', 'Qd'], ['As', 'Jh', '2c'])
        # A=14, K=13, Q=12, J=11 - that's 4 consecutive, actually OESD
        self.assertTrue(result['has_straight_draw'])
        self.assertTrue(result['connects'])
        self.assertEqual(result['weight'], 1.5)

    def test_gutshot_low(self):
        """8-6 on 9-7-2 board should be gutshot (needs 5 or T)."""
        result = HandEvaluator.get_board_connection(['8h', '6d'], ['9s', '7h', '2c'])
        # 9, 8, 7, 6 - that's 4 consecutive in window 6-10!
        self.assertTrue(result['has_straight_draw'])
        self.assertTrue(result['connects'])
        self.assertEqual(result['weight'], 1.5)

    def test_wheel_draw(self):
        """A-2 on 3-4-K board should be wheel draw."""
        result = HandEvaluator.get_board_connection(['Ah', '2d'], ['3s', '4h', 'Kc'])
        self.assertTrue(result['has_straight_draw'])
        self.assertTrue(result['connects'])
        self.assertEqual(result['weight'], 1.5)

    def test_no_straight_draw(self):
        """AK on 7-5-2 should not have straight draw."""
        result = HandEvaluator.get_board_connection(['Ah', 'Kd'], ['7s', '5h', '2c'])
        self.assertFalse(result['has_straight_draw'])


class TestGetBoardConnectionAir(unittest.TestCase):
    """Test that non-connecting hands are correctly identified."""

    def test_complete_miss(self):
        """AK on 7-5-2 rainbow should be air."""
        result = HandEvaluator.get_board_connection(['Ah', 'Kd'], ['7s', '5h', '2c'])
        self.assertFalse(result['connects'])
        self.assertFalse(result['has_pair'])
        self.assertFalse(result['has_overpair'])
        self.assertFalse(result['has_underpair'])
        self.assertFalse(result['has_flush_draw'])
        self.assertFalse(result['has_straight_draw'])
        self.assertEqual(result['weight'], 0.5)


class TestGetBoardConnectionEdgeCases(unittest.TestCase):
    """Test edge cases for board connection."""

    def test_empty_board(self):
        """Empty board should return no connection."""
        result = HandEvaluator.get_board_connection(['Ah', 'Kd'], [])
        self.assertFalse(result['connects'])
        self.assertEqual(result['weight'], 1.0)

    def test_ten_card_format(self):
        """10 should be parsed correctly (not just T)."""
        result = HandEvaluator.get_board_connection(['10h', 'Kd'], ['10s', '7h', '2c'])
        self.assertTrue(result['has_pair'])
        self.assertEqual(result['weight'], 2.0)

    def test_multiple_connections(self):
        """Hand with both pair and flush draw should weight as pair."""
        result = HandEvaluator.get_board_connection(['Ah', 'Kh'], ['As', '7h', '2h'])
        self.assertTrue(result['has_pair'])
        self.assertTrue(result['has_flush_draw'])
        self.assertTrue(result['connects'])
        # Pair takes precedence for weight
        self.assertEqual(result['weight'], 2.0)


class TestGetBoardConnectionWeighting(unittest.TestCase):
    """Test that weights are assigned correctly."""

    def test_pair_weight(self):
        """Pairs should have weight 2.0."""
        result = HandEvaluator.get_board_connection(['Ah', 'Kd'], ['As', '7h', '2c'])
        self.assertEqual(result['weight'], 2.0)

    def test_overpair_weight(self):
        """Overpairs should have weight 2.0."""
        result = HandEvaluator.get_board_connection(['Ah', 'Ad'], ['Ks', '7h', '2c'])
        self.assertEqual(result['weight'], 2.0)

    def test_underpair_weight(self):
        """Underpairs should have weight 1.5."""
        result = HandEvaluator.get_board_connection(['2h', '2d'], ['As', 'Kh', 'Qc'])
        self.assertEqual(result['weight'], 1.5)

    def test_flush_draw_weight(self):
        """Flush draws should have weight 1.5."""
        result = HandEvaluator.get_board_connection(['6h', '5h'], ['Qh', '7h', '2c'])
        self.assertEqual(result['weight'], 1.5)

    def test_straight_draw_weight(self):
        """Straight draws should have weight 1.5."""
        result = HandEvaluator.get_board_connection(['9h', '8d'], ['7s', '6h', '2c'])
        self.assertEqual(result['weight'], 1.5)

    def test_gutshot_weight(self):
        """Gutshots should have weight 1.5."""
        result = HandEvaluator.get_board_connection(['Jh', 'Td'], ['9s', '7h', '2c'])
        self.assertEqual(result['weight'], 1.5)

    def test_air_weight(self):
        """Air should have weight 0.5."""
        result = HandEvaluator.get_board_connection(['Ah', 'Kd'], ['7s', '5h', '2c'])
        self.assertEqual(result['weight'], 0.5)


class TestPlanExamples(unittest.TestCase):
    """Test the specific examples from the plan document."""

    def test_example_pair(self):
        """['Ah', 'Kd'] + ['As', '7h', '2c'] -> has_pair=True, weight=2.0"""
        result = HandEvaluator.get_board_connection(['Ah', 'Kd'], ['As', '7h', '2c'])
        self.assertTrue(result['has_pair'])
        self.assertEqual(result['weight'], 2.0)

    def test_example_overpair(self):
        """['Qh', 'Qd'] + ['Js', '7h', '2c'] -> has_overpair=True, weight=2.0"""
        result = HandEvaluator.get_board_connection(['Qh', 'Qd'], ['Js', '7h', '2c'])
        self.assertTrue(result['has_overpair'])
        self.assertEqual(result['weight'], 2.0)

    def test_example_underpair(self):
        """['2h', '2d'] + ['As', 'Kh', 'Qc'] -> has_underpair=True, weight=1.5"""
        result = HandEvaluator.get_board_connection(['2h', '2d'], ['As', 'Kh', 'Qc'])
        self.assertTrue(result['has_underpair'])
        self.assertEqual(result['weight'], 1.5)

    def test_example_flush_draw(self):
        """['Ah', 'Kh'] + ['Qh', '7h', '2c'] -> has_flush_draw=True, weight=1.5"""
        result = HandEvaluator.get_board_connection(['Ah', 'Kh'], ['Qh', '7h', '2c'])
        self.assertTrue(result['has_flush_draw'])
        self.assertGreaterEqual(result['weight'], 1.5)

    def test_example_oesd(self):
        """['9h', '8d'] + ['7s', '6h', '2c'] -> has_straight_draw=True, weight=1.5"""
        result = HandEvaluator.get_board_connection(['9h', '8d'], ['7s', '6h', '2c'])
        self.assertTrue(result['has_straight_draw'])
        self.assertEqual(result['weight'], 1.5)

    def test_example_gutshot(self):
        """['Jh', 'Td'] + ['Qs', '9h', '2c'] -> has_straight_draw=True (gutshot), weight=1.5"""
        result = HandEvaluator.get_board_connection(['Jh', 'Td'], ['Qs', '9h', '2c'])
        # Q=12, J=11, T=10, 9=9 -> window 9-13 has 4 ranks
        self.assertTrue(result['has_straight_draw'])
        self.assertEqual(result['weight'], 1.5)

    def test_example_air(self):
        """['Ah', 'Kd'] + ['7s', '5h', '2c'] -> connects=False, weight=0.5"""
        result = HandEvaluator.get_board_connection(['Ah', 'Kd'], ['7s', '5h', '2c'])
        self.assertFalse(result['connects'])
        self.assertEqual(result['weight'], 0.5)


if __name__ == '__main__':
    unittest.main()
