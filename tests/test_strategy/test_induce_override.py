"""Phase B Item 2 induce_override tests.

Covers (1) the new barrel-signal gate logic — one positive baseline
scenario, then mutate each gate component to verify it correctly
blocks; (2) confidence-scaled redistribution math; (3) ablation hook;
(4) trace shape including the new barrel_frequency inputs.
"""

import pytest

from poker.strategy.exploitation import (
    AggregatedOpponentStats,
    DecisionContext,
)
from poker.strategy.induce_override import (
    CALL_PROB_MAX,
    CALL_PROB_MIN,
    ELIGIBLE_NUT_STATUS,
    MIN_BARREL_FREQUENCY,
    MIN_BARREL_OPPORTUNITIES,
    MIN_EFFECTIVE_STACK_BB,
    MIN_HANDS_OBSERVED,
    OPPS_RAMP_MAX,
    RATE_RAMP_MAX,
    apply_induce_override,
    compute_call_probability,
    compute_induce_override_strategy,
    should_apply_induce_override,
)
from poker.strategy.intervention_trace import (
    InterventionOperation,
    layer_order_for,
)
from poker.strategy.strategy_profile import StrategyProfile


# ── Fixtures ───────────────────────────────────────────────────────

def _barreler_stats(**overrides) -> AggregatedOpponentStats:
    """ManiacBot-like opponent that passes the Phase B Item 2 gate."""
    base = dict(
        hands_observed=30,
        vpip=0.80, pfr=0.80,
        aggression_factor=5.0,
        all_in_frequency=0.0,
        aggression_factor_postflop=4.0,
        cbet_attempt_rate=0.95,
        postflop_seen_as_pfr_count=20,
        cbet_faced_count=0,
        all_in_per_facing_bet=0.0,
        facing_bet_opportunities=0,
        postflop_jam_open_rate=0.0,
        postflop_open_opportunities=15,
        # Phase B Item 1 barrel fields — the gate now reads these
        barrel_frequency=0.90,
        barrel_opportunities=20,
    )
    base.update(overrides)
    return AggregatedOpponentStats(**base)


def _facing_bet_context() -> DecisionContext:
    """Hero facing a medium bet — not all-in, not big."""
    return DecisionContext(
        facing_all_in=False,
        facing_big_bet=False,
        bet_size_pot_ratio=0.6,
        bet_bucket='medium',
        required_equity=0.32,
    )


def _facing_bet_strategy() -> StrategyProfile:
    return StrategyProfile(action_probabilities={
        'fold': 0.20,
        'call': 0.50,
        'raise_75': 0.30,
    })


def _baseline_kwargs(**overrides):
    base = dict(
        stats=_barreler_stats(),
        hand_strength='nuts',
        nut_status=ELIGIBLE_NUT_STATUS,
        street='flop',
        position='IP',
        danger_flag_count=0,
        effective_stack_bb=100.0,
        active_opponent_count=1,
        decision_context=_facing_bet_context(),
        has_call=True,
        has_fold=True,
        adaptation_bias=0.8,
        tilt_factor=1.0,
    )
    base.update(overrides)
    return base


# ── Gate: positive baseline ───────────────────────────────────────

class TestGateBaseline:
    def test_baseline_scenario_fires(self):
        should_fire, reason = should_apply_induce_override(**_baseline_kwargs())
        assert should_fire
        assert reason == 'gate_pass'


# ── Gate: each component blocks when violated ────────────────────

class TestGateBlocks:
    def test_no_call_action_blocks(self):
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(has_call=False)
        )
        assert not should_fire
        assert reason == 'no_call_action'

    def test_no_fold_action_means_not_facing_bet(self):
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(has_fold=False)
        )
        assert not should_fire
        assert reason == 'not_facing_bet'

    def test_facing_all_in_blocks(self):
        ctx = DecisionContext(facing_all_in=True)
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(decision_context=ctx)
        )
        assert not should_fire
        assert reason == 'facing_all_in'

    def test_river_street_blocks(self):
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(street='river')
        )
        assert not should_fire
        assert 'river' in reason

    def test_oop_blocks(self):
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(position='OOP')
        )
        assert not should_fire
        assert reason == 'oop_not_supported_phase_a'

    def test_multiway_blocks(self):
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(active_opponent_count=2)
        )
        assert not should_fire
        assert reason == 'multiway_not_supported_phase_a'

    def test_short_stack_blocks(self):
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(effective_stack_bb=MIN_EFFECTIVE_STACK_BB - 1)
        )
        assert not should_fire
        assert reason == 'below_stack_floor'

    def test_strong_made_not_eligible_phase_b(self):
        # Phase B Item 3 adds strong_made; Item 2 is still nuts-only.
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(hand_strength='strong_made')
        )
        assert not should_fire
        assert reason == 'hand_class_strong_made'

    def test_near_nuts_status_blocks(self):
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(nut_status='near_nuts')
        )
        assert not should_fire
        assert 'nut_status' in reason

    def test_wet_board_blocks(self):
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(danger_flag_count=2)
        )
        assert not should_fire
        assert reason == 'board_too_dangerous'

    def test_psychology_suppression_blocks(self):
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(adaptation_bias=0.0)
        )
        assert not should_fire
        assert reason == 'psychology_suppressed'

    def test_cold_start_hands_blocks(self):
        cold_stats = _barreler_stats(hands_observed=MIN_HANDS_OBSERVED - 1)
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(stats=cold_stats)
        )
        assert not should_fire
        assert reason == 'cold_start_hands'

    def test_cold_start_barrel_sample_blocks(self):
        cold_stats = _barreler_stats(
            barrel_opportunities=MIN_BARREL_OPPORTUNITIES - 1,
        )
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(stats=cold_stats)
        )
        assert not should_fire
        assert reason == 'cold_start_barrel_sample'

    def test_low_barrel_frequency_blocks(self):
        low_barrel_stats = _barreler_stats(
            barrel_frequency=MIN_BARREL_FREQUENCY - 0.01,
        )
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(stats=low_barrel_stats)
        )
        assert not should_fire
        assert reason == 'barrel_frequency_below_threshold'

    def test_phase_a_proxy_no_longer_blocks(self):
        """AF_pf and cbet_attempt_rate are no longer in the gate.
        A player with low AF_pf but high barrel_frequency should now
        pass — Phase B reads the barrel signal directly."""
        stats = _barreler_stats(
            aggression_factor_postflop=1.5,   # below Phase A threshold
            cbet_attempt_rate=0.40,            # below Phase A threshold
            barrel_frequency=0.90,             # above Phase B threshold
            barrel_opportunities=20,
        )
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(stats=stats)
        )
        assert should_fire
        assert reason == 'gate_pass'

    def test_station_blocks(self):
        station_stats = _barreler_stats(
            vpip=0.80,
            aggression_factor=0.5,
            vpip_per_voluntary_opportunity=0.85,
        )
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(stats=station_stats)
        )
        assert not should_fire
        assert reason == 'opponent_is_hyper_passive'


# ── Confidence-scaled call probability ─────────────────────────────

class TestCallProbability:
    def test_at_minimum_gate_yields_call_prob_min(self):
        stats = _barreler_stats(
            barrel_frequency=MIN_BARREL_FREQUENCY,
            barrel_opportunities=MIN_BARREL_OPPORTUNITIES,
        )
        assert compute_call_probability(stats) == pytest.approx(CALL_PROB_MIN)

    def test_at_maximum_gate_yields_call_prob_max(self):
        stats = _barreler_stats(
            barrel_frequency=RATE_RAMP_MAX,
            barrel_opportunities=int(OPPS_RAMP_MAX),
        )
        assert compute_call_probability(stats) == pytest.approx(CALL_PROB_MAX)

    def test_high_freq_low_sample_scales_intermediate(self):
        # Saturated rate, half sample (midpoint between MIN and MAX)
        stats = _barreler_stats(
            barrel_frequency=1.0,
            barrel_opportunities=int((OPPS_RAMP_MAX + MIN_BARREL_OPPORTUNITIES) / 2),
        )
        prob = compute_call_probability(stats)
        # rate_intensity=1.0, sample_confidence≈0.5, intensity≈0.5
        # call_prob ≈ 0.70 + 0.5 * 0.20 = 0.80
        assert CALL_PROB_MIN < prob < CALL_PROB_MAX
        assert prob == pytest.approx(0.80, abs=0.02)

    def test_low_freq_saturated_sample_scales_intermediate(self):
        # Mid rate, saturated sample
        stats = _barreler_stats(
            barrel_frequency=(RATE_RAMP_MAX + MIN_BARREL_FREQUENCY) / 2,
            barrel_opportunities=int(OPPS_RAMP_MAX),
        )
        prob = compute_call_probability(stats)
        # rate_intensity≈0.5, sample_confidence=1.0, intensity≈0.5
        # call_prob ≈ 0.80
        assert prob == pytest.approx(0.80, abs=0.01)


# ── Redistribution ─────────────────────────────────────────────────

class TestRedistribution:
    def test_compute_strategy_call_plus_raise(self):
        before = _facing_bet_strategy()
        after = compute_induce_override_strategy(before, call_probability=0.85)
        assert after.action_probabilities['call'] == pytest.approx(0.85)
        # All non-call mass goes to raise_75 (only raise action)
        assert after.action_probabilities['raise_75'] == pytest.approx(0.15)
        # 'fold' was dropped — induce decides between call and raise only
        assert 'fold' not in after.action_probabilities

    def test_compute_strategy_multiple_raise_actions_split_evenly(self):
        before = StrategyProfile(action_probabilities={
            'fold': 0.10, 'call': 0.40,
            'raise_50': 0.20, 'raise_75': 0.20, 'jam': 0.10,
        })
        after = compute_induce_override_strategy(before, call_probability=0.70)
        assert after.action_probabilities['call'] == pytest.approx(0.70)
        # 0.30 split across 3 raise actions
        assert after.action_probabilities['raise_50'] == pytest.approx(0.10)
        assert after.action_probabilities['raise_75'] == pytest.approx(0.10)
        assert after.action_probabilities['jam'] == pytest.approx(0.10)

    def test_compute_strategy_no_raise_falls_back_to_100_call(self):
        # Pathological: facing bet with no raise option in strategy
        before = StrategyProfile(action_probabilities={'fold': 0.5, 'call': 0.5})
        after = compute_induce_override_strategy(before, call_probability=0.80)
        assert after.action_probabilities == {'call': 1.0}

    def test_apply_returns_scaled_call_when_gate_passes(self):
        strategy = _facing_bet_strategy()
        kwargs = _baseline_kwargs()
        kwargs.pop('has_call')
        kwargs.pop('has_fold')
        new_strategy, trace = apply_induce_override(strategy, **kwargs)
        assert trace.fired
        # Stats have barrel_frequency=0.90 (saturated), barrel_opportunities=20
        # rate_intensity = ramp(0.90, 0.60, 0.85) = 1.0 (saturated)
        # sample_confidence = ramp(20, 5, 50) = 15/45 ≈ 0.333
        # intensity ≈ 0.333, call_prob ≈ 0.70 + 0.333*0.20 ≈ 0.767
        assert new_strategy.action_probabilities['call'] == pytest.approx(0.767, abs=0.005)

    def test_apply_returns_unchanged_strategy_when_gate_fails(self):
        strategy = _facing_bet_strategy()
        kwargs = _baseline_kwargs(street='river')
        kwargs.pop('has_call')
        kwargs.pop('has_fold')
        new_strategy, trace = apply_induce_override(strategy, **kwargs)
        assert not trace.fired
        assert new_strategy is strategy


# ── Ablation ────────────────────────────────────────────────────────

class TestAblation:
    def test_disabled_rule_short_circuits(self):
        strategy = _facing_bet_strategy()
        kwargs = _baseline_kwargs()
        kwargs.pop('has_call')
        kwargs.pop('has_fold')
        disable_rules = frozenset({('induce_override', 'default')})
        new_strategy, trace = apply_induce_override(
            strategy, disable_rules=disable_rules, **kwargs,
        )
        assert not trace.fired
        assert new_strategy is strategy


# ── Trace contents ─────────────────────────────────────────────────

class TestTraceShape:
    def test_fired_trace_layer_and_operation(self):
        strategy = _facing_bet_strategy()
        kwargs = _baseline_kwargs()
        kwargs.pop('has_call')
        kwargs.pop('has_fold')
        _, trace = apply_induce_override(strategy, **kwargs)
        assert trace.fired
        assert trace.layer == 'induce_override'
        assert trace.layer_order == layer_order_for('induce_override')
        assert trace.operation == InterventionOperation.OVERRIDE.value

    def test_fired_trace_inputs_capture_barrel_signal(self):
        strategy = _facing_bet_strategy()
        kwargs = _baseline_kwargs()
        kwargs.pop('has_call')
        kwargs.pop('has_fold')
        _, trace = apply_induce_override(strategy, **kwargs)
        inputs = trace.inputs
        assert inputs['hand_strength'] == 'nuts'
        assert inputs['street'] == 'flop'
        # Phase B Item 2: trace records barrel signal + call_prob
        assert inputs['barrel_frequency'] == 0.9
        assert inputs['barrel_opportunities'] == 20
        assert 'call_probability' in inputs
        assert CALL_PROB_MIN <= inputs['call_probability'] <= CALL_PROB_MAX

    def test_fired_trace_action_changed_when_raise_was_primary(self):
        strategy = StrategyProfile(action_probabilities={
            'fold': 0.10, 'call': 0.30, 'raise_75': 0.60,
        })
        kwargs = _baseline_kwargs()
        kwargs.pop('has_call')
        kwargs.pop('has_fold')
        _, trace = apply_induce_override(strategy, **kwargs)
        assert trace.action_changed
        assert trace.primary_action_after == 'call'

    def test_no_op_trace_has_reason_code(self):
        strategy = _facing_bet_strategy()
        kwargs = _baseline_kwargs(street='river')
        kwargs.pop('has_call')
        kwargs.pop('has_fold')
        _, trace = apply_induce_override(strategy, **kwargs)
        assert not trace.fired
        assert 'river' in trace.reason_code
