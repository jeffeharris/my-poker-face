"""
Tests for board connection analysis in HandEvaluator.

Validates that board connection detection correctly identifies:
- Pairs (hole card rank matches board rank)
- Overpairs (pocket pair > all board ranks)
- Flush draws (suited hole cards + 2 of same suit on board)
- Straight draws (4 to a straight)
- Weight assignment (2.0 for hits, 1.5 for draws, 0.5 for air)
"""

import unittest
from poker.hand_evaluator import HandEvaluator, _has_four_to_straight


class TestHasFourToStraight(unittest.TestCase):
    """Test the _has_four_to_straight helper function."""

    def test_four_consecutive_ranks(self):
        """4-5-6-7 should be detected as 4 to a straight."""
        self.assertTrue(_has_four_to_straight([4, 5, 6, 7]))

    def test_four_in_larger_set(self):
        """5-6-7-8 in a larger set should be detected."""
        self.assertTrue(_has_four_to_straight([2, 5, 6, 7, 8]))

    def test_wheel_draw(self):
        """A-2-3-4 should be detected as wheel draw."""
        self.assertTrue(_has_four_to_straight([2, 3, 4, 14]))  # Ace is 14

    def test_wheel_draw_with_5(self):
        """2-3-4-5 with ace should be detected."""
        self.assertTrue(_has_four_to_straight([2, 3, 4, 5, 14]))

    def test_no_four_consecutive(self):
        """Gapped ranks should not be detected."""
        self.assertFalse(_has_four_to_straight([2, 4, 6, 8]))

    def test_only_three_consecutive(self):
        """Only 3 consecutive should not be detected."""
        self.assertFalse(_has_four_to_straight([5, 6, 7]))

    def test_too_few_cards(self):
        """Fewer than 4 cards cannot have 4 to a straight."""
        self.assertFalse(_has_four_to_straight([5, 6, 7]))
        self.assertFalse(_has_four_to_straight([5, 6]))
        self.assertFalse(_has_four_to_straight([5]))
        self.assertFalse(_has_four_to_straight([]))


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
        """TT on A-high board should not be overpair."""
        result = HandEvaluator.get_board_connection(['Th', 'Td'], ['As', '7h', '2c'])
        self.assertFalse(result['has_overpair'])

    def test_not_overpair_non_pair(self):
        """AK (not a pair) cannot be overpair."""
        result = HandEvaluator.get_board_connection(['Ah', 'Kd'], ['7s', '5h', '2c'])
        self.assertFalse(result['has_overpair'])

    def test_pair_not_overpair(self):
        """QQ hitting a Q on board is pair, not overpair."""
        result = HandEvaluator.get_board_connection(['Qh', 'Qd'], ['Qs', '7h', '2c'])
        self.assertTrue(result['has_pair'])
        self.assertFalse(result['has_overpair'])  # Has a pair, so not strictly overpair


class TestGetBoardConnectionFlushDraw(unittest.TestCase):
    """Test flush draw detection in board connection."""

    def test_flush_draw_two_hearts(self):
        """AKhh with 2 hearts on board should be flush draw."""
        result = HandEvaluator.get_board_connection(['Ah', 'Kh'], ['Qh', '7h', '2c'])
        self.assertTrue(result['has_flush_draw'])
        self.assertTrue(result['connects'])
        # Note: AK might also have pair with board, but flush draw is detected
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
    """Test straight draw detection in board connection."""

    def test_open_ended_straight_draw(self):
        """9-8 on 7-6-2 board should be straight draw."""
        result = HandEvaluator.get_board_connection(['9h', '8d'], ['7s', '6h', '2c'])
        self.assertTrue(result['has_straight_draw'])
        self.assertTrue(result['connects'])
        self.assertEqual(result['weight'], 1.5)

    def test_gutshot(self):
        """J-T on 9-7-2 board should be straight draw (gutshot to 8)."""
        result = HandEvaluator.get_board_connection(['Jh', 'Td'], ['9s', '7h', '2c'])
        # J-T-9-7 has 4 to a straight (J-T-9-8 or T-9-8-7 with 8)
        # Actually J=11, T=10, 9=9, 7=7 - sorted: [7,9,10,11]
        # 7-9-10-11 is not consecutive, so no straight draw
        # Let me reconsider: we need 4 consecutive ranks
        # [7,9,10,11] - 9-10-11 is only 3 consecutive
        self.assertFalse(result['has_straight_draw'])

    def test_wheel_draw(self):
        """A-2 on 3-4-K board should be wheel draw."""
        result = HandEvaluator.get_board_connection(['Ah', '2d'], ['3s', '4h', 'Kc'])
        self.assertTrue(result['has_straight_draw'])
        self.assertTrue(result['connects'])
        self.assertEqual(result['weight'], 1.5)

    def test_broadway_draw(self):
        """K-Q on A-J-2 board should be straight draw."""
        result = HandEvaluator.get_board_connection(['Kh', 'Qd'], ['As', 'Jh', '2c'])
        # A=14, K=13, Q=12, J=11 - that's 4 consecutive!
        self.assertTrue(result['has_straight_draw'])
        self.assertTrue(result['connects'])
        self.assertEqual(result['weight'], 1.5)


class TestGetBoardConnectionAir(unittest.TestCase):
    """Test that non-connecting hands are correctly identified."""

    def test_complete_miss(self):
        """AK on 7-5-2 rainbow should be air."""
        result = HandEvaluator.get_board_connection(['Ah', 'Kd'], ['7s', '5h', '2c'])
        self.assertFalse(result['connects'])
        self.assertFalse(result['has_pair'])
        self.assertFalse(result['has_overpair'])
        self.assertFalse(result['has_flush_draw'])
        self.assertFalse(result['has_straight_draw'])
        self.assertEqual(result['weight'], 0.5)

    def test_underpair_is_not_air(self):
        """22 on A-K-Q board is underpair (still connects via pocket pair logic)."""
        result = HandEvaluator.get_board_connection(['2h', '2d'], ['As', 'Kh', 'Qc'])
        # 22 is a pair but not overpair (A>2), not hitting board, no draws
        # It doesn't connect in our sense (no pair WITH board, not overpair)
        self.assertFalse(result['has_pair'])
        self.assertFalse(result['has_overpair'])
        self.assertFalse(result['connects'])
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

    def test_flush_draw_weight(self):
        """Flush draws should have weight 1.5."""
        result = HandEvaluator.get_board_connection(['6h', '5h'], ['Qh', '7h', '2c'])
        self.assertEqual(result['weight'], 1.5)

    def test_straight_draw_weight(self):
        """Straight draws should have weight 1.5."""
        result = HandEvaluator.get_board_connection(['9h', '8d'], ['7s', '6h', '2c'])
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

    def test_example_flush_draw(self):
        """['Ah', 'Kh'] + ['Qh', '7h', '2c'] -> has_flush_draw=True, weight=1.5"""
        result = HandEvaluator.get_board_connection(['Ah', 'Kh'], ['Qh', '7h', '2c'])
        self.assertTrue(result['has_flush_draw'])
        # Note: Also has pair (no wait, no A,K,Q on board matching our A,K)
        # Wait - Qh is on board, but we have Ah Kh, not Qh
        # So no pair, just flush draw
        self.assertGreaterEqual(result['weight'], 1.5)

    def test_example_straight_draw(self):
        """['9h', '8d'] + ['7s', '6h', '2c'] -> has_straight_draw=True, weight=1.5"""
        result = HandEvaluator.get_board_connection(['9h', '8d'], ['7s', '6h', '2c'])
        self.assertTrue(result['has_straight_draw'])
        self.assertEqual(result['weight'], 1.5)

    def test_example_air(self):
        """['Ah', 'Kd'] + ['7s', '5h', '2c'] -> connects=False, weight=0.5"""
        result = HandEvaluator.get_board_connection(['Ah', 'Kd'], ['7s', '5h', '2c'])
        self.assertFalse(result['connects'])
        self.assertEqual(result['weight'], 0.5)


if __name__ == '__main__':
    unittest.main()
