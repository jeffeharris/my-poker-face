"""Phase 8.1a follow-up: cbet_attempt_rate/postflop_seen_as_pfr_count surface tests.

These fields exist on OpponentTendencies (populated by CbetDetector
events) but were not propagated through the aggregator path until now.
Without surfacing, downstream rules consuming AggregatedOpponentStats
would always see the neutral defaults (0.5 rate, 0 count) regardless
of observed barreling behavior.

Verifies all four aggregator entry points propagate the fields:
  - opponent_model._build_aggregate_from_single (single-opponent path)
  - opponent_model._build_aggregate_from_multi (multi-opponent path)
  - exploitation.aggregate_from_spots (single-opponent + 60%-dominant + multi)
  - exploitation._copy_stats (used by aggregate_from_spots branches)
"""

import pytest

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


def _tendencies(**kwargs) -> OpponentTendencies:
    """Build OpponentTendencies, overriding internal counters as needed.

    The cbet_attempt_rate field is derived from internal counters via
    _recalculate_stats, so callers set _cbet_attempt_count and
    _postflop_seen_as_pfr_count and we recompute.
    """
    cbet_attempt_count = kwargs.pop('_cbet_attempt_count', 0)
    pfr_seen_count = kwargs.pop('_postflop_seen_as_pfr_count', 0)
    t = OpponentTendencies(**kwargs)
    t._cbet_attempt_count = cbet_attempt_count
    t._postflop_seen_as_pfr_count = pfr_seen_count
    t._recalculate_stats()
    return t


def _stats(**kwargs) -> AggregatedOpponentStats:
    base = dict(
        hands_observed=50,
        vpip=0.5,
        pfr=0.5,
        aggression_factor=2.0,
        all_in_frequency=0.0,
        fold_to_cbet=0.5,
        cbet_faced_count=10,
        cbet_attempt_rate=0.5,
        postflop_seen_as_pfr_count=10,
        aggression_factor_postflop=2.0,
        all_in_per_facing_bet=0.0,
        facing_bet_opportunities=0,
        postflop_jam_open_rate=0.0,
        postflop_open_opportunities=0,
    )
    base.update(kwargs)
    return AggregatedOpponentStats(**base)


def _spot(
    name: str, stats: AggregatedOpponentStats, *, committed_this_hand: int = 100
) -> OpponentSpot:
    return OpponentSpot(
        name=name,
        stats=stats,
        is_active=True,
        is_aggressor=False,
        is_all_in=False,
        current_bet=0,
        stack=10000,
        committed_this_street=0,
        committed_this_hand=committed_this_hand,
    )


# ── Dataclass defaults ──────────────────────────────────────────────────


class TestDefaults:
    def test_neutral_defaults(self):
        s = AggregatedOpponentStats()
        assert s.cbet_attempt_rate == 0.5
        assert s.postflop_seen_as_pfr_count == 0


# ── opponent_model.py aggregators ───────────────────────────────────────


class TestBuildFromSingle:
    def test_propagates_cbet_attempt_rate_from_tendencies(self):
        t = _tendencies(
            hands_observed=20,
            _cbet_attempt_count=17,
            _postflop_seen_as_pfr_count=20,
        )
        # Sanity: tendencies computed the rate correctly
        assert t.cbet_attempt_rate == pytest.approx(0.85)

        agg = _build_aggregate_from_single(t)
        assert agg.cbet_attempt_rate == pytest.approx(0.85)
        assert agg.postflop_seen_as_pfr_count == 20

    def test_zero_opportunities_uses_neutral(self):
        t = _tendencies(hands_observed=5)
        agg = _build_aggregate_from_single(t)
        assert agg.cbet_attempt_rate == 0.5
        assert agg.postflop_seen_as_pfr_count == 0


class TestBuildFromMulti:
    def test_averages_rates_min_counter(self):
        t1 = _tendencies(
            hands_observed=20,
            _cbet_attempt_count=18,
            _postflop_seen_as_pfr_count=20,
        )
        t2 = _tendencies(
            hands_observed=30,
            _cbet_attempt_count=15,
            _postflop_seen_as_pfr_count=30,
        )
        agg = _build_aggregate_from_multi([t1, t2])
        # Rates averaged: (0.9 + 0.5) / 2 = 0.7
        assert agg.cbet_attempt_rate == pytest.approx(0.7)
        # Counter uses MIN (limiting confidence)
        assert agg.postflop_seen_as_pfr_count == 20


# ── exploitation.py spot-based aggregator ───────────────────────────────


class TestAggregateFromSpots:
    def test_single_opponent_verbatim(self):
        spot = _spot(
            'Villain',
            _stats(
                cbet_attempt_rate=0.92,
                postflop_seen_as_pfr_count=33,
            ),
        )
        agg = aggregate_from_spots([spot])
        assert agg.cbet_attempt_rate == 0.92
        assert agg.postflop_seen_as_pfr_count == 33

    def test_dominant_opponent_verbatim(self):
        """60% rule fires — dominant opponent's stats forward verbatim."""
        dominant = _spot(
            'Dominant',
            _stats(
                cbet_attempt_rate=0.80,
                postflop_seen_as_pfr_count=40,
            ),
            committed_this_hand=1000,
        )
        other = _spot(
            'Other',
            _stats(
                cbet_attempt_rate=0.30,
                postflop_seen_as_pfr_count=10,
            ),
            committed_this_hand=100,
        )
        agg = aggregate_from_spots([dominant, other])
        # Dominant committed 1000 / 1100 = 91% > 60% threshold
        assert agg.cbet_attempt_rate == 0.80
        assert agg.postflop_seen_as_pfr_count == 40

    def test_multi_opponent_averaged_with_min_counter(self):
        a = _spot(
            'A',
            _stats(
                cbet_attempt_rate=0.80,
                postflop_seen_as_pfr_count=25,
            ),
            committed_this_hand=100,
        )
        b = _spot(
            'B',
            _stats(
                cbet_attempt_rate=0.40,
                postflop_seen_as_pfr_count=15,
            ),
            committed_this_hand=100,
        )
        # Equal commitments — 60% rule doesn't fire
        agg = aggregate_from_spots([a, b])
        assert agg.cbet_attempt_rate == pytest.approx(0.60)
        assert agg.postflop_seen_as_pfr_count == 15
