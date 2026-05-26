"""Phase B Item 1 tests for barrel_frequency tracking.

Verifies the end-to-end pipeline:
  - CbetDetector emits barrel-attempt and third-barrel-attempt events
    in the right conditions (PFR cbet → called → next-street decision)
  - OpponentTendencies.update_barrel_attempt + update_third_barrel_attempt
    drive the derived `barrel_frequency` / `third_barrel_frequency` math
  - Aggregator surface (AggregatedOpponentStats) propagates the fields
    from OpponentTendencies via all the construction paths
  - The "clean opportunity" gate excludes donk-bet scenarios
"""

import pytest

from poker.memory.cbet_detector import CbetDetector
from poker.memory.opponent_model import (
    OpponentTendencies,
    _build_aggregate_from_multi,
    _build_aggregate_from_single,
)
from poker.strategy.exploitation import (
    AggregatedOpponentStats,
    OpponentSpot,
    aggregate_from_spots,
)

# ── CbetDetector emission tests ────────────────────────────────────


class TestBarrelDetection:
    def test_full_cbet_barrel_third_barrel_sequence(self):
        """Classic triple-barrel: PFR cbets all three streets after each call."""
        det = CbetDetector()
        # Preflop: Maniac raises, Hero calls
        det.record_action('Maniac', 'raise', 'PRE_FLOP', ['Hero', 'Maniac'])
        det.record_action('Hero', 'call', 'PRE_FLOP', ['Hero', 'Maniac'])
        # Flop: Hero checks OOP, Maniac cbets, Hero calls
        det.record_action('Hero', 'check', 'FLOP', ['Hero', 'Maniac'])
        det.record_action('Maniac', 'raise', 'FLOP', ['Hero', 'Maniac'])
        det.record_action('Hero', 'call', 'FLOP', ['Hero', 'Maniac'])
        # Turn: Hero checks, Maniac barrels, Hero calls
        det.record_action('Hero', 'check', 'TURN', ['Hero', 'Maniac'])
        det.record_action('Maniac', 'raise', 'TURN', ['Hero', 'Maniac'])
        det.record_action('Hero', 'call', 'TURN', ['Hero', 'Maniac'])
        # River: Hero checks, Maniac third-barrels
        det.record_action('Hero', 'check', 'RIVER', ['Hero', 'Maniac'])
        det.record_action('Maniac', 'raise', 'RIVER', ['Hero', 'Maniac'])

        assert det.consume_pfr_attempt_events() == [('Maniac', True)]
        assert det.consume_barrel_attempt_events() == [('Maniac', True)]
        assert det.consume_third_barrel_attempt_events() == [('Maniac', True)]

    def test_turn_check_after_called_cbet_emits_false(self):
        """PFR gave up on the turn — barrel attempt = False."""
        det = CbetDetector()
        det.record_action('Maniac', 'raise', 'PRE_FLOP', ['Hero', 'Maniac'])
        det.record_action('Hero', 'call', 'PRE_FLOP', ['Hero', 'Maniac'])
        det.record_action('Hero', 'check', 'FLOP', ['Hero', 'Maniac'])
        det.record_action('Maniac', 'raise', 'FLOP', ['Hero', 'Maniac'])
        det.record_action('Hero', 'call', 'FLOP', ['Hero', 'Maniac'])
        det.record_action('Hero', 'check', 'TURN', ['Hero', 'Maniac'])
        det.record_action('Maniac', 'check', 'TURN', ['Hero', 'Maniac'])

        assert det.consume_barrel_attempt_events() == [('Maniac', False)]
        # No third barrel event — PFR never barreled turn, so no
        # opportunity for a third barrel.
        assert det.consume_third_barrel_attempt_events() == []

    def test_no_barrel_event_when_cbet_was_folded_to(self):
        """All players folded to the cbet — no barrel opportunity exists."""
        det = CbetDetector()
        det.record_action('Maniac', 'raise', 'PRE_FLOP', ['Hero', 'Maniac'])
        det.record_action('Hero', 'call', 'PRE_FLOP', ['Hero', 'Maniac'])
        det.record_action('Hero', 'check', 'FLOP', ['Hero', 'Maniac'])
        det.record_action('Maniac', 'raise', 'FLOP', ['Hero', 'Maniac'])
        det.record_action('Hero', 'fold', 'FLOP', ['Hero', 'Maniac'])
        # Hand over. No barrel event.

        assert det.consume_barrel_attempt_events() == []

    def test_no_barrel_event_when_pfr_never_cbet(self):
        """PFR checked the flop — no cbet, no barrel-opportunity tracking."""
        det = CbetDetector()
        det.record_action('Maniac', 'raise', 'PRE_FLOP', ['Hero', 'Maniac'])
        det.record_action('Hero', 'call', 'PRE_FLOP', ['Hero', 'Maniac'])
        det.record_action('Hero', 'check', 'FLOP', ['Hero', 'Maniac'])
        det.record_action('Maniac', 'check', 'FLOP', ['Hero', 'Maniac'])
        det.record_action('Hero', 'check', 'TURN', ['Hero', 'Maniac'])
        det.record_action('Maniac', 'raise', 'TURN', ['Hero', 'Maniac'])

        # PFR-attempt event fires (declined c-bet)
        assert det.consume_pfr_attempt_events() == [('Maniac', False)]
        # No barrel event — _cbet_called was never True
        assert det.consume_barrel_attempt_events() == []

    def test_donk_bet_excludes_barrel_attempt(self):
        """If hero donks the turn before PFR acts, PFR's response isn't a
        clean barrel decision — exclude from rate."""
        det = CbetDetector()
        det.record_action('Maniac', 'raise', 'PRE_FLOP', ['Hero', 'Maniac'])
        det.record_action('Hero', 'call', 'PRE_FLOP', ['Hero', 'Maniac'])
        det.record_action('Hero', 'check', 'FLOP', ['Hero', 'Maniac'])
        det.record_action('Maniac', 'raise', 'FLOP', ['Hero', 'Maniac'])
        det.record_action('Hero', 'call', 'FLOP', ['Hero', 'Maniac'])
        # Hero donk-bets turn first
        det.record_action('Hero', 'raise', 'TURN', ['Hero', 'Maniac'])
        det.record_action('Maniac', 'call', 'TURN', ['Hero', 'Maniac'])

        # No barrel attempt event — PFR didn't have a clean turn decision
        assert det.consume_barrel_attempt_events() == []

    def test_third_barrel_skipped_when_turn_barrel_folded_to(self):
        """If turn barrel got folded to (didn't get called), no
        third-barrel opportunity."""
        det = CbetDetector()
        det.record_action('Maniac', 'raise', 'PRE_FLOP', ['Hero', 'Maniac'])
        det.record_action('Hero', 'call', 'PRE_FLOP', ['Hero', 'Maniac'])
        det.record_action('Hero', 'check', 'FLOP', ['Hero', 'Maniac'])
        det.record_action('Maniac', 'raise', 'FLOP', ['Hero', 'Maniac'])
        det.record_action('Hero', 'call', 'FLOP', ['Hero', 'Maniac'])
        det.record_action('Hero', 'check', 'TURN', ['Hero', 'Maniac'])
        det.record_action('Maniac', 'raise', 'TURN', ['Hero', 'Maniac'])
        det.record_action('Hero', 'fold', 'TURN', ['Hero', 'Maniac'])

        assert det.consume_barrel_attempt_events() == [('Maniac', True)]
        # Turn barrel was folded to → no third barrel opportunity
        assert det.consume_third_barrel_attempt_events() == []

    def test_reset_clears_barrel_state(self):
        det = CbetDetector()
        det.record_action('Maniac', 'raise', 'PRE_FLOP', ['Hero', 'Maniac'])
        det.record_action('Hero', 'call', 'PRE_FLOP', ['Hero', 'Maniac'])
        det.record_action('Hero', 'check', 'FLOP', ['Hero', 'Maniac'])
        det.record_action('Maniac', 'raise', 'FLOP', ['Hero', 'Maniac'])
        det.record_action('Hero', 'call', 'FLOP', ['Hero', 'Maniac'])
        # Drain to verify state was set
        det.consume_barrel_attempt_events()  # may be empty (no turn action yet)

        det.reset_for_new_hand()
        assert not det._cbet_called
        assert not det._barrel_attempt_recorded
        assert not det._turn_barrel_made
        assert det._pending_barrel_attempts == []


# ── OpponentTendencies math ────────────────────────────────────────


class TestBarrelTendenciesMath:
    def test_neutral_prior_until_first_opportunity(self):
        t = OpponentTendencies()
        assert t.barrel_frequency == 0.5
        assert t.third_barrel_frequency == 0.5

    def test_update_barrel_attempt_True(self):
        t = OpponentTendencies()
        t.update_barrel_attempt(True)
        assert t.barrel_frequency == 1.0
        assert t._barrel_count == 1
        assert t._barrel_opportunity_count == 1

    def test_update_barrel_attempt_mix(self):
        t = OpponentTendencies()
        t.update_barrel_attempt(True)
        t.update_barrel_attempt(True)
        t.update_barrel_attempt(False)
        assert t.barrel_frequency == pytest.approx(2 / 3)
        assert t._barrel_opportunity_count == 3

    def test_update_third_barrel_attempt_independent(self):
        t = OpponentTendencies()
        t.update_barrel_attempt(True)
        t.update_third_barrel_attempt(False)
        assert t.barrel_frequency == 1.0
        assert t.third_barrel_frequency == 0.0
        # Counters independent
        assert t._barrel_opportunity_count == 1
        assert t._third_barrel_opportunity_count == 1


# ── Aggregator surface ─────────────────────────────────────────────


class TestAggregatorSurface:
    def test_aggregated_stats_defaults(self):
        s = AggregatedOpponentStats()
        assert s.barrel_frequency == 0.5
        assert s.barrel_opportunities == 0
        assert s.third_barrel_frequency == 0.5
        assert s.third_barrel_opportunities == 0

    def test_build_aggregate_from_single_propagates(self):
        t = OpponentTendencies(hands_observed=20)
        t._barrel_count = 9
        t._barrel_opportunity_count = 10
        t._third_barrel_count = 4
        t._third_barrel_opportunity_count = 8
        t._recalculate_stats()
        agg = _build_aggregate_from_single(t)
        assert agg.barrel_frequency == pytest.approx(0.9)
        assert agg.barrel_opportunities == 10
        assert agg.third_barrel_frequency == pytest.approx(0.5)
        assert agg.third_barrel_opportunities == 8

    def test_build_aggregate_from_multi_averages_rates_min_counters(self):
        t1 = OpponentTendencies(hands_observed=20)
        t1._barrel_count = 10
        t1._barrel_opportunity_count = 10
        t1._recalculate_stats()
        t2 = OpponentTendencies(hands_observed=20)
        t2._barrel_count = 4
        t2._barrel_opportunity_count = 10
        t2._recalculate_stats()
        agg = _build_aggregate_from_multi([t1, t2])
        assert agg.barrel_frequency == pytest.approx(0.7)
        assert agg.barrel_opportunities == 10

    def test_aggregate_from_spots_propagates(self):
        stats = AggregatedOpponentStats(
            hands_observed=50,
            barrel_frequency=0.88,
            barrel_opportunities=25,
            third_barrel_frequency=0.6,
            third_barrel_opportunities=15,
        )
        spot = OpponentSpot(
            name='Villain',
            stats=stats,
            is_active=True,
            is_aggressor=False,
            is_all_in=False,
            current_bet=0,
            stack=10000,
            committed_this_street=0,
            committed_this_hand=100,
        )
        agg = aggregate_from_spots([spot])
        assert agg.barrel_frequency == 0.88
        assert agg.barrel_opportunities == 25
        assert agg.third_barrel_frequency == 0.6
        assert agg.third_barrel_opportunities == 15
