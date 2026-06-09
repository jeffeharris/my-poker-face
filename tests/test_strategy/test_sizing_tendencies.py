"""Tests for per-player preflop sizing personalities (sizing_tendencies P1).

The substrate (docs/plans/SIZING_TENDENCIES.md, Sequencing P1): a deterministic,
persona-seeded `base_size_bias` that scales the chart-derived raise size BEFORE
the live jitter, so same-archetype players visibly size differently while the
deterministic / Baseline-GTO path (multiplier 1.0) stays byte-identical.

Covers:
  * the no-op invariant (size_multiplier=1.0 == the pre-personality path),
  * composition order (multiplier applied before jitter),
  * sample_sizing_personality determinism + per-persona variety + archetype lean,
  * resolve_size_multiplier (P1 = context-independent base bias),
  * the parse_sizing_tendencies override-lane parser.
"""

from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from poker.strategy.action_mapper import _compute_raise_to, resolve_preflop_sizing
from poker.strategy.sizing_tendencies import (
    ARCHETYPE_SIZE_BIAS,
    SIZE_MULT_MAX,
    SIZE_MULT_MIN,
    SizeContext,
    SizingPersonality,
    parse_sizing_tendencies,
    resolve_size_multiplier,
    sample_sizing_personality,
)


def _make_preflop_game_state(player_stack=10000, highest_bet=200, big_blind=100):
    player = SimpleNamespace(stack=player_stack, bet=0)
    return SimpleNamespace(
        players=[player],
        current_ante=big_blind,
        highest_bet=highest_bet,
        min_raise_amount=big_blind,
        pot={'total': 300},
    )


class TestNoOpInvariant:
    """size_multiplier=1.0 must produce byte-identical amounts to the current
    code path (mirrors the spot_tendencies no-op-preflop invariant)."""

    def test_compute_raise_to_default_multiplier_is_noop(self):
        # Default (1.0) == explicit 1.0 == the pre-personality value.
        base = _compute_raise_to(2.5, 100, 50, 10000, big_blind=100)
        assert base == 250
        assert _compute_raise_to(2.5, 100, 50, 10000, big_blind=100, size_multiplier=1.0) == 250
        assert _compute_raise_to(3.0, 250, 50, 10000, big_blind=100, size_multiplier=1.0) == 750

    def test_resolve_preflop_default_matches_explicit_one(self):
        state = _make_preflop_game_state()
        # The existing deterministic assertion must still hold unchanged.
        assert resolve_preflop_sizing('raise_3bb', state, 0) == ('raise', 300)
        assert resolve_preflop_sizing('raise_3bb', state, 0, size_multiplier=1.0) == ('raise', 300)

    def test_jittered_path_identical_with_multiplier_one(self):
        # With the same seed, multiplier=1.0 must be byte-identical to omitting it
        # entirely across many jittered samples (the live path stays exact at 1.0).
        for seed in (1, 7, 42, 99):
            state = _make_preflop_game_state()
            rng_a = random.Random(seed)
            rng_b = random.Random(seed)
            a = [
                resolve_preflop_sizing('raise_3bb', state, 0, rng=rng_a, sizing_jitter=0.12)
                for _ in range(40)
            ]
            b = [
                resolve_preflop_sizing(
                    'raise_3bb', state, 0, rng=rng_b, sizing_jitter=0.12, size_multiplier=1.0
                )
                for _ in range(40)
            ]
            assert a == b

    def test_neutral_personality_resolves_to_one(self):
        assert resolve_size_multiplier(SizingPersonality.neutral()) == 1.0
        assert resolve_size_multiplier(None) == 1.0
        assert SizingPersonality.neutral().is_neutral


class TestCompositionOrder:
    """chart token × size multiplier (center) → jitter ±band → human-round."""

    def test_multiplier_scales_center_before_jitter(self):
        # multiplier=1.2 with NO jitter → target 2.5bb*1.2 = 360 → clamps fine.
        state = _make_preflop_game_state(highest_bet=100, big_blind=100)
        # min_raise = highest_bet + min_raise_amount = 200; 3bb*1.2 = 360
        _, amt = resolve_preflop_sizing('raise_3bb', state, 0, size_multiplier=1.2)
        assert amt == 360
        _, amt_small = resolve_preflop_sizing('raise_3bb', state, 0, size_multiplier=0.9)
        assert amt_small == 270

    def test_jitter_band_centers_on_scaled_target(self):
        # With multiplier=1.2 the jitter band must sit around 360 (3bb*1.2), i.e.
        # higher than the un-scaled 300 center — proves the multiplier hits BEFORE
        # the ±12% jitter, not after.
        state = _make_preflop_game_state(highest_bet=100, big_blind=100)
        rng = random.Random(123)
        amounts = [
            resolve_preflop_sizing(
                'raise_3bb', state, 0, rng=rng, sizing_jitter=0.12, size_multiplier=1.2
            )[1]
            for _ in range(200)
        ]
        mean = sum(amounts) / len(amounts)
        # Band is [360*0.88, 360*1.12] = [316.8, 403.2], rounded; mean ~360.
        assert 340 <= mean <= 380, mean
        # And clearly above the un-scaled 300 center's band.
        assert min(amounts) > 300


class TestSamplePersonalityDeterminism:
    def _anchors(self, looseness, aggression):
        return SimpleNamespace(baseline_looseness=looseness, baseline_aggression=aggression)

    def test_same_persona_same_bias(self):
        a = self._anchors(0.85, 0.85)  # maniac
        p1 = sample_sizing_personality(a, persona_seed='Gordon Ramsay', archetype_key='maniac')
        p2 = sample_sizing_personality(a, persona_seed='Gordon Ramsay', archetype_key='maniac')
        assert p1.base_size_bias == p2.base_size_bias

    def test_different_personas_differ(self):
        a = self._anchors(0.85, 0.85)
        biases = {
            sample_sizing_personality(
                a, persona_seed=f'Maniac {i}', archetype_key='maniac'
            ).base_size_bias
            for i in range(30)
        }
        # 30 distinct personas → many distinct biases (the per-player spread).
        assert len(biases) >= 25

    def test_bias_clamped_to_band(self):
        a = self._anchors(0.85, 0.85)
        for i in range(500):
            b = sample_sizing_personality(
                a, persona_seed=f'p{i}', archetype_key='maniac'
            ).base_size_bias
            assert SIZE_MULT_MIN <= b <= SIZE_MULT_MAX

    def test_int_seed_is_used_directly(self):
        a = self._anchors(0.5, 0.5)
        p1 = sample_sizing_personality(a, persona_seed=12345, archetype_key='tag')
        p2 = sample_sizing_personality(a, persona_seed=12345, archetype_key='tag')
        assert p1.base_size_bias == p2.base_size_bias

    def test_archetype_key_inferred_from_anchors(self):
        # No explicit key → classified from anchors; maniac anchors → maniac mean.
        a = self._anchors(0.9, 0.9)
        p = sample_sizing_personality(a, persona_seed='x')
        assert SIZE_MULT_MIN <= p.base_size_bias <= SIZE_MULT_MAX


class TestArchetypeLean:
    """Per-archetype MEAN leans (maniac biggest, nit smallest) while spreads
    overlap. Checked on the SAMPLE MEAN over many personas (the lean), not any
    one draw (the overlap)."""

    def _anchors(self):
        return SimpleNamespace(baseline_looseness=0.5, baseline_aggression=0.5)

    def _sample_mean(self, key, n=4000):
        a = self._anchors()
        vals = [
            sample_sizing_personality(
                a, persona_seed=f'{key}-{i}', archetype_key=key
            ).base_size_bias
            for i in range(n)
        ]
        return sum(vals) / len(vals)

    def test_maniac_mean_above_nit_mean(self):
        assert self._sample_mean('maniac') > self._sample_mean('nit')

    def test_means_track_palette_centers(self):
        # Sample means land near the configured palette means (clamping pulls them
        # in only slightly since means sit well inside the band).
        for key, (mean, _sigma) in ARCHETYPE_SIZE_BIAS.items():
            got = self._sample_mean(key)
            assert abs(got - mean) < 0.02, (key, got, mean)

    def test_distributions_overlap(self):
        # The anti-caricature property: nit and maniac per-player biases OVERLAP
        # heavily — a given size maps to many types. There must exist a nit sized
        # bigger than some maniac.
        a = self._anchors()
        nit = [
            sample_sizing_personality(a, persona_seed=f'n{i}', archetype_key='nit').base_size_bias
            for i in range(200)
        ]
        maniac = [
            sample_sizing_personality(a, persona_seed=f'm{i}', archetype_key='maniac').base_size_bias
            for i in range(200)
        ]
        assert max(nit) > min(maniac), "nit/maniac size distributions should overlap"


class TestResolveSizeMultiplier:
    def test_p1_is_context_independent(self):
        p = SizingPersonality(base_size_bias=1.07)
        ctx_a = SizeContext(scenario='rfi', hand_strength='strong', position='UTG')
        ctx_b = SizeContext(scenario='vs_3bet', hand_strength='not_strong', position='BB')
        # P1: only base_size_bias is consulted — context does not change the result.
        assert resolve_size_multiplier(p, ctx_a) == pytest.approx(1.07)
        assert resolve_size_multiplier(p, ctx_b) == pytest.approx(1.07)
        assert resolve_size_multiplier(p, None) == pytest.approx(1.07)


class TestParseSizingTendencies:
    def test_empty_is_empty_tuple(self):
        assert parse_sizing_tendencies(None) == ()
        assert parse_sizing_tendencies([]) == ()

    def test_parses_json_pairs(self):
        out = parse_sizing_tendencies([['overbet_lean', 0.6], ['anchor_number', 1]])
        assert out == (('overbet_lean', 0.6), ('anchor_number', 1.0))
        assert all(isinstance(s, float) for _, s in out)

    def test_carried_onto_personality_behaviors(self):
        a = SimpleNamespace(baseline_looseness=0.5, baseline_aggression=0.5)
        p = sample_sizing_personality(
            a, persona_seed='x', archetype_key='tag',
            sizing_tendencies=(('overbet_lean', 0.5),),
        )
        assert p.behaviors == (('overbet_lean', 0.5),)
        assert not p.is_neutral  # has behaviors
