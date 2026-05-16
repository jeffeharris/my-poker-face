"""Phase A induce_override tests.

Covers (1) the gate logic — one positive baseline scenario, then
mutate each gate component to verify it correctly blocks; (2) the
redistribution — 100% call when fired; (3) ablation hook;
(4) trace shape including reason_codes.
"""

import pytest

from poker.strategy.exploitation import (
    AggregatedOpponentStats,
    DecisionContext,
)
from poker.strategy.induce_override import (
    ELIGIBLE_NUT_STATUS,
    ELIGIBLE_STREETS,
    MIN_AGGRESSION_FACTOR_POSTFLOP,
    MIN_CBET_ATTEMPT_RATE,
    MIN_EFFECTIVE_STACK_BB,
    MIN_HANDS_OBSERVED,
    MIN_POSTFLOP_SEEN_AS_PFR,
    apply_induce_override,
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
    """ManiacBot-like opponent that passes the barreler proxy gate."""
    base = dict(
        hands_observed=30,
        vpip=0.80, pfr=0.80,
        aggression_factor=5.0,
        all_in_frequency=0.0,
        aggression_factor_postflop=4.0,
        cbet_attempt_rate=0.85,
        postflop_seen_as_pfr_count=12,
        cbet_faced_count=0,
        all_in_per_facing_bet=0.0,
        facing_bet_opportunities=0,
        postflop_jam_open_rate=0.0,
        postflop_open_opportunities=15,
        # vpip_per_voluntary_opportunity defaults to 0.5 — keep low so
        # _is_hyper_passive returns False (requires > 0.70 AND AF < 0.80,
        # neither of which holds for a maniac)
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
    """Strategy shape the rule sees: post-personality with fold/call/raise."""
    return StrategyProfile(action_probabilities={
        'fold': 0.20,
        'call': 0.50,
        'raise_75': 0.30,
    })


def _baseline_kwargs(**overrides):
    """Phase A gate-pass scenario. Override any single field to test
    its blocking behavior."""
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

    def test_preflop_street_blocks(self):
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(street='preflop')
        )
        assert not should_fire
        assert 'preflop' in reason

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

    def test_strong_made_not_eligible_in_phase_a(self):
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(hand_strength='strong_made')
        )
        assert not should_fire
        assert reason == 'hand_class_strong_made'

    def test_near_nuts_status_blocks(self):
        # Phase A is `actual_nuts` only; `near_nuts` waits for Phase B.
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

    def test_cold_start_cbet_sample_blocks(self):
        cold_stats = _barreler_stats(
            postflop_seen_as_pfr_count=MIN_POSTFLOP_SEEN_AS_PFR - 1,
        )
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(stats=cold_stats)
        )
        assert not should_fire
        assert reason == 'cold_start_cbet_sample'

    def test_low_af_postflop_blocks(self):
        low_af_stats = _barreler_stats(
            aggression_factor_postflop=MIN_AGGRESSION_FACTOR_POSTFLOP - 0.1,
        )
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(stats=low_af_stats)
        )
        assert not should_fire
        assert reason == 'af_postflop_below_threshold'

    def test_low_cbet_rate_blocks(self):
        low_cbet_stats = _barreler_stats(
            cbet_attempt_rate=MIN_CBET_ATTEMPT_RATE - 0.01,
        )
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(stats=low_cbet_stats)
        )
        assert not should_fire
        assert reason == 'cbet_rate_below_threshold'

    def test_station_blocks(self):
        # A passive station: high VPIP-per-vol, low AF. Even though
        # this scenario wouldn't normally clear AF_pf, we construct a
        # pathological one to check the exclusion fires.
        station_stats = _barreler_stats(
            vpip=0.80,
            aggression_factor=0.5,  # below HYPER_PASSIVE_AF_THRESHOLD (0.80)
            vpip_per_voluntary_opportunity=0.85,  # above 0.70 threshold
        )
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(stats=station_stats)
        )
        assert not should_fire
        assert reason == 'opponent_is_hyper_passive'


# ── Redistribution ─────────────────────────────────────────────────

class TestRedistribution:
    def test_smooth_call_is_100_percent(self):
        before = _facing_bet_strategy()
        after = compute_induce_override_strategy(before)
        assert after.action_probabilities == {'call': 1.0}

    def test_apply_returns_smooth_call_when_gate_passes(self):
        strategy = _facing_bet_strategy()
        kwargs = _baseline_kwargs()
        # apply_induce_override doesn't take has_call/has_fold —
        # they're derived from the strategy. Strip them.
        kwargs.pop('has_call')
        kwargs.pop('has_fold')
        new_strategy, trace = apply_induce_override(strategy, **kwargs)
        assert trace.fired
        assert new_strategy.action_probabilities == {'call': 1.0}

    def test_apply_returns_unchanged_strategy_when_gate_fails(self):
        strategy = _facing_bet_strategy()
        kwargs = _baseline_kwargs(street='river')  # gate fails
        kwargs.pop('has_call')
        kwargs.pop('has_fold')
        new_strategy, trace = apply_induce_override(strategy, **kwargs)
        assert not trace.fired
        assert new_strategy is strategy


# ── Ablation ────────────────────────────────────────────────────────

class TestAblation:
    def test_disabled_rule_short_circuits_with_disabled_trace(self):
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
        # The disabled trace should not look like a regular no-op
        assert trace.reason_code in ('disabled_by_ablation', '')


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
        assert trace.rule_id == 'default'
        assert trace.layer_order == layer_order_for('induce_override')
        assert trace.operation == InterventionOperation.OVERRIDE.value

    def test_fired_trace_inputs_capture_barreler_signal(self):
        strategy = _facing_bet_strategy()
        kwargs = _baseline_kwargs()
        kwargs.pop('has_call')
        kwargs.pop('has_fold')
        _, trace = apply_induce_override(strategy, **kwargs)
        inputs = trace.inputs
        assert inputs['hand_strength'] == 'nuts'
        assert inputs['nut_status'] == ELIGIBLE_NUT_STATUS
        assert inputs['street'] == 'flop'
        assert inputs['position'] == 'IP'
        assert inputs['aggression_factor_postflop'] == 4.0
        assert inputs['cbet_attempt_rate'] == 0.85
        assert inputs['postflop_seen_as_pfr_count'] == 12

    def test_fired_trace_action_changed_when_raise_was_primary(self):
        # Strategy where raise is primary (>50%) → induce flip to call
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
