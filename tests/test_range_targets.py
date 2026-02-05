"""Tests for range_targets module - personal adaptive range targets for coaching."""

import pytest
from flask_app.services.range_targets import (
    DEFAULT_RANGE_TARGETS,
    GATE_EXPANSIONS,
    normalize_position,
    get_range_target,
    expand_ranges_for_gate,
    get_expanded_ranges,
)


class TestDefaultRangeTargets:
    """Test the default range targets for beginners."""

    def test_default_targets_exist_for_all_positions(self):
        """All standard positions should have default range targets."""
        expected_positions = ['UTG', 'UTG+1', 'MP', 'CO', 'BTN', 'BB']
        for pos in expected_positions:
            assert pos in DEFAULT_RANGE_TARGETS, f"Missing position: {pos}"

    def test_default_targets_are_tight(self):
        """Default targets should be tight for beginners (<=25%)."""
        for pos, pct in DEFAULT_RANGE_TARGETS.items():
            assert 0 < pct <= 0.25, f"{pos} range {pct} should be tight (<=25%)"

    def test_position_hierarchy(self):
        """Late position should have wider range than early position."""
        assert DEFAULT_RANGE_TARGETS['BTN'] > DEFAULT_RANGE_TARGETS['UTG']
        assert DEFAULT_RANGE_TARGETS['CO'] > DEFAULT_RANGE_TARGETS['UTG']
        assert DEFAULT_RANGE_TARGETS['MP'] >= DEFAULT_RANGE_TARGETS['UTG']


class TestNormalizePosition:
    """Test the normalize_position function."""

    def test_utg_variations(self):
        """Various UTG formats should normalize correctly."""
        assert normalize_position('UTG') == 'UTG'
        assert normalize_position('utg') == 'UTG'
        assert normalize_position('Under The Gun') == 'UTG'
        assert normalize_position('under_the_gun') == 'UTG'

    def test_utg_plus_one(self):
        """UTG+1 variations should normalize correctly."""
        assert normalize_position('UTG+1') == 'UTG+1'
        assert normalize_position('utg+1') == 'UTG+1'
        assert normalize_position('utg 1') == 'UTG+1'

    def test_middle_position(self):
        """Middle position variations should normalize correctly."""
        assert normalize_position('MP') == 'MP'
        assert normalize_position('mp') == 'MP'
        assert normalize_position('Middle Position') == 'MP'
        assert normalize_position('middle_position_1') == 'MP'
        assert normalize_position('middle_position_2') == 'MP'

    def test_cutoff(self):
        """Cutoff variations should normalize correctly."""
        assert normalize_position('CO') == 'CO'
        assert normalize_position('co') == 'CO'
        assert normalize_position('Cutoff') == 'CO'
        assert normalize_position('cutoff') == 'CO'

    def test_button(self):
        """Button variations should normalize correctly."""
        assert normalize_position('BTN') == 'BTN'
        assert normalize_position('btn') == 'BTN'
        assert normalize_position('Button') == 'BTN'
        assert normalize_position('button') == 'BTN'
        assert normalize_position('Dealer') == 'BTN'

    def test_blinds(self):
        """Blind positions should normalize to BB."""
        # Full names with "blind" in them
        assert normalize_position('Big Blind') == 'BB'
        assert normalize_position('big_blind_player') == 'BB'
        assert normalize_position('Small Blind') == 'BB'  # Small blind treated as BB
        assert normalize_position('small_blind') == 'BB'
        # Short forms
        assert normalize_position('BB') == 'BB'
        assert normalize_position('bb') == 'BB'
        assert normalize_position('SB') == 'BB'  # Small blind treated as BB
        assert normalize_position('sb') == 'BB'

    def test_empty_string_fallback(self):
        """Empty string should fallback to MP."""
        assert normalize_position('') == 'MP'

    def test_unknown_fallback(self):
        """Unknown positions should fallback to MP."""
        assert normalize_position('unknown_position') == 'MP'
        assert normalize_position('random') == 'MP'


class TestGetRangeTarget:
    """Test the get_range_target function."""

    def test_returns_correct_target(self):
        """Should return the correct target for normalized positions."""
        result = get_range_target(DEFAULT_RANGE_TARGETS, 'UTG')
        assert result == DEFAULT_RANGE_TARGETS['UTG']

    def test_normalizes_position(self):
        """Should normalize position before lookup."""
        result = get_range_target(DEFAULT_RANGE_TARGETS, 'under_the_gun')
        assert result == DEFAULT_RANGE_TARGETS['UTG']

        result = get_range_target(DEFAULT_RANGE_TARGETS, 'Button')
        assert result == DEFAULT_RANGE_TARGETS['BTN']

    def test_fallback_for_unknown(self):
        """Unknown positions normalize to MP, so return MP's target."""
        result = get_range_target(DEFAULT_RANGE_TARGETS, 'xyz_unknown')
        # Unknown positions normalize to MP, so we get MP's range
        assert result == DEFAULT_RANGE_TARGETS['MP']


class TestGateExpansions:
    """Test gate-based range expansion configuration."""

    def test_expansions_exist_for_gates(self):
        """Should have expansion configs for gates 2-4."""
        assert 2 in GATE_EXPANSIONS
        assert 3 in GATE_EXPANSIONS
        assert 4 in GATE_EXPANSIONS

    def test_expansions_increase_progressively(self):
        """Higher gates should have wider ranges."""
        for pos in ['UTG', 'BTN', 'CO']:
            assert GATE_EXPANSIONS[3][pos] > GATE_EXPANSIONS[2][pos]
            assert GATE_EXPANSIONS[4][pos] > GATE_EXPANSIONS[3][pos]

    def test_expansion_values_are_reasonable(self):
        """Expansion values should be between 0 and 50%."""
        for gate, expansions in GATE_EXPANSIONS.items():
            for pos, pct in expansions.items():
                assert 0 < pct <= 0.50, f"Gate {gate} {pos} range {pct} out of bounds"


class TestExpandRangesForGate:
    """Test the expand_ranges_for_gate function."""

    def test_expand_for_gate_2(self):
        """Gate 2 should return gate 2 expansions."""
        result = expand_ranges_for_gate(DEFAULT_RANGE_TARGETS, 2)
        assert result == GATE_EXPANSIONS[2]

    def test_expand_for_gate_3(self):
        """Gate 3 should return gate 3 expansions."""
        result = expand_ranges_for_gate(DEFAULT_RANGE_TARGETS, 3)
        assert result == GATE_EXPANSIONS[3]

    def test_expand_unknown_gate_returns_copy(self):
        """Unknown gate should return a copy of current targets."""
        initial = {'UTG': 0.10, 'BTN': 0.15}
        result = expand_ranges_for_gate(initial, 99)

        assert result == initial
        assert result is not initial  # Should be a copy

    def test_does_not_mutate_input(self):
        """Should not mutate the input dict."""
        initial = {'UTG': 0.10, 'BTN': 0.15}
        initial_copy = initial.copy()
        expand_ranges_for_gate(initial, 2)

        assert initial == initial_copy


class TestGetExpandedRanges:
    """Test the get_expanded_ranges convenience function."""

    def test_gate_1_returns_defaults(self):
        """Gate 1 should return default ranges."""
        result = get_expanded_ranges(1)
        assert result == DEFAULT_RANGE_TARGETS

    def test_gate_0_returns_defaults(self):
        """Gate 0 (or negative) should return default ranges."""
        result = get_expanded_ranges(0)
        assert result == DEFAULT_RANGE_TARGETS

    def test_gate_2_returns_gate_2_expansion(self):
        """Gate 2 should return gate 2 expansion ranges."""
        result = get_expanded_ranges(2)
        assert result == GATE_EXPANSIONS[2]

    def test_gate_3_returns_gate_3_expansion(self):
        """Gate 3 should return gate 3 expansion ranges."""
        result = get_expanded_ranges(3)
        assert result == GATE_EXPANSIONS[3]

    def test_gate_4_returns_gate_4_expansion(self):
        """Gate 4 should return gate 4 expansion ranges."""
        result = get_expanded_ranges(4)
        assert result == GATE_EXPANSIONS[4]

    def test_high_gate_returns_highest_expansion(self):
        """Gates higher than 4 should return gate 4 expansion."""
        result = get_expanded_ranges(10)
        assert result == GATE_EXPANSIONS[4]

    def test_returns_copy_not_reference(self):
        """Should return a copy, not the original dict."""
        result = get_expanded_ranges(1)
        assert result is not DEFAULT_RANGE_TARGETS

        result2 = get_expanded_ranges(2)
        assert result2 is not GATE_EXPANSIONS[2]


class TestIntegrationWithHandTiers:
    """Test integration with hand_tiers.is_hand_in_range."""

    def test_premium_hand_in_tight_range(self):
        """Premium hands should be in even the tightest ranges."""
        from poker.hand_tiers import is_hand_in_range

        # AA is top 0.5% - should be in 8% range
        assert is_hand_in_range('AA', 0.08) is True
        assert is_hand_in_range('KK', 0.08) is True
        assert is_hand_in_range('AKs', 0.08) is True

    def test_hand_at_boundary(self):
        """Hands near the boundary should respect range limits."""
        from poker.hand_tiers import is_hand_in_range

        # AJs is in top 15%, AJo is not (it's around top 20%)
        assert is_hand_in_range('AJs', 0.15) is True
        assert is_hand_in_range('AJo', 0.15) is False
        assert is_hand_in_range('AJo', 0.25) is True

    def test_marginal_hand_with_gate_expansion(self):
        """Marginal hands should become playable as ranges widen."""
        from poker.hand_tiers import is_hand_in_range

        # 87s is around top 20-25%
        gate1_btn = DEFAULT_RANGE_TARGETS['BTN']  # 25%
        gate4_btn = GATE_EXPANSIONS[4]['BTN']     # 40%

        # Should be borderline at gate 1, definitely in at gate 4
        result_gate1 = is_hand_in_range('87s', gate1_btn)
        result_gate4 = is_hand_in_range('87s', gate4_btn)

        # Gate 4 should allow it
        assert result_gate4 is True
