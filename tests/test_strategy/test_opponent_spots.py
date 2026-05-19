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
        # Stakes: A=500, B=300, C=200 → weights 0.5, 0.3, 0.2.
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
        assert result.vpip == pytest.approx(0.5 * 0.6 + 0.3 * 0.3 + 0.2 * 0.3)
        assert result.aggression_factor == pytest.approx(
            0.5 * 3.0 + 0.3 * 1.5 + 0.2 * 1.5
        )
        assert result.fold_to_cbet == pytest.approx(
            0.5 * 0.4 + 0.3 * 0.7 + 0.2 * 0.6
        )
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
        check has nothing to evaluate and the helper falls through to
        equal-weight (stake-weighted with all-zero weights collapses to
        equal weight by design)."""
        spots = [
            _spot('A', committed_this_hand=0, stats=_stats(vpip=0.4)),
            _spot('B', committed_this_hand=0, stats=_stats(vpip=0.6)),
        ]
        result = aggregate_from_spots(spots)
        assert result.vpip == pytest.approx(0.5)

    def test_single_station_dominates_when_betting_most(self):
        """Regression: a single calling station that has put in the bulk
        of the non-hero money should drive the aggregate VPIP past the
        hyper_passive threshold (0.70), even when below the 60% cliff.

        Stakes 50% / 25% / 25% with one station (VPIP 0.98) and two
        TAGs (VPIP 0.20) — equal-weight average lands at ~0.46 and
        misses the station entirely. Stake-weighted lands at ~0.59 if
        weights were 50/25/25, but here the station has put in 55%
        (just under 60%) so the stake-weighted aggregate rises to
        0.55*0.98 + 0.225*0.20 + 0.225*0.20 ≈ 0.629. Stronger station
        share (75%) pushes the aggregate over the 0.70 threshold.
        """
        station_stats = _stats(
            hands_observed=50,
            vpip_per_voluntary_opportunity=0.98,
            aggression_factor=0.5,
        )
        tag_stats = _stats(
            hands_observed=50,
            vpip_per_voluntary_opportunity=0.20,
            aggression_factor=2.5,
        )
        # 75/12.5/12.5 — under the 60% cliff would not apply here (since
        # the cliff IS 60%), so we use exactly 55% on station to keep the
        # weighted-path code under test rather than the dominant cliff.
        spots = [
            _spot('Station', committed_this_hand=550, stats=station_stats),
            _spot('TAG1',    committed_this_hand=225, stats=tag_stats),
            _spot('TAG2',    committed_this_hand=225, stats=tag_stats),
        ]
        result = aggregate_from_spots(spots)
        # Equal-weight would be (0.98 + 0.20 + 0.20)/3 = 0.46.
        # Stake-weighted is 0.55*0.98 + 0.225*0.20 + 0.225*0.20 = 0.629.
        assert result.vpip_per_voluntary_opportunity == pytest.approx(
            0.55 * 0.98 + 0.225 * 0.20 + 0.225 * 0.20
        )
        assert result.aggression_factor == pytest.approx(
            0.55 * 0.5 + 0.225 * 2.5 + 0.225 * 2.5
        )


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


# ── Phase 6.7b Part A: conservative multiway c-bet intensity ─────────────

from poker.strategy.exploitation import (
    FULL_CBET_SAMPLE_CONFIDENCE,
    HIGH_FOLD_TO_CBET_THRESHOLD,
    MIN_CBET_FACED_FOR_DETECTION,
    compute_multiway_cbet_intensity,
)


def _foldy_stats(
    fold_to_cbet: float = 0.85,
    cbet_faced_count: int = FULL_CBET_SAMPLE_CONFIDENCE,
) -> AggregatedOpponentStats:
    return AggregatedOpponentStats(
        hands_observed=100,
        fold_to_cbet=fold_to_cbet,
        cbet_faced_count=cbet_faced_count,
    )


class TestComputeMultiwayCbetIntensity:
    def test_empty_spots_returns_zero(self):
        assert compute_multiway_cbet_intensity([]) == 0.0

    def test_hu_returns_zero(self):
        """HU (1 active) is handled by the existing HU c-bet rule."""
        spots = [_spot('A', stats=_foldy_stats())]
        assert compute_multiway_cbet_intensity(spots) == 0.0

    def test_all_foldy_with_full_samples_returns_min(self):
        """min(intensity) across 2 fully-foldy opponents."""
        a = _spot('A', stats=_foldy_stats(fold_to_cbet=0.85, cbet_faced_count=10))
        b = _spot('B', stats=_foldy_stats(fold_to_cbet=0.85, cbet_faced_count=10))
        # Both at full intensity (rate 1.0 × sample 1.0)
        assert compute_multiway_cbet_intensity([a, b]) == pytest.approx(1.0)

    def test_one_foldy_one_partial_returns_min(self):
        """Mixed sample sizes → take the smaller intensity."""
        full = _spot('Full', stats=_foldy_stats(
            fold_to_cbet=0.85, cbet_faced_count=10,
        ))
        partial = _spot('Partial', stats=_foldy_stats(
            fold_to_cbet=0.85, cbet_faced_count=7,  # ramp = 0.5 confidence
        ))
        result = compute_multiway_cbet_intensity([full, partial])
        assert result == pytest.approx(0.5, rel=1e-6)

    def test_one_not_foldy_returns_zero(self):
        """If any opponent's fold_to_cbet <= 0.60, intensity is 0."""
        foldy = _spot('Foldy', stats=_foldy_stats(fold_to_cbet=0.85))
        station = _spot('Station', stats=_foldy_stats(fold_to_cbet=0.30))
        assert compute_multiway_cbet_intensity([foldy, station]) == 0.0

    def test_at_threshold_returns_zero(self):
        """Strict > on fold_to_cbet — at the threshold is NOT foldy."""
        foldy = _spot('Foldy', stats=_foldy_stats(fold_to_cbet=0.85))
        at_thresh = _spot(
            'AtThresh',
            stats=_foldy_stats(fold_to_cbet=HIGH_FOLD_TO_CBET_THRESHOLD),
        )
        assert compute_multiway_cbet_intensity([foldy, at_thresh]) == 0.0

    def test_one_low_sample_returns_zero(self):
        """If any opponent has fewer than MIN samples, intensity is 0."""
        a = _spot('A', stats=_foldy_stats())
        b = _spot(
            'B',
            stats=_foldy_stats(cbet_faced_count=MIN_CBET_FACED_FOR_DETECTION - 1),
        )
        assert compute_multiway_cbet_intensity([a, b]) == 0.0

    def test_unknown_opponent_returns_zero(self):
        """Default stats (no observations) block the bluff."""
        a = _spot('Foldy', stats=_foldy_stats())
        b = _spot('Unknown', stats=AggregatedOpponentStats())  # all defaults
        assert compute_multiway_cbet_intensity([a, b]) == 0.0

    def test_any_all_in_returns_zero(self):
        """An all-in player can't fold — pure bluff EV collapses."""
        foldy = _spot('Foldy', stats=_foldy_stats())
        all_in = _spot(
            'AllIn', stats=_foldy_stats(), is_all_in=True, stack=0,
        )
        assert compute_multiway_cbet_intensity([foldy, all_in]) == 0.0

    def test_inactive_excluded_from_eligible_set(self):
        """Folded opponents don't count toward the all-foldy gate."""
        a = _spot('A', stats=_foldy_stats())
        b = _spot('B', stats=_foldy_stats())
        folded_station = _spot(
            'FoldedStation', is_active=False,
            stats=_foldy_stats(fold_to_cbet=0.10),
        )
        # The station is inactive, so 2 active foldy opponents fire.
        assert compute_multiway_cbet_intensity([a, b, folded_station]) > 0.0


# ── compute_exploitation_offsets multiway c-bet integration ──────────────

from poker.strategy.exploitation import (
    DecisionContext,
    compute_exploitation_offsets,
)


class TestMultiwayCbetOffsets:
    def test_fires_in_multiway_flop_aggressor_spot(self):
        ctx = DecisionContext(
            is_flop_as_preflop_aggressor=True, active_opponent_count=2,
        )
        offsets = compute_exploitation_offsets(
            stats=AggregatedOpponentStats(hands_observed=100),
            adaptation_bias=0.85,
            decision_context=ctx,
            available_actions=['check', 'bet_33', 'bet_67'],
            multiway_cbet_intensity=1.0,
        )
        assert offsets.get('bet_33', 0.0) > 0.0
        assert offsets.get('bet_67', 0.0) > 0.0
        assert offsets.get('check', 0.0) < 0.0

    def test_zero_intensity_does_not_fire(self):
        ctx = DecisionContext(
            is_flop_as_preflop_aggressor=True, active_opponent_count=3,
        )
        offsets = compute_exploitation_offsets(
            stats=AggregatedOpponentStats(hands_observed=100),
            adaptation_bias=0.85,
            decision_context=ctx,
            available_actions=['check', 'bet_33'],
            multiway_cbet_intensity=0.0,
        )
        assert offsets == {}

    def test_does_not_fire_hu(self):
        """multiway_cbet_intensity must be ignored when active_opponent_count == 1
        (the HU rule handles those spots from aggregate stats)."""
        ctx = DecisionContext(
            is_flop_as_preflop_aggressor=True, active_opponent_count=1,
        )
        offsets = compute_exploitation_offsets(
            stats=AggregatedOpponentStats(hands_observed=100),
            adaptation_bias=0.85,
            decision_context=ctx,
            available_actions=['check', 'bet_33'],
            multiway_cbet_intensity=1.0,  # provided but ignored
        )
        # HU rule reads from `stats`, which has fold_to_cbet=0.5 default,
        # so it produces no offsets either. Net: no multiway path fires.
        assert offsets.get('bet_33', 0.0) == 0.0

    def test_does_not_fire_outside_c_bet_spot(self):
        """is_flop_as_preflop_aggressor=False suppresses the rule."""
        ctx = DecisionContext(
            is_flop_as_preflop_aggressor=False, active_opponent_count=3,
        )
        offsets = compute_exploitation_offsets(
            stats=AggregatedOpponentStats(hands_observed=100),
            adaptation_bias=0.85,
            decision_context=ctx,
            available_actions=['check', 'bet_33'],
            multiway_cbet_intensity=1.0,
        )
        assert offsets.get('bet_33', 0.0) == 0.0

    def test_partial_intensity_scales_offsets(self):
        """Half intensity → half offset magnitude."""
        ctx = DecisionContext(
            is_flop_as_preflop_aggressor=True, active_opponent_count=2,
        )
        full = compute_exploitation_offsets(
            stats=AggregatedOpponentStats(hands_observed=100),
            adaptation_bias=0.85, decision_context=ctx,
            available_actions=['check', 'bet_33'],
            multiway_cbet_intensity=1.0,
        )
        half = compute_exploitation_offsets(
            stats=AggregatedOpponentStats(hands_observed=100),
            adaptation_bias=0.85, decision_context=ctx,
            available_actions=['check', 'bet_33'],
            multiway_cbet_intensity=0.5,
        )
        assert half['bet_33'] == pytest.approx(full['bet_33'] * 0.5, rel=1e-6)
