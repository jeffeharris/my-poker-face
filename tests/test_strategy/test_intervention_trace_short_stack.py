"""Phase 7.6 Step 4: short_stack layer trace tests.

short_stack uses operation='clamp' (Codex r3 disambiguation): it
bounds medium-raise mass at short depth without VETOing those actions
from consideration. The action remains in the distribution; its mass
may be reduced toward zero, but it's not a hard prohibition.

Covers:
  - fire path emits 'clamp' trace at short stacks with redistributed
    medium-raise mass shifted to jam (or fold fallback)
  - deep-stack early-out emits fired=False (stack_deep)
  - no-medium-raises early-out emits fired=False
  - JSON round-trip
"""

from __future__ import annotations

import json

import pytest

from poker.strategy.intervention_trace import (
    InterventionOperation,
    trace_to_json_dict,
    validate_trace,
)
from poker.strategy.short_stack import apply_short_stack_heuristics
from poker.strategy.strategy_profile import StrategyProfile


class TestShortStackFireTrace:
    def test_short_depth_emits_clamp_trace(self):
        """8 BB stack → full suppression → medium-raise mass moved to jam."""
        base = StrategyProfile(action_probabilities={
            'fold': 0.3, 'call': 0.2, 'raise_3bb': 0.3, 'bet_67': 0.2,
        })
        _result, trace = apply_short_stack_heuristics(
            strategy=base,
            effective_stack_bb=8.0,  # short
            legal_actions=['fold', 'call', 'raise', 'all_in'],
        )
        assert trace.layer == 'short_stack'
        # Plan §2 inserted defense_floor at slot 4; short_stack shifted to 5.
        assert trace.layer_order == 5
        assert trace.fired is True
        assert trace.operation == InterventionOperation.CLAMP.value
        assert trace.effect == 'distribution_clamped'
        assert trace.effect_size > 0.0
        assert trace.preserved_prior_intent is True
        assert trace.inputs['effective_stack_bb'] == pytest.approx(8.0)
        assert trace.inputs['sink_action'] == 'jam'
        validate_trace(trace)

    def test_medium_depth_partial_suppression(self):
        """15 BB → 50% suppression — fire trace with smaller effect_size."""
        base = StrategyProfile(action_probabilities={
            'fold': 0.3, 'call': 0.4, 'raise_3bb': 0.3,
        })
        _result, trace = apply_short_stack_heuristics(
            strategy=base,
            effective_stack_bb=15.0,
            legal_actions=['fold', 'call', 'raise', 'all_in'],
        )
        assert trace.fired is True
        # Suppression factor should be ~0.5
        assert trace.inputs['suppression_factor'] == pytest.approx(0.5, abs=0.05)

    def test_redistributed_mass_recorded_in_extra(self):
        base = StrategyProfile(action_probabilities={
            'fold': 0.2, 'call': 0.2, 'raise_3bb': 0.6,
        })
        _result, trace = apply_short_stack_heuristics(
            strategy=base,
            effective_stack_bb=8.0,
            legal_actions=['fold', 'call', 'raise', 'all_in'],
        )
        # All 0.6 raise mass moved to jam at 8 BB (full suppression).
        assert trace.extra['redistributed_mass'] == pytest.approx(0.6)
        assert 'raise_3bb' in trace.extra['medium_raises_suppressed']

    def test_fallback_to_fold_when_no_jam_legal(self):
        base = StrategyProfile(action_probabilities={
            'fold': 0.3, 'call': 0.4, 'raise_3bb': 0.3,
        })
        _result, trace = apply_short_stack_heuristics(
            strategy=base,
            effective_stack_bb=8.0,
            legal_actions=['fold', 'call', 'raise'],  # no all_in
        )
        assert trace.fired is True
        assert trace.inputs['sink_action'] == 'fold'


class TestShortStackNoOpPaths:
    def test_deep_stack_emits_no_op(self):
        base = StrategyProfile(action_probabilities={
            'fold': 0.3, 'call': 0.4, 'raise_3bb': 0.3,
        })
        _result, trace = apply_short_stack_heuristics(
            strategy=base,
            effective_stack_bb=100.0,  # deep
            legal_actions=['fold', 'call', 'raise', 'all_in'],
        )
        assert trace.fired is False
        assert trace.reason_code == 'stack_deep'

    def test_no_medium_raises_emits_no_op(self):
        base = StrategyProfile(action_probabilities={
            'fold': 0.5, 'call': 0.4, 'jam': 0.1,  # only jam, no medium raises
        })
        _result, trace = apply_short_stack_heuristics(
            strategy=base,
            effective_stack_bb=8.0,
            legal_actions=['fold', 'call', 'all_in'],
        )
        assert trace.fired is False
        assert trace.reason_code == 'no_medium_raises_in_strategy'

    def test_no_legal_sink_emits_no_op(self):
        """Pathological: medium raises in strategy but neither jam nor
        fold is legal. Returns input unchanged with reason_code."""
        base = StrategyProfile(action_probabilities={
            'call': 0.5, 'raise_3bb': 0.5,
        })
        _result, trace = apply_short_stack_heuristics(
            strategy=base,
            effective_stack_bb=8.0,
            legal_actions=['call', 'raise'],  # no all_in, no fold
        )
        assert trace.fired is False
        assert trace.reason_code == 'no_legal_sink_action'


class TestShortStackTraceJsonRoundTrip:
    def test_fire_trace_round_trips(self):
        base = StrategyProfile(action_probabilities={
            'fold': 0.3, 'call': 0.4, 'raise_3bb': 0.3,
        })
        _result, trace = apply_short_stack_heuristics(
            strategy=base,
            effective_stack_bb=8.0,
            legal_actions=['fold', 'call', 'raise', 'all_in'],
        )
        payload = trace_to_json_dict(trace)
        decoded = json.loads(json.dumps(payload))
        assert decoded['operation'] == 'clamp'
        assert decoded['layer'] == 'short_stack'
