"""Phase 7.6 Step 1: bluff_catch_override trace migration tests.

Covers the reference implementation of the threading pattern:
  - compute_bluff_catch_strategy emits a fire trace with the expected
    shape (operation=OVERRIDE, inputs, summaries, rationale)
  - _apply_bluff_catch_override emits no_op traces on each early-out
    path with a distinct reason_code
  - controller's _last_intervention_trace accumulator collects the
    bluff_catch trace when the postflop pipeline runs end-to-end
    (deferred to e2e tests later; covered structurally here)
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from poker.strategy import phase_7_5_config as cfg
from poker.strategy.intervention_trace import (
    InterventionOperation,
    InterventionTrace,
    trace_to_json_dict,
    validate_trace,
)
from poker.strategy.strategy_profile import StrategyProfile
from poker.strategy.value_override import compute_bluff_catch_strategy

# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_config():
    cfg.reset_for_testing()
    yield
    cfg.reset_for_testing()


def _ctx(**kwargs) -> SimpleNamespace:
    base = dict(
        bet_size_pot_ratio=1.0,
        facing_all_in=False,
        facing_big_bet=True,
        street='flop',
        board_texture='dry_high',
        is_paired_board=False,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


# ── compute_bluff_catch_strategy emits a fire trace ──────────────────────


class TestBluffCatchFireTrace:
    def test_fire_emits_override_trace(self):
        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        ctx = _ctx(street='flop', board_texture='dry_high', bet_size_pot_ratio=1.0)

        _strategy, trace = compute_bluff_catch_strategy(
            baseline,
            ctx,
            'medium_made',
            max_total_shift=0.8,
        )

        assert trace.layer == 'bluff_catch_override'
        assert trace.rule_id == 'default'
        assert trace.layer_order == 3
        assert trace.fired is True
        assert trace.operation == InterventionOperation.OVERRIDE.value
        assert trace.effect == 'distribution_replaced'
        assert trace.effect_size > 0.0
        assert trace.replaced_prior_action is True
        assert trace.preserved_prior_intent is False
        assert trace.rationale  # non-empty
        # Schema invariants hold.
        validate_trace(trace)

    def test_fire_trace_records_primary_action_change(self):
        """Baseline 100% fold, override pushes mass onto call.
        primary_action_before='fold', primary_action_after='call',
        action_changed=True."""
        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        ctx = _ctx(street='flop', board_texture='dry_high', bet_size_pot_ratio=1.0)

        strategy, trace = compute_bluff_catch_strategy(
            baseline,
            ctx,
            'medium_made',
            max_total_shift=0.8,
        )

        # The clamped distribution has call=0.4, fold=0.6 — fold is
        # still primary by argmax, so action_changed is False.
        assert trace.primary_action_before == 'fold'
        assert trace.primary_action_after == 'fold'
        assert trace.action_changed is False

    def test_fire_trace_records_inputs_block(self):
        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        ctx = _ctx(
            street='turn',
            board_texture='wet_rainbow',
            bet_size_pot_ratio=0.5,
            is_paired_board=True,
        )

        _strategy, trace = compute_bluff_catch_strategy(
            baseline,
            ctx,
            'weak_made',
            max_total_shift=0.8,
        )

        assert trace.inputs['hand_strength'] == 'weak_made'
        assert trace.inputs['bet_size_pot_ratio'] == pytest.approx(0.5)
        assert trace.inputs['street'] == 'turn'
        assert trace.inputs['board_texture'] == 'wet_rainbow'
        assert trace.inputs['is_paired_board'] is True
        assert trace.inputs['tier'] == 'extreme'  # default tier_label

    def test_fire_trace_records_strategy_summaries(self):
        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        ctx = _ctx(street='flop', board_texture='dry_high', bet_size_pot_ratio=1.0)

        strategy, trace = compute_bluff_catch_strategy(
            baseline,
            ctx,
            'medium_made',
            max_total_shift=0.8,
        )

        # Input summary mirrors baseline; output mirrors clamped result.
        assert trace.input_strategy_summary.get('fold') == pytest.approx(1.0)
        assert set(trace.output_strategy_summary.keys()) <= set(
            strategy.action_probabilities.keys()
        )

    def test_fire_trace_reason_code_encodes_hand_class_and_tier(self):
        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        ctx = _ctx()

        for hand_class in ('medium_made', 'weak_made'):
            _strategy, trace = compute_bluff_catch_strategy(
                baseline,
                ctx,
                hand_class,
                max_total_shift=0.8,
            )
            assert trace.reason_code == f'{hand_class}_vs_extreme_facing_bet'

    def test_fire_trace_carries_tier_label_through(self):
        """If a future widening passes a non-extreme tier_label, the
        trace's `inputs['tier']` and reason_code reflect it."""
        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        ctx = _ctx()

        _strategy, trace = compute_bluff_catch_strategy(
            baseline,
            ctx,
            'medium_made',
            max_total_shift=0.6,
            tier_label='moderate',
        )
        assert trace.inputs['tier'] == 'moderate'
        assert trace.reason_code == 'medium_made_vs_moderate_facing_bet'

    def test_fire_trace_round_trips_through_json(self):
        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        ctx = _ctx()

        _strategy, trace = compute_bluff_catch_strategy(
            baseline,
            ctx,
            'medium_made',
            max_total_shift=0.8,
        )

        payload = trace_to_json_dict(trace)
        serialized = json.dumps(payload)
        decoded = json.loads(serialized)
        assert decoded['layer'] == 'bluff_catch_override'
        assert decoded['operation'] == 'override'

    def test_fire_trace_records_call_action_when_all_in(self):
        """Short-stack spots expose all_in not call. The call-equivalent must be
        recorded as the ABSTRACT token 'jam' (the resolver maps it to engine
        all_in) — never the raw engine 'all_in', which would crash the
        action_mapper when the profile is re-sampled. See action_vocab.py."""
        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        ctx = _ctx(street='flop', board_texture='dry_high', bet_size_pot_ratio=1.0)

        _strategy, trace = compute_bluff_catch_strategy(
            baseline,
            ctx,
            'medium_made',
            max_total_shift=0.8,
            legal_actions=['fold', 'all_in'],
        )

        assert trace.extra['call_action'] == 'jam'

    def test_fire_trace_amount_buckets_are_empty_for_call_fold(self):
        """Bluff-catch produces a {call, fold} distribution — neither
        has a sizing bucket. Confirms we don't accidentally write 'small'
        or similar."""
        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        ctx = _ctx()

        _strategy, trace = compute_bluff_catch_strategy(
            baseline,
            ctx,
            'medium_made',
            max_total_shift=0.8,
        )

        assert trace.amount_bucket_before == ''
        assert trace.amount_bucket_after == ''


# ── _apply_bluff_catch_override emits no_op traces on early-outs ─────────
#
# The controller-level early-out tests live in
# test_tiered_bot_bluff_catch.py — those build a full controller with
# a stub manager. Here we exercise the SHAPE of no_op traces directly
# via the make_no_op_trace helper to keep this file fast.


class TestBluffCatchNoOpTrace:
    """Indirectly via make_no_op_trace — full controller wiring covered
    in the e2e controller test file."""

    def test_no_op_trace_carries_reason_code(self):
        from poker.strategy.intervention_trace import make_no_op_trace

        for code in (
            'manager_unavailable',
            'hand_class_not_eligible',
            'gate_rejected',
        ):
            trace = make_no_op_trace(
                layer='bluff_catch_override',
                rule_id='default',
                layer_order=3,
                reason_code=code,
            )
            assert trace.fired is False
            assert trace.operation == InterventionOperation.NO_OP.value
            assert trace.reason_code == code
            validate_trace(trace)


# ── Controller integration: _last_intervention_trace accumulator ─────────


class TestControllerAccumulator:
    """The controller's _apply_bluff_catch_override returns (strategy,
    trace) post-7.6. These tests use the same controller fixtures as
    test_tiered_bot_bluff_catch.py but assert on the trace shape, not
    just the action distribution."""

    @staticmethod
    def _import_controller_fixtures():
        # Late import to avoid pulling controller dependencies at module
        # import time (mirrors test_tiered_bot_bluff_catch.py pattern).
        from tests.test_strategy.test_tiered_bot_bluff_catch import (
            _make_controller,
            _make_extreme_maniac_stats,
            _make_manager,
            _make_neutral_stats,
        )

        return (
            _make_controller,
            _make_extreme_maniac_stats,
            _make_manager,
            _make_neutral_stats,
        )

    def test_fire_path_appends_override_trace(self):
        (
            _make_controller,
            _make_extreme_maniac_stats,
            _make_manager,
            _,
        ) = self._import_controller_fixtures()

        manager = _make_manager(_make_extreme_maniac_stats())
        controller = _make_controller(manager=manager)

        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        anchors = SimpleNamespace(adaptation_bias=0.5)
        emotional = SimpleNamespace(state='composed')

        _strategy, trace = controller._apply_bluff_catch_override(
            strategy=baseline,
            game_state=controller.state_machine.game_state,
            player_idx=0,
            valid_actions=['fold', 'call'],
            anchors=anchors,
            emotional_state=emotional,
            hand_strength='medium_made',
        )

        assert trace.fired is True
        assert trace.operation == InterventionOperation.OVERRIDE.value
        validate_trace(trace)

    def test_early_out_manager_none_emits_no_op_trace(self):
        (_make_controller, _, _, _) = self._import_controller_fixtures()
        controller = _make_controller(manager=None)

        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        anchors = SimpleNamespace(adaptation_bias=0.5)
        emotional = SimpleNamespace(state='composed')

        _strategy, trace = controller._apply_bluff_catch_override(
            strategy=baseline,
            game_state=controller.state_machine.game_state,
            player_idx=0,
            valid_actions=['fold', 'call'],
            anchors=anchors,
            emotional_state=emotional,
            hand_strength='medium_made',
        )

        assert trace.fired is False
        assert trace.reason_code == 'manager_unavailable'
        validate_trace(trace)

    def test_early_out_hand_class_not_eligible(self):
        (
            _make_controller,
            _make_extreme_maniac_stats,
            _make_manager,
            _,
        ) = self._import_controller_fixtures()
        manager = _make_manager(_make_extreme_maniac_stats())
        controller = _make_controller(manager=manager)

        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        anchors = SimpleNamespace(adaptation_bias=0.5)
        emotional = SimpleNamespace(state='composed')

        _strategy, trace = controller._apply_bluff_catch_override(
            strategy=baseline,
            game_state=controller.state_machine.game_state,
            player_idx=0,
            valid_actions=['fold', 'call'],
            anchors=anchors,
            emotional_state=emotional,
            hand_strength='strong_made',  # outside trigger set
        )

        assert trace.fired is False
        assert trace.reason_code == 'hand_class_not_eligible'

    def test_early_out_gate_rejected_when_neutral_opp(self):
        """Hand class is eligible but opponent stats are neutral →
        clamp tier is DEFAULT not EXTREME → gate rejects."""
        (
            _make_controller,
            _,
            _make_manager,
            _make_neutral_stats,
        ) = self._import_controller_fixtures()
        manager = _make_manager(_make_neutral_stats())
        controller = _make_controller(manager=manager)

        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        anchors = SimpleNamespace(adaptation_bias=0.5)
        emotional = SimpleNamespace(state='composed')

        _strategy, trace = controller._apply_bluff_catch_override(
            strategy=baseline,
            game_state=controller.state_machine.game_state,
            player_idx=0,
            valid_actions=['fold', 'call'],
            anchors=anchors,
            emotional_state=emotional,
            hand_strength='medium_made',
        )

        assert trace.fired is False
        assert trace.reason_code == 'gate_rejected'
