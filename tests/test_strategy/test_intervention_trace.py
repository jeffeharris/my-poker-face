"""Phase 7.6 Step 1 schema tests for InterventionTrace.

Covers framework-level invariants that apply to every emitted trace:
  - canonical layer names + rule_id allowlist per layer
  - JSON round-trip via `trace_to_json_dict`
  - operation/fired consistency invariants
  - OVERRIDE ⇒ replaced_prior_action invariant (Codex r3)
  - safe serialization of enums, dataclasses, numpy-like scalars,
    non-finite floats

Per-layer fire/no-op trace shape lives in
test_intervention_trace_<layer>.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

import pytest

from poker.strategy.intervention_trace import (
    InterventionOperation,
    InterventionTrace,
    TRACE_SCHEMA_VERSION,
    _LAYER_NAMES,
    _RULE_IDS_BY_LAYER,
    amount_bucket,
    l1_distance,
    make_no_op_trace,
    primary_action,
    summarize_strategy,
    trace_to_json_dict,
    validate_trace,
)


# ── Canonical layer / rule_id allowlist ──────────────────────────────────


class TestLayerNames:
    def test_every_rule_id_layer_is_in_layer_names(self):
        for layer in _RULE_IDS_BY_LAYER:
            assert layer in _LAYER_NAMES, (
                f"_RULE_IDS_BY_LAYER has {layer!r} which is not in "
                f"_LAYER_NAMES — keep these two in sync"
            )

    def test_every_layer_has_at_least_one_rule_id(self):
        for layer in _LAYER_NAMES:
            assert layer in _RULE_IDS_BY_LAYER, (
                f"_LAYER_NAMES has {layer!r} with no rule_ids defined "
                "in _RULE_IDS_BY_LAYER"
            )
            assert len(_RULE_IDS_BY_LAYER[layer]) >= 1


# ── validate_trace invariants ────────────────────────────────────────────


class TestValidateTrace:
    def test_accepts_canonical_no_op_trace(self):
        trace = make_no_op_trace(
            layer='bluff_catch_override',
            rule_id='default',
            layer_order=3,
            reason_code='hand_class_not_eligible',
        )
        validate_trace(trace)  # no raise

    def test_rejects_unknown_layer(self):
        trace = InterventionTrace(layer='not_a_real_layer')
        with pytest.raises(ValueError, match='not in canonical _LAYER_NAMES'):
            validate_trace(trace)

    def test_rejects_unknown_rule_id_for_layer(self):
        trace = InterventionTrace(
            layer='bluff_catch_override', rule_id='hyper_aggressive',
        )
        with pytest.raises(ValueError, match='not valid for layer'):
            validate_trace(trace)

    def test_override_requires_replaced_prior_action(self):
        """Codex r3 invariant: operation=='override' ⇒ replaced_prior_action."""
        trace = InterventionTrace(
            layer='bluff_catch_override',
            rule_id='default',
            fired=True,
            operation=InterventionOperation.OVERRIDE.value,
            effect='distribution_replaced',
            replaced_prior_action=False,  # violation
        )
        with pytest.raises(ValueError, match='replaced_prior_action=True'):
            validate_trace(trace)

    def test_fired_true_must_not_be_no_op(self):
        trace = InterventionTrace(
            layer='bluff_catch_override', rule_id='default',
            fired=True, operation=InterventionOperation.NO_OP.value,
        )
        with pytest.raises(ValueError, match="fired=True with operation='no_op'"):
            validate_trace(trace)

    def test_fired_false_must_be_no_op(self):
        trace = InterventionTrace(
            layer='bluff_catch_override', rule_id='default',
            fired=False, operation=InterventionOperation.ADJUST.value,
        )
        with pytest.raises(ValueError, match="fired=False with"):
            validate_trace(trace)


# ── JSON round-trip ──────────────────────────────────────────────────────


class TestJsonRoundTrip:
    def test_no_op_trace_round_trips(self):
        trace = make_no_op_trace(
            layer='bluff_catch_override',
            rule_id='default',
            layer_order=3,
            reason_code='manager_unavailable',
        )
        payload = trace_to_json_dict(trace)
        # json.dumps must not raise — payload is JSON-safe.
        serialized = json.dumps(payload)
        decoded = json.loads(serialized)
        assert decoded['layer'] == 'bluff_catch_override'
        assert decoded['fired'] is False
        assert decoded['operation'] == 'no_op'
        assert decoded['reason_code'] == 'manager_unavailable'
        assert decoded['schema_version'] == TRACE_SCHEMA_VERSION

    def test_override_trace_round_trips(self):
        trace = InterventionTrace(
            layer='bluff_catch_override',
            rule_id='default',
            layer_order=3,
            fired=True,
            operation=InterventionOperation.OVERRIDE.value,
            effect='distribution_replaced',
            effect_size=0.6,
            action_changed=False,
            primary_action_before='call',
            primary_action_after='call',
            replaced_prior_action=True,
            prior_action_source='exploitation.hyper_aggressive',
            preserved_prior_intent=False,
            reason_code='medium_made_vs_extreme_facing_bet',
            rationale='Medium pair vs extreme jammer',
            confidence=1.0,
            inputs={'hand_strength': 'medium_made', 'bet_size_pot_ratio': 0.5},
            input_strategy_summary={'fold': 0.3, 'call': 0.5},
            output_strategy_summary={'fold': 0.4, 'call': 0.6},
        )
        payload = trace_to_json_dict(trace)
        serialized = json.dumps(payload)
        decoded = json.loads(serialized)

        assert decoded['operation'] == 'override'
        assert decoded['inputs']['hand_strength'] == 'medium_made'
        assert decoded['input_strategy_summary']['fold'] == 0.3

    def test_safe_serializes_enum_values_in_inputs(self):
        class _DummyEnum(str, Enum):
            FOO = 'foo'

        trace = InterventionTrace(
            layer='bluff_catch_override', rule_id='default',
            inputs={'tier': _DummyEnum.FOO},
        )
        payload = trace_to_json_dict(trace)
        # Serializer collapsed the enum to its value, no enum reference.
        assert payload['inputs']['tier'] == 'foo'
        json.dumps(payload)  # no TypeError

    def test_safe_serializes_nested_dataclasses_in_extra(self):
        @dataclass
        class _Nested:
            label: str
            count: int

        trace = InterventionTrace(
            layer='bluff_catch_override', rule_id='default',
            extra={'detail': _Nested(label='abc', count=3)},
        )
        payload = trace_to_json_dict(trace)
        assert payload['extra']['detail'] == {'label': 'abc', 'count': 3}
        json.dumps(payload)

    def test_safe_serializes_non_finite_floats_as_null(self):
        trace = InterventionTrace(
            layer='bluff_catch_override', rule_id='default',
            inputs={'unbounded': float('inf'), 'undefined': float('nan')},
        )
        payload = trace_to_json_dict(trace)
        # json.dumps with default settings rejects inf/nan — confirms
        # the serializer scrubs them.
        assert payload['inputs']['unbounded'] is None
        assert payload['inputs']['undefined'] is None
        json.dumps(payload)


# ── Pure helpers ─────────────────────────────────────────────────────────


class TestPureHelpers:
    def test_l1_distance_identical_distributions_is_zero(self):
        a = {'fold': 0.5, 'call': 0.5}
        assert l1_distance(a, a) == pytest.approx(0.0)

    def test_l1_distance_disjoint_action_sets(self):
        a = {'fold': 1.0}
        b = {'call': 1.0}
        # |1-0| + |0-1| = 2
        assert l1_distance(a, b) == pytest.approx(2.0)

    def test_l1_distance_partial_overlap(self):
        a = {'fold': 0.6, 'call': 0.4}
        b = {'fold': 0.3, 'call': 0.4, 'raise': 0.3}
        # |0.6-0.3| + |0.4-0.4| + |0-0.3| = 0.6
        assert l1_distance(a, b) == pytest.approx(0.6)

    def test_primary_action_argmax(self):
        assert primary_action({'fold': 0.6, 'call': 0.3, 'raise': 0.1}) == 'fold'

    def test_primary_action_empty_returns_empty_string(self):
        assert primary_action({}) == ''

    def test_primary_action_zero_distribution_returns_empty_string(self):
        """All-zero dist has no real argmax — sentinel empty string."""
        assert primary_action({'fold': 0.0, 'call': 0.0}) == ''

    def test_summarize_strategy_caps_to_top_n(self):
        probs = {
            'fold': 0.4, 'call': 0.3, 'raise_2.5': 0.2,
            'raise_4': 0.05, 'raise_6': 0.05,
        }
        summary = summarize_strategy(probs, top_n=3)
        assert set(summary.keys()) == {'fold', 'call', 'raise_2.5'}

    def test_summarize_strategy_rounds_to_4dp(self):
        probs = {'fold': 0.123456789}
        assert summarize_strategy(probs)['fold'] == 0.1235

    def test_amount_bucket_jam_aliases(self):
        assert amount_bucket('all_in') == 'jam'
        assert amount_bucket('jam') == 'jam'

    def test_amount_bucket_returns_empty_for_non_sizing_actions(self):
        for action in ('fold', 'call', 'check'):
            assert amount_bucket(action) == ''

    def test_amount_bucket_preflop_bb_multiples(self):
        # ≤ 2.5x bb → small
        assert amount_bucket('raise_2.5') == 'small'
        assert amount_bucket('raise_2.2') == 'small'
        # 2.5x..4x → medium
        assert amount_bucket('raise_3') == 'medium'
        assert amount_bucket('raise_4') == 'medium'
        # 4x..8x → large
        assert amount_bucket('raise_6') == 'large'
        # > 8x but < 10x → jam-ish (rare in practice)
        assert amount_bucket('raise_9') == 'jam'

    def test_amount_bucket_postflop_pot_percents(self):
        # ≤ 50% pot → small
        assert amount_bucket('bet_33') == 'small'
        # ≤ 100% pot → medium
        assert amount_bucket('bet_67') == 'medium'
        assert amount_bucket('bet_100') == 'medium'
        # ≤ 200% pot → large
        assert amount_bucket('raise_150') == 'large'
        # > 200% pot → jam-ish
        assert amount_bucket('bet_300') == 'jam'

    def test_amount_bucket_unparseable_suffix_returns_empty(self):
        assert amount_bucket('raise_foo') == ''
