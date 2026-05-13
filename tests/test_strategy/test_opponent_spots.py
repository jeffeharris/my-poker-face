"""Phase 6.7a tests: OpponentSpot, aggregate_from_spots, select_primary_aggressor."""

import pytest

from poker.strategy.exploitation import (
    AggregatedOpponentStats,
    OpponentSpot,
    aggregate_from_spots,
    select_primary_aggressor,
)


# ── Fixtures ────────────────────────────────────────────────────────────

def _stats(**kwargs) -> AggregatedOpponentStats:
    base = dict(
        hands_observed=50, vpip=0.5, pfr=0.25,
        aggression_factor=1.5, all_in_frequency=0.05,
        fold_to_cbet=0.5, cbet_faced_count=0,
    )
    base.update(kwargs)
    return AggregatedOpponentStats(**base)


def _spot(
    name: str = 'Opp',
    *,
    stats=None,
    is_active: bool = True,
    is_aggressor: bool = False,
    is_all_in: bool = False,
    current_bet: int = 0,
    stack: int = 10000,
    committed_this_street: int = 0,
    committed_this_hand: int = 0,
) -> OpponentSpot:
    return OpponentSpot(
        name=name,
        stats=stats if stats is not None else _stats(),
        is_active=is_active,
        is_aggressor=is_aggressor,
        is_all_in=is_all_in,
        current_bet=current_bet,
        stack=stack,
        committed_this_street=committed_this_street,
        committed_this_hand=committed_this_hand,
    )


# ── aggregate_from_spots: behavior parity with aggregate_active_opponents ─

class TestAggregateFromSpotsEmpty:
    def test_empty_spots_returns_zero_init(self):
        result = aggregate_from_spots([])
        assert result == AggregatedOpponentStats()

    def test_only_folded_spots_returns_zero_init(self):
        spots = [
            _spot('A', is_active=False, stats=_stats(hands_observed=50)),
            _spot('B', is_active=False, stats=_stats(hands_observed=80)),
        ]
        assert aggregate_from_spots(spots) == AggregatedOpponentStats()

    def test_zero_hand_spots_excluded(self):
        spots = [_spot('A', stats=_stats(hands_observed=0))]
        assert aggregate_from_spots(spots) == AggregatedOpponentStats()


class TestAggregateFromSpotsSingle:
    def test_single_active_opponent_returns_their_stats(self):
        stats = _stats(
            hands_observed=42, vpip=0.33, pfr=0.18,
            aggression_factor=2.5, all_in_frequency=0.08,
            fold_to_cbet=0.75, cbet_faced_count=12,
        )
        result = aggregate_from_spots([_spot('Bob', stats=stats)])
        assert result.hands_observed == 42
        assert result.vpip == pytest.approx(0.33)
        assert result.pfr == pytest.approx(0.18)
        assert result.aggression_factor == pytest.approx(2.5)
        assert result.all_in_frequency == pytest.approx(0.08)
        assert result.fold_to_cbet == pytest.approx(0.75)
        assert result.cbet_faced_count == 12

    def test_folded_opponents_ignored(self):
        active = _spot('Alive', stats=_stats(hands_observed=42, vpip=0.7))
        folded = _spot(
            'Folded', is_active=False,
            stats=_stats(hands_observed=100, vpip=0.1),
        )
        result = aggregate_from_spots([active, folded])
        # Single active opponent → their stats verbatim
        assert result.hands_observed == 42
        assert result.vpip == pytest.approx(0.7)


class TestAggregateFromSpots60Rule:
    def test_dominant_opponent_returns_their_stats(self):
        spots = [
            _spot(
                'Dom', current_bet=700, committed_this_hand=700,
                stats=_stats(
                    hands_observed=120, vpip=0.85, pfr=0.55,
                    aggression_factor=4.5, all_in_frequency=0.4,
                    fold_to_cbet=0.30, cbet_faced_count=18,
                ),
            ),
            _spot(
                'B', current_bet=200, committed_this_hand=200,
                stats=_stats(
                    hands_observed=80, vpip=0.20,
                    fold_to_cbet=0.60, cbet_faced_count=8,
                ),
            ),
            _spot(
                'C', current_bet=100, committed_this_hand=100,
                stats=_stats(
                    hands_observed=60, vpip=0.30,
                    fold_to_cbet=0.55, cbet_faced_count=5,
                ),
            ),
        ]
        result = aggregate_from_spots(spots)
        assert result.hands_observed == 120
        assert result.vpip == pytest.approx(0.85)
        assert result.aggression_factor == pytest.approx(4.5)
        assert result.fold_to_cbet == pytest.approx(0.30)
        assert result.cbet_faced_count == 18

    def test_below_60_percent_uses_weighted_average(self):
        # A has 50% of hand-level total; below the 60% threshold.
        spots = [
            _spot(
                'A', committed_this_hand=500,
                stats=_stats(
                    hands_observed=100, vpip=0.6, pfr=0.30,
                    aggression_factor=3.0, all_in_frequency=0.2,
                    fold_to_cbet=0.4, cbet_faced_count=10,
                ),
            ),
            _spot(
                'B', committed_this_hand=300,
                stats=_stats(
                    hands_observed=100, vpip=0.3, pfr=0.15,
                    aggression_factor=1.5, all_in_frequency=0.05,
                    fold_to_cbet=0.7, cbet_faced_count=12,
                ),
            ),
            _spot(
                'C', committed_this_hand=200,
                stats=_stats(
                    hands_observed=100, vpip=0.3, pfr=0.15,
                    aggression_factor=1.5, all_in_frequency=0.05,
                    fold_to_cbet=0.6, cbet_faced_count=8,
                ),
            ),
        ]
        result = aggregate_from_spots(spots)
        assert result.vpip == pytest.approx((0.6 + 0.3 + 0.3) / 3)
        assert result.aggression_factor == pytest.approx((3.0 + 1.5 + 1.5) / 3)
        assert result.fold_to_cbet == pytest.approx((0.4 + 0.7 + 0.6) / 3)
        # hands_observed and cbet_faced_count use MIN
        assert result.hands_observed == 100
        assert result.cbet_faced_count == 8

    def test_hands_observed_is_min_when_averaging(self):
        spots = [
            _spot('A', committed_this_hand=100, stats=_stats(hands_observed=50)),
            _spot('B', committed_this_hand=100, stats=_stats(hands_observed=80)),
            _spot('C', committed_this_hand=100, stats=_stats(hands_observed=100)),
        ]
        result = aggregate_from_spots(spots)
        assert result.hands_observed == 50

    def test_zero_total_committed_uses_average(self):
        """When no money is committed yet (e.g. preflop pre-blinds), the 60%
        check has nothing to evaluate and the helper falls through to the
        weighted-average path."""
        spots = [
            _spot('A', committed_this_hand=0, stats=_stats(vpip=0.4)),
            _spot('B', committed_this_hand=0, stats=_stats(vpip=0.6)),
        ]
        result = aggregate_from_spots(spots)
        assert result.vpip == pytest.approx(0.5)


# ── select_primary_aggressor ────────────────────────────────────────────

class TestSelectPrimaryAggressor:
    def test_strictly_highest_returns_that_spot(self):
        spots = [
            _spot('A', current_bet=300),
            _spot('B', current_bet=100),
            _spot('C', current_bet=100),
        ]
        result = select_primary_aggressor(spots, highest_current_bet=300,
                                          recent_aggressor_name=None)
        assert result is not None
        assert result.name == 'A'

    def test_strictly_highest_among_lower_ties(self):
        """Strictly-highest is unambiguous even when lower-bet opponents tie."""
        spots = [
            _spot('A', current_bet=300),
            _spot('B', current_bet=200),
            _spot('C', current_bet=200),
        ]
        result = select_primary_aggressor(spots, 300, None)
        assert result is not None
        assert result.name == 'A'

    def test_tied_with_single_is_aggressor_flag_returns_that_spot(self):
        spots = [
            _spot('A', current_bet=300, is_aggressor=True),
            _spot('B', current_bet=300, is_aggressor=False),
        ]
        result = select_primary_aggressor(spots, 300, None)
        assert result is not None
        assert result.name == 'A'

    def test_tied_with_multiple_is_aggressor_flags_returns_none(self):
        """Multiple flags is ill-defined state; fall back to aggregate."""
        spots = [
            _spot('A', current_bet=300, is_aggressor=True),
            _spot('B', current_bet=300, is_aggressor=True),
        ]
        result = select_primary_aggressor(spots, 300, None)
        assert result is None

    def test_tied_with_recent_aggressor_name_match(self):
        spots = [
            _spot('A', current_bet=300, is_aggressor=False),
            _spot('B', current_bet=300, is_aggressor=False),
        ]
        result = select_primary_aggressor(spots, 300, recent_aggressor_name='B')
        assert result is not None
        assert result.name == 'B'

    def test_tied_with_unmatched_recent_aggressor_name_returns_none(self):
        """Recent aggressor isn't in the tied set → ambiguous → None."""
        spots = [
            _spot('A', current_bet=300),
            _spot('B', current_bet=300),
        ]
        result = select_primary_aggressor(spots, 300, recent_aggressor_name='Z')
        assert result is None

    def test_tied_no_flag_no_recent_returns_none(self):
        spots = [
            _spot('A', current_bet=300),
            _spot('B', current_bet=300),
        ]
        assert select_primary_aggressor(spots, 300, None) is None

    def test_inactive_spots_excluded_from_tied_set(self):
        """Folded opponent at highest bet doesn't trigger ambiguity."""
        spots = [
            _spot('Folded', current_bet=300, is_active=False),
            _spot('Live', current_bet=300, is_active=True),
        ]
        result = select_primary_aggressor(spots, 300, None)
        assert result is not None
        assert result.name == 'Live'

    def test_flag_must_be_at_highest_bet_to_win(self):
        """is_aggressor on a lower-bet spot doesn't win the disambiguation."""
        spots = [
            _spot('Lower', current_bet=100, is_aggressor=True),
            _spot('Tie1', current_bet=300),
            _spot('Tie2', current_bet=300),
        ]
        result = select_primary_aggressor(spots, 300, None)
        assert result is None  # no flag in the tied set
