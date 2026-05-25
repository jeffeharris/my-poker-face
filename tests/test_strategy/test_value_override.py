"""Unit tests for poker/strategy/value_override.py (Phase 6.5)."""

import pytest

from poker.strategy.exploitation import (
    GATING_FLOOR,
    MIN_HANDS_DEFAULT,
    AggregatedOpponentStats,
    DecisionContext,
)
from poker.strategy.strategy_profile import StrategyProfile
from poker.strategy.value_override import (
    HandStrengthClass,
    compute_value_override_strategy,
    should_apply_value_override,
)


def _maniac_stats(hands: int = 100) -> AggregatedOpponentStats:
    return AggregatedOpponentStats(
        hands_observed=hands,
        vpip=0.85,
        pfr=0.70,
        aggression_factor=8.0,
        all_in_frequency=0.45,
    )


def _passive_stats(hands: int = 100) -> AggregatedOpponentStats:
    return AggregatedOpponentStats(
        hands_observed=hands,
        vpip=0.30,
        pfr=0.10,
        aggression_factor=1.0,
        all_in_frequency=0.0,
    )


# ── should_apply_value_override ──────────────────────────────────────────


class TestShouldApplyValueOverride:
    """Gating conditions for the value override."""

    def test_fires_with_strong_and_maniac(self):
        assert should_apply_value_override(
            stats=_maniac_stats(),
            hand_strength=HandStrengthClass.NUTS.value,
            decision_context=DecisionContext(facing_all_in=True),
            adaptation_bias=0.85,
        )

    def test_skips_when_hand_not_strong(self):
        """Marginal/weak hands fall through to existing offsets."""
        assert not should_apply_value_override(
            stats=_maniac_stats(),
            hand_strength='medium_made',
            decision_context=DecisionContext(facing_all_in=True),
            adaptation_bias=0.85,
        )

    def test_skips_when_opponent_not_aggressive(self):
        """Strong hand + passive opp → table handles it normally."""
        assert not should_apply_value_override(
            stats=_passive_stats(),
            hand_strength=HandStrengthClass.NUTS.value,
            decision_context=DecisionContext(facing_all_in=True),
            adaptation_bias=0.85,
        )

    def test_skips_when_cold_start(self):
        """Below MIN_HANDS_DEFAULT, no override (low confidence)."""
        assert not should_apply_value_override(
            stats=_maniac_stats(hands=MIN_HANDS_DEFAULT - 1),
            hand_strength=HandStrengthClass.NUTS.value,
            decision_context=DecisionContext(facing_all_in=True),
            adaptation_bias=0.85,
        )

    def test_skips_at_gating_floor(self):
        """adaptation_bias × tilt_factor at the floor → no override."""
        assert not should_apply_value_override(
            stats=_maniac_stats(),
            hand_strength=HandStrengthClass.NUTS.value,
            decision_context=DecisionContext(facing_all_in=True),
            adaptation_bias=GATING_FLOOR,
        )

    def test_skips_when_tilt_zero(self):
        """Heavy tilt (tilt_factor=0) suppresses override."""
        assert not should_apply_value_override(
            stats=_maniac_stats(),
            hand_strength=HandStrengthClass.NUTS.value,
            decision_context=DecisionContext(facing_all_in=True),
            adaptation_bias=0.85,
            tilt_factor=0.0,
        )

    def test_strong_made_qualifies(self):
        assert should_apply_value_override(
            stats=_maniac_stats(),
            hand_strength=HandStrengthClass.STRONG_MADE.value,
            decision_context=DecisionContext(facing_big_bet=True),
            adaptation_bias=0.85,
        )

    def test_strong_preflop_qualifies(self):
        assert should_apply_value_override(
            stats=_maniac_stats(),
            hand_strength=HandStrengthClass.STRONG.value,
            decision_context=DecisionContext(is_preflop=True),
            adaptation_bias=0.85,
        )

    def test_not_strong_skips(self):
        assert not should_apply_value_override(
            stats=_maniac_stats(),
            hand_strength=HandStrengthClass.NOT_STRONG.value,
            decision_context=DecisionContext(facing_all_in=True),
            adaptation_bias=0.85,
        )


# ── compute_value_override_strategy ──────────────────────────────────────


class TestComputeValueOverrideFacingAllIn:
    """Facing-all-in → 100% call (or 100% jam if no call)."""

    def test_with_call_available(self):
        s = StrategyProfile(action_probabilities={'fold': 0.5, 'call': 0.5})
        result, _trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(facing_all_in=True),
            hand_strength=HandStrengthClass.NUTS.value,
        )
        assert result.action_probabilities == {'call': 1.0}

    def test_with_only_jam_available(self):
        s = StrategyProfile(action_probabilities={'fold': 0.5, 'jam': 0.5})
        result, _trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(facing_all_in=True),
            hand_strength=HandStrengthClass.NUTS.value,
        )
        assert result.action_probabilities == {'jam': 1.0}

    def test_no_call_no_jam_falls_back(self):
        s = StrategyProfile(action_probabilities={'fold': 1.0})
        result, _trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(facing_all_in=True),
            hand_strength=HandStrengthClass.NUTS.value,
        )
        # Pathological state — leave the strategy alone
        assert result is s


class TestComputeValueOverrideFacingBet:
    """Facing any non-all-in bet → 50% call, 50% raise-like."""

    def test_call_and_one_raise_split_evenly(self):
        s = StrategyProfile(
            action_probabilities={
                'fold': 0.5,
                'call': 0.3,
                'raise_3bb': 0.2,
            }
        )
        result, _trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(facing_big_bet=True),
            hand_strength=HandStrengthClass.STRONG_MADE.value,
        )
        assert result.action_probabilities['call'] == pytest.approx(0.5)
        assert result.action_probabilities['raise_3bb'] == pytest.approx(0.5)
        assert 'fold' not in result.action_probabilities

    def test_call_and_multiple_raises_split_evenly(self):
        s = StrategyProfile(
            action_probabilities={
                'fold': 0.4,
                'call': 0.2,
                'raise_3bb': 0.2,
                'raise_67': 0.2,
            }
        )
        result, _trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(facing_big_bet=True),
            hand_strength=HandStrengthClass.STRONG_MADE.value,
        )
        # Two raise actions split the 0.5 raise mass
        assert result.action_probabilities['call'] == pytest.approx(0.5)
        assert result.action_probabilities['raise_3bb'] == pytest.approx(0.25)
        assert result.action_probabilities['raise_67'] == pytest.approx(0.25)

    def test_only_call_when_no_raise_available(self):
        s = StrategyProfile(action_probabilities={'fold': 0.7, 'call': 0.3})
        result, _trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(facing_big_bet=True),
            hand_strength=HandStrengthClass.STRONG_MADE.value,
        )
        assert result.action_probabilities == {'call': 1.0}


class TestComputeValueOverrideOpenSpot:
    """Open spot → raise-heavy, scaled by hand class."""

    def test_nuts_raises_95_percent(self):
        s = StrategyProfile(
            action_probabilities={
                'check': 0.5,
                'bet_67': 0.5,
            }
        )
        result, _trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(is_preflop=False),
            hand_strength=HandStrengthClass.NUTS.value,
        )
        assert result.action_probabilities['bet_67'] == pytest.approx(0.95)
        assert result.action_probabilities['check'] == pytest.approx(0.05)

    def test_strong_made_raises_80_percent(self):
        s = StrategyProfile(
            action_probabilities={
                'check': 0.5,
                'bet_67': 0.5,
            }
        )
        result, _trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(is_preflop=False),
            hand_strength=HandStrengthClass.STRONG_MADE.value,
        )
        assert result.action_probabilities['bet_67'] == pytest.approx(0.80)
        assert result.action_probabilities['check'] == pytest.approx(0.20)

    def test_strong_preflop_raises_90_percent(self):
        s = StrategyProfile(
            action_probabilities={
                'check': 0.5,
                'raise_3bb': 0.5,
            }
        )
        result, _trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(is_preflop=True),
            hand_strength=HandStrengthClass.STRONG.value,
        )
        assert result.action_probabilities['raise_3bb'] == pytest.approx(0.90)
        assert result.action_probabilities['check'] == pytest.approx(0.10)

    def test_multiple_raises_split_evenly(self):
        s = StrategyProfile(
            action_probabilities={
                'check': 0.5,
                'bet_67': 0.3,
                'bet_100': 0.2,
            }
        )
        result, _trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(is_preflop=False),
            hand_strength=HandStrengthClass.NUTS.value,
        )
        # 0.95 raise mass split between two raises
        assert result.action_probabilities['bet_67'] == pytest.approx(0.475)
        assert result.action_probabilities['bet_100'] == pytest.approx(0.475)
        assert result.action_probabilities['check'] == pytest.approx(0.05)

    def test_call_used_when_no_check(self):
        """If 'check' isn't available but 'call' is, use call for passive mass."""
        s = StrategyProfile(
            action_probabilities={
                'call': 0.5,
                'raise_3bb': 0.5,
            }
        )
        result, _trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(is_preflop=True),
            hand_strength=HandStrengthClass.STRONG.value,
        )
        assert result.action_probabilities['raise_3bb'] == pytest.approx(0.90)
        assert result.action_probabilities['call'] == pytest.approx(0.10)


class TestComputeValueOverrideGeneral:
    """Cross-cutting invariants."""

    def test_renormalizes_to_one(self):
        s = StrategyProfile(
            action_probabilities={
                'fold': 0.4,
                'call': 0.4,
                'raise_3bb': 0.2,
            }
        )
        result, _trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(facing_big_bet=True),
            hand_strength=HandStrengthClass.STRONG_MADE.value,
        )
        total = sum(result.action_probabilities.values())
        assert total == pytest.approx(1.0)

    def test_does_not_invent_new_actions(self):
        """Output dist only contains action labels from the input strategy."""
        s = StrategyProfile(
            action_probabilities={
                'fold': 0.5,
                'call': 0.3,
                'raise_3bb': 0.2,
            }
        )
        result, _trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(facing_big_bet=True),
            hand_strength=HandStrengthClass.STRONG_MADE.value,
        )
        # Every key in result must be in input.
        for action in result.action_probabilities:
            assert action in s.action_probabilities

    def test_fold_excluded_from_override_output(self):
        """Strong hand vs aggressor never folds — fold mass is zero."""
        s = StrategyProfile(
            action_probabilities={
                'fold': 0.8,
                'call': 0.2,
            }
        )
        result, _trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(facing_big_bet=True),
            hand_strength=HandStrengthClass.STRONG_MADE.value,
        )
        # No fold in output
        assert (
            'fold' not in result.action_probabilities
            or result.action_probabilities.get('fold', 0.0) == 0.0
        )
