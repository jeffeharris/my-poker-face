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
    SIZE_BY_STRENGTH,
    SIZE_BY_STRENGTH_GAP,
    SIZE_BY_STRENGTH_WEIGHTS,
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
            sample_sizing_personality(
                a, persona_seed=f'm{i}', archetype_key='maniac'
            ).base_size_bias
            for i in range(200)
        ]
        assert max(nit) > min(maniac), "nit/maniac size distributions should overlap"


class TestResolveSizeMultiplier:
    def test_no_behavior_is_context_independent(self):
        # A personality with NO palette behaviors stays context-independent (the P1
        # contract): only base_size_bias is consulted.
        p = SizingPersonality(base_size_bias=1.07)
        ctx_a = SizeContext(scenario='rfi', hand_strength='strong', position='UTG')
        ctx_b = SizeContext(scenario='vs_3bet', hand_strength='not_strong', position='BB')
        assert resolve_size_multiplier(p, ctx_a) == pytest.approx(1.07)
        assert resolve_size_multiplier(p, ctx_b) == pytest.approx(1.07)
        assert resolve_size_multiplier(p, None) == pytest.approx(1.07)


class TestSizeByStrength:
    """P2: size_by_strength scales UP for strong hands, DOWN for not-strong, around
    the base_size_bias center, composed multiplicatively and clamped."""

    def test_strong_sizes_up_weak_sizes_down(self):
        p = SizingPersonality(base_size_bias=1.0, behaviors=((SIZE_BY_STRENGTH, 1.0),))
        strong = resolve_size_multiplier(p, SizeContext(hand_strength='strong'))
        weak = resolve_size_multiplier(p, SizeContext(hand_strength='not_strong'))
        assert strong > 1.0 > weak
        half_gap = 0.5 * SIZE_BY_STRENGTH_GAP
        assert strong == pytest.approx(1.0 + half_gap)
        assert weak == pytest.approx(1.0 - half_gap)

    def test_center_preserved_on_average(self):
        # Mean of strong+weak (a strength-balanced mix) stays ≈ base bias — only the
        # CORRELATION with strength is the tell, not the absolute size.
        p = SizingPersonality(base_size_bias=1.03, behaviors=((SIZE_BY_STRENGTH, 1.0),))
        strong = resolve_size_multiplier(p, SizeContext(hand_strength='strong'))
        weak = resolve_size_multiplier(p, SizeContext(hand_strength='not_strong'))
        assert (strong + weak) / 2 == pytest.approx(1.03, abs=0.01)

    def test_no_context_or_none_strength_is_base_bias(self):
        # size_by_strength present but hand_strength unknown → base bias only (no-op).
        p = SizingPersonality(base_size_bias=1.05, behaviors=((SIZE_BY_STRENGTH, 1.0),))
        assert resolve_size_multiplier(p, None) == pytest.approx(1.05)
        assert resolve_size_multiplier(p, SizeContext(hand_strength=None)) == pytest.approx(1.05)

    def test_strength_param_scales_the_swing(self):
        weak_param = SizingPersonality(base_size_bias=1.0, behaviors=((SIZE_BY_STRENGTH, 0.5),))
        full_param = SizingPersonality(base_size_bias=1.0, behaviors=((SIZE_BY_STRENGTH, 1.0),))
        ctx = SizeContext(hand_strength='strong')
        # A 0.5-strength tell swings half as far from center as a 1.0-strength tell.
        assert (resolve_size_multiplier(weak_param, ctx) - 1.0) == pytest.approx(
            0.5 * (resolve_size_multiplier(full_param, ctx) - 1.0)
        )

    def test_composed_multiplier_clamped(self):
        # A big-biased recreational player with a strong hand can't balloon past MAX.
        p = SizingPersonality(base_size_bias=SIZE_MULT_MAX, behaviors=((SIZE_BY_STRENGTH, 1.0),))
        assert resolve_size_multiplier(p, SizeContext(hand_strength='strong')) <= SIZE_MULT_MAX
        p_lo = SizingPersonality(base_size_bias=SIZE_MULT_MIN, behaviors=((SIZE_BY_STRENGTH, 1.0),))
        assert (
            resolve_size_multiplier(p_lo, SizeContext(hand_strength='not_strong')) >= SIZE_MULT_MIN
        )

    def test_neutral_personality_invariant_to_strength(self):
        # Baseline-GTO / neutral → exactly 1.0 regardless of hand_strength.
        n = SizingPersonality.neutral()
        assert resolve_size_multiplier(n, SizeContext(hand_strength='strong')) == 1.0
        assert resolve_size_multiplier(n, SizeContext(hand_strength='not_strong')) == 1.0
        assert resolve_size_multiplier(None, SizeContext(hand_strength='strong')) == 1.0


class TestPaletteSampling:
    """Archetype-weighted palette sampling: recreational tiers carry size_by_strength,
    the disciplined/competent archetypes never do (regs stay clean)."""

    def _anchors(self):
        return SimpleNamespace(baseline_looseness=0.5, baseline_aggression=0.5)

    def _carries_rate(self, key, n=4000):
        a = self._anchors()
        carries = sum(
            1
            for i in range(n)
            if any(
                b == SIZE_BY_STRENGTH
                for b, _ in sample_sizing_personality(
                    a, persona_seed=f'{key}-{i}', archetype_key=key
                ).behaviors
            )
        )
        return carries / n

    def test_regs_never_carry_the_tell(self):
        # tag + the disciplined tiers must NEVER sample size_by_strength.
        for key in ('tag', 'nit', 'rock', 'lag', 'maniac'):
            assert self._carries_rate(key) == 0.0, key

    def test_reg_multiplier_invariant_to_hand_strength(self):
        # A reg (tag) — sampled, no tell — sizes identically for strong & weak hands.
        a = self._anchors()
        for i in range(50):
            p = sample_sizing_personality(a, persona_seed=f'tag-{i}', archetype_key='tag')
            strong = resolve_size_multiplier(p, SizeContext(hand_strength='strong'))
            weak = resolve_size_multiplier(p, SizeContext(hand_strength='not_strong'))
            assert strong == weak == pytest.approx(p.base_size_bias)

    def test_recreational_tiers_carry_with_expected_probability(self):
        for key in ('calling_station', 'weak_fish'):
            rate = self._carries_rate(key)
            expected = SIZE_BY_STRENGTH_WEIGHTS[key]
            assert rate == pytest.approx(expected, abs=0.03), (key, rate, expected)

    def test_recreational_persona_has_learnable_tell(self):
        # A carrying recreational persona sizes strong > weak (the earned read).
        a = self._anchors()
        carriers = [
            p
            for i in range(40)
            for p in [
                sample_sizing_personality(a, persona_seed=f'wf-{i}', archetype_key='weak_fish')
            ]
            if any(b == SIZE_BY_STRENGTH for b, _ in p.behaviors)
        ]
        assert carriers, "expected some weak_fish to carry size_by_strength"
        p = carriers[0]
        strong = resolve_size_multiplier(p, SizeContext(hand_strength='strong'))
        weak = resolve_size_multiplier(p, SizeContext(hand_strength='not_strong'))
        assert strong > weak


class TestOverrideLanePinsBehaviors:
    def test_explicit_override_wins_over_archetype_draw(self):
        # The per-personality override lane pins behaviors even on a clean reg.
        a = SimpleNamespace(baseline_looseness=0.5, baseline_aggression=0.5)
        p = sample_sizing_personality(
            a,
            persona_seed='tag-x',
            archetype_key='tag',
            sizing_tendencies=((SIZE_BY_STRENGTH, 1.0),),
        )
        assert p.behaviors == ((SIZE_BY_STRENGTH, 1.0),)
        strong = resolve_size_multiplier(p, SizeContext(hand_strength='strong'))
        weak = resolve_size_multiplier(p, SizeContext(hand_strength='not_strong'))
        assert strong > weak

    def test_explicit_override_skips_palette_draw(self):
        # A pinned override means the archetype palette draw is skipped — a
        # recreational persona pinned to () carries NO sampled tell.
        a = SimpleNamespace(baseline_looseness=0.6, baseline_aggression=0.3)
        # Pin a different (P3-shaped) behavior; size_by_strength must not be added.
        p = sample_sizing_personality(
            a,
            persona_seed='cs-pin',
            archetype_key='calling_station',
            sizing_tendencies=(('anchor_number', 1.0),),
        )
        assert p.behaviors == (('anchor_number', 1.0),)
        assert not any(b == SIZE_BY_STRENGTH for b, _ in p.behaviors)


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
            a,
            persona_seed='x',
            archetype_key='tag',
            sizing_tendencies=(('overbet_lean', 0.5),),
        )
        assert p.behaviors == (('overbet_lean', 0.5),)
        assert not p.is_neutral  # has behaviors
