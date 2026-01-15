"""Tests for preflop hand classification in controllers.py"""

import unittest
from poker.controllers import (
    classify_preflop_hand,
    _get_canonical_hand,
    _get_hand_category,
    _get_hand_percentile,
    PREMIUM_HANDS,
    TOP_10_HANDS,
    TOP_20_HANDS,
    TOP_35_HANDS,
)


class TestCanonicalHand(unittest.TestCase):
    """Test conversion of hole cards to canonical notation."""

    def test_pocket_pairs(self):
        """Pocket pairs should be two-character notation."""
        self.assertEqual(_get_canonical_hand(['A♠', 'A♦']), 'AA')
        self.assertEqual(_get_canonical_hand(['K♥', 'K♣']), 'KK')
        self.assertEqual(_get_canonical_hand(['2♠', '2♥']), '22')
        self.assertEqual(_get_canonical_hand(['T♠', 'T♦']), 'TT')

    def test_suited_hands(self):
        """Suited hands should end with 's'."""
        self.assertEqual(_get_canonical_hand(['A♠', 'K♠']), 'AKs')
        self.assertEqual(_get_canonical_hand(['Q♥', 'J♥']), 'QJs')
        self.assertEqual(_get_canonical_hand(['9♣', '8♣']), '98s')
        # Order shouldn't matter
        self.assertEqual(_get_canonical_hand(['K♠', 'A♠']), 'AKs')

    def test_offsuit_hands(self):
        """Offsuit hands should end with 'o'."""
        self.assertEqual(_get_canonical_hand(['A♠', 'K♦']), 'AKo')
        self.assertEqual(_get_canonical_hand(['Q♥', 'J♣']), 'QJo')
        self.assertEqual(_get_canonical_hand(['7♠', '2♦']), '72o')

    def test_ten_handling(self):
        """10 should be converted to T."""
        self.assertEqual(_get_canonical_hand(['10♠', '10♦']), 'TT')
        self.assertEqual(_get_canonical_hand(['A♠', '10♠']), 'ATs')
        self.assertEqual(_get_canonical_hand(['10♥', '9♥']), 'T9s')

    def test_high_card_first(self):
        """Higher card should always come first."""
        self.assertEqual(_get_canonical_hand(['2♠', 'A♠']), 'A2s')
        self.assertEqual(_get_canonical_hand(['5♦', 'K♥']), 'K5o')
        self.assertEqual(_get_canonical_hand(['3♣', '7♣']), '73s')


class TestHandCategory(unittest.TestCase):
    """Test hand category descriptions."""

    def test_pocket_pairs(self):
        """Test pocket pair categories."""
        self.assertEqual(_get_hand_category('AA'), 'High pocket pair')
        self.assertEqual(_get_hand_category('KK'), 'High pocket pair')
        self.assertEqual(_get_hand_category('QQ'), 'High pocket pair')
        self.assertEqual(_get_hand_category('JJ'), 'High pocket pair')
        self.assertEqual(_get_hand_category('TT'), 'Medium pocket pair')
        self.assertEqual(_get_hand_category('99'), 'Medium pocket pair')
        self.assertEqual(_get_hand_category('77'), 'Medium pocket pair')
        self.assertEqual(_get_hand_category('66'), 'Low pocket pair')
        self.assertEqual(_get_hand_category('22'), 'Low pocket pair')

    def test_broadway_hands(self):
        """Test broadway hand categories."""
        self.assertEqual(_get_hand_category('AKs'), 'Suited broadway')
        self.assertEqual(_get_hand_category('AKo'), 'Offsuit broadway')
        self.assertEqual(_get_hand_category('KQs'), 'Suited broadway')
        self.assertEqual(_get_hand_category('JTs'), 'Suited broadway')
        self.assertEqual(_get_hand_category('QTo'), 'Offsuit broadway')

    def test_ace_hands(self):
        """Test ace-x hand categories."""
        self.assertEqual(_get_hand_category('A5s'), 'Suited ace')
        self.assertEqual(_get_hand_category('A2s'), 'Suited ace')
        self.assertEqual(_get_hand_category('A7o'), 'Offsuit ace')

    def test_connectors(self):
        """Test connector categories."""
        self.assertEqual(_get_hand_category('98s'), 'Suited connector')
        self.assertEqual(_get_hand_category('76s'), 'Suited connector')
        self.assertEqual(_get_hand_category('54s'), 'Suited connector')
        self.assertEqual(_get_hand_category('98o'), 'Offsuit connector')

    def test_gappers(self):
        """Test suited gapper category."""
        self.assertEqual(_get_hand_category('97s'), 'Suited gapper')
        self.assertEqual(_get_hand_category('86s'), 'Suited gapper')
        self.assertEqual(_get_hand_category('T7s'), 'Suited gapper')

    def test_unconnected(self):
        """Test unconnected/trash hand categories."""
        self.assertEqual(_get_hand_category('72o'), 'Unconnected cards')
        self.assertEqual(_get_hand_category('83o'), 'Unconnected cards')
        self.assertEqual(_get_hand_category('94o'), 'Unconnected cards')


class TestHandPercentile(unittest.TestCase):
    """Test hand percentile rankings."""

    def test_premium_hands(self):
        """Premium hands should be top 3%."""
        for hand in ['AA', 'KK', 'QQ', 'JJ', 'AKs']:
            self.assertIn('Top 3%', _get_hand_percentile(hand), f"{hand} should be top 3%")

    def test_top_10_hands(self):
        """Top 10 hands should be identified."""
        for hand in ['TT', 'AKo', 'AQs', 'AJs', 'KQs']:
            result = _get_hand_percentile(hand)
            self.assertTrue(
                'Top 3%' in result or 'Top 10%' in result,
                f"{hand} should be top 10%"
            )

    def test_top_20_hands(self):
        """Top 20 hands should be identified."""
        for hand in ['99', '88', '77', 'ATs', 'KJs', 'QJs', 'JTs']:
            result = _get_hand_percentile(hand)
            self.assertTrue(
                'Top' in result and ('3%' in result or '10%' in result or '20%' in result),
                f"{hand} should be top 20%"
            )

    def test_trash_hands(self):
        """Trash hands should be bottom percentile."""
        for hand in ['72o', '83o', '42o', '32o']:
            result = _get_hand_percentile(hand)
            self.assertTrue(
                'Bottom' in result or 'Below average' in result,
                f"{hand} should be bottom/below average"
            )


class TestClassifyPreflopHand(unittest.TestCase):
    """Test the main classify_preflop_hand function."""

    def test_premium_hand_output(self):
        """Premium hands should show top percentile."""
        result = classify_preflop_hand(['A♠', 'A♦'])
        self.assertIn('AA', result)
        self.assertIn('Top 3%', result)
        self.assertIn('High pocket pair', result)

    def test_suited_broadway_output(self):
        """Suited broadway should show category and percentile."""
        result = classify_preflop_hand(['A♠', 'K♠'])
        self.assertIn('AKs', result)
        self.assertIn('Top 3%', result)
        self.assertIn('Suited broadway', result)

    def test_trash_hand_output(self):
        """Trash hands should show bottom percentile."""
        result = classify_preflop_hand(['7♠', '2♦'])
        self.assertIn('72o', result)
        self.assertIn('Bottom', result)
        self.assertIn('Unconnected', result)

    def test_suited_connector_output(self):
        """Suited connectors should be properly categorized."""
        result = classify_preflop_hand(['9♠', '8♠'])
        self.assertIn('98s', result)
        self.assertIn('Top 35%', result)

    def test_unicode_suit_handling(self):
        """Should handle unicode suit symbols correctly."""
        # Various unicode representations
        result1 = classify_preflop_hand(['A♠', 'K♠'])
        result2 = classify_preflop_hand(['A♥', 'K♥'])
        result3 = classify_preflop_hand(['A♦', 'K♦'])
        result4 = classify_preflop_hand(['A♣', 'K♣'])

        for result in [result1, result2, result3, result4]:
            self.assertIn('AKs', result)
            self.assertIn('Top 3%', result)

    def test_empty_hand_returns_none(self):
        """Empty or invalid hands should return None."""
        self.assertIsNone(classify_preflop_hand([]))
        self.assertIsNone(classify_preflop_hand(['A♠']))

    def test_output_format(self):
        """Output should follow expected format: canonical - category, percentile."""
        result = classify_preflop_hand(['Q♦', 'Q♥'])
        # Should be: "QQ - High pocket pair, Top 3% of starting hands"
        self.assertRegex(result, r'^[A-Z0-9]{2,3} - .+, .+ starting hand')


class TestHandRangeSets(unittest.TestCase):
    """Test that hand range sets are properly defined."""

    def test_premium_subset_of_top10(self):
        """Premium hands should be subset of top 10."""
        self.assertTrue(PREMIUM_HANDS.issubset(TOP_10_HANDS))

    def test_top10_subset_of_top20(self):
        """Top 10 hands should be subset of top 20."""
        self.assertTrue(TOP_10_HANDS.issubset(TOP_20_HANDS))

    def test_top20_subset_of_top35(self):
        """Top 20 hands should be subset of top 35."""
        self.assertTrue(TOP_20_HANDS.issubset(TOP_35_HANDS))

    def test_premium_hands_count(self):
        """Premium hands should be roughly top 3% (5-6 hands)."""
        self.assertGreaterEqual(len(PREMIUM_HANDS), 4)
        self.assertLessEqual(len(PREMIUM_HANDS), 8)

    def test_expected_premium_hands(self):
        """Verify expected premium hands are included."""
        expected = {'AA', 'KK', 'QQ', 'AKs'}
        self.assertTrue(expected.issubset(PREMIUM_HANDS))


if __name__ == '__main__':
    unittest.main()
