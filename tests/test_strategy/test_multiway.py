"""Tests for multiway pot heuristic adjustments."""

import pytest

from poker.strategy.multiway import (
    apply_multiway_adjustment,
    _bluff_mult,
    _check_mult,
)
from poker.strategy.strategy_profile import StrategyProfile


class TestBluffMult:
    """Tests for bluff frequency multiplier calculation."""

    def test_ip_3_players(self):
        assert _bluff_mult(3, 'IP') == pytest.approx(0.5)

    def test_ip_4_players(self):
        assert _bluff_mult(4, 'IP') == pytest.approx(0.4)

    def test_ip_5_players(self):
        assert _bluff_mult(5, 'IP') == pytest.approx(0.3)

    def test_ip_clamps_at_floor(self):
        # 8 players: 0.5 + (8-3)*-0.1 = 0.0 → clamped to 0.1
        assert _bluff_mult(8, 'IP') == pytest.approx(0.1)

    def test_oop_3_players(self):
        assert _bluff_mult(3, 'OOP') == pytest.approx(0.3)

    def test_oop_4_players(self):
        assert _bluff_mult(4, 'OOP') == pytest.approx(0.2)

    def test_oop_clamps_at_floor(self):
        # 6 players: 0.3 + (6-3)*-0.1 = 0.0 → clamped to 0.1
        assert _bluff_mult(6, 'OOP') == pytest.approx(0.1)


class TestCheckMult:
    """Tests for check frequency multiplier."""

    def test_ip(self):
        assert _check_mult('IP') == pytest.approx(1.3)

    def test_oop(self):
        assert _check_mult('OOP') == pytest.approx(1.5)


class TestApplyMultiwayAdjustment:
    """Tests for apply_multiway_adjustment()."""

    def _make_strategy(self, probs):
        return StrategyProfile(action_probabilities=probs)

    def test_heads_up_unchanged(self):
        """2 players returns strategy unchanged."""
        strategy = self._make_strategy({
            'fold': 0.2, 'check': 0.3, 'bet_half': 0.5,
        })
        result = apply_multiway_adjustment(strategy, 2, 'IP')
        assert result is strategy

    def test_one_player_unchanged(self):
        """1 player returns strategy unchanged."""
        strategy = self._make_strategy({'check': 0.5, 'bet_half': 0.5})
        result = apply_multiway_adjustment(strategy, 1, 'IP')
        assert result is strategy

    def test_three_way_ip_reduces_aggression(self):
        """3-way IP: aggressive actions scaled by 0.5, check by 1.3."""
        strategy = self._make_strategy({
            'fold': 0.2, 'check': 0.3, 'call': 0.2, 'bet_half': 0.3,
        })
        result = apply_multiway_adjustment(strategy, 3, 'IP')
        probs = result.action_probabilities

        # bet_half should be reduced relative to fold/call
        assert probs['bet_half'] < 0.3
        # check should be boosted relative to fold/call
        assert probs['check'] > probs['fold']
        # Renormalized to 1.0
        assert sum(probs.values()) == pytest.approx(1.0)

    def test_three_way_oop_more_passive(self):
        """3-way OOP is more passive than IP (lower bluff mult, higher check mult)."""
        strategy = self._make_strategy({
            'fold': 0.2, 'check': 0.3, 'call': 0.2, 'raise_3bb': 0.3,
        })
        ip_result = apply_multiway_adjustment(strategy, 3, 'IP')
        oop_result = apply_multiway_adjustment(strategy, 3, 'OOP')

        # OOP should have less aggression than IP
        assert oop_result.action_probabilities['raise_3bb'] < ip_result.action_probabilities['raise_3bb']
        # OOP should have more checking than IP
        assert oop_result.action_probabilities['check'] > ip_result.action_probabilities['check']

    def test_more_players_less_aggression(self):
        """More opponents = less aggression."""
        strategy = self._make_strategy({
            'fold': 0.2, 'check': 0.3, 'bet_half': 0.5,
        })
        result_3 = apply_multiway_adjustment(strategy, 3, 'IP')
        result_5 = apply_multiway_adjustment(strategy, 5, 'IP')

        assert result_5.action_probabilities['bet_half'] < result_3.action_probabilities['bet_half']

    def test_renormalization(self):
        """Result probabilities always sum to 1.0."""
        strategy = self._make_strategy({
            'fold': 0.1, 'check': 0.2, 'call': 0.1,
            'raise_3bb': 0.3, 'jam': 0.3,
        })
        for n in [3, 4, 5, 6]:
            for pos in ['IP', 'OOP']:
                result = apply_multiway_adjustment(strategy, n, pos)
                assert sum(result.action_probabilities.values()) == pytest.approx(1.0)

    def test_jam_treated_as_aggressive(self):
        """Jam action is reduced like other aggressive actions."""
        strategy = self._make_strategy({
            'fold': 0.2, 'check': 0.3, 'jam': 0.5,
        })
        result = apply_multiway_adjustment(strategy, 3, 'IP')
        # jam's share of the pie should shrink
        assert result.action_probabilities['jam'] < 0.5

    def test_call_unchanged_before_renorm(self):
        """Call probability stays at its raw value (only renorm changes it)."""
        strategy = self._make_strategy({
            'call': 0.5, 'bet_half': 0.5,
        })
        result = apply_multiway_adjustment(strategy, 3, 'IP')
        # Raw: call=0.5, bet_half=0.5*0.5=0.25 → total=0.75
        # Renorm: call=0.5/0.75, bet_half=0.25/0.75
        assert result.action_probabilities['call'] == pytest.approx(0.5 / 0.75)
        assert result.action_probabilities['bet_half'] == pytest.approx(0.25 / 0.75)

    def test_all_zero_probs_handled(self):
        """All-zero distribution doesn't crash."""
        strategy = self._make_strategy({'fold': 0.0, 'check': 0.0})
        result = apply_multiway_adjustment(strategy, 3, 'IP')
        # All zeros stay zero
        assert all(v == 0.0 for v in result.action_probabilities.values())

    def test_preserves_action_keys(self):
        """Result has the same action keys as input."""
        strategy = self._make_strategy({
            'fold': 0.1, 'check': 0.2, 'call': 0.3, 'raise_2.5bb': 0.4,
        })
        result = apply_multiway_adjustment(strategy, 4, 'OOP')
        assert set(result.action_probabilities.keys()) == set(strategy.action_probabilities.keys())
