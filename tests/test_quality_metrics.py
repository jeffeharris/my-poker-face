"""Tests for poker/quality_metrics.py - shared quality indicator logic."""

import json
import pytest

from poker.quality_metrics import (
    SHORT_STACK_BB,
    MARGINAL_STACK_BB,
    BLUFF_THRESHOLD,
    TRASH_EQUITY,
    categorize_allin_row,
    compute_allin_categorizations,
    build_quality_indicators,
)


class TestConstants:
    """Test that constants have expected values."""

    def test_short_stack_bb(self):
        assert SHORT_STACK_BB == 10

    def test_marginal_stack_bb(self):
        assert MARGINAL_STACK_BB == 15

    def test_bluff_threshold(self):
        assert BLUFF_THRESHOLD == 50

    def test_trash_equity(self):
        assert TRASH_EQUITY == 0.25


class TestCategorizeAllinRow:
    """Test categorize_allin_row function."""

    def test_intentional_bluff_is_defensible(self):
        """High bluff_likelihood (>=50) should return None (defensible)."""
        ai_response = json.dumps({'bluff_likelihood': 70, 'hand_strength': 'high card'})
        result = categorize_allin_row(stack_bb=20.0, ai_response=ai_response, equity=0.1)
        assert result is None

    def test_short_stack_is_defensible(self):
        """Stack <= 10BB should return None regardless of hand quality."""
        ai_response = json.dumps({'bluff_likelihood': 10, 'hand_strength': 'high card'})
        result = categorize_allin_row(stack_bb=8.0, ai_response=ai_response, equity=0.1)
        assert result is None

    def test_short_stack_boundary(self):
        """Stack == 10BB should be defensible."""
        ai_response = json.dumps({'bluff_likelihood': 10, 'hand_strength': 'high card'})
        result = categorize_allin_row(stack_bb=10.0, ai_response=ai_response, equity=0.1)
        assert result is None

    def test_marginal_stack_with_trash_hand(self):
        """Stack 11-15BB with trash hand should be 'marginal'."""
        ai_response = json.dumps({'bluff_likelihood': 10, 'hand_strength': 'high card'})
        result = categorize_allin_row(stack_bb=12.0, ai_response=ai_response, equity=0.1)
        assert result == 'marginal'

    def test_marginal_stack_boundary_upper(self):
        """Stack == 15BB with trash hand should be 'marginal'."""
        ai_response = json.dumps({'bluff_likelihood': 10, 'hand_strength': 'high card'})
        result = categorize_allin_row(stack_bb=15.0, ai_response=ai_response, equity=0.2)
        assert result == 'marginal'

    def test_deep_stack_with_trash_hand_is_suspicious(self):
        """Stack > 15BB with trash hand should be 'suspicious'."""
        ai_response = json.dumps({'bluff_likelihood': 10, 'hand_strength': 'high card'})
        result = categorize_allin_row(stack_bb=20.0, ai_response=ai_response, equity=0.1)
        assert result == 'suspicious'

    def test_deep_stack_with_low_equity_is_suspicious(self):
        """Stack > 15BB with low equity (< 0.25) should be 'suspicious'."""
        ai_response = json.dumps({'bluff_likelihood': 10, 'hand_strength': 'pair of twos'})
        result = categorize_allin_row(stack_bb=25.0, ai_response=ai_response, equity=0.20)
        assert result == 'suspicious'

    def test_deep_stack_with_good_hand_is_defensible(self):
        """Stack > 15BB with good hand (no high card, equity >= 0.25) is defensible."""
        ai_response = json.dumps({'bluff_likelihood': 10, 'hand_strength': 'pair of aces'})
        result = categorize_allin_row(stack_bb=25.0, ai_response=ai_response, equity=0.60)
        assert result is None

    def test_null_ai_response(self):
        """None ai_response should use default bluff threshold (skip)."""
        result = categorize_allin_row(stack_bb=20.0, ai_response=None, equity=0.1)
        # Default bluff is 50, which is >= threshold, so defensible
        assert result is None

    def test_invalid_json_ai_response(self):
        """Invalid JSON should use default values."""
        result = categorize_allin_row(stack_bb=20.0, ai_response="not json", equity=0.1)
        assert result is None

    def test_null_stack_bb_treated_as_deep(self):
        """None stack_bb is treated as deep stack (suspicious if trash)."""
        ai_response = json.dumps({'bluff_likelihood': 10, 'hand_strength': 'high card'})
        result = categorize_allin_row(stack_bb=None, ai_response=ai_response, equity=0.1)
        assert result == 'suspicious'

    def test_bluff_at_threshold_boundary(self):
        """bluff_likelihood == 50 should be defensible (intentional bluff)."""
        ai_response = json.dumps({'bluff_likelihood': 50, 'hand_strength': 'high card'})
        result = categorize_allin_row(stack_bb=20.0, ai_response=ai_response, equity=0.1)
        assert result is None

    def test_bluff_just_below_threshold(self):
        """bluff_likelihood == 49 with trash should be suspicious."""
        ai_response = json.dumps({'bluff_likelihood': 49, 'hand_strength': 'high card'})
        result = categorize_allin_row(stack_bb=20.0, ai_response=ai_response, equity=0.1)
        assert result == 'suspicious'

    def test_equity_at_threshold_boundary(self):
        """equity == 0.25 should NOT be considered trash."""
        ai_response = json.dumps({'bluff_likelihood': 10, 'hand_strength': 'pair'})
        result = categorize_allin_row(stack_bb=20.0, ai_response=ai_response, equity=0.25)
        assert result is None

    def test_equity_just_below_threshold(self):
        """equity == 0.24 should be considered trash."""
        ai_response = json.dumps({'bluff_likelihood': 10, 'hand_strength': 'pair'})
        result = categorize_allin_row(stack_bb=20.0, ai_response=ai_response, equity=0.24)
        assert result == 'suspicious'


class TestComputeAllinCategorizations:
    """Test compute_allin_categorizations function."""

    def test_empty_results(self):
        """Empty cursor results should return (0, 0)."""
        suspicious, marginal = compute_allin_categorizations([])
        assert suspicious == 0
        assert marginal == 0

    def test_all_defensible(self):
        """All defensible rows should return (0, 0)."""
        rows = [
            (5.0, json.dumps({'bluff_likelihood': 10, 'hand_strength': 'high card'}), 0.1),  # short stack
            (20.0, json.dumps({'bluff_likelihood': 70, 'hand_strength': 'high card'}), 0.1),  # intentional bluff
            (20.0, json.dumps({'bluff_likelihood': 10, 'hand_strength': 'pair'}), 0.5),  # good hand
        ]
        suspicious, marginal = compute_allin_categorizations(rows)
        assert suspicious == 0
        assert marginal == 0

    def test_mixed_categories(self):
        """Test with mix of suspicious, marginal, and defensible."""
        rows = [
            # Suspicious: deep stack, low bluff, trash hand
            (25.0, json.dumps({'bluff_likelihood': 10, 'hand_strength': 'high card'}), 0.1),
            # Marginal: medium stack, low bluff, trash hand
            (12.0, json.dumps({'bluff_likelihood': 20, 'hand_strength': 'high card'}), 0.15),
            # Defensible: short stack
            (8.0, json.dumps({'bluff_likelihood': 5, 'hand_strength': 'high card'}), 0.05),
            # Another suspicious
            (30.0, json.dumps({'bluff_likelihood': 30, 'hand_strength': 'nothing'}), 0.2),
        ]
        suspicious, marginal = compute_allin_categorizations(rows)
        assert suspicious == 2
        assert marginal == 1


class TestBuildQualityIndicators:
    """Test build_quality_indicators function."""

    def test_basic_indicators(self):
        """Test basic quality indicators without survival metrics."""
        result = build_quality_indicators(
            fold_mistakes=5,
            total_all_ins=20,
            total_folds=50,
            total_decisions=100,
            suspicious_allins=3,
            marginal_allins=2,
        )

        assert result['suspicious_allins'] == 3
        assert result['marginal_allins'] == 2
        assert result['fold_mistakes'] == 5
        assert result['fold_mistake_rate'] == 10.0  # 5/50 * 100
        assert result['total_all_ins'] == 20
        assert result['total_folds'] == 50
        assert result['total_decisions'] == 100
        assert 'total_eliminations' not in result
        assert 'all_in_wins' not in result
        assert 'all_in_losses' not in result

    def test_with_survival_metrics(self):
        """Test quality indicators with survival metrics."""
        result = build_quality_indicators(
            fold_mistakes=2,
            total_all_ins=15,
            total_folds=30,
            total_decisions=80,
            suspicious_allins=1,
            marginal_allins=0,
            total_eliminations=5,
            all_in_wins=8,
            all_in_losses=4,
        )

        assert result['total_eliminations'] == 5
        assert result['all_in_wins'] == 8
        assert result['all_in_losses'] == 4
        assert result['all_in_survival_rate'] == 66.7  # 8/(8+4) * 100, rounded to 1 decimal

    def test_zero_folds_division(self):
        """Test that zero folds doesn't cause division error."""
        result = build_quality_indicators(
            fold_mistakes=0,
            total_all_ins=10,
            total_folds=0,
            total_decisions=50,
            suspicious_allins=0,
            marginal_allins=0,
        )

        assert result['fold_mistake_rate'] == 0

    def test_zero_showdowns_survival_rate(self):
        """Test that zero showdowns gives None for survival rate."""
        result = build_quality_indicators(
            fold_mistakes=0,
            total_all_ins=0,
            total_folds=10,
            total_decisions=20,
            suspicious_allins=0,
            marginal_allins=0,
            all_in_wins=0,
            all_in_losses=0,
        )

        assert result['all_in_survival_rate'] is None
