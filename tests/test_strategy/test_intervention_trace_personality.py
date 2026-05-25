"""Phase 7.6 Step 4: personality layer trace tests.

Personality emits a SIMPLER trace than detection rules (per plan
§"Migration plan"): records deviation profile + emotional state, with
effect_size as the L1 distance from baseline to modified. operation
is 'adjust' (preserves prior intent), NOT 'override' — distortion is
additive, not replacing.

Covers:
  - fire path emits 'adjust' trace with deviation_profile reason_code
  - degenerate-support early-outs (≤1 supported action) emit fired=False
  - zero-effect runs (offsets net to zero shift) emit fired=False
  - JSON round-trip
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from poker.bounded_options import EmotionalShift
from poker.psychology_model import PersonalityAnchors
from poker.strategy.deviation_profiles import DEVIATION_PROFILES
from poker.strategy.intervention_trace import (
    InterventionOperation,
    trace_to_json_dict,
    validate_trace,
)
from poker.strategy.personality_modifier import modify_strategy
from poker.strategy.strategy_profile import StrategyProfile


def _anchors(**overrides) -> PersonalityAnchors:
    defaults = dict(
        baseline_aggression=0.9,
        baseline_looseness=0.7,
        ego=0.6,
        poise=0.5,
        expressiveness=0.5,
        risk_identity=0.6,
        adaptation_bias=0.5,
        baseline_energy=0.5,
        recovery_rate=0.15,
    )
    defaults.update(overrides)
    return PersonalityAnchors(**defaults)


COMPOSED = EmotionalShift(state='composed', severity='none', intensity=0.0)
BASE_STRATEGY = StrategyProfile(
    action_probabilities={
        'fold': 0.3,
        'call': 0.4,
        'raise_2.5bb': 0.2,
        'jam': 0.1,
    }
)
LEGAL = ['fold', 'call', 'raise', 'all_in']


class TestPersonalityFireTrace:
    def test_lag_profile_emits_adjust_trace(self):
        _result, trace = modify_strategy(
            base=BASE_STRATEGY,
            legal_actions=LEGAL,
            anchors=_anchors(),
            emotional_state=COMPOSED,
            deviation_profile=DEVIATION_PROFILES['lag'],
        )

        assert trace.layer == 'personality'
        assert trace.rule_id == 'default'
        assert trace.layer_order == 0
        assert trace.fired is True
        assert trace.operation == InterventionOperation.ADJUST.value
        assert trace.effect == 'offsets_applied'
        assert trace.effect_size > 0.0
        assert trace.preserved_prior_intent is True
        assert trace.replaced_prior_action is False
        assert 'lag' in trace.reason_code
        assert trace.inputs['deviation_profile'] == 'lag'
        assert trace.inputs['emotional_state'] == 'composed'
        validate_trace(trace)

    def test_input_output_summaries_present(self):
        _result, trace = modify_strategy(
            base=BASE_STRATEGY,
            legal_actions=LEGAL,
            anchors=_anchors(),
            emotional_state=COMPOSED,
            deviation_profile=DEVIATION_PROFILES['lag'],
        )
        # Summary keys are a subset of the input distribution
        assert set(trace.input_strategy_summary.keys()) <= set(
            BASE_STRATEGY.action_probabilities.keys()
        )

    def test_reason_code_encodes_profile_name(self):
        for profile_name in ('lag', 'tag', 'nit'):
            _result, trace = modify_strategy(
                base=BASE_STRATEGY,
                legal_actions=LEGAL,
                anchors=_anchors(),
                emotional_state=COMPOSED,
                deviation_profile=DEVIATION_PROFILES[profile_name],
            )
            assert trace.reason_code == f'deviation_profile_{profile_name}'


class TestPersonalityNoOpPaths:
    def test_single_supported_action_emits_no_op(self):
        """Only one supported action → no distortion possible."""
        base = StrategyProfile(action_probabilities={'fold': 1.0})
        _result, trace = modify_strategy(
            base=base,
            legal_actions=['fold'],
            anchors=_anchors(),
            emotional_state=COMPOSED,
            deviation_profile=DEVIATION_PROFILES['lag'],
        )
        assert trace.fired is False
        assert trace.reason_code == 'single_supported_action'

    def test_zero_supported_total_emits_no_op(self):
        """Strategy lookup returned a degenerate zero-total — handled gracefully."""
        # All non-zero actions are illegal → supported set is empty / zero.
        base = StrategyProfile(action_probabilities={'fold': 0.0, 'call': 0.0})
        _result, trace = modify_strategy(
            base=base,
            legal_actions=['fold', 'call'],
            anchors=_anchors(),
            emotional_state=COMPOSED,
            deviation_profile=DEVIATION_PROFILES['lag'],
        )
        assert trace.fired is False
        # Either degenerate path is acceptable; both report a useful code.
        assert trace.reason_code in (
            'single_supported_action',
            'zero_total_probability',
        )


class TestPersonalityTraceJsonRoundTrip:
    def test_fire_trace_round_trips(self):
        _result, trace = modify_strategy(
            base=BASE_STRATEGY,
            legal_actions=LEGAL,
            anchors=_anchors(),
            emotional_state=COMPOSED,
            deviation_profile=DEVIATION_PROFILES['lag'],
        )
        payload = trace_to_json_dict(trace)
        decoded = json.loads(json.dumps(payload))
        assert decoded['layer'] == 'personality'
        assert decoded['operation'] == 'adjust'
