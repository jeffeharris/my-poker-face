"""Golden gate for the induce_override trace-assembly refactor.

These tests pin the EXACT (new_strategy, InterventionTrace) output of the
four `_apply_*` branch functions in `poker.strategy.induce_override` for
both their fire path and a no-op (gate-fail) path.

The expected values below are HARD-CODED captures from the pre-refactor
code. The behavior-preserving refactor extracts a shared trace-assembly
helper; these tests must remain bit-identical green across that change.
Any divergence is a regression in the refactor, not a test to update.

Branch coverage (4 fire + 4 no-op regimes):
  - _apply_facing_bet_induce   (Item 2, IP facing-bet smooth-call)
  - _apply_open_spot_induce     (Item 4, IP open-spot check-back)
  - _apply_oop_trap_check       (Item 5a, OOP open-spot trap-check)
  - _apply_oop_check_raise      (Item 5b, OOP facing-bet check-raise)

No-op path: each branch driven into the `facing_all_in` gate failure,
which must return the input strategy object unchanged plus a no-op trace.
"""

import pytest

from poker.strategy.exploitation import (
    AggregatedOpponentStats,
    DecisionContext,
)
from poker.strategy.induce_override import (
    _apply_facing_bet_induce,
    _apply_oop_check_raise,
    _apply_oop_trap_check,
    _apply_open_spot_induce,
)
from poker.strategy.intervention_trace import InterventionTrace
from poker.strategy.strategy_profile import StrategyProfile

# ── Input builders (mirror test_induce_override.py fixtures) ────────


def _barreler_stats() -> AggregatedOpponentStats:
    return AggregatedOpponentStats(
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
        barrel_frequency=0.90,
        barrel_opportunities=20,
    )


def _trap_bait_stats() -> AggregatedOpponentStats:
    return AggregatedOpponentStats(
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
        barrel_frequency=0.5,
        barrel_opportunities=0,
        flop_check_then_barrel_rate=0.80,
        flop_check_barrel_opportunities=20,
    )


def _cbet_spammer_stats() -> AggregatedOpponentStats:
    return AggregatedOpponentStats(
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
        flop_check_then_barrel_rate=0.5,
        flop_check_barrel_opportunities=0,
    )


def _facing_ctx() -> DecisionContext:
    return DecisionContext(
        facing_all_in=False,
        facing_big_bet=False,
        bet_size_pot_ratio=0.6,
        bet_bucket='medium',
        required_equity=0.32,
    )


def _open_ctx() -> DecisionContext:
    return DecisionContext(facing_all_in=False, bet_size_pot_ratio=0.0)


def _allin_ctx() -> DecisionContext:
    return DecisionContext(facing_all_in=True)


def _facing_strategy() -> StrategyProfile:
    return StrategyProfile(
        action_probabilities={'fold': 0.20, 'call': 0.50, 'raise_75': 0.30}
    )


def _open_strategy() -> StrategyProfile:
    return StrategyProfile(
        action_probabilities={'check': 0.30, 'raise_50': 0.30, 'raise_75': 0.40}
    )


def _oop_facing_strategy() -> StrategyProfile:
    return StrategyProfile(
        action_probabilities={'fold': 0.10, 'call': 0.60, 'raise_75': 0.30}
    )


# ── Assertion helper ───────────────────────────────────────────────


def _assert_trace(trace: InterventionTrace, expected: dict):
    """Assert every captured field of the trace matches the golden dict."""
    assert isinstance(trace, InterventionTrace)
    assert trace.layer == expected['layer']
    assert trace.rule_id == expected['rule_id']
    assert trace.layer_order == expected['layer_order']
    assert trace.fired == expected['fired']
    assert trace.operation == expected['operation']
    assert trace.effect == expected['effect']
    # effect_size flows from l1_distance, whose summation iterates a set
    # union of action keys — so its low-order bits are inherently
    # process-nondeterministic (key iteration order varies run-to-run),
    # independent of this refactor. Pin the value tightly but not bit-exact.
    assert trace.effect_size == pytest.approx(expected['effect_size'], abs=1e-12)
    assert trace.action_changed == expected['action_changed']
    assert trace.primary_action_before == expected['primary_action_before']
    assert trace.primary_action_after == expected['primary_action_after']
    assert trace.reason_code == expected['reason_code']
    assert trace.rationale == expected['rationale']
    assert trace.inputs == expected['inputs']
    assert trace.input_strategy_summary == expected['input_strategy_summary']
    assert trace.output_strategy_summary == expected['output_strategy_summary']


# ── Golden expectations (hard-coded captures from pre-refactor code) ─

_NOOP_FACING_ALL_IN = {
    'layer': 'induce_override',
    'rule_id': 'default',
    'layer_order': 2,
    'fired': False,
    'operation': 'no_op',
    'effect': 'no_op',
    'effect_size': 0.0,
    'action_changed': False,
    'primary_action_before': '',
    'primary_action_after': '',
    'reason_code': 'facing_all_in',
    'rationale': '',
    'inputs': {},
    'input_strategy_summary': {},
    'output_strategy_summary': {},
}


# ── Tests: facing-bet induce (Item 2) ──────────────────────────────


class TestFacingBetInduceGolden:
    def test_fire(self):
        strategy = _facing_strategy()
        new_strategy, trace = _apply_facing_bet_induce(
            strategy,
            stats=_barreler_stats(),
            hand_strength='nuts',
            nut_status='actual_nuts',
            street='flop',
            position='IP',
            danger_flag_count=0,
            effective_stack_bb=100.0,
            active_opponent_count=1,
            decision_context=_facing_ctx(),
            has_call=True,
            has_fold=True,
            adaptation_bias=0.8,
            tilt_factor=1.0,
        )
        assert new_strategy.action_probabilities == {
            'call': 0.7666666666666666,
            'raise_75': 0.2333333333333334,
        }
        _assert_trace(trace, {
            'layer': 'induce_override',
            'rule_id': 'default',
            'layer_order': 2,
            'fired': True,
            'operation': 'override',
            'effect': 'smooth_call',
            'effect_size': 0.5333333333333332,
            'action_changed': False,
            'primary_action_before': 'call',
            'primary_action_after': 'call',
            'reason_code': 'induced_flop_facing_bet',
            'rationale': (
                'induce override: nuts IP on flop, barrel_freq=0.90, '
                'barrel_opps=20, call_prob=0.77, stack=100.0 BB '
                '→ smooth-call to induce barrel'
            ),
            'inputs': {
                'hand_strength': 'nuts',
                'nut_status': 'actual_nuts',
                'street': 'flop',
                'position': 'IP',
                'danger_flag_count': 0,
                'effective_stack_bb': 100.0,
                'active_opponent_count': 1,
                'barrel_frequency': 0.9,
                'barrel_opportunities': 20,
                'third_barrel_frequency': 0.5,
                'third_barrel_opportunities': 0,
                'call_probability': 0.7667,
                'hands_observed': 30,
            },
            'input_strategy_summary': {'call': 0.5, 'fold': 0.2, 'raise_75': 0.3},
            'output_strategy_summary': {'call': 0.7667, 'raise_75': 0.2333},
        })

    def test_noop_facing_all_in(self):
        strategy = _facing_strategy()
        new_strategy, trace = _apply_facing_bet_induce(
            strategy,
            stats=_barreler_stats(),
            hand_strength='nuts',
            nut_status='actual_nuts',
            street='flop',
            position='IP',
            danger_flag_count=0,
            effective_stack_bb=100.0,
            active_opponent_count=1,
            decision_context=_allin_ctx(),
            has_call=True,
            has_fold=True,
            adaptation_bias=0.8,
            tilt_factor=1.0,
        )
        assert new_strategy is strategy
        _assert_trace(trace, _NOOP_FACING_ALL_IN)


# ── Tests: open-spot induce (Item 4) ───────────────────────────────


class TestOpenSpotInduceGolden:
    def test_fire(self):
        strategy = _open_strategy()
        new_strategy, trace = _apply_open_spot_induce(
            strategy,
            stats=_trap_bait_stats(),
            hand_strength='nuts',
            nut_status='actual_nuts',
            street='flop',
            position='IP',
            danger_flag_count=0,
            effective_stack_bb=100.0,
            active_opponent_count=1,
            decision_context=_open_ctx(),
            has_check=True,
            has_fold=False,
            adaptation_bias=0.8,
            tilt_factor=1.0,
        )
        assert new_strategy.action_probabilities == {
            'check': 0.7,
            'raise_50': 0.15000000000000002,
            'raise_75': 0.15000000000000002,
        }
        _assert_trace(trace, {
            'layer': 'induce_override',
            'rule_id': 'default',
            'layer_order': 2,
            'fired': True,
            'operation': 'override',
            'effect': 'check_back',
            'effect_size': 0.7999999999999999,
            'action_changed': True,
            'primary_action_before': 'raise_75',
            'primary_action_after': 'check',
            'reason_code': 'induced_flop_open_spot',
            'rationale': (
                'induce override: nuts IP on flop open spot, fcb_rate=0.80, '
                'fcb_opps=20, stack=100.0 BB → check back to induce barrel'
            ),
            'inputs': {
                'hand_strength': 'nuts',
                'nut_status': 'actual_nuts',
                'street': 'flop',
                'position': 'IP',
                'danger_flag_count': 0,
                'effective_stack_bb': 100.0,
                'active_opponent_count': 1,
                'flop_check_then_barrel_rate': 0.8,
                'flop_check_barrel_opportunities': 20,
                'check_probability': 0.7,
                'hands_observed': 30,
            },
            'input_strategy_summary': {
                'check': 0.3,
                'raise_50': 0.3,
                'raise_75': 0.4,
            },
            'output_strategy_summary': {
                'check': 0.7,
                'raise_50': 0.15,
                'raise_75': 0.15,
            },
        })

    def test_noop_facing_all_in(self):
        strategy = _open_strategy()
        new_strategy, trace = _apply_open_spot_induce(
            strategy,
            stats=_trap_bait_stats(),
            hand_strength='nuts',
            nut_status='actual_nuts',
            street='flop',
            position='IP',
            danger_flag_count=0,
            effective_stack_bb=100.0,
            active_opponent_count=1,
            decision_context=_allin_ctx(),
            has_check=True,
            has_fold=False,
            adaptation_bias=0.8,
            tilt_factor=1.0,
        )
        assert new_strategy is strategy
        _assert_trace(trace, _NOOP_FACING_ALL_IN)


# ── Tests: OOP trap-check (Item 5a) ────────────────────────────────


class TestOOPTrapCheckGolden:
    def test_fire(self):
        strategy = _open_strategy()
        new_strategy, trace = _apply_oop_trap_check(
            strategy,
            stats=_cbet_spammer_stats(),
            hand_strength='nuts',
            nut_status='actual_nuts',
            street='flop',
            position='OOP',
            danger_flag_count=0,
            effective_stack_bb=100.0,
            active_opponent_count=1,
            decision_context=_open_ctx(),
            has_check=True,
            has_fold=False,
            adaptation_bias=0.8,
            tilt_factor=1.0,
        )
        assert new_strategy.action_probabilities == {
            'check': 0.8,
            'raise_50': 0.09999999999999998,
            'raise_75': 0.09999999999999998,
        }
        _assert_trace(trace, {
            'layer': 'induce_override',
            'rule_id': 'default',
            'layer_order': 2,
            'fired': True,
            'operation': 'override',
            'effect': 'trap_check',
            'effect_size': 1.0,
            'action_changed': True,
            'primary_action_before': 'raise_75',
            'primary_action_after': 'check',
            'reason_code': 'induced_flop_oop_trap_check',
            'rationale': (
                'induce override: nuts OOP on flop open spot, '
                'cbet_attempt_rate=0.90, pfr_seen=15, stack=100.0 BB '
                '→ check to set check-raise trap'
            ),
            'inputs': {
                'hand_strength': 'nuts',
                'nut_status': 'actual_nuts',
                'street': 'flop',
                'position': 'OOP',
                'danger_flag_count': 0,
                'effective_stack_bb': 100.0,
                'active_opponent_count': 1,
                'cbet_attempt_rate': 0.9,
                'postflop_seen_as_pfr_count': 15,
                'check_probability': 0.8,
                'hands_observed': 30,
            },
            'input_strategy_summary': {
                'check': 0.3,
                'raise_50': 0.3,
                'raise_75': 0.4,
            },
            'output_strategy_summary': {
                'check': 0.8,
                'raise_50': 0.1,
                'raise_75': 0.1,
            },
        })

    def test_noop_facing_all_in(self):
        strategy = _open_strategy()
        new_strategy, trace = _apply_oop_trap_check(
            strategy,
            stats=_cbet_spammer_stats(),
            hand_strength='nuts',
            nut_status='actual_nuts',
            street='flop',
            position='OOP',
            danger_flag_count=0,
            effective_stack_bb=100.0,
            active_opponent_count=1,
            decision_context=_allin_ctx(),
            has_check=True,
            has_fold=False,
            adaptation_bias=0.8,
            tilt_factor=1.0,
        )
        assert new_strategy is strategy
        _assert_trace(trace, _NOOP_FACING_ALL_IN)


# ── Tests: OOP check-raise (Item 5b) ───────────────────────────────


class TestOOPCheckRaiseGolden:
    def test_fire(self):
        strategy = _oop_facing_strategy()
        new_strategy, trace = _apply_oop_check_raise(
            strategy,
            stats=_cbet_spammer_stats(),
            hand_strength='nuts',
            nut_status='actual_nuts',
            street='flop',
            position='OOP',
            danger_flag_count=0,
            effective_stack_bb=100.0,
            active_opponent_count=1,
            decision_context=_facing_ctx(),
            has_call=True,
            has_fold=True,
            adaptation_bias=0.8,
            tilt_factor=1.0,
        )
        assert new_strategy.action_probabilities == {
            'call': 0.19999999999999996,
            'raise_75': 0.8,
        }
        _assert_trace(trace, {
            'layer': 'induce_override',
            'rule_id': 'default',
            'layer_order': 2,
            'fired': True,
            'operation': 'override',
            'effect': 'check_raise',
            'effect_size': 1.0,
            'action_changed': True,
            'primary_action_before': 'call',
            'primary_action_after': 'raise_75',
            'reason_code': 'induced_flop_oop_check_raise',
            'rationale': (
                'induce override: nuts OOP on flop facing cbet, '
                'cbet_attempt_rate=0.90, barrel_freq=0.80, pfr_seen=15, '
                'stack=100.0 BB → check-raise to complete trap'
            ),
            'inputs': {
                'hand_strength': 'nuts',
                'nut_status': 'actual_nuts',
                'street': 'flop',
                'position': 'OOP',
                'danger_flag_count': 0,
                'effective_stack_bb': 100.0,
                'active_opponent_count': 1,
                'cbet_attempt_rate': 0.9,
                'postflop_seen_as_pfr_count': 15,
                'barrel_frequency': 0.8,
                'barrel_opportunities': 10,
                'raise_probability': 0.8,
                'hands_observed': 30,
            },
            'input_strategy_summary': {
                'call': 0.6,
                'fold': 0.1,
                'raise_75': 0.3,
            },
            'output_strategy_summary': {'call': 0.2, 'raise_75': 0.8},
        })

    def test_noop_facing_all_in(self):
        strategy = _oop_facing_strategy()
        new_strategy, trace = _apply_oop_check_raise(
            strategy,
            stats=_cbet_spammer_stats(),
            hand_strength='nuts',
            nut_status='actual_nuts',
            street='flop',
            position='OOP',
            danger_flag_count=0,
            effective_stack_bb=100.0,
            active_opponent_count=1,
            decision_context=_allin_ctx(),
            has_call=True,
            has_fold=True,
            adaptation_bias=0.8,
            tilt_factor=1.0,
        )
        assert new_strategy is strategy
        _assert_trace(trace, _NOOP_FACING_ALL_IN)
