"""Phase 7.5 Step 0 tests for aggregate_from_spots() with new fields.

Verifies that 6.7a's aggregate_from_spots() correctly aggregates the
five new Phase 7.5 Step 0 fields on AggregatedOpponentStats:
  - aggression_factor_postflop (float, averaged)
  - all_in_per_facing_bet (float, averaged)
  - facing_bet_opportunities (int, MIN)
  - postflop_jam_open_rate (float, averaged)
  - postflop_open_opportunities (int, MIN)

Policy is intentionally consistent with legacy aggregator: float rate
fields use equal-weight average; sample counter fields use MIN. NOT
sample-weighted in v1 — see plan §"aggregation policy" for rationale.

Also verifies the single-opponent and 60%-dominant branches forward
all Phase 7.5 fields verbatim.
"""

import pytest

from poker.strategy.exploitation import (
    AggregatedOpponentStats,
    OpponentSpot,
    aggregate_from_spots,
)

# ── Fixtures ─────────────────────────────────────────────────────────────


def _stats(**kwargs) -> AggregatedOpponentStats:
    """Build AggregatedOpponentStats with sensible defaults + overrides."""
    base = dict(
        hands_observed=50,
        vpip=0.5,
        pfr=0.25,
        aggression_factor=1.5,
        all_in_frequency=0.05,
        fold_to_cbet=0.5,
        cbet_faced_count=10,
        # Phase 7.5 defaults
        aggression_factor_postflop=1.0,
        all_in_per_facing_bet=0.0,
        facing_bet_opportunities=0,
        postflop_jam_open_rate=0.0,
        postflop_open_opportunities=0,
    )
    base.update(kwargs)
    return AggregatedOpponentStats(**base)


def _spot(
    name: str,
    stats: AggregatedOpponentStats,
    *,
    is_active: bool = True,
    committed_this_hand: int = 0,
) -> OpponentSpot:
    return OpponentSpot(
        name=name,
        stats=stats,
        is_active=is_active,
        is_aggressor=False,
        is_all_in=False,
        current_bet=0,
        stack=10000,
        committed_this_street=0,
        committed_this_hand=committed_this_hand,
    )


# ── Single-opponent path: verbatim forwarding ────────────────────────────


class TestSingleOpponent:
    def test_phase_75_fields_forwarded_verbatim(self):
        stats = _stats(
            aggression_factor_postflop=5.5,
            all_in_per_facing_bet=0.25,
            facing_bet_opportunities=75,
            postflop_jam_open_rate=0.15,
            postflop_open_opportunities=40,
        )
        result = aggregate_from_spots([_spot('A', stats)])
        assert result.aggression_factor_postflop == 5.5
        assert result.all_in_per_facing_bet == 0.25
        assert result.facing_bet_opportunities == 75
        assert result.postflop_jam_open_rate == 0.15
        assert result.postflop_open_opportunities == 40


# ── 60%-dominant path: verbatim forwarding ───────────────────────────────


class TestDominantOpponent:
    def test_dominant_phase_75_fields_forwarded_verbatim(self):
        """When 60% rule fires, ALL fields (including 7.5) come from the
        dominant opponent — NOT averaged with the others."""
        dominant_stats = _stats(
            hands_observed=200,
            aggression_factor_postflop=7.0,  # Extreme
            all_in_per_facing_bet=0.40,
            facing_bet_opportunities=150,
            postflop_jam_open_rate=0.30,
            postflop_open_opportunities=100,
        )
        other_stats = _stats(
            hands_observed=80,
            aggression_factor_postflop=1.0,
            all_in_per_facing_bet=0.02,
            facing_bet_opportunities=20,
            postflop_jam_open_rate=0.01,
            postflop_open_opportunities=15,
        )
        spots = [
            _spot('Dom', dominant_stats, committed_this_hand=700),
            _spot('Other', other_stats, committed_this_hand=100),
            _spot('Third', other_stats, committed_this_hand=50),
        ]
        result = aggregate_from_spots(spots)
        # Dominant has >60% of committed money → its stats verbatim.
        assert result.aggression_factor_postflop == 7.0
        assert result.all_in_per_facing_bet == 0.40
        assert result.facing_bet_opportunities == 150
        assert result.postflop_jam_open_rate == 0.30
        assert result.postflop_open_opportunities == 100


# ── Weighted-average path: equal-weight averages + MIN sample counts ─────


class TestWeightedAverageNew:
    def test_phase_75_rates_equal_weight_averaged(self):
        """When no opponent dominates (60% rule doesn't fire), Phase 7.5
        rate fields are equal-weight averaged across opponents."""
        spots = [
            _spot(
                'A',
                _stats(
                    aggression_factor_postflop=6.0,
                    all_in_per_facing_bet=0.40,
                    postflop_jam_open_rate=0.30,
                ),
                committed_this_hand=300,
            ),
            _spot(
                'B',
                _stats(
                    aggression_factor_postflop=2.0,
                    all_in_per_facing_bet=0.10,
                    postflop_jam_open_rate=0.05,
                ),
                committed_this_hand=300,
            ),
            _spot(
                'C',
                _stats(
                    aggression_factor_postflop=1.0,
                    all_in_per_facing_bet=0.04,
                    postflop_jam_open_rate=0.01,
                ),
                committed_this_hand=300,
            ),
        ]
        result = aggregate_from_spots(spots)
        # Equal-weight averages
        assert result.aggression_factor_postflop == pytest.approx((6.0 + 2.0 + 1.0) / 3)
        assert result.all_in_per_facing_bet == pytest.approx((0.40 + 0.10 + 0.04) / 3)
        assert result.postflop_jam_open_rate == pytest.approx((0.30 + 0.05 + 0.01) / 3)

    def test_phase_75_sample_counts_use_min(self):
        """Opportunity counters take MIN across active spots, matching
        the policy for legacy hands_observed / cbet_faced_count."""
        spots = [
            _spot(
                'A',
                _stats(
                    facing_bet_opportunities=150,
                    postflop_open_opportunities=100,
                ),
                committed_this_hand=300,
            ),
            _spot(
                'B',
                _stats(
                    facing_bet_opportunities=60,
                    postflop_open_opportunities=40,
                ),
                committed_this_hand=300,
            ),
            _spot(
                'C',
                _stats(
                    facing_bet_opportunities=200,
                    postflop_open_opportunities=80,
                ),
                committed_this_hand=300,
            ),
        ]
        result = aggregate_from_spots(spots)
        # MIN over active spots
        assert result.facing_bet_opportunities == 60
        assert result.postflop_open_opportunities == 40


# ── Empty / inactive cases ───────────────────────────────────────────────


class TestEmptyAndInactive:
    def test_empty_spots_returns_zero_defaults_for_new_fields(self):
        result = aggregate_from_spots([])
        assert result.aggression_factor_postflop == 1.0
        assert result.all_in_per_facing_bet == 0.0
        assert result.facing_bet_opportunities == 0
        assert result.postflop_jam_open_rate == 0.0
        assert result.postflop_open_opportunities == 0

    def test_inactive_spots_excluded_before_aggregation(self):
        """Folded spots are filtered before averaging."""
        spots = [
            _spot(
                'Active',
                _stats(
                    aggression_factor_postflop=5.0,
                    facing_bet_opportunities=100,
                ),
                committed_this_hand=300,
            ),
            # Folded — should be ignored.
            _spot(
                'Folded',
                _stats(
                    aggression_factor_postflop=1.0,
                    facing_bet_opportunities=20,
                ),
                is_active=False,
                committed_this_hand=200,
            ),
        ]
        result = aggregate_from_spots(spots)
        # Only Active counts → its stats forwarded verbatim (single-opp path)
        assert result.aggression_factor_postflop == 5.0
        assert result.facing_bet_opportunities == 100
