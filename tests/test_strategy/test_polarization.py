"""Polarization Phase B — gate hyper_passive fold-reduction on the
aggression-polarization signal.

Spec: docs/plans/POLARIZATION_DETECTION.md

These tests exercise:
  - `compute_aggression_polarization` (the pure helper)
  - The hyper_passive rule's two halves (value-extraction always fires;
    fold-reduction suppressed when polarization >= POLARIZATION_HIGH)
  - The diagnostic surface on `rule_context['hyper_passive']`
  - The §5.5 per-rule budget still clamps the rule when only the
    value-extraction half fires
  - `aggregate_from_spots` stake-weights the new equity fields and
    MIN-aggregates their sample counters
"""

import pytest

from poker.strategy.exploitation import (
    AggregatedOpponentStats,
    DecisionContext,
    MAX_L1_SHIFT_BY_RULE,
    MIN_SAMPLE_FOR_GATE,
    OpponentSpot,
    POLARIZATION_HIGH,
    POLARIZATION_LOW,
    aggregate_from_spots,
    compute_aggression_polarization,
    compute_exploitation_offsets,
    compute_exploitation_offsets_with_traces,
)


def _polarized_station_stats(hands: int = 100) -> AggregatedOpponentStats:
    """Calling station whose raises are heavily value-weighted.

    Matches CaseBot's empirical signature: raises with equity ~0.80,
    calls with equity ~0.30. Sample on both buckets is well above
    MIN_SAMPLE_FOR_GATE.
    """
    return AggregatedOpponentStats(
        hands_observed=hands,
        vpip=0.75, pfr=0.05,
        vpip_per_voluntary_opportunity=0.85,
        pfr_per_open_opportunity=0.05,
        preflop_voluntary_opportunities=hands,
        preflop_open_opportunities=hands // 2,
        aggression_factor=0.3,
        all_in_frequency=0.0,
        # Phase A equity-at-action fields — polarized signature.
        equity_when_raising_postflop=0.80,
        equity_when_calling_postflop=0.30,
        _equity_raising_count=20,
        _equity_calling_count=40,
    )


def _noisy_station_stats(hands: int = 100) -> AggregatedOpponentStats:
    """Loose-passive station whose raises and calls have similar equity.

    Classic fish — the legacy hyper_passive behavior (push raises +
    reduce folds) is the correct exploit.
    """
    return AggregatedOpponentStats(
        hands_observed=hands,
        vpip=0.75, pfr=0.05,
        vpip_per_voluntary_opportunity=0.85,
        pfr_per_open_opportunity=0.05,
        preflop_voluntary_opportunities=hands,
        preflop_open_opportunities=hands // 2,
        aggression_factor=0.3,
        all_in_frequency=0.0,
        equity_when_raising_postflop=0.50,
        equity_when_calling_postflop=0.48,
        _equity_raising_count=20,
        _equity_calling_count=40,
    )


def _undersampled_station_stats(hands: int = 100) -> AggregatedOpponentStats:
    """Station with polarized equity numbers but too few samples to gate.

    Phase B says: below MIN_SAMPLE_FOR_GATE on either bucket, return
    neutral 0.0 → legacy behavior fires unchanged.
    """
    return AggregatedOpponentStats(
        hands_observed=hands,
        vpip=0.75, pfr=0.05,
        vpip_per_voluntary_opportunity=0.85,
        pfr_per_open_opportunity=0.05,
        preflop_voluntary_opportunities=hands,
        preflop_open_opportunities=hands // 2,
        aggression_factor=0.3,
        all_in_frequency=0.0,
        equity_when_raising_postflop=0.80,
        equity_when_calling_postflop=0.30,
        # Only 3 raising samples — below MIN_SAMPLE_FOR_GATE = 8.
        _equity_raising_count=3,
        _equity_calling_count=40,
    )


class TestComputeAggressionPolarization:
    def test_above_threshold_signals_polarized(self):
        stats = _polarized_station_stats()
        signal = compute_aggression_polarization(stats)
        assert signal == pytest.approx(0.50, abs=1e-9)
        assert signal >= POLARIZATION_HIGH

    def test_near_zero_signals_noisy(self):
        stats = _noisy_station_stats()
        signal = compute_aggression_polarization(stats)
        assert abs(signal) < POLARIZATION_HIGH

    def test_undersampled_raising_returns_neutral(self):
        stats = _undersampled_station_stats()
        assert compute_aggression_polarization(stats) == 0.0

    def test_undersampled_calling_returns_neutral(self):
        stats = AggregatedOpponentStats(
            equity_when_raising_postflop=0.80,
            equity_when_calling_postflop=0.30,
            _equity_raising_count=40,
            _equity_calling_count=3,
        )
        assert compute_aggression_polarization(stats) == 0.0

    def test_exactly_at_min_sample_gates_on(self):
        """At MIN_SAMPLE_FOR_GATE the signal becomes trustworthy
        (the helper uses `<` for the gate, not `<=`)."""
        stats = AggregatedOpponentStats(
            equity_when_raising_postflop=0.80,
            equity_when_calling_postflop=0.30,
            _equity_raising_count=MIN_SAMPLE_FOR_GATE,
            _equity_calling_count=MIN_SAMPLE_FOR_GATE,
        )
        assert compute_aggression_polarization(stats) == pytest.approx(0.50)

    def test_negative_signal_for_bluffer(self):
        """Phase D shape: raises with junk, calls with strength.
        Helper returns the raw negative signal so Phase D can consume it."""
        stats = AggregatedOpponentStats(
            equity_when_raising_postflop=0.30,
            equity_when_calling_postflop=0.60,
            _equity_raising_count=20,
            _equity_calling_count=20,
        )
        signal = compute_aggression_polarization(stats)
        assert signal == pytest.approx(-0.30)
        assert signal < POLARIZATION_LOW


class TestHyperPassiveGate:
    def _context(self) -> DecisionContext:
        # Not facing all-in / not preflop-open → the hyper_passive rule
        # fires through its standard branch.
        return DecisionContext()

    def test_polarized_station_suppresses_fold_reduction(self):
        offsets = compute_exploitation_offsets(
            _polarized_station_stats(), adaptation_bias=0.85,
            decision_context=self._context(),
            available_actions=['fold', 'call', 'raise_67'],
        )
        # Value-extraction half still fires.
        assert offsets.get('raise_67', 0.0) > 0
        # Fold-reduction half is suppressed.
        assert offsets.get('fold', 0.0) == 0.0

    def test_noisy_station_keeps_fold_reduction(self):
        offsets = compute_exploitation_offsets(
            _noisy_station_stats(), adaptation_bias=0.85,
            decision_context=self._context(),
            available_actions=['fold', 'call', 'raise_67'],
        )
        # Legacy behavior: both halves fire.
        assert offsets.get('raise_67', 0.0) > 0
        assert offsets.get('fold', 0.0) < 0

    def test_undersampled_keeps_fold_reduction(self):
        """Below MIN_SAMPLE_FOR_GATE the gate stays inactive."""
        offsets = compute_exploitation_offsets(
            _undersampled_station_stats(), adaptation_bias=0.85,
            decision_context=self._context(),
            available_actions=['fold', 'call', 'raise_67'],
        )
        assert offsets.get('raise_67', 0.0) > 0
        assert offsets.get('fold', 0.0) < 0

    def _hyper_passive_inputs(self, stats):
        _offsets, traces = compute_exploitation_offsets_with_traces(
            stats, adaptation_bias=0.85,
            decision_context=self._context(),
            available_actions=['fold', 'call', 'raise_67'],
        )
        hp_trace = next(
            t for t in traces
            if t.layer == 'exploitation' and t.rule_id == 'hyper_passive'
        )
        return hp_trace.inputs

    def test_polarized_diagnostic_surface(self):
        inputs = self._hyper_passive_inputs(_polarized_station_stats())
        assert inputs['polarization_gate'] == 'polarized_station'
        assert inputs['polarization'] == pytest.approx(0.50, abs=1e-9)

    def test_noisy_diagnostic_surface(self):
        inputs = self._hyper_passive_inputs(_noisy_station_stats())
        assert inputs['polarization_gate'] == 'noisy_station'

    def test_undersampled_diagnostic_surface(self):
        inputs = self._hyper_passive_inputs(_undersampled_station_stats())
        assert inputs['polarization_gate'] == 'insufficient_sample'

    def test_budget_clamp_still_applies_to_polarized_half(self):
        """§5.5: the rule's L1 contribution must not exceed
        MAX_L1_SHIFT_BY_RULE['hyper_passive']. When only the
        value-extraction half fires (polarized branch), the post-rule
        clamp still scales the rule when it overshoots its budget.
        """
        # Saturate the value-extraction half with many raise actions
        # to push the rule's L1 above its 0.80 budget.
        actions = (
            ['fold', 'call']
            + [f'raise_{i}' for i in range(20)]
        )
        offsets = compute_exploitation_offsets(
            _polarized_station_stats(), adaptation_bias=0.85,
            decision_context=self._context(),
            available_actions=actions,
        )
        # Sum of absolute hyper_passive contributions ≤ budget.
        budget = MAX_L1_SHIFT_BY_RULE[('exploitation', 'hyper_passive')]
        # Only raise_* actions and possibly fold contribute. Fold is
        # zero in the polarized branch, so the only L1 mass is the
        # raise stack.
        raise_l1 = sum(
            abs(v) for k, v in offsets.items()
            if k.startswith('raise_')
        )
        assert raise_l1 <= budget + 1e-6


class TestAggregateFromSpotsEquityWeighting:
    """`aggregate_from_spots` stake-weights the equity-at-action means
    and MIN-aggregates the per-bucket sample counters."""

    def _spot(
        self, name: str, committed: int,
        eq_raising: float, eq_calling: float,
        n_raising: int, n_calling: int,
    ) -> OpponentSpot:
        return OpponentSpot(
            name=name,
            stats=AggregatedOpponentStats(
                hands_observed=50,
                equity_when_raising_postflop=eq_raising,
                equity_when_calling_postflop=eq_calling,
                _equity_raising_count=n_raising,
                _equity_calling_count=n_calling,
            ),
            is_active=True,
            committed_this_hand=committed,
        )

    def test_single_opponent_copies_equity_fields(self):
        spot = self._spot('a', 100, 0.80, 0.30, 20, 40)
        agg = aggregate_from_spots([spot])
        assert agg.equity_when_raising_postflop == pytest.approx(0.80)
        assert agg.equity_when_calling_postflop == pytest.approx(0.30)
        assert agg._equity_raising_count == 20
        assert agg._equity_calling_count == 40

    def test_stake_weighted_average_across_opponents(self):
        # Two opponents in a multiway pot. Keep both stakes below the
        # 60% dominance threshold so the weighted path is exercised
        # rather than the dominant fast path.
        # Polarized 55% (33/60), noisy 45% (27/60) — within the
        # weighted regime since neither crosses 0.60.
        polarized = self._spot('polarized', 33, 0.80, 0.30, 20, 40)
        noisy = self._spot('noisy', 27, 0.50, 0.50, 20, 40)
        agg = aggregate_from_spots([polarized, noisy])
        # Weighted: (0.80 * 33 + 0.50 * 27) / 60 = 0.665
        assert agg.equity_when_raising_postflop == pytest.approx(0.665)
        # Calling: (0.30 * 33 + 0.50 * 27) / 60 = 0.39
        assert agg.equity_when_calling_postflop == pytest.approx(0.39)
        # MIN counts (limiting factor).
        assert agg._equity_raising_count == 20
        assert agg._equity_calling_count == 40

    def test_min_aggregation_picks_limiting_factor(self):
        """MIN across active opponents matches the cbet_faced_count
        policy — gate confidence is bounded by the worst-observed
        sample, not the best."""
        a = self._spot('a', 10, 0.80, 0.30, 20, 40)
        b = self._spot('b', 10, 0.80, 0.30, 5, 12)
        agg = aggregate_from_spots([a, b])
        assert agg._equity_raising_count == 5
        assert agg._equity_calling_count == 12

    def test_dominant_opponent_fast_path_copies_fields(self):
        """When one opponent has >60% of the money the dominant path
        returns their stats verbatim — including equity fields."""
        dominant = self._spot('dom', 100, 0.85, 0.25, 25, 50)
        small = self._spot('small', 10, 0.40, 0.60, 10, 10)
        agg = aggregate_from_spots([dominant, small])
        assert agg.equity_when_raising_postflop == pytest.approx(0.85)
        assert agg.equity_when_calling_postflop == pytest.approx(0.25)
        assert agg._equity_raising_count == 25
        assert agg._equity_calling_count == 50
