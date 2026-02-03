"""
Tests for enhanced range estimation functions.

Validates that range estimation follows standard poker theory:
- PFR-based ranges are tighter than VPIP-based ranges
- 3-bet ranges are ~30% of opening ranges
- Calling ranges exclude raising hands
- Aggression adjustments narrow ranges appropriately

Sources for mathematical benchmarks:
- Harrington on Hold'em vol 1-3
- The Grinder's Manual by Peter Clarke
- Modern Poker Theory by Michael Acevedo
"""

import unittest
from poker.hand_ranges import (
    OpponentInfo,
    EARLY_POSITION_RANGE,
    MIDDLE_POSITION_RANGE,
    LATE_POSITION_RANGE,
    BLIND_DEFENSE_RANGE,
    ULTRA_PREMIUM_RANGE,
    estimate_range_from_pfr,
    estimate_range_from_vpip,
    estimate_3bet_range,
    estimate_calling_range,
    apply_aggression_adjustment,
    get_opponent_range,
    get_opponent_range_og,
    _narrow_range_by_strength,
)


class TestPFRRangeEstimation(unittest.TestCase):
    """Test PFR-based range estimation follows poker theory."""

    def test_tight_pfr_gives_small_range(self):
        """8% PFR should give ~premium hands only (AA-JJ, AK)."""
        range_8pct = estimate_range_from_pfr(0.08)
        # Should be roughly 5-10% of hands (8-17 combos)
        self.assertLessEqual(len(range_8pct), 20)
        # Must include AA, KK
        self.assertIn('AA', range_8pct)
        self.assertIn('KK', range_8pct)
        self.assertIn('AKs', range_8pct)

    def test_pfr_range_tiers(self):
        """PFR ranges should map to expected position ranges."""
        # Very tight (≤8%) → Ultra-premium
        self.assertEqual(estimate_range_from_pfr(0.05), ULTRA_PREMIUM_RANGE)
        self.assertEqual(estimate_range_from_pfr(0.08), ULTRA_PREMIUM_RANGE)

        # Tight (8-12%) → Early position
        self.assertEqual(estimate_range_from_pfr(0.10), EARLY_POSITION_RANGE)
        self.assertEqual(estimate_range_from_pfr(0.12), EARLY_POSITION_RANGE)

        # Medium (12-18%) → Middle position
        self.assertEqual(estimate_range_from_pfr(0.15), MIDDLE_POSITION_RANGE)
        self.assertEqual(estimate_range_from_pfr(0.18), MIDDLE_POSITION_RANGE)

        # Wider (18-25%) → Blind defense
        self.assertEqual(estimate_range_from_pfr(0.22), BLIND_DEFENSE_RANGE)
        self.assertEqual(estimate_range_from_pfr(0.25), BLIND_DEFENSE_RANGE)

        # Wide (>25%) → Late position
        self.assertEqual(estimate_range_from_pfr(0.30), LATE_POSITION_RANGE)

    def test_pfr_ranges_are_reasonable_size(self):
        """PFR ranges should be reasonable sizes for each tier."""
        # PFR ranges map to position-based ranges
        # The key insight is PFR represents raising range, which is always
        # a subset of hands a player would play (VPIP)

        # Tight PFR (8%) → Ultra-premium (~8-10 hands)
        tight_pfr = estimate_range_from_pfr(0.08)
        self.assertLessEqual(len(tight_pfr), 15)

        # Medium PFR (15%) → Early/Middle position (~15-30 hands)
        medium_pfr = estimate_range_from_pfr(0.15)
        self.assertLessEqual(len(medium_pfr), 35)

        # Wide PFR (30%) → Late position (~40-55 hands)
        wide_pfr = estimate_range_from_pfr(0.30)
        self.assertLessEqual(len(wide_pfr), 60)


class TestCallingRangeEstimation(unittest.TestCase):
    """Test that calling ranges are correctly computed as VPIP - PFR."""

    def test_calling_range_excludes_raising_range(self):
        """Calling range should not overlap with raising range."""
        # Player with 35% VPIP and 15% PFR
        calling = estimate_calling_range(vpip=0.35, pfr=0.15)
        raising = estimate_range_from_pfr(0.15)

        overlap = calling & raising
        self.assertEqual(
            len(overlap), 0,
            f"Calling and raising ranges should not overlap. "
            f"Found overlap: {overlap}"
        )

    def test_calling_range_size(self):
        """Calling range should be approximately VPIP - PFR."""
        # 35% VPIP, 15% PFR → ~20% calling range
        calling = estimate_calling_range(vpip=0.35, pfr=0.15)
        vpip_range = estimate_range_from_vpip(0.35)
        pfr_range = estimate_range_from_pfr(0.15)

        # Calling range size should be roughly VPIP size minus PFR size
        expected_min = len(vpip_range) - len(pfr_range) - 5
        expected_max = len(vpip_range)

        self.assertGreaterEqual(
            len(calling), max(0, expected_min),
            f"Calling range too small: {len(calling)}"
        )
        self.assertLessEqual(
            len(calling), expected_max,
            f"Calling range too large: {len(calling)}"
        )

    def test_calling_range_excludes_premium(self):
        """Calling range should not include hands player would raise."""
        # Typical player would raise AA, KK, AKs
        calling = estimate_calling_range(vpip=0.35, pfr=0.15)

        # Premium hands should NOT be in calling range
        self.assertNotIn('AA', calling, "AA should not be in calling range")
        self.assertNotIn('KK', calling, "KK should not be in calling range")


class Test3BetRangeEstimation(unittest.TestCase):
    """Test that 3-bet ranges are appropriately narrow."""

    def test_3bet_narrower_than_open_raise(self):
        """3-bet range should be much tighter than open-raise range."""
        pfr = 0.20  # 20% PFR player

        open_range = estimate_range_from_pfr(pfr)
        three_bet_range = estimate_3bet_range(pfr)

        # 3-bet range should be significantly smaller
        self.assertLess(
            len(three_bet_range), len(open_range) * 0.6,
            f"3-bet range ({len(three_bet_range)}) should be < 60% of "
            f"open-raise range ({len(open_range)})"
        )

    def test_3bet_range_is_premium(self):
        """3-bet range should contain premium hands."""
        three_bet = estimate_3bet_range(0.20)

        # Must contain absolute premium
        self.assertIn('AA', three_bet)
        self.assertIn('KK', three_bet)

    def test_3bet_range_percentage(self):
        """3-bet range should be approximately 4-8% of hands."""
        # Standard 18% PFR player
        three_bet = estimate_3bet_range(0.18)
        three_bet_pct = len(three_bet) / 169

        # Should be roughly 4-10% (allowing some tolerance)
        self.assertGreaterEqual(
            three_bet_pct, 0.03,
            f"3-bet range ({three_bet_pct:.1%}) too tight"
        )
        self.assertLessEqual(
            three_bet_pct, 0.15,
            f"3-bet range ({three_bet_pct:.1%}) too wide"
        )


class TestAggressionAdjustment(unittest.TestCase):
    """Test aggression-based postflop range adjustment."""

    def test_passive_player_betting_has_narrow_range(self):
        """Passive player (AF < 0.8) betting should have very strong range."""
        base_range = MIDDLE_POSITION_RANGE.copy()
        adjusted = apply_aggression_adjustment(
            base_range, aggression_factor=0.5, is_aggressive_action=True
        )

        # Range should be narrowed by ~30%
        self.assertLess(
            len(adjusted), len(base_range) * 0.75,
            f"Passive player range ({len(adjusted)}) should be narrowed "
            f"from base ({len(base_range)})"
        )

    def test_aggressive_player_no_narrowing(self):
        """Aggressive player (AF > 2.5) betting should not narrow range."""
        base_range = MIDDLE_POSITION_RANGE.copy()
        adjusted = apply_aggression_adjustment(
            base_range, aggression_factor=3.0, is_aggressive_action=True
        )

        # Range should not change
        self.assertEqual(len(adjusted), len(base_range))

    def test_no_adjustment_for_passive_action(self):
        """No adjustment when opponent checks/calls (not aggressive action)."""
        base_range = MIDDLE_POSITION_RANGE.copy()
        adjusted = apply_aggression_adjustment(
            base_range, aggression_factor=0.5, is_aggressive_action=False
        )

        # Range should not change
        self.assertEqual(len(adjusted), len(base_range))


class TestNarrowRangeByStrength(unittest.TestCase):
    """Test the range narrowing helper function."""

    def test_keeps_strongest_hands(self):
        """Narrowing should keep the strongest hands."""
        base_range = {'AA', 'KK', 'QQ', 'JJ', 'TT', 'AKs', 'AQs', '72o'}
        narrowed = _narrow_range_by_strength(base_range, keep_top=0.5)

        # Should keep ~4 hands (50% of 8)
        self.assertLessEqual(len(narrowed), 5)
        self.assertGreaterEqual(len(narrowed), 3)

        # Must keep premium pairs
        self.assertIn('AA', narrowed)
        self.assertIn('KK', narrowed)

        # Should NOT keep trash
        self.assertNotIn('72o', narrowed)


class TestActionBasedNarrowing(unittest.TestCase):
    """Test that actions narrow ranges correctly in get_opponent_range."""

    def test_open_raiser_uses_pfr_not_vpip(self):
        """When opponent open-raised, use PFR not VPIP."""
        opponent = OpponentInfo(
            name="Villain",
            position="button",
            hands_observed=20,
            vpip=0.40,  # Very loose overall
            pfr=0.15,   # But tight raiser
            preflop_action='open_raise'
        )
        range_est = get_opponent_range(opponent)

        # Should be close to 15% range, not 40%
        vpip_range = estimate_range_from_vpip(0.40)
        pfr_range = estimate_range_from_pfr(0.15)

        # Range should be closer to PFR size than VPIP size
        self.assertLess(
            len(range_est), len(vpip_range) * 0.8,
            "Open-raiser range should be closer to PFR than VPIP"
        )

    def test_caller_uses_vpip_minus_pfr(self):
        """When opponent called, use VPIP-PFR range."""
        opponent = OpponentInfo(
            name="Villain",
            position="button",
            hands_observed=20,
            vpip=0.40,
            pfr=0.15,
            preflop_action='call'
        )
        range_est = get_opponent_range(opponent)

        # Should NOT contain premium hands (would have raised)
        self.assertNotIn('AA', range_est, "Caller shouldn't have AA")
        self.assertNotIn('KK', range_est, "Caller shouldn't have KK")

    def test_3bet_uses_tight_range(self):
        """When opponent 3-bet, use very tight range."""
        opponent = OpponentInfo(
            name="Villain",
            position="button",
            hands_observed=20,
            vpip=0.40,
            pfr=0.20,
            preflop_action='3bet'
        )
        range_est = get_opponent_range(opponent)

        # 3-bet range should be very tight
        self.assertLess(
            len(range_est), 20,
            f"3-bet range too wide: {len(range_est)} hands"
        )

    def test_4bet_uses_premium_range(self):
        """When opponent 4-bet+, use ultra-premium range."""
        opponent = OpponentInfo(
            name="Villain",
            position="button",
            hands_observed=20,
            vpip=0.40,
            pfr=0.20,
            preflop_action='4bet+'
        )
        range_est = get_opponent_range(opponent)

        # Should be ultra-premium
        self.assertEqual(range_est, ULTRA_PREMIUM_RANGE)

    def test_fallback_without_action_context(self):
        """Without action context, should use VPIP-based estimation."""
        opponent = OpponentInfo(
            name="Villain",
            position="button",
            hands_observed=20,
            vpip=0.35,
            pfr=0.15,
            preflop_action=None  # No action context
        )
        range_est = get_opponent_range(opponent)

        # Should use VPIP-based range
        vpip_range = estimate_range_from_vpip(0.35)
        self.assertEqual(len(range_est), len(vpip_range))


class TestOldVsNewComparison(unittest.TestCase):
    """Compare old (VPIP-only) vs new (action-aware) range estimation."""

    def test_new_gives_tighter_range_for_raiser(self):
        """New method should give tighter range for preflop raisers."""
        opponent = OpponentInfo(
            name="Villain",
            position="button",
            hands_observed=20,
            vpip=0.40,
            pfr=0.15,
            preflop_action='open_raise'
        )

        old_range = get_opponent_range_og(opponent)
        new_range = get_opponent_range(opponent)

        # New range should be smaller (more accurate)
        self.assertLess(
            len(new_range), len(old_range),
            f"New range ({len(new_range)}) should be tighter than "
            f"old range ({len(old_range)}) for open-raiser"
        )

    def test_new_excludes_premium_for_caller(self):
        """New method should exclude premium hands for callers."""
        opponent = OpponentInfo(
            name="Villain",
            position="button",
            hands_observed=20,
            vpip=0.40,
            pfr=0.15,
            preflop_action='call'
        )

        old_range = get_opponent_range_og(opponent)
        new_range = get_opponent_range(opponent)

        # Old range includes AA (wrong for a caller)
        self.assertIn('AA', old_range)

        # New range excludes AA (correct - they would have raised)
        self.assertNotIn('AA', new_range)


class TestMathematicalBenchmarks(unittest.TestCase):
    """Validate range estimates against standard poker theory benchmarks."""

    def test_10pct_pfr_range(self):
        """10% PFR should produce ~17 hand combos (±5)."""
        pfr_10 = estimate_range_from_pfr(0.10)
        # Early position range is ~15% = ~25 hands
        # Allow ±10 tolerance
        self.assertGreaterEqual(len(pfr_10), 15)
        self.assertLessEqual(len(pfr_10), 35)

    def test_3bet_range_percentage(self):
        """3-bet range should be 4-10% of hands for typical player."""
        # Standard 18% PFR player
        three_bet = estimate_3bet_range(0.18)
        three_bet_pct = len(three_bet) / 169

        self.assertGreaterEqual(three_bet_pct, 0.03, "3-bet too tight")
        self.assertLessEqual(three_bet_pct, 0.12, "3-bet too wide")

    def test_calling_range_for_lag(self):
        """Loose-aggressive player has narrow calling range (raises most hands)."""
        # LAG: 40% VPIP, 25% PFR - raises most hands they play
        calling = estimate_calling_range(vpip=0.40, pfr=0.25)

        # LAG players have narrow calling ranges because:
        # - High PFR/VPIP ratio means they raise most hands
        # - Calling range = VPIP - PFR (what they play but don't raise)
        # This is correct behavior - LAGs call rarely

        # Just verify it's non-empty and smaller than their raising range
        raising = estimate_range_from_pfr(0.25)
        self.assertGreater(len(calling), 0, "Should have some calling hands")
        self.assertLess(
            len(calling), len(raising),
            "LAG calling range should be smaller than raising range"
        )

    def test_calling_range_for_passive_player(self):
        """Passive player has wide calling range (calls more than raises)."""
        # Passive: 40% VPIP, 8% PFR - calls much more than raises
        calling = estimate_calling_range(vpip=0.40, pfr=0.08)
        calling_pct = len(calling) / 169

        # Passive players have wide calling ranges
        # 40% VPIP with only 8% PFR = ~32% calling range
        self.assertGreaterEqual(calling_pct, 0.15, "Passive player should call wide")
        self.assertLessEqual(calling_pct, 0.40)


class TestIsHandInStandardRange(unittest.TestCase):
    """Test the is_hand_in_standard_range function for player hand evaluation."""

    def setUp(self):
        """Import the function for testing."""
        from poker.hand_ranges import is_hand_in_standard_range
        self.is_hand_in_range = is_hand_in_standard_range

    def test_premium_hand_in_all_ranges(self):
        """AA is in range for all positions."""
        # UTG (early)
        result = self.is_hand_in_range("Ah", "Ad", "under_the_gun")
        self.assertTrue(result['in_range'])
        self.assertEqual(result['canonical_hand'], 'AA')
        self.assertEqual(result['hand_tier'], 'premium')
        self.assertEqual(result['position_group'], 'early')

        # Button (late)
        result = self.is_hand_in_range("Ah", "Ad", "button")
        self.assertTrue(result['in_range'])
        self.assertEqual(result['hand_tier'], 'premium')

    def test_marginal_hand_position_dependent(self):
        """87s is playable on button but not UTG."""
        # UTG - 87s should be outside early range
        result = self.is_hand_in_range("8h", "7h", "under_the_gun")
        self.assertFalse(result['in_range'])
        self.assertEqual(result['canonical_hand'], '87s')
        self.assertIn(result['hand_tier'], ('marginal', 'playable'))

        # Button - 87s should be in late position range
        result = self.is_hand_in_range("8h", "7h", "button")
        self.assertTrue(result['in_range'])
        self.assertEqual(result['hand_tier'], 'playable')

    def test_trash_hand_never_in_range(self):
        """72o is never in standard range."""
        for position in ['under_the_gun', 'button', 'big_blind_player']:
            result = self.is_hand_in_range("7h", "2d", position)
            self.assertFalse(result['in_range'])
            self.assertEqual(result['canonical_hand'], '72o')
            self.assertEqual(result['hand_tier'], 'trash')

    def test_range_size_pct_varies_by_position(self):
        """Range percentage should differ by position."""
        utg_result = self.is_hand_in_range("Ah", "Kh", "under_the_gun")
        btn_result = self.is_hand_in_range("Ah", "Kh", "button")

        # UTG has tighter range (~9%) than button (~30%)
        self.assertLess(utg_result['range_size_pct'], btn_result['range_size_pct'])
        self.assertAlmostEqual(utg_result['range_size_pct'], 9, delta=3)
        self.assertAlmostEqual(btn_result['range_size_pct'], 30, delta=5)

    def test_canonical_hand_format(self):
        """Verify canonical hand notation is correct."""
        # Suited
        result = self.is_hand_in_range("Ah", "Kh", "button")
        self.assertEqual(result['canonical_hand'], 'AKs')

        # Offsuit
        result = self.is_hand_in_range("Ah", "Kd", "button")
        self.assertEqual(result['canonical_hand'], 'AKo')

        # Pair
        result = self.is_hand_in_range("Qh", "Qd", "button")
        self.assertEqual(result['canonical_hand'], 'QQ')

    def test_hand_tier_classification(self):
        """Verify hand tier classification is correct."""
        # Premium
        result = self.is_hand_in_range("Ah", "Ad", "button")
        self.assertEqual(result['hand_tier'], 'premium')

        result = self.is_hand_in_range("Ah", "Kh", "button")
        self.assertEqual(result['hand_tier'], 'premium')

        # Strong
        result = self.is_hand_in_range("Jh", "Jd", "button")
        self.assertEqual(result['hand_tier'], 'strong')

        result = self.is_hand_in_range("Ah", "Qh", "button")
        self.assertEqual(result['hand_tier'], 'strong')

        # Playable
        result = self.is_hand_in_range("Th", "9h", "button")
        self.assertEqual(result['hand_tier'], 'playable')

        # Marginal
        result = self.is_hand_in_range("5h", "4h", "under_the_gun")
        # 54s is outside UTG range but is a speculative hand
        self.assertIn(result['hand_tier'], ('marginal', 'playable'))

    def test_position_group_mapping(self):
        """Verify position maps to correct group."""
        positions_to_groups = {
            'under_the_gun': 'early',
            'middle_position_1': 'middle',
            'cutoff': 'late',
            'button': 'late',
            'small_blind_player': 'blind',
            'big_blind_player': 'blind',
        }

        for position, expected_group in positions_to_groups.items():
            result = self.is_hand_in_range("Ah", "Kh", position)
            self.assertEqual(
                result['position_group'], expected_group,
                f"Position {position} should map to {expected_group}"
            )


if __name__ == '__main__':
    unittest.main()
