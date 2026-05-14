"""Tests for the §5 bluff-reduction exploitation rule.

The rule mirrors `value_vs_station` but with the inverse hand-strength
gate: when hero has an air-class hand AND a station is in the field,
the rule pushes bet_*/raise_* offsets negative (cut bluff frequency)
and shifts mass toward check/fold (the passive line). Stations don't
fold to bluffs, so the bluff is -EV.

The hand-strength gate is enforced at the controller level by passing
intensity=0 for non-air hands. Tests here exercise the offset-rule
directly via `compute_exploitation_offsets_with_traces`.
"""

import pytest

from poker.strategy.exploitation import (
    AggregatedOpponentStats,
    DecisionContext,
    compute_exploitation_offsets,
    compute_exploitation_offsets_with_traces,
)


def _stats() -> AggregatedOpponentStats:
    """Generic non-fatal stats — content doesn't matter when intensity
    is passed in directly (bluff_reduction only reads intensity)."""
    return AggregatedOpponentStats(
        hands_observed=100,
        vpip=0.50, pfr=0.20,
        aggression_factor=1.5,
        all_in_frequency=0.0,
    )


def _ctx() -> DecisionContext:
    return DecisionContext(is_preflop=False)


class TestBluffReductionFires:
    """Intensity > 0 → bet_*/raise_* shrink, check/fold grow."""

    def test_bet_actions_get_negative_offset(self):
        offsets = compute_exploitation_offsets(
            stats=_stats(), adaptation_bias=1.0, decision_context=_ctx(),
            available_actions=['fold', 'check', 'bet_33', 'bet_67', 'bet_100'],
            bluff_reduction_intensity=1.0,
        )
        # All bet_* actions get -0.20 * scale (= -0.20 * 1.0 * 1.0)
        assert offsets.get('bet_33', 0.0) < 0
        assert offsets.get('bet_67', 0.0) < 0
        assert offsets.get('bet_100', 0.0) < 0

    def test_raise_actions_get_negative_offset(self):
        offsets = compute_exploitation_offsets(
            stats=_stats(), adaptation_bias=1.0, decision_context=_ctx(),
            available_actions=['fold', 'call', 'raise_67', 'raise_150'],
            bluff_reduction_intensity=1.0,
        )
        assert offsets.get('raise_67', 0.0) < 0
        assert offsets.get('raise_150', 0.0) < 0

    def test_check_action_gets_positive_offset(self):
        offsets = compute_exploitation_offsets(
            stats=_stats(), adaptation_bias=1.0, decision_context=_ctx(),
            available_actions=['fold', 'check', 'bet_67'],
            bluff_reduction_intensity=1.0,
        )
        assert offsets.get('check', 0.0) > 0

    def test_fold_action_gets_positive_offset(self):
        # When facing a bet, fold (not check) is the passive line.
        offsets = compute_exploitation_offsets(
            stats=_stats(), adaptation_bias=1.0, decision_context=_ctx(),
            available_actions=['fold', 'call', 'raise_67'],
            bluff_reduction_intensity=1.0,
        )
        assert offsets.get('fold', 0.0) > 0

    def test_call_action_untouched(self):
        # The rule should NOT push call mass (that's value_vs_station /
        # defense_floor's job).
        offsets = compute_exploitation_offsets(
            stats=_stats(), adaptation_bias=1.0, decision_context=_ctx(),
            available_actions=['fold', 'call', 'raise_67'],
            bluff_reduction_intensity=1.0,
        )
        # No 'call' key emitted by this rule (call_amount-independent).
        # When combined with other rules call may appear, but with only
        # bluff_reduction firing, call shouldn't have a key.
        # (Allowing 0.0 in case another defaulted rule wrote 0 — but
        # call shouldn't have a nontrivial offset.)
        assert abs(offsets.get('call', 0.0)) < 1e-9


class TestBluffReductionDoesNotFireAtZeroIntensity:
    """Default intensity=0.0 keeps the rule no-op."""

    def test_intensity_zero_produces_no_offsets(self):
        offsets = compute_exploitation_offsets(
            stats=_stats(), adaptation_bias=1.0, decision_context=_ctx(),
            available_actions=['fold', 'check', 'bet_67'],
            bluff_reduction_intensity=0.0,
        )
        # Other rules may fire on these stats, but the bluff_reduction
        # contribution is zero — easiest to check via the traced
        # variant.
        _, traces = compute_exploitation_offsets_with_traces(
            stats=_stats(), adaptation_bias=1.0, decision_context=_ctx(),
            available_actions=['fold', 'check', 'bet_67'],
            bluff_reduction_intensity=0.0,
        )
        br_trace = next(
            t for t in traces
            if (t.layer, t.rule_id) == ('bluff_reduction', 'default')
        )
        assert br_trace.fired is False
        assert br_trace.reason_code == 'intensity_zero_or_gated'


class TestBluffReductionTrace:
    """The rule emits a properly-structured trace."""

    def test_fires_emits_adjust_trace(self):
        _, traces = compute_exploitation_offsets_with_traces(
            stats=_stats(), adaptation_bias=1.0, decision_context=_ctx(),
            available_actions=['fold', 'check', 'bet_67'],
            bluff_reduction_intensity=0.5,
        )
        br = next(
            t for t in traces
            if (t.layer, t.rule_id) == ('bluff_reduction', 'default')
        )
        assert br.fired is True
        assert br.operation == 'adjust'
        assert br.layer_order == 1
        assert br.reason_code == 'air_hand_vs_station'
        assert br.inputs.get('bluff_reduction_intensity') == 0.5

    def test_intensity_scales_offset_magnitude(self):
        """Half intensity → half magnitude."""
        full = compute_exploitation_offsets(
            stats=_stats(), adaptation_bias=1.0, decision_context=_ctx(),
            available_actions=['fold', 'check', 'bet_67'],
            bluff_reduction_intensity=1.0,
        )
        half = compute_exploitation_offsets(
            stats=_stats(), adaptation_bias=1.0, decision_context=_ctx(),
            available_actions=['fold', 'check', 'bet_67'],
            bluff_reduction_intensity=0.5,
        )
        assert half.get('bet_67', 0.0) == pytest.approx(
            full.get('bet_67', 0.0) * 0.5, abs=1e-6,
        )


class TestBluffReductionDisable:
    """Ablation: disable_rules={('bluff_reduction', 'default')} suppresses."""

    def test_disabled_rule_emits_disabled_trace(self):
        _, traces = compute_exploitation_offsets_with_traces(
            stats=_stats(), adaptation_bias=1.0, decision_context=_ctx(),
            available_actions=['fold', 'check', 'bet_67'],
            bluff_reduction_intensity=1.0,
            disable_rules={('bluff_reduction', 'default')},
        )
        br = next(
            t for t in traces
            if (t.layer, t.rule_id) == ('bluff_reduction', 'default')
        )
        assert br.fired is False
        assert br.reason_code == 'disabled_by_ablation'

    def test_disabled_offsets_absent(self):
        offsets = compute_exploitation_offsets(
            stats=_stats(), adaptation_bias=1.0, decision_context=_ctx(),
            available_actions=['fold', 'check', 'bet_67'],
            bluff_reduction_intensity=1.0,
            disable_rules={('bluff_reduction', 'default')},
        )
        # When disabled the rule shouldn't contribute. Other rules may
        # still emit offsets, but the negative bet_67 shift from
        # bluff_reduction should be absent.
        assert offsets.get('bet_67', 0.0) >= 0.0


class TestRuleSeparationFromValueVsStation:
    """bluff_reduction and value_vs_station push bet_* in OPPOSITE
    directions. The hand-class gate (enforced upstream by the
    controller) ensures only one fires per decision."""

    def test_value_vs_station_pushes_bet_positive(self):
        offsets = compute_exploitation_offsets(
            stats=_stats(), adaptation_bias=1.0, decision_context=_ctx(),
            available_actions=['fold', 'check', 'bet_67'],
            value_vs_station_intensity=1.0,
        )
        assert offsets.get('bet_67', 0.0) > 0

    def test_bluff_reduction_pushes_bet_negative(self):
        offsets = compute_exploitation_offsets(
            stats=_stats(), adaptation_bias=1.0, decision_context=_ctx(),
            available_actions=['fold', 'check', 'bet_67'],
            bluff_reduction_intensity=1.0,
        )
        assert offsets.get('bet_67', 0.0) < 0

    def test_both_intensities_at_one_partially_cancel_on_bet(self):
        """If both fired simultaneously (shouldn't normally happen, but
        confirm the rule doesn't double-up), the bet_67 offsets
        partially offset each other: +0.30 from vvs, -0.20 from
        bluff_reduction → +0.10 net."""
        offsets = compute_exploitation_offsets(
            stats=_stats(), adaptation_bias=1.0, decision_context=_ctx(),
            available_actions=['fold', 'check', 'bet_67'],
            value_vs_station_intensity=1.0,
            bluff_reduction_intensity=1.0,
        )
        # phase_8_multiplier scales both. Net bet_67 = +0.3 - 0.2 = +0.1 * scale
        # (positive, but smaller than pure vvs).
        bet_offset = offsets.get('bet_67', 0.0)
        assert bet_offset > 0  # still positive net
        # Compare to pure value_vs_station
        vvs_only = compute_exploitation_offsets(
            stats=_stats(), adaptation_bias=1.0, decision_context=_ctx(),
            available_actions=['fold', 'check', 'bet_67'],
            value_vs_station_intensity=1.0,
        )
        assert bet_offset < vvs_only.get('bet_67', 0.0)
