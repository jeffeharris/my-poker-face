"""Phase 7.6 Step 5: NarrationFacts adapter tests.

Covers:
  - Allowlist filtering: personality / short_stack / math_floor never
    surface, allowlisted layers do
  - REASON_CODE_TO_OBSERVATION lookups + fallbacks
  - Top-3 cap + suppressed_facts_count accounting
  - primary_factor is the highest-scoring fact
  - Override-chain downranking (Codex r3): superseded layers' facts
    are present but don't dominate
  - Intensity vs certainty buckets are independent (Codex r2)
  - render_narration_prompt produces a structured prompt block
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from poker.strategy.intervention_trace import (
    InterventionOperation,
    InterventionTrace,
    make_no_op_trace,
)
from poker.strategy.narration_facts import (
    LAYER_RULE_NARRATIVE_WEIGHT,
    NARRATION_ALLOWLIST,
    NARRATION_MAX_FACTS,
    REASON_CODE_TO_OBSERVATION,
    NarrationContext,
    _certainty_bucket,
    _intensity_bucket,
    _score_fact_importance,
    render_narration_prompt,
    traces_to_narration_facts,
)

_CTX_FLOP = NarrationContext(
    street='flop',
    position_context='in_position',
    risk_posture='balanced',
)


def _fire_trace(
    layer: str,
    rule_id: str = 'default',
    *,
    operation: str = InterventionOperation.ADJUST.value,
    reason_code: str = '',
    effect_size: float = 0.3,
    confidence: float = 0.7,
    action_changed: bool = True,
    primary_action_after: str = 'call',
    layer_order: int = 1,
    replaced_prior_action: bool = False,
    prior_action_source: str = '',
) -> InterventionTrace:
    return InterventionTrace(
        layer=layer,
        rule_id=rule_id,
        layer_order=layer_order,
        fired=True,
        operation=operation,
        effect='offsets_applied' if operation == 'adjust' else 'distribution_replaced',
        effect_size=effect_size,
        action_changed=action_changed,
        primary_action_before='fold' if action_changed else primary_action_after,
        primary_action_after=primary_action_after,
        replaced_prior_action=replaced_prior_action,
        prior_action_source=prior_action_source,
        reason_code=reason_code,
        confidence=confidence,
    )


# ── Allowlist filtering ──────────────────────────────────────────────────


class TestAllowlistFiltering:
    def test_personality_never_surfaces(self):
        """Even a fired personality trace is suppressed — mechanical,
        not narratable."""
        traces = [_fire_trace('personality', 'default')]
        result = traces_to_narration_facts(traces, _CTX_FLOP)
        assert result.facts == []
        assert result.suppressed_facts_count >= 1

    def test_short_stack_never_surfaces(self):
        traces = [
            _fire_trace('short_stack', 'default', operation=InterventionOperation.CLAMP.value)
        ]
        result = traces_to_narration_facts(traces, _CTX_FLOP)
        assert result.facts == []

    def test_math_floor_never_surfaces(self):
        traces = [_fire_trace('math_floor', 'default', operation=InterventionOperation.VETO.value)]
        result = traces_to_narration_facts(traces, _CTX_FLOP)
        assert result.facts == []

    def test_unknown_layer_suppressed(self):
        # An unknown layer is rejected (not in allowlist).
        traces = [_fire_trace('mystery_layer', 'default')]
        result = traces_to_narration_facts(traces, _CTX_FLOP)
        assert result.facts == []

    def test_no_op_trace_suppressed(self):
        traces = [
            make_no_op_trace(
                layer='bluff_catch_override',
                rule_id='default',
                layer_order=3,
                reason_code='hand_class_not_eligible',
            )
        ]
        result = traces_to_narration_facts(traces, _CTX_FLOP)
        assert result.facts == []
        assert result.suppressed_facts_count == 1

    def test_allowlisted_fired_trace_surfaces(self):
        traces = [
            _fire_trace(
                'bluff_catch_override',
                'default',
                operation=InterventionOperation.OVERRIDE.value,
                reason_code='medium_made_vs_extreme_facing_bet',
                layer_order=3,
            )
        ]
        result = traces_to_narration_facts(traces, _CTX_FLOP)
        assert len(result.facts) == 1
        assert result.primary_factor is not None
        assert 'showdown value' in result.primary_factor.observation


# ── REASON_CODE mappings ─────────────────────────────────────────────────


class TestReasonCodeMapping:
    def test_curated_reason_code_produces_specific_phrasing(self):
        traces = [
            _fire_trace(
                'exploitation',
                'hyper_aggressive',
                reason_code='extreme_tier_via_all_in_frequency',
            )
        ]
        result = traces_to_narration_facts(traces, _CTX_FLOP)
        assert result.facts[0].observation == "Opponent's been jamming a lot"

    def test_unknown_reason_code_falls_back_to_generic(self):
        """A reason_code not in REASON_CODE_TO_OBSERVATION still produces
        a fact via the per-(layer, rule_id) fallback."""
        traces = [
            _fire_trace(
                'exploitation',
                'hyper_aggressive',
                reason_code='novel_unmapped_reason',
            )
        ]
        result = traces_to_narration_facts(traces, _CTX_FLOP)
        assert len(result.facts) == 1
        # Some non-empty phrasing was produced.
        assert result.facts[0].observation != ''

    def test_every_curated_reason_code_has_observation_and_why(self):
        """Sanity check on the curated dict — both fields must be
        non-empty so the prompt never emits a blank line."""
        for code, (obs, why) in REASON_CODE_TO_OBSERVATION.items():
            assert obs, f"reason_code {code!r} has empty observation"
            assert why, f"reason_code {code!r} has empty why_it_matters"


# ── Top-N cap ────────────────────────────────────────────────────────────


class TestTopNCap:
    def test_caps_to_3_facts(self):
        """If more than 3 allowlisted rules fire, only top 3 surface."""
        traces = [
            _fire_trace(
                'exploitation', 'hyper_aggressive', reason_code='extreme_tier_via_all_in_frequency'
            ),
            _fire_trace('exploitation', 'hyper_passive', reason_code='station_value_extract'),
            _fire_trace('exploitation', 'tight_nit', reason_code='nit_steal_open'),
            _fire_trace('exploitation', 'high_fold_to_cbet', reason_code='hu_cbet_exploit'),
            _fire_trace(
                'bluff_catch_override',
                'default',
                operation=InterventionOperation.OVERRIDE.value,
                reason_code='medium_made_vs_extreme_facing_bet',
                layer_order=3,
            ),
        ]
        result = traces_to_narration_facts(traces, _CTX_FLOP)
        assert len(result.facts) == NARRATION_MAX_FACTS  # 3
        # 5 fired traces, top 3 kept → 2 suppressed by cap.
        assert result.suppressed_facts_count == 2

    def test_primary_factor_is_highest_scoring(self):
        """bluff_catch_override (operation=OVERRIDE + action_changed +
        layer_order=3) should outrank a simple exploitation adjust."""
        traces = [
            _fire_trace(
                'exploitation',
                'hyper_aggressive',
                reason_code='medium_tier_via_aggression_factor',
                operation=InterventionOperation.ADJUST.value,
                layer_order=1,
            ),
            _fire_trace(
                'bluff_catch_override',
                'default',
                operation=InterventionOperation.OVERRIDE.value,
                reason_code='medium_made_vs_extreme_facing_bet',
                layer_order=3,
                action_changed=True,
            ),
        ]
        result = traces_to_narration_facts(traces, _CTX_FLOP)
        assert result.primary_factor is not None
        assert result.primary_factor.layer == 'bluff_catch_override'


# ── Override-chain downranking (Codex r3) ────────────────────────────────


class TestOverrideChainDownranking:
    def test_overridden_layer_downranked(self):
        """When a later layer's prior_action_source points at an earlier
        layer, the earlier layer's importance is multiplied by 0.3."""
        traces = [
            _fire_trace(
                'exploitation',
                'hyper_aggressive',
                operation=InterventionOperation.ADJUST.value,
                reason_code='extreme_tier_via_all_in_frequency',
                layer_order=1,
                primary_action_after='call',
            ),
            _fire_trace(
                'bluff_catch_override',
                'default',
                operation=InterventionOperation.OVERRIDE.value,
                reason_code='medium_made_vs_extreme_facing_bet',
                layer_order=3,
                primary_action_after='call',
                replaced_prior_action=True,
                prior_action_source='exploitation.hyper_aggressive',
            ),
        ]
        result = traces_to_narration_facts(traces, _CTX_FLOP)
        # Both surface, but bluff_catch_override is primary (higher score).
        assert result.primary_factor.layer == 'bluff_catch_override'
        # The downranked exploitation fact should still be present (top-3
        # cap still allows it), but its importance < bluff_catch's.
        if len(result.facts) >= 2:
            scores = {f.layer: f.importance for f in result.facts}
            assert scores['bluff_catch_override'] > scores.get('exploitation', 0)


# ── Bucket helpers ───────────────────────────────────────────────────────


class TestBuckets:
    def test_intensity_bucket_thresholds(self):
        assert _intensity_bucket(0.0) == 'subtle'
        assert _intensity_bucket(0.1) == 'subtle'
        assert _intensity_bucket(0.2) == 'noticeable'
        assert _intensity_bucket(0.4) == 'noticeable'
        assert _intensity_bucket(0.5) == 'strong'
        assert _intensity_bucket(2.0) == 'strong'

    def test_certainty_bucket_thresholds(self):
        assert _certainty_bucket(0.0) == 'tentative'
        assert _certainty_bucket(0.3) == 'tentative'
        assert _certainty_bucket(0.5) == 'confident'
        assert _certainty_bucket(0.7) == 'confident'
        assert _certainty_bucket(0.8) == 'sure'
        assert _certainty_bucket(1.0) == 'sure'

    def test_intensity_and_certainty_independent(self):
        """Codex r2: 'strong effect' ≠ 'high confidence'. A subtle
        effect can be highly certain; a strong effect can be tentative."""
        # Strong effect, tentative certainty (high effect_size, low confidence)
        traces = [
            _fire_trace(
                'bluff_catch_override',
                'default',
                operation=InterventionOperation.OVERRIDE.value,
                reason_code='medium_made_vs_extreme_facing_bet',
                effect_size=0.8,
                confidence=0.3,
                layer_order=3,
            )
        ]
        result = traces_to_narration_facts(traces, _CTX_FLOP)
        fact = result.facts[0]
        assert fact.intensity_bucket == 'strong'
        assert fact.certainty_bucket == 'tentative'


# ── _score_fact_importance directly ──────────────────────────────────────


class TestScoreFactImportance:
    def test_override_higher_than_adjust(self):
        ctx = _CTX_FLOP
        override = _fire_trace(
            'bluff_catch_override',
            'default',
            operation=InterventionOperation.OVERRIDE.value,
            layer_order=3,
            action_changed=True,
        )
        adjust = _fire_trace(
            'exploitation',
            'hyper_aggressive',
            operation=InterventionOperation.ADJUST.value,
            layer_order=1,
            action_changed=False,
        )
        assert _score_fact_importance(override, ctx, False) > _score_fact_importance(
            adjust, ctx, False
        )

    def test_overridden_flag_downranks_0_3x(self):
        ctx = _CTX_FLOP
        trace = _fire_trace(
            'exploitation',
            'hyper_aggressive',
            operation=InterventionOperation.ADJUST.value,
            layer_order=1,
        )
        live = _score_fact_importance(trace, ctx, False)
        overridden = _score_fact_importance(trace, ctx, True)
        # Downranked score should be ~0.3 × live (with rounding).
        assert overridden == pytest.approx(live * 0.3, abs=0.001)


# ── render_narration_prompt ──────────────────────────────────────────────


class TestRenderPrompt:
    def test_empty_facts_returns_empty_string(self):
        result = traces_to_narration_facts([], _CTX_FLOP)
        prompt = render_narration_prompt(result)
        assert prompt == ''

    def test_prompt_includes_observation_lines(self):
        traces = [
            _fire_trace(
                'bluff_catch_override',
                'default',
                operation=InterventionOperation.OVERRIDE.value,
                reason_code='medium_made_vs_extreme_facing_bet',
                layer_order=3,
            )
        ]
        result = traces_to_narration_facts(traces, _CTX_FLOP)
        prompt = render_narration_prompt(result)
        assert 'WHAT YOU NOTICED' in prompt
        assert 'WHAT YOU DECIDED' in prompt
        assert 'showdown value' in prompt
        # suppressed_facts_count is debug-only — never in the prompt.
        assert 'suppressed' not in prompt.lower()

    def test_prompt_doesnt_leak_rationale(self):
        """The LLM must never see the dev `rationale` string — only the
        player-facing observation from REASON_CODE_TO_OBSERVATION."""
        traces = [
            _fire_trace(
                'exploitation',
                'hyper_aggressive',
                reason_code='extreme_tier_via_all_in_frequency',
                confidence=0.85,
            )
        ]
        # Set a rationale that contains dev-internal stat names.
        traces[0] = replace(
            traces[0],
            rationale="postflop_jam_open_rate=0.32 (extreme tier)",
        )
        result = traces_to_narration_facts(traces, _CTX_FLOP)
        prompt = render_narration_prompt(result)
        assert 'postflop_jam_open_rate' not in prompt
        assert '0.32' not in prompt


# ── Action intent ────────────────────────────────────────────────────────


class TestActionIntent:
    def test_bluff_catch_override_is_bluff_catch_intent(self):
        traces = [
            _fire_trace(
                'bluff_catch_override',
                'default',
                operation=InterventionOperation.OVERRIDE.value,
                reason_code='medium_made_vs_extreme_facing_bet',
                layer_order=3,
            )
        ]
        result = traces_to_narration_facts(traces, _CTX_FLOP)
        assert result.facts[0].action_intent == 'bluff_catch'

    def test_strong_hand_override_is_value_bet_intent(self):
        traces = [
            _fire_trace(
                'strong_hand_override',
                'default',
                operation=InterventionOperation.OVERRIDE.value,
                reason_code='facing_all_in_call',
                layer_order=2,
            )
        ]
        result = traces_to_narration_facts(traces, _CTX_FLOP)
        assert result.facts[0].action_intent == 'value_bet'

    def test_tight_nit_is_steal_intent(self):
        traces = [
            _fire_trace(
                'exploitation',
                'tight_nit',
                reason_code='nit_steal_open',
            )
        ]
        result = traces_to_narration_facts(traces, _CTX_FLOP)
        assert result.facts[0].action_intent == 'steal'


# ── Summary intensity ───────────────────────────────────────────────────


class TestSummaryIntensity:
    def test_summary_intensity_is_max_across_facts(self):
        """summary_intensity = highest intensity_bucket across surfaced facts."""
        traces = [
            _fire_trace(
                'exploitation',
                'hyper_aggressive',
                reason_code='medium_tier_via_aggression_factor',
                effect_size=0.1,
            ),  # subtle
            _fire_trace(
                'bluff_catch_override',
                'default',
                operation=InterventionOperation.OVERRIDE.value,
                reason_code='medium_made_vs_extreme_facing_bet',
                effect_size=0.6,  # strong
                layer_order=3,
            ),
        ]
        result = traces_to_narration_facts(traces, _CTX_FLOP)
        assert result.summary_intensity == 'strong'
