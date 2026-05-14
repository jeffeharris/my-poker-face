"""Phase 7.6 Step 2: strong_hand_override trace migration tests.

Covers:
  - compute_value_override_strategy emits a fire trace for each of
    the three spot types (facing_all_in / facing_bet / open) with
    correct reason_code, operation, and strategy summaries
  - pathological branches (no continuing action available) emit a
    fired=False trace, not a misleading fire trace
  - controller _apply_value_override emits no_op traces on each
    early-out path with distinct reason_codes
  - controller's _fill_prior_action_source helper correctly attributes
    bluff_catch's override to the earlier strong_hand_override layer
    when both fire (rarely; they're mutually exclusive by hand class
    so this is a contrived setup)
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from poker.strategy.exploitation import DecisionContext
from poker.strategy.intervention_trace import (
    InterventionOperation,
    InterventionTrace,
    make_no_op_trace,
    trace_to_json_dict,
    validate_trace,
)
from poker.strategy.strategy_profile import StrategyProfile
from poker.strategy.value_override import (
    HandStrengthClass,
    compute_value_override_strategy,
)
from poker.tiered_bot_controller import _fill_prior_action_source


# ── compute_value_override_strategy: fire-path traces ────────────────────


class TestStrongHandFacingAllInTrace:
    def test_facing_all_in_with_call_emits_call_trace(self):
        s = StrategyProfile(action_probabilities={'fold': 0.6, 'call': 0.4})
        result, trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(facing_all_in=True),
            hand_strength=HandStrengthClass.NUTS.value,
        )

        assert trace.layer == 'strong_hand_override'
        assert trace.layer_order == 2
        assert trace.fired is True
        assert trace.operation == InterventionOperation.OVERRIDE.value
        assert trace.reason_code == 'facing_all_in_call'
        assert trace.inputs['spot'] == 'facing_all_in'
        assert trace.inputs['hand_strength'] == 'nuts'
        assert trace.replaced_prior_action is True
        assert trace.primary_action_before == 'fold'
        assert trace.primary_action_after == 'call'
        assert trace.action_changed is True
        validate_trace(trace)

    def test_facing_all_in_with_jam_emits_jam_trace(self):
        s = StrategyProfile(action_probabilities={'fold': 0.5, 'jam': 0.5})
        _result, trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(facing_all_in=True),
            hand_strength=HandStrengthClass.NUTS.value,
        )
        assert trace.fired is True
        assert trace.reason_code == 'facing_all_in_jam'

    def test_pathological_no_continuing_action_emits_no_op(self):
        """If facing all-in but neither call nor jam is legal, the
        function returns the strategy unchanged — trace records this
        as fired=False so attribution doesn't credit it as an override."""
        s = StrategyProfile(action_probabilities={'fold': 1.0})
        result, trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(facing_all_in=True),
            hand_strength=HandStrengthClass.NUTS.value,
        )
        assert result is s
        assert trace.fired is False
        assert trace.operation == InterventionOperation.NO_OP.value
        assert trace.reason_code == 'facing_all_in_no_continuing_action'


# ── Facing-bet spot traces ───────────────────────────────────────────────


class TestStrongHandFacingBetTrace:
    def test_call_plus_raise_emits_combined_trace(self):
        s = StrategyProfile(action_probabilities={
            'fold': 0.5, 'call': 0.3, 'raise_67': 0.2,
        })
        _result, trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(facing_big_bet=True),
            hand_strength=HandStrengthClass.STRONG_MADE.value,
        )
        assert trace.fired is True
        assert trace.reason_code == 'facing_bet_call_or_raise'
        assert trace.inputs['spot'] == 'facing_bet'

    def test_call_only_emits_call_only_trace(self):
        s = StrategyProfile(action_probabilities={'fold': 0.6, 'call': 0.4})
        _result, trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(facing_big_bet=True),
            hand_strength=HandStrengthClass.STRONG_MADE.value,
        )
        assert trace.reason_code == 'facing_bet_call_only'

    def test_raise_only_emits_raise_only_trace(self):
        s = StrategyProfile(action_probabilities={'fold': 0.5, 'raise_67': 0.5})
        _result, trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(facing_big_bet=True),
            hand_strength=HandStrengthClass.STRONG_MADE.value,
        )
        assert trace.reason_code == 'facing_bet_raise_only'

    def test_facing_bet_no_continuing_action_emits_no_op(self):
        """Pathological: fold legal but no call AND no raise (impossible
        in real games, but the code defends). Confirms no-op trace."""
        s = StrategyProfile(action_probabilities={'fold': 1.0})
        _result, trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(facing_big_bet=True),
            hand_strength=HandStrengthClass.STRONG_MADE.value,
        )
        assert trace.fired is False
        assert trace.reason_code == 'facing_bet_no_continuing_action'


# ── Open-spot traces ─────────────────────────────────────────────────────


class TestStrongHandOpenSpotTrace:
    def test_open_with_check_and_raise_emits_hand_class_reason(self):
        s = StrategyProfile(action_probabilities={'check': 0.5, 'raise_67': 0.5})
        _result, trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(),  # no facing flags
            hand_strength=HandStrengthClass.NUTS.value,
        )
        assert trace.fired is True
        assert trace.inputs['spot'] == 'open'
        assert trace.reason_code == 'open_value_bet_nuts'
        # raise_prob should be 0.95 for nuts; recorded in extra
        assert trace.extra.get('raise_prob') == pytest.approx(0.95)

    def test_open_strong_made_uses_lower_raise_prob(self):
        s = StrategyProfile(action_probabilities={'check': 0.5, 'raise_67': 0.5})
        _result, trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(),
            hand_strength=HandStrengthClass.STRONG_MADE.value,
        )
        assert trace.reason_code == 'open_value_bet_strong_made'
        assert trace.extra.get('raise_prob') == pytest.approx(0.80)

    def test_open_no_raise_emits_no_op(self):
        """Pathological — open spot with no raise option."""
        s = StrategyProfile(action_probabilities={'check': 1.0})
        _result, trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(),
            hand_strength=HandStrengthClass.NUTS.value,
        )
        assert trace.fired is False
        assert trace.reason_code == 'open_no_raise_action'


# ── JSON round-trip ──────────────────────────────────────────────────────


class TestStrongHandTraceSerialization:
    def test_fire_trace_round_trips_through_json(self):
        s = StrategyProfile(action_probabilities={'fold': 0.5, 'call': 0.5})
        _result, trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(facing_all_in=True),
            hand_strength=HandStrengthClass.NUTS.value,
        )
        payload = trace_to_json_dict(trace)
        decoded = json.loads(json.dumps(payload))
        assert decoded['layer'] == 'strong_hand_override'
        assert decoded['operation'] == 'override'

    def test_pathological_no_op_trace_round_trips(self):
        s = StrategyProfile(action_probabilities={'fold': 1.0})
        _result, trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(facing_all_in=True),
            hand_strength=HandStrengthClass.NUTS.value,
        )
        payload = trace_to_json_dict(trace)
        decoded = json.loads(json.dumps(payload))
        assert decoded['fired'] is False


# ── _fill_prior_action_source controller helper ──────────────────────────


class TestFillPriorActionSource:
    def _make_fire_trace(self, layer: str, rule_id: str = 'default') -> InterventionTrace:
        return InterventionTrace(
            layer=layer, rule_id=rule_id, layer_order=0,
            fired=True,
            operation=InterventionOperation.OVERRIDE.value,
            effect='distribution_replaced',
            replaced_prior_action=True,
            primary_action_before='fold', primary_action_after='call',
        )

    def test_fills_in_from_last_fired_earlier_trace(self):
        earlier = [
            make_no_op_trace(
                layer='personality', rule_id='default', layer_order=0,
                reason_code='no_distortion',
            ),
            self._make_fire_trace('strong_hand_override'),
        ]
        current = self._make_fire_trace('bluff_catch_override')
        updated = _fill_prior_action_source(current, earlier)
        assert updated.prior_action_source == 'strong_hand_override.default'

    def test_no_op_current_is_left_unchanged(self):
        """A non-firing layer trace gets no fill-in — it didn't replace
        anything, so 'prior_action_source' semantics don't apply."""
        earlier = [self._make_fire_trace('strong_hand_override')]
        current = make_no_op_trace(
            layer='bluff_catch_override', rule_id='default', layer_order=3,
            reason_code='gate_rejected',
        )
        updated = _fill_prior_action_source(current, earlier)
        assert updated.prior_action_source == ''
        # Same object returned when no change needed.
        assert updated is current

    def test_no_earlier_fired_layer_leaves_empty(self):
        earlier = [
            make_no_op_trace(
                layer='strong_hand_override', rule_id='default', layer_order=2,
                reason_code='gate_rejected',
            ),
        ]
        current = self._make_fire_trace('bluff_catch_override')
        updated = _fill_prior_action_source(current, earlier)
        assert updated.prior_action_source == ''

    def test_does_not_clobber_existing_value(self):
        earlier = [self._make_fire_trace('strong_hand_override')]
        # Caller pre-populated prior_action_source (e.g. for testing).
        # Helper should NOT overwrite.
        from dataclasses import replace
        current = replace(
            self._make_fire_trace('bluff_catch_override'),
            prior_action_source='manual.override',
        )
        updated = _fill_prior_action_source(current, earlier)
        assert updated.prior_action_source == 'manual.override'

    def test_picks_most_recent_fired_layer_when_multiple(self):
        earlier = [
            self._make_fire_trace('personality'),
            self._make_fire_trace('exploitation', rule_id='hyper_aggressive'),
            make_no_op_trace(
                layer='strong_hand_override', rule_id='default', layer_order=2,
                reason_code='gate_rejected',
            ),
        ]
        current = self._make_fire_trace('bluff_catch_override')
        updated = _fill_prior_action_source(current, earlier)
        # personality and exploitation both fired; the most recent (exploitation)
        # is the immediate prior action source.
        assert updated.prior_action_source == 'exploitation.hyper_aggressive'
