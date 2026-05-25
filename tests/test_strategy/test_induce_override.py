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
    HAND_CLASS_GATES,
    MIN_BARREL_FREQUENCY,
    MIN_BARREL_OPPORTUNITIES,
    MIN_CBET_ATTEMPT_RATE,
    MIN_EFFECTIVE_STACK_BB,
    MIN_FLOP_CHECK_THEN_BARREL_FREQUENCY,
    MIN_FLOP_CHECK_THEN_BARREL_OPPORTUNITIES,
    MIN_HANDS_OBSERVED,
    MIN_OOP_CHECK_RAISE_BARREL_FREQUENCY,
    MIN_POSTFLOP_SEEN_AS_PFR,
    OOP_CHECK_RAISE_PROBABILITY,
    OOP_TRAP_CHECK_PROBABILITY,
    OPEN_SPOT_CHECK_PROBABILITY,
    OPPS_RAMP_MAX,
    RATE_RAMP_MAX,
    apply_induce_override,
    compute_call_probability,
    compute_induce_override_strategy,
    compute_oop_check_raise_strategy,
    compute_oop_trap_check_strategy,
    compute_open_spot_induce_strategy,
    should_apply_induce_override,
    should_apply_oop_check_raise,
    should_apply_oop_trap_check,
    should_apply_open_spot_induce,
)

# Phase B Item 2 baseline: nuts + actual_nuts. Used in fixtures where
# the test predates Item 3's strong_made extension.
ELIGIBLE_NUT_STATUS = 'actual_nuts'
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
        vpip=0.80,
        pfr=0.80,
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
    return StrategyProfile(
        action_probabilities={
            'fold': 0.20,
            'call': 0.50,
            'raise_75': 0.30,
        }
    )


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
        should_fire, reason = should_apply_induce_override(**_baseline_kwargs(has_call=False))
        assert not should_fire
        assert reason == 'no_call_action'

    def test_no_fold_action_means_not_facing_bet(self):
        should_fire, reason = should_apply_induce_override(**_baseline_kwargs(has_fold=False))
        assert not should_fire
        assert reason == 'not_facing_bet'

    def test_facing_all_in_blocks(self):
        ctx = DecisionContext(facing_all_in=True)
        should_fire, reason = should_apply_induce_override(**_baseline_kwargs(decision_context=ctx))
        assert not should_fire
        assert reason == 'facing_all_in'

    def test_river_street_blocks(self):
        should_fire, reason = should_apply_induce_override(**_baseline_kwargs(street='river'))
        assert not should_fire
        assert 'river' in reason

    def test_oop_blocks(self):
        should_fire, reason = should_apply_induce_override(**_baseline_kwargs(position='OOP'))
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

    def test_medium_made_not_eligible(self):
        # Phase B Item 3 extends to strong_made; weaker classes still blocked.
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(hand_strength='medium_made')
        )
        assert not should_fire
        assert reason == 'hand_class_medium_made'

    def test_near_nuts_blocks_for_nuts_class(self):
        # nuts class requires actual_nuts (the only nut_status in its
        # allowlist). near_nuts → block. (Item 3 lets strong_made accept
        # near_nuts; the nuts class itself stays strict.)
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(nut_status='near_nuts')
        )
        assert not should_fire
        assert 'nut_status' in reason

    def test_wet_board_blocks(self):
        should_fire, reason = should_apply_induce_override(**_baseline_kwargs(danger_flag_count=2))
        assert not should_fire
        assert reason == 'board_too_dangerous'

    def test_psychology_suppression_blocks(self):
        should_fire, reason = should_apply_induce_override(**_baseline_kwargs(adaptation_bias=0.0))
        assert not should_fire
        assert reason == 'psychology_suppressed'

    def test_cold_start_hands_blocks(self):
        cold_stats = _barreler_stats(hands_observed=MIN_HANDS_OBSERVED - 1)
        should_fire, reason = should_apply_induce_override(**_baseline_kwargs(stats=cold_stats))
        assert not should_fire
        assert reason == 'cold_start_hands'

    def test_cold_start_barrel_sample_blocks(self):
        cold_stats = _barreler_stats(
            barrel_opportunities=MIN_BARREL_OPPORTUNITIES - 1,
        )
        should_fire, reason = should_apply_induce_override(**_baseline_kwargs(stats=cold_stats))
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
            aggression_factor_postflop=1.5,  # below Phase A threshold
            cbet_attempt_rate=0.40,  # below Phase A threshold
            barrel_frequency=0.90,  # above Phase B threshold
            barrel_opportunities=20,
        )
        should_fire, reason = should_apply_induce_override(**_baseline_kwargs(stats=stats))
        assert should_fire
        assert reason == 'gate_pass'

    def test_station_blocks(self):
        station_stats = _barreler_stats(
            vpip=0.80,
            aggression_factor=0.5,
            vpip_per_voluntary_opportunity=0.85,
        )
        should_fire, reason = should_apply_induce_override(**_baseline_kwargs(stats=station_stats))
        assert not should_fire
        assert reason == 'opponent_is_hyper_passive'


# ── Phase B Item 3: strong_made inclusion ──────────────────────────


class TestItem3StrongMade:
    """strong_made class is eligible with stricter texture + nut_status
    requirements vs the original nuts class."""

    def test_strong_made_actual_nuts_dry_board_fires(self):
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(
                hand_strength='strong_made',
                nut_status='actual_nuts',
                danger_flag_count=0,
            )
        )
        assert should_fire
        assert reason == 'gate_pass'

    def test_strong_made_near_nuts_dry_board_fires(self):
        # Item 3 explicitly extends nut_status to near_nuts for strong_made.
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(
                hand_strength='strong_made',
                nut_status='near_nuts',
                danger_flag_count=0,
            )
        )
        assert should_fire
        assert reason == 'gate_pass'

    def test_strong_made_non_nut_strong_blocked(self):
        # non_nut_strong is excluded from strong_made's allowlist.
        # E.g. top pair good kicker on a board with possible higher made
        # hands — too risky to trap.
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(
                hand_strength='strong_made',
                nut_status='non_nut_strong',
                danger_flag_count=0,
            )
        )
        assert not should_fire
        assert reason == 'nut_status_non_nut_strong'

    def test_strong_made_bluff_catcher_blocked(self):
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(
                hand_strength='strong_made',
                nut_status='bluff_catcher',
                danger_flag_count=0,
            )
        )
        assert not should_fire
        assert reason == 'nut_status_bluff_catcher'

    def test_strong_made_one_danger_flag_blocked(self):
        # Stricter texture gate for strong_made: 0 danger flags only.
        # (Nuts class still tolerates 1 — see HAND_CLASS_GATES.)
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(
                hand_strength='strong_made',
                nut_status='actual_nuts',
                danger_flag_count=1,
            )
        )
        assert not should_fire
        assert reason == 'board_too_dangerous'

    def test_nuts_class_still_tolerates_one_danger_flag(self):
        # Nuts class is unchanged from Phase B Item 2: 1 danger flag OK.
        should_fire, reason = should_apply_induce_override(
            **_baseline_kwargs(
                hand_strength='nuts',
                nut_status='actual_nuts',
                danger_flag_count=1,
            )
        )
        assert should_fire
        assert reason == 'gate_pass'

    def test_hand_class_gates_table_shape(self):
        # Sanity-check the gating table shape so future edits don't
        # silently drop a class or relax a constraint.
        assert 'nuts' in HAND_CLASS_GATES
        assert 'strong_made' in HAND_CLASS_GATES
        nuts_statuses, nuts_max_danger = HAND_CLASS_GATES['nuts']
        sm_statuses, sm_max_danger = HAND_CLASS_GATES['strong_made']
        # Sanity: strong_made is STRICTER on danger flags
        assert sm_max_danger < nuts_max_danger
        # Sanity: strong_made's nut_status allowlist includes nuts'
        assert nuts_statuses <= sm_statuses


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
        before = StrategyProfile(
            action_probabilities={
                'fold': 0.10,
                'call': 0.40,
                'raise_50': 0.20,
                'raise_75': 0.20,
                'jam': 0.10,
            }
        )
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
            strategy,
            disable_rules=disable_rules,
            **kwargs,
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
        strategy = StrategyProfile(
            action_probabilities={
                'fold': 0.10,
                'call': 0.30,
                'raise_75': 0.60,
            }
        )
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


# ── Phase B Item 4: open-spot IP induce ───────────────────────────


def _trap_bait_stats(**overrides) -> AggregatedOpponentStats:
    """TrapBaitBot-like opponent that passes the Phase B Item 4 gate."""
    base = dict(
        hands_observed=30,
        vpip=0.80,
        pfr=0.70,
        aggression_factor=4.0,
        all_in_frequency=0.0,
        aggression_factor_postflop=3.0,
        cbet_attempt_rate=0.30,
        postflop_seen_as_pfr_count=15,
        cbet_faced_count=0,
        all_in_per_facing_bet=0.0,
        facing_bet_opportunities=0,
        postflop_jam_open_rate=0.0,
        postflop_open_opportunities=15,
        # Facing-bet barrel stats stay neutral — Item 4 reads the
        # flop-check-then-barrel rate instead.
        barrel_frequency=0.5,
        barrel_opportunities=0,
        flop_check_then_barrel_rate=0.80,
        flop_check_barrel_opportunities=20,
    )
    base.update(overrides)
    return AggregatedOpponentStats(**base)


def _open_spot_strategy() -> StrategyProfile:
    """Hero free to act, default strategy mixes check/bet (no fold)."""
    return StrategyProfile(
        action_probabilities={
            'check': 0.30,
            'raise_50': 0.30,
            'raise_75': 0.40,
        }
    )


def _open_spot_context() -> DecisionContext:
    return DecisionContext(facing_all_in=False, bet_size_pot_ratio=0.0)


def _open_spot_kwargs(**overrides):
    base = dict(
        stats=_trap_bait_stats(),
        hand_strength='nuts',
        nut_status='actual_nuts',
        street='flop',
        position='IP',
        danger_flag_count=0,
        effective_stack_bb=100.0,
        active_opponent_count=1,
        decision_context=_open_spot_context(),
        has_check=True,
        has_fold=False,
        adaptation_bias=0.8,
        tilt_factor=1.0,
    )
    base.update(overrides)
    return base


class TestOpenSpotGateBaseline:
    def test_baseline_open_spot_fires(self):
        should_fire, reason = should_apply_open_spot_induce(**_open_spot_kwargs())
        assert should_fire
        assert reason == 'gate_pass'


class TestOpenSpotGateBlocks:
    def test_no_check_blocks(self):
        should_fire, reason = should_apply_open_spot_induce(**_open_spot_kwargs(has_check=False))
        assert not should_fire
        assert reason == 'no_check_action'

    def test_facing_bet_blocks_open_spot(self):
        # Open-spot branch defers to facing-bet branch when fold is offered.
        should_fire, reason = should_apply_open_spot_induce(**_open_spot_kwargs(has_fold=True))
        assert not should_fire
        assert reason == 'facing_bet_use_facing_branch'

    def test_oop_blocks_open_spot(self):
        should_fire, reason = should_apply_open_spot_induce(**_open_spot_kwargs(position='OOP'))
        assert not should_fire
        assert reason == 'oop_not_supported_open_spot'

    def test_river_blocks(self):
        should_fire, reason = should_apply_open_spot_induce(**_open_spot_kwargs(street='river'))
        assert not should_fire
        assert 'river' in reason

    def test_cold_start_fcb_sample_blocks(self):
        cold_stats = _trap_bait_stats(
            flop_check_barrel_opportunities=MIN_FLOP_CHECK_THEN_BARREL_OPPORTUNITIES - 1,
        )
        should_fire, reason = should_apply_open_spot_induce(**_open_spot_kwargs(stats=cold_stats))
        assert not should_fire
        assert reason == 'cold_start_flop_check_barrel_sample'

    def test_low_fcb_rate_blocks(self):
        low_stats = _trap_bait_stats(
            flop_check_then_barrel_rate=MIN_FLOP_CHECK_THEN_BARREL_FREQUENCY - 0.01,
        )
        should_fire, reason = should_apply_open_spot_induce(**_open_spot_kwargs(stats=low_stats))
        assert not should_fire
        assert reason == 'flop_check_barrel_rate_below_threshold'

    def test_strong_made_dry_board_fires(self):
        should_fire, reason = should_apply_open_spot_induce(
            **_open_spot_kwargs(
                hand_strength='strong_made',
                nut_status='near_nuts',
                danger_flag_count=0,
            )
        )
        assert should_fire
        assert reason == 'gate_pass'

    def test_strong_made_wet_board_blocks(self):
        should_fire, reason = should_apply_open_spot_induce(
            **_open_spot_kwargs(
                hand_strength='strong_made',
                nut_status='actual_nuts',
                danger_flag_count=1,
            )
        )
        assert not should_fire
        assert reason == 'board_too_dangerous'


class TestOpenSpotRedistribution:
    def test_check_plus_raise(self):
        before = _open_spot_strategy()
        after = compute_open_spot_induce_strategy(before)
        assert after.action_probabilities['check'] == pytest.approx(OPEN_SPOT_CHECK_PROBABILITY)
        # 0.30 split across raise_50 and raise_75 = 0.15 each
        assert after.action_probabilities['raise_50'] == pytest.approx(0.15)
        assert after.action_probabilities['raise_75'] == pytest.approx(0.15)

    def test_no_raise_falls_back_to_100_check(self):
        before = StrategyProfile(action_probabilities={'check': 1.0})
        after = compute_open_spot_induce_strategy(before)
        assert after.action_probabilities == {'check': 1.0}


class TestOpenSpotApply:
    def test_open_spot_fire_via_dispatch(self):
        """Dispatch in apply_induce_override should route open-spot
        strategies (has_check, no has_fold) to the open-spot branch."""
        strategy = _open_spot_strategy()
        kwargs = _open_spot_kwargs()
        kwargs.pop('has_check')
        kwargs.pop('has_fold')
        new_strategy, trace = apply_induce_override(strategy, **kwargs)
        assert trace.fired
        assert trace.effect == 'check_back'
        assert trace.reason_code == 'induced_flop_open_spot'
        assert new_strategy.action_probabilities['check'] == pytest.approx(
            OPEN_SPOT_CHECK_PROBABILITY
        )

    def test_open_spot_trace_inputs_capture_fcb_signal(self):
        strategy = _open_spot_strategy()
        kwargs = _open_spot_kwargs()
        kwargs.pop('has_check')
        kwargs.pop('has_fold')
        _, trace = apply_induce_override(strategy, **kwargs)
        inputs = trace.inputs
        assert inputs['flop_check_then_barrel_rate'] == 0.8
        assert inputs['flop_check_barrel_opportunities'] == 20
        assert inputs['check_probability'] == OPEN_SPOT_CHECK_PROBABILITY
        assert 'barrel_frequency' not in inputs  # not relevant for open-spot

    def test_dispatch_neither_branch_returns_no_op(self):
        """If neither has_fold nor has_check (impossible in normal play,
        but defensive), no branch fires."""
        strategy = StrategyProfile(action_probabilities={'call': 1.0})
        kwargs = _open_spot_kwargs()
        kwargs.pop('has_check')
        kwargs.pop('has_fold')
        new_strategy, trace = apply_induce_override(strategy, **kwargs)
        assert not trace.fired
        assert trace.reason_code == 'no_actionable_spot'

    def test_dispatch_facing_bet_still_works(self):
        """Sanity: facing-bet dispatch (has_fold=True) still hits the
        Item 2 branch with smooth_call effect, not check_back."""
        strategy = _facing_bet_strategy()
        kwargs = _baseline_kwargs()
        kwargs.pop('has_call')
        kwargs.pop('has_fold')
        _, trace = apply_induce_override(strategy, **kwargs)
        assert trace.fired
        assert trace.effect == 'smooth_call'
        assert 'facing_bet' in trace.reason_code

    def test_open_spot_ablation_short_circuits(self):
        strategy = _open_spot_strategy()
        kwargs = _open_spot_kwargs()
        kwargs.pop('has_check')
        kwargs.pop('has_fold')
        disable_rules = frozenset({('induce_override', 'default')})
        new_strategy, trace = apply_induce_override(
            strategy,
            disable_rules=disable_rules,
            **kwargs,
        )
        assert not trace.fired
        assert new_strategy is strategy


# ── Phase B Item 5: OOP induce (trap-check + check-raise) ─────────


def _cbet_spammer_stats(**overrides) -> AggregatedOpponentStats:
    """Maniac-like opponent that cbets often AND barrels — passes both
    Item 5 OOP gates."""
    base = dict(
        hands_observed=30,
        vpip=0.80,
        pfr=0.80,
        aggression_factor=5.0,
        all_in_frequency=0.0,
        aggression_factor_postflop=4.0,
        cbet_attempt_rate=0.90,
        postflop_seen_as_pfr_count=15,
        cbet_faced_count=0,
        all_in_per_facing_bet=0.0,
        facing_bet_opportunities=0,
        postflop_jam_open_rate=0.0,
        postflop_open_opportunities=15,
        barrel_frequency=0.80,
        barrel_opportunities=10,
        # Item 4 stats neutral
        flop_check_then_barrel_rate=0.5,
        flop_check_barrel_opportunities=0,
    )
    base.update(overrides)
    return AggregatedOpponentStats(**base)


def _oop_open_spot_strategy() -> StrategyProfile:
    """Hero OOP free to act with strong hand — default has mixed check/bet."""
    return StrategyProfile(
        action_probabilities={
            'check': 0.30,
            'raise_50': 0.30,
            'raise_75': 0.40,
        }
    )


def _oop_facing_bet_strategy() -> StrategyProfile:
    """Hero OOP facing cbet with strong hand — default has mostly call."""
    return StrategyProfile(
        action_probabilities={
            'fold': 0.10,
            'call': 0.60,
            'raise_75': 0.30,
        }
    )


def _oop_open_spot_kwargs(**overrides):
    base = dict(
        stats=_cbet_spammer_stats(),
        hand_strength='nuts',
        nut_status='actual_nuts',
        street='flop',
        position='OOP',
        danger_flag_count=0,
        effective_stack_bb=100.0,
        active_opponent_count=1,
        decision_context=DecisionContext(facing_all_in=False, bet_size_pot_ratio=0.0),
        has_check=True,
        has_fold=False,
        adaptation_bias=0.8,
        tilt_factor=1.0,
    )
    base.update(overrides)
    return base


def _oop_facing_bet_kwargs(**overrides):
    base = dict(
        stats=_cbet_spammer_stats(),
        hand_strength='nuts',
        nut_status='actual_nuts',
        street='flop',
        position='OOP',
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


class TestOOPTrapCheckGate:
    def test_baseline_oop_open_spot_fires(self):
        should_fire, reason = should_apply_oop_trap_check(**_oop_open_spot_kwargs())
        assert should_fire
        assert reason == 'gate_pass'

    def test_no_check_blocks(self):
        should_fire, reason = should_apply_oop_trap_check(**_oop_open_spot_kwargs(has_check=False))
        assert not should_fire
        assert reason == 'no_check_action'

    def test_facing_bet_blocks_oop_trap_check(self):
        should_fire, reason = should_apply_oop_trap_check(**_oop_open_spot_kwargs(has_fold=True))
        assert not should_fire
        assert reason == 'facing_bet_use_facing_branch'

    def test_ip_blocks_oop_trap_check(self):
        should_fire, reason = should_apply_oop_trap_check(**_oop_open_spot_kwargs(position='IP'))
        assert not should_fire
        assert reason == 'ip_not_supported_oop_branch'

    def test_cold_start_cbet_sample_blocks(self):
        cold = _cbet_spammer_stats(
            postflop_seen_as_pfr_count=MIN_POSTFLOP_SEEN_AS_PFR - 1,
        )
        should_fire, reason = should_apply_oop_trap_check(**_oop_open_spot_kwargs(stats=cold))
        assert not should_fire
        assert reason == 'cold_start_cbet_sample'

    def test_low_cbet_attempt_rate_blocks(self):
        low = _cbet_spammer_stats(
            cbet_attempt_rate=MIN_CBET_ATTEMPT_RATE - 0.01,
        )
        should_fire, reason = should_apply_oop_trap_check(**_oop_open_spot_kwargs(stats=low))
        assert not should_fire
        assert reason == 'cbet_attempt_rate_below_threshold'

    def test_river_blocks(self):
        should_fire, reason = should_apply_oop_trap_check(**_oop_open_spot_kwargs(street='river'))
        assert not should_fire
        assert 'river' in reason


class TestOOPTrapCheckRedistribution:
    def test_check_plus_raise_split(self):
        before = _oop_open_spot_strategy()
        after = compute_oop_trap_check_strategy(before)
        assert after.action_probabilities['check'] == pytest.approx(OOP_TRAP_CHECK_PROBABILITY)
        # 0.20 split across raise_50 and raise_75 = 0.10 each
        assert after.action_probabilities['raise_50'] == pytest.approx(0.10)
        assert after.action_probabilities['raise_75'] == pytest.approx(0.10)

    def test_no_raise_falls_back_to_100_check(self):
        before = StrategyProfile(action_probabilities={'check': 1.0})
        after = compute_oop_trap_check_strategy(before)
        assert after.action_probabilities == {'check': 1.0}


class TestOOPCheckRaiseGate:
    def test_baseline_oop_facing_bet_fires(self):
        should_fire, reason = should_apply_oop_check_raise(**_oop_facing_bet_kwargs())
        assert should_fire
        assert reason == 'gate_pass'

    def test_no_call_blocks(self):
        should_fire, reason = should_apply_oop_check_raise(**_oop_facing_bet_kwargs(has_call=False))
        assert not should_fire
        assert reason == 'no_call_action'

    def test_no_fold_blocks(self):
        # not facing a bet
        should_fire, reason = should_apply_oop_check_raise(**_oop_facing_bet_kwargs(has_fold=False))
        assert not should_fire
        assert reason == 'not_facing_bet'

    def test_ip_blocks_check_raise(self):
        should_fire, reason = should_apply_oop_check_raise(**_oop_facing_bet_kwargs(position='IP'))
        assert not should_fire
        assert reason == 'ip_not_supported_oop_branch'

    def test_low_barrel_frequency_blocks(self):
        low = _cbet_spammer_stats(
            barrel_frequency=MIN_OOP_CHECK_RAISE_BARREL_FREQUENCY - 0.01,
        )
        should_fire, reason = should_apply_oop_check_raise(**_oop_facing_bet_kwargs(stats=low))
        assert not should_fire
        assert reason == 'barrel_frequency_below_threshold'

    def test_low_cbet_attempt_rate_blocks(self):
        low = _cbet_spammer_stats(
            cbet_attempt_rate=MIN_CBET_ATTEMPT_RATE - 0.01,
        )
        should_fire, reason = should_apply_oop_check_raise(**_oop_facing_bet_kwargs(stats=low))
        assert not should_fire
        assert reason == 'cbet_attempt_rate_below_threshold'


class TestOOPCheckRaiseRedistribution:
    def test_raise_plus_call_split(self):
        before = _oop_facing_bet_strategy()
        after = compute_oop_check_raise_strategy(before)
        # 0.80 to the single raise action
        assert after.action_probabilities['raise_75'] == pytest.approx(OOP_CHECK_RAISE_PROBABILITY)
        # 0.20 to call
        assert after.action_probabilities['call'] == pytest.approx(0.20)
        # fold dropped (we have a strong hand)
        assert 'fold' not in after.action_probabilities

    def test_multiple_raises_split_evenly(self):
        before = StrategyProfile(
            action_probabilities={
                'fold': 0.10,
                'call': 0.50,
                'raise_50': 0.20,
                'raise_75': 0.15,
                'jam': 0.05,
            }
        )
        after = compute_oop_check_raise_strategy(before)
        # 0.80 split across 3 raise actions ≈ 0.267 each
        for action in ('raise_50', 'raise_75', 'jam'):
            assert after.action_probabilities[action] == pytest.approx(
                OOP_CHECK_RAISE_PROBABILITY / 3
            )
        assert after.action_probabilities['call'] == pytest.approx(0.20)

    def test_no_raise_falls_back_to_call(self):
        before = StrategyProfile(action_probabilities={'fold': 0.5, 'call': 0.5})
        after = compute_oop_check_raise_strategy(before)
        assert after.action_probabilities == {'call': 1.0}


class TestApplyOOPDispatch:
    """Test the 2×2 dispatch in apply_induce_override correctly routes
    by both action-set (facing-bet vs open-spot) and position."""

    def test_oop_open_spot_routes_to_trap_check(self):
        strategy = _oop_open_spot_strategy()
        kwargs = _oop_open_spot_kwargs()
        kwargs.pop('has_check')
        kwargs.pop('has_fold')
        new_strategy, trace = apply_induce_override(strategy, **kwargs)
        assert trace.fired
        assert trace.effect == 'trap_check'
        assert trace.reason_code == 'induced_flop_oop_trap_check'

    def test_oop_facing_bet_routes_to_check_raise(self):
        strategy = _oop_facing_bet_strategy()
        kwargs = _oop_facing_bet_kwargs()
        kwargs.pop('has_call')
        kwargs.pop('has_fold')
        new_strategy, trace = apply_induce_override(strategy, **kwargs)
        assert trace.fired
        assert trace.effect == 'check_raise'
        assert trace.reason_code == 'induced_flop_oop_check_raise'

    def test_ip_facing_bet_still_routes_to_smooth_call(self):
        """Item 2 path: IP + facing bet → smooth-call (effect smooth_call)."""
        strategy = _facing_bet_strategy()
        kwargs = _baseline_kwargs()
        kwargs.pop('has_call')
        kwargs.pop('has_fold')
        new_strategy, trace = apply_induce_override(strategy, **kwargs)
        assert trace.fired
        assert trace.effect == 'smooth_call'

    def test_ip_open_spot_still_routes_to_check_back(self):
        """Item 4 path: IP + open spot → check back (effect check_back)."""
        strategy = _open_spot_strategy()
        kwargs = _open_spot_kwargs()
        kwargs.pop('has_check')
        kwargs.pop('has_fold')
        new_strategy, trace = apply_induce_override(strategy, **kwargs)
        assert trace.fired
        assert trace.effect == 'check_back'

    def test_oop_check_raise_trace_inputs(self):
        strategy = _oop_facing_bet_strategy()
        kwargs = _oop_facing_bet_kwargs()
        kwargs.pop('has_call')
        kwargs.pop('has_fold')
        _, trace = apply_induce_override(strategy, **kwargs)
        inputs = trace.inputs
        assert inputs['cbet_attempt_rate'] == 0.9
        assert inputs['barrel_frequency'] == 0.8
        assert inputs['raise_probability'] == OOP_CHECK_RAISE_PROBABILITY

    def test_oop_trap_check_trace_inputs(self):
        strategy = _oop_open_spot_strategy()
        kwargs = _oop_open_spot_kwargs()
        kwargs.pop('has_check')
        kwargs.pop('has_fold')
        _, trace = apply_induce_override(strategy, **kwargs)
        inputs = trace.inputs
        assert inputs['cbet_attempt_rate'] == 0.9
        assert inputs['check_probability'] == OOP_TRAP_CHECK_PROBABILITY

    def test_oop_ablation_short_circuits(self):
        strategy = _oop_facing_bet_strategy()
        kwargs = _oop_facing_bet_kwargs()
        kwargs.pop('has_call')
        kwargs.pop('has_fold')
        disable_rules = frozenset({('induce_override', 'default')})
        new_strategy, trace = apply_induce_override(
            strategy,
            disable_rules=disable_rules,
            **kwargs,
        )
        assert not trace.fired
        assert new_strategy is strategy
