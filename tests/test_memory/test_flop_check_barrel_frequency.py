"""Phase B Item 4 tests for `flop_check_then_barrel_rate` tracking.

Verifies the end-to-end pipeline:
  - CbetDetector emits flop-check-then-barrel events when a player
    checks flop OOP, the flop goes check-through, and that player's
    first turn action is the barrel attempt
  - OpponentTendencies.update_flop_check_barrel_attempt drives the
    derived `flop_check_then_barrel_rate` math
  - AggregatedOpponentStats propagates the new field through all
    construction paths
  - Correct exclusion when the flop did NOT go check-through (any
    flop bet kills the opportunity)
"""

import pytest

from poker.memory.cbet_detector import CbetDetector
from poker.memory.opponent_model import (
    OpponentTendencies,
    _build_aggregate_from_single,
    _build_aggregate_from_multi,
)
from poker.strategy.exploitation import (
    AggregatedOpponentStats,
    OpponentSpot,
    aggregate_from_spots,
)


# ── CbetDetector emission tests ────────────────────────────────────

class TestFlopCheckBarrelDetection:
    def test_check_through_flop_then_turn_bet_emits_true(self):
        """Classic trap-bait: BB checks flop OOP, SB checks back, BB bets turn."""
        det = CbetDetector()
        det.record_action('SB', 'raise', 'PRE_FLOP', ['SB', 'BB'])
        det.record_action('BB', 'call', 'PRE_FLOP', ['SB', 'BB'])
        # Flop: BB checks, SB checks back (check-through)
        det.record_action('BB', 'check', 'FLOP', ['SB', 'BB'])
        det.record_action('SB', 'check', 'FLOP', ['SB', 'BB'])
        # Turn: BB barrels
        det.record_action('BB', 'raise', 'TURN', ['SB', 'BB'])

        assert det.consume_flop_check_barrel_attempt_events() == [('BB', True)]

    def test_check_through_flop_then_turn_check_emits_false(self):
        """BB checks flop, SB checks back, BB checks turn — declined barrel."""
        det = CbetDetector()
        det.record_action('SB', 'raise', 'PRE_FLOP', ['SB', 'BB'])
        det.record_action('BB', 'call', 'PRE_FLOP', ['SB', 'BB'])
        det.record_action('BB', 'check', 'FLOP', ['SB', 'BB'])
        det.record_action('SB', 'check', 'FLOP', ['SB', 'BB'])
        det.record_action('BB', 'check', 'TURN', ['SB', 'BB'])

        assert det.consume_flop_check_barrel_attempt_events() == [('BB', False)]

    def test_flop_with_bet_does_not_emit(self):
        """If anyone bet the flop, no flop-check-then-barrel opportunity."""
        det = CbetDetector()
        det.record_action('SB', 'raise', 'PRE_FLOP', ['SB', 'BB'])
        det.record_action('BB', 'call', 'PRE_FLOP', ['SB', 'BB'])
        det.record_action('BB', 'check', 'FLOP', ['SB', 'BB'])
        det.record_action('SB', 'raise', 'FLOP', ['SB', 'BB'])  # SB cbets
        det.record_action('BB', 'call', 'FLOP', ['SB', 'BB'])
        det.record_action('BB', 'check', 'TURN', ['SB', 'BB'])

        # No flop-check-then-barrel event — flop didn't go check-through
        assert det.consume_flop_check_barrel_attempt_events() == []

    def test_donk_turn_excludes_attribution(self):
        """If BB checks flop, SB checks, then SB donks turn before BB acts,
        BB doesn't get a flop-check-barrel attempt (they were beaten to it)."""
        # In standard poker order BB always acts first on turn (OOP), so
        # this scenario is impossible in practice — included for parity
        # with the regular barrel detector's donk-bet guard.
        det = CbetDetector()
        det.record_action('SB', 'raise', 'PRE_FLOP', ['SB', 'BB'])
        det.record_action('BB', 'call', 'PRE_FLOP', ['SB', 'BB'])
        det.record_action('BB', 'check', 'FLOP', ['SB', 'BB'])
        det.record_action('SB', 'check', 'FLOP', ['SB', 'BB'])
        # Hypothetical out-of-order: SB acts first on turn with a bet
        det.record_action('SB', 'raise', 'TURN', ['SB', 'BB'])
        # BB then calls — no clean barrel-first opportunity for BB
        det.record_action('BB', 'call', 'TURN', ['SB', 'BB'])

        assert det.consume_flop_check_barrel_attempt_events() == []

    def test_first_checker_attribution_in_multiway(self):
        """Multi-way: first voluntary flop checker (the player furthest OOP)
        is attributed. UTG checks first, MP checks, BTN checks, UTG bets turn."""
        det = CbetDetector()
        det.record_action('BTN', 'raise', 'PRE_FLOP', ['UTG', 'MP', 'BTN'])
        det.record_action('UTG', 'call', 'PRE_FLOP', ['UTG', 'MP', 'BTN'])
        det.record_action('MP', 'call', 'PRE_FLOP', ['UTG', 'MP', 'BTN'])
        # Flop check-through
        det.record_action('UTG', 'check', 'FLOP', ['UTG', 'MP', 'BTN'])
        det.record_action('MP', 'check', 'FLOP', ['UTG', 'MP', 'BTN'])
        det.record_action('BTN', 'check', 'FLOP', ['UTG', 'MP', 'BTN'])
        # UTG (the first flop checker) bets turn
        det.record_action('UTG', 'raise', 'TURN', ['UTG', 'MP', 'BTN'])

        assert det.consume_flop_check_barrel_attempt_events() == [('UTG', True)]

    def test_reset_clears_state(self):
        det = CbetDetector()
        det.record_action('SB', 'raise', 'PRE_FLOP', ['SB', 'BB'])
        det.record_action('BB', 'call', 'PRE_FLOP', ['SB', 'BB'])
        det.record_action('BB', 'check', 'FLOP', ['SB', 'BB'])
        det.record_action('SB', 'check', 'FLOP', ['SB', 'BB'])

        det.reset_for_new_hand()
        assert det._first_flop_checker is None
        assert not det._flop_check_barrel_attempt_recorded
        assert det._pending_flop_check_barrel_attempts == []


# ── OpponentTendencies math ────────────────────────────────────────

class TestFlopCheckBarrelTendenciesMath:
    def test_neutral_prior_until_first_opportunity(self):
        t = OpponentTendencies()
        assert t.flop_check_then_barrel_rate == 0.5

    def test_update_flop_check_barrel_attempt_True(self):
        t = OpponentTendencies()
        t.update_flop_check_barrel_attempt(True)
        assert t.flop_check_then_barrel_rate == 1.0
        assert t._flop_check_barrel_count == 1
        assert t._flop_check_barrel_opportunity_count == 1

    def test_update_flop_check_barrel_attempt_mix(self):
        t = OpponentTendencies()
        t.update_flop_check_barrel_attempt(True)
        t.update_flop_check_barrel_attempt(True)
        t.update_flop_check_barrel_attempt(False)
        assert t.flop_check_then_barrel_rate == pytest.approx(2/3)
        assert t._flop_check_barrel_opportunity_count == 3


# ── Aggregator surface ─────────────────────────────────────────────

class TestAggregatorSurface:
    def test_aggregated_stats_defaults(self):
        s = AggregatedOpponentStats()
        assert s.flop_check_then_barrel_rate == 0.5
        assert s.flop_check_barrel_opportunities == 0

    def test_build_aggregate_from_single_propagates(self):
        t = OpponentTendencies(hands_observed=20)
        t._flop_check_barrel_count = 7
        t._flop_check_barrel_opportunity_count = 10
        t._recalculate_stats()
        agg = _build_aggregate_from_single(t)
        assert agg.flop_check_then_barrel_rate == pytest.approx(0.7)
        assert agg.flop_check_barrel_opportunities == 10

    def test_build_aggregate_from_multi_averages_rates_min_counters(self):
        t1 = OpponentTendencies(hands_observed=20)
        t1._flop_check_barrel_count = 8
        t1._flop_check_barrel_opportunity_count = 10
        t1._recalculate_stats()
        t2 = OpponentTendencies(hands_observed=20)
        t2._flop_check_barrel_count = 4
        t2._flop_check_barrel_opportunity_count = 8
        t2._recalculate_stats()
        agg = _build_aggregate_from_multi([t1, t2])
        # avg of 0.8 and 0.5 = 0.65
        assert agg.flop_check_then_barrel_rate == pytest.approx(0.65)
        # min of 10 and 8 = 8
        assert agg.flop_check_barrel_opportunities == 8

    def test_aggregate_from_spots_propagates(self):
        stats = AggregatedOpponentStats(
            hands_observed=50,
            flop_check_then_barrel_rate=0.72,
            flop_check_barrel_opportunities=18,
        )
        spot = OpponentSpot(
            name='TrapBait', stats=stats,
            is_active=True, is_aggressor=False, is_all_in=False,
            current_bet=0, stack=10000,
            committed_this_street=0, committed_this_hand=100,
        )
        agg = aggregate_from_spots([spot])
        assert agg.flop_check_then_barrel_rate == 0.72
        assert agg.flop_check_barrel_opportunities == 18
