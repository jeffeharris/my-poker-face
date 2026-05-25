"""Unit tests for poker/strategy/short_stack.py (Phase 6 Step B)."""

import pytest

from poker.strategy.short_stack import (
    DEPTH_DEEP_BB,
    DEPTH_SHORT_BB,
    apply_short_stack_heuristics,
    medium_raise_suppression_factor,
)
from poker.strategy.strategy_profile import StrategyProfile

# ── Suppression factor ──────────────────────────────────────────────────


class TestSuppressionFactor:
    """Linear ramp: 0% at 20 BB → 100% at 10 BB."""

    def test_no_suppression_at_deep(self):
        assert medium_raise_suppression_factor(100) == 0.0
        assert medium_raise_suppression_factor(DEPTH_DEEP_BB) == 0.0
        assert medium_raise_suppression_factor(25) == 0.0

    def test_full_suppression_at_short(self):
        assert medium_raise_suppression_factor(DEPTH_SHORT_BB) == 1.0
        assert medium_raise_suppression_factor(5) == 1.0
        assert medium_raise_suppression_factor(0.5) == 1.0

    def test_linear_interpolation_mid(self):
        # 15 BB is midpoint between 20 and 10 → 50% suppression
        assert medium_raise_suppression_factor(15.0) == pytest.approx(0.5)

    def test_linear_interpolation_12bb(self):
        # 12 BB → (20 - 12) / 10 = 0.8
        assert medium_raise_suppression_factor(12.0) == pytest.approx(0.8)

    def test_linear_interpolation_18bb(self):
        # 18 BB → (20 - 18) / 10 = 0.2
        assert medium_raise_suppression_factor(18.0) == pytest.approx(0.2)


# ── apply_short_stack_heuristics ────────────────────────────────────────


class TestApplyDeepStack:
    """At deep stack (>20 BB), the strategy is returned unchanged."""

    def test_no_change_at_30bb(self):
        s = StrategyProfile(
            action_probabilities={
                'fold': 0.3,
                'call': 0.3,
                'raise_2.5bb': 0.4,
            }
        )
        result, _trace = apply_short_stack_heuristics(
            s,
            effective_stack_bb=30.0,
            legal_actions=['fold', 'call', 'raise', 'all_in'],
        )
        assert result is s

    def test_no_change_at_exactly_20bb(self):
        s = StrategyProfile(
            action_probabilities={
                'fold': 0.3,
                'call': 0.3,
                'raise_2.5bb': 0.4,
            }
        )
        result, _trace = apply_short_stack_heuristics(
            s,
            effective_stack_bb=20.0,
            legal_actions=['fold', 'call', 'raise', 'all_in'],
        )
        assert result is s


class TestApplyShortStack:
    """At short stack (<= 10 BB), all medium-raise mass moves to jam/fold."""

    def test_full_suppression_redistributes_to_jam(self):
        s = StrategyProfile(
            action_probabilities={
                'fold': 0.2,
                'call': 0.3,
                'raise_2.5bb': 0.5,
            }
        )
        result, _trace = apply_short_stack_heuristics(
            s,
            effective_stack_bb=8.0,
            legal_actions=['fold', 'call', 'raise', 'all_in'],
        )
        # All raise mass moved to jam
        assert result.action_probabilities['raise_2.5bb'] == 0.0
        assert result.action_probabilities['jam'] == pytest.approx(0.5)
        # Other mass unchanged
        assert result.action_probabilities['fold'] == pytest.approx(0.2)
        assert result.action_probabilities['call'] == pytest.approx(0.3)

    def test_falls_back_to_fold_when_no_jam(self):
        """If all_in isn't legal, medium-raise mass goes to fold instead."""
        s = StrategyProfile(
            action_probabilities={
                'fold': 0.2,
                'call': 0.3,
                'raise_2.5bb': 0.5,
            }
        )
        result, _trace = apply_short_stack_heuristics(
            s,
            effective_stack_bb=8.0,
            legal_actions=['fold', 'call', 'raise'],  # no all_in
        )
        assert result.action_probabilities['raise_2.5bb'] == 0.0
        assert result.action_probabilities['fold'] == pytest.approx(0.7)
        assert (
            'jam' not in result.action_probabilities
            or result.action_probabilities.get('jam', 0.0) == 0.0
        )


class TestApplyMidRange:
    """At mid-depth (10-20 BB), partial suppression."""

    def test_50_percent_suppression_at_15bb(self):
        s = StrategyProfile(
            action_probabilities={
                'fold': 0.2,
                'call': 0.3,
                'raise_2.5bb': 0.5,
            }
        )
        result, _trace = apply_short_stack_heuristics(
            s,
            effective_stack_bb=15.0,
            legal_actions=['fold', 'call', 'raise', 'all_in'],
        )
        # 50% of 0.5 = 0.25 kept on raise, 0.25 moved to jam
        assert result.action_probabilities['raise_2.5bb'] == pytest.approx(0.25)
        assert result.action_probabilities['jam'] == pytest.approx(0.25)

    def test_80_percent_suppression_at_12bb(self):
        s = StrategyProfile(
            action_probabilities={
                'fold': 0.2,
                'call': 0.3,
                'raise_2.5bb': 0.5,
            }
        )
        result, _trace = apply_short_stack_heuristics(
            s,
            effective_stack_bb=12.0,
            legal_actions=['fold', 'call', 'raise', 'all_in'],
        )
        # 80% of 0.5 = 0.4 moved to jam, 0.1 kept
        assert result.action_probabilities['raise_2.5bb'] == pytest.approx(0.1)
        assert result.action_probabilities['jam'] == pytest.approx(0.4)


class TestApplyEdgeCases:
    """Robustness against weird input strategies."""

    def test_no_medium_raises_returns_unchanged(self):
        s = StrategyProfile(
            action_probabilities={
                'fold': 0.4,
                'call': 0.6,
            }
        )
        result, _trace = apply_short_stack_heuristics(
            s,
            effective_stack_bb=8.0,
            legal_actions=['fold', 'call'],
        )
        assert result is s

    def test_existing_jam_mass_preserved_and_added_to(self):
        """If strategy already has jam mass, we add to it."""
        s = StrategyProfile(
            action_probabilities={
                'fold': 0.1,
                'call': 0.2,
                'raise_2.5bb': 0.4,
                'jam': 0.3,
            }
        )
        result, _trace = apply_short_stack_heuristics(
            s,
            effective_stack_bb=8.0,
            legal_actions=['fold', 'call', 'raise', 'all_in'],
        )
        # All raise_2.5bb mass (0.4) added to existing jam (0.3) = 0.7
        assert result.action_probabilities['jam'] == pytest.approx(0.7)
        assert result.action_probabilities['raise_2.5bb'] == 0.0

    def test_multiple_medium_raises_all_suppressed(self):
        s = StrategyProfile(
            action_probabilities={
                'fold': 0.1,
                'call': 0.2,
                'raise_2.5bb': 0.3,
                'raise_3bb': 0.2,
                'raise_4x': 0.2,
            }
        )
        result, _trace = apply_short_stack_heuristics(
            s,
            effective_stack_bb=8.0,
            legal_actions=['fold', 'call', 'raise', 'all_in'],
        )
        # All three raise actions cleared; total 0.7 mass to jam
        assert result.action_probabilities['raise_2.5bb'] == 0.0
        assert result.action_probabilities['raise_3bb'] == 0.0
        assert result.action_probabilities['raise_4x'] == 0.0
        assert result.action_probabilities['jam'] == pytest.approx(0.7)

    def test_postflop_bet_actions_treated_as_medium_raise(self):
        """bet_67, bet_100, etc. are also medium raises."""
        s = StrategyProfile(
            action_probabilities={
                'fold': 0.1,
                'check': 0.3,
                'bet_67': 0.4,
                'bet_100': 0.2,
            }
        )
        result, _trace = apply_short_stack_heuristics(
            s,
            effective_stack_bb=8.0,
            legal_actions=['fold', 'check', 'raise', 'all_in'],
        )
        assert result.action_probabilities['bet_67'] == 0.0
        assert result.action_probabilities['bet_100'] == 0.0
        assert result.action_probabilities['jam'] == pytest.approx(0.6)

    def test_renormalizes_to_one(self):
        s = StrategyProfile(
            action_probabilities={
                'fold': 0.2,
                'call': 0.3,
                'raise_2.5bb': 0.5,
            }
        )
        result, _trace = apply_short_stack_heuristics(
            s,
            effective_stack_bb=12.0,
            legal_actions=['fold', 'call', 'raise', 'all_in'],
        )
        total = sum(result.action_probabilities.values())
        assert total == pytest.approx(1.0)

    def test_zero_prob_raises_skipped(self):
        """Raises with 0 probability shouldn't trigger redistribution."""
        s = StrategyProfile(
            action_probabilities={
                'fold': 0.5,
                'call': 0.5,
                'raise_2.5bb': 0.0,
            }
        )
        result, _trace = apply_short_stack_heuristics(
            s,
            effective_stack_bb=8.0,
            legal_actions=['fold', 'call', 'raise', 'all_in'],
        )
        # No raise mass to redistribute → unchanged
        assert result is s

    def test_no_legal_jam_and_no_legal_fold_is_noop(self):
        """Pathological: neither fold nor all_in legal. Don't corrupt."""
        s = StrategyProfile(
            action_probabilities={
                'call': 0.5,
                'raise_2.5bb': 0.5,
            }
        )
        result, _trace = apply_short_stack_heuristics(
            s,
            effective_stack_bb=8.0,
            legal_actions=['call', 'raise'],  # no fold, no all_in
        )
        # Falls back to fold, but fold isn't legal, so no-op.
        assert result is s
