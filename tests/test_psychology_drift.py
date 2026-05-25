"""Per-session archetype drift — pure-function tests.

Validates `apply_session_drift` (poker/psychology_model.py): drift
strength derives from poise + recovery_rate, identity-load-bearing
anchors stay tightly bounded, and chaotic characters drift visibly
while stoic characters don't move.
"""

import random
from dataclasses import replace

import pytest

from poker.psychology_model import (
    DRIFT_BASE_SIGMA,
    DRIFT_STRENGTH_FLOOR,
    PersonalityAnchors,
    apply_session_drift,
)
from poker.strategy.deviation_profiles import select_deviation_profile


def _stoic_anchors() -> PersonalityAnchors:
    """High poise + high recovery_rate → drift_strength near zero."""
    return PersonalityAnchors(
        baseline_aggression=0.7,
        baseline_looseness=0.35,
        ego=0.5,
        poise=0.95,
        expressiveness=0.4,
        risk_identity=0.5,
        adaptation_bias=0.5,
        baseline_energy=0.5,
        recovery_rate=0.85,
    )


def _chaotic_anchors() -> PersonalityAnchors:
    """Low poise + low recovery_rate → high drift_strength."""
    return PersonalityAnchors(
        baseline_aggression=0.8,
        baseline_looseness=0.7,
        ego=0.7,
        poise=0.3,
        expressiveness=0.7,
        risk_identity=0.6,
        adaptation_bias=0.5,
        baseline_energy=0.7,
        recovery_rate=0.1,
    )


def _just_above_floor_anchors() -> PersonalityAnchors:
    """drift_strength slightly above DRIFT_STRENGTH_FLOOR (0.05).

    (1 - poise) * (1 - recovery_rate) = 0.15 * 0.4 = 0.06 — drifts,
    but barely.
    """
    return PersonalityAnchors(
        baseline_aggression=0.5,
        baseline_looseness=0.4,
        ego=0.5,
        poise=0.85,
        expressiveness=0.5,
        risk_identity=0.5,
        adaptation_bias=0.5,
        baseline_energy=0.5,
        recovery_rate=0.6,
    )


class TestDriftStrengthGate:
    def test_stoic_anchors_skip_drift(self):
        anchors = _stoic_anchors()
        # drift_strength = 0.05 * 0.15 = 0.0075 — below 0.05 floor.
        result = apply_session_drift(anchors, random.Random(42))
        assert result is anchors, (
            "Stoic anchors should return the original instance (no drift). "
            f"poise={anchors.poise}, recovery_rate={anchors.recovery_rate} → "
            f"drift_strength={(1-anchors.poise)*(1-anchors.recovery_rate):.4f}"
        )

    def test_chaotic_anchors_drift(self):
        anchors = _chaotic_anchors()
        result = apply_session_drift(anchors, random.Random(42))
        # At least one anchor should have moved (cumulative miss is
        # astronomically unlikely at chaotic sigma).
        moved = any(
            abs(getattr(result, name) - getattr(anchors, name)) > 1e-9 for name in DRIFT_BASE_SIGMA
        )
        assert moved, "Chaotic anchors should drift on at least one axis"

    def test_just_above_floor_drifts(self):
        """drift_strength clearly above DRIFT_STRENGTH_FLOOR (0.05) drifts.
        Construction: poise=0.85, recovery_rate=0.6 →
        drift_strength = 0.15 * 0.4 = 0.06 — just above the floor."""
        above = _just_above_floor_anchors()
        # Compute the actual drift_strength to make the test's premise
        # robust against float arithmetic and future floor tweaks.
        drift_strength = (1.0 - above.poise) * (1.0 - above.recovery_rate)
        assert drift_strength > DRIFT_STRENGTH_FLOOR, (
            f"Test premise broken: drift_strength={drift_strength} should "
            f"exceed floor={DRIFT_STRENGTH_FLOOR}"
        )
        result = apply_session_drift(above, random.Random(42))
        moved = any(
            abs(getattr(result, name) - getattr(above, name)) > 1e-9 for name in DRIFT_BASE_SIGMA
        )
        assert moved, f"drift_strength={drift_strength:.4f} above floor should drift"


class TestSourceAnchorsHeldConstant:
    """poise + recovery_rate are the source of drift_strength; drifting
    them creates feedback instability. They must never change."""

    def test_poise_unchanged_on_chaotic(self):
        anchors = _chaotic_anchors()
        result = apply_session_drift(anchors, random.Random(42))
        assert result.poise == anchors.poise

    def test_recovery_rate_unchanged_on_chaotic(self):
        anchors = _chaotic_anchors()
        result = apply_session_drift(anchors, random.Random(42))
        assert result.recovery_rate == anchors.recovery_rate

    def test_poise_recovery_not_in_sigma_table(self):
        # Defends against accidentally adding either to DRIFT_BASE_SIGMA.
        assert 'poise' not in DRIFT_BASE_SIGMA
        assert 'recovery_rate' not in DRIFT_BASE_SIGMA


class TestArchetypeStability:
    """Drift must NOT push a character across DeviationProfile thresholds.
    The selection logic is in `select_deviation_profile`; the thresholds
    sit at looseness/aggression == 0.25 (nit) and 0.80 (maniac)."""

    @pytest.mark.parametrize("seed", list(range(50)))
    def test_chaotic_lag_stays_lag(self, seed):
        """LAG sits at aggression=0.8, looseness=0.7 — close to the maniac
        threshold (>0.80 on both). Drift envelope ±0.02 on those two
        anchors must NOT cross the threshold."""
        anchors = _chaotic_anchors()
        original_profile = select_deviation_profile(anchors)
        result = apply_session_drift(anchors, random.Random(seed))
        drifted_profile = select_deviation_profile(result)
        assert drifted_profile is original_profile, (
            f"seed {seed}: LAG drifted into a different profile. "
            f"agg={result.baseline_aggression:.4f}, "
            f"loose={result.baseline_looseness:.4f}"
        )

    @pytest.mark.parametrize("seed", list(range(50)))
    def test_chaotic_tag_stays_tag(self, seed):
        """TAG at aggression=0.7, looseness=0.35 — well clear of both
        thresholds. Drift envelope easily safe."""
        chaotic_tag = PersonalityAnchors(
            baseline_aggression=0.7,
            baseline_looseness=0.35,
            ego=0.5,
            poise=0.4,
            expressiveness=0.6,
            risk_identity=0.5,
            adaptation_bias=0.5,
            baseline_energy=0.5,
            recovery_rate=0.15,
        )
        original = select_deviation_profile(chaotic_tag)
        drifted = select_deviation_profile(apply_session_drift(chaotic_tag, random.Random(seed)))
        assert drifted is original, f"seed {seed}: TAG drifted out"

    @pytest.mark.parametrize("seed", list(range(50)))
    def test_chaotic_nit_stays_nit(self, seed):
        """Nit at 0.15/0.15 has plenty of margin to 0.25, but a chaotic
        Nit would have low poise (Nits have poise=0.9 in the sim's
        archetypes — not chaotic; we construct an artificial one)."""
        chaotic_nit = PersonalityAnchors(
            baseline_aggression=0.15,
            baseline_looseness=0.15,
            ego=0.3,
            poise=0.4,
            expressiveness=0.3,
            risk_identity=0.3,
            adaptation_bias=0.3,
            baseline_energy=0.3,
            recovery_rate=0.15,
        )
        original = select_deviation_profile(chaotic_nit)
        drifted = select_deviation_profile(apply_session_drift(chaotic_nit, random.Random(seed)))
        assert drifted is original, f"seed {seed}: Nit drifted out"


class TestClampAndRange:
    """All drifted anchors must remain in [0, 1]."""

    def test_extreme_anchor_clamps_high(self):
        """Anchor at 0.99 with high sigma must stay <= 1.0 after drift."""
        anchors = PersonalityAnchors(
            baseline_aggression=0.5,
            baseline_looseness=0.5,
            ego=0.5,
            poise=0.1,
            expressiveness=0.99,
            risk_identity=0.5,
            adaptation_bias=0.5,
            baseline_energy=0.99,
            recovery_rate=0.1,
        )
        # drift_strength = 0.9 * 0.9 = 0.81 → very high noise
        # expressiveness sigma = 0.10 * 0.81 = 0.081. Run many samples to
        # ensure the clamp catches the tail.
        for seed in range(200):
            result = apply_session_drift(anchors, random.Random(seed))
            for name in DRIFT_BASE_SIGMA:
                v = getattr(result, name)
                assert 0.0 <= v <= 1.0, f"seed {seed}: {name} = {v} (out of [0,1])"


class TestDeterminism:
    def test_same_seed_same_drift(self):
        anchors = _chaotic_anchors()
        result_a = apply_session_drift(anchors, random.Random(42))
        result_b = apply_session_drift(anchors, random.Random(42))
        for name in DRIFT_BASE_SIGMA:
            assert getattr(result_a, name) == getattr(result_b, name)

    def test_different_seeds_different_drift(self):
        anchors = _chaotic_anchors()
        result_a = apply_session_drift(anchors, random.Random(42))
        result_b = apply_session_drift(anchors, random.Random(43))
        # At least one anchor moves between seeds (probability of all 7
        # collisions at gaussian noise is ~0).
        differ = any(
            getattr(result_a, name) != getattr(result_b, name) for name in DRIFT_BASE_SIGMA
        )
        assert differ, "Different seeds produced identical drift"


class TestDriftMagnitude:
    """Sanity-check the expected drift envelope for the sim archetypes."""

    def test_lag_drift_envelope_within_expected_bounds(self):
        """LAG (poise 0.5, recovery 0.15) → drift_strength = 0.425.
        Across 500 samples, the empirical std on baseline_aggression
        should be approximately sigma_base * drift_strength = 0.0085.
        Allow a generous 2x band to absorb sampling noise."""
        lag = PersonalityAnchors(
            baseline_aggression=0.8,
            baseline_looseness=0.7,
            ego=0.6,
            poise=0.5,
            expressiveness=0.6,
            risk_identity=0.6,
            adaptation_bias=0.5,
            baseline_energy=0.7,
            recovery_rate=0.15,
        )
        agg_samples = []
        for seed in range(500):
            r = apply_session_drift(lag, random.Random(seed))
            agg_samples.append(r.baseline_aggression)
        mean = sum(agg_samples) / len(agg_samples)
        var = sum((x - mean) ** 2 for x in agg_samples) / len(agg_samples)
        std = var**0.5
        expected_sigma = 0.02 * 0.425  # 0.0085
        # 2x band on either side
        assert (
            expected_sigma * 0.5 < std < expected_sigma * 2.0
        ), f"LAG baseline_aggression std = {std:.4f}, expected ~{expected_sigma:.4f}"

    def test_maniac_drift_envelope_wider_than_lag(self):
        """Maniac (poise 0.3, recovery 0.1) → drift_strength = 0.63.
        Should produce wider drift than LAG (drift_strength = 0.425)."""

        def std_of_aggression(anchors):
            samples = [
                apply_session_drift(anchors, random.Random(seed)).baseline_aggression
                for seed in range(500)
            ]
            mean = sum(samples) / len(samples)
            var = sum((x - mean) ** 2 for x in samples) / len(samples)
            return var**0.5

        lag = PersonalityAnchors(
            baseline_aggression=0.8,
            baseline_looseness=0.7,
            ego=0.6,
            poise=0.5,
            expressiveness=0.6,
            risk_identity=0.6,
            adaptation_bias=0.5,
            baseline_energy=0.7,
            recovery_rate=0.15,
        )
        # Maniac uses 0.85/0.85 for the aggression/looseness so that
        # `select_deviation_profile` routes to the maniac profile from
        # the start (anything > 0.80 on both). But for the drift std
        # check, we need numbers that won't bump into the [0,1] clamp.
        # Use a "maniac-like" instance away from the clamp boundary.
        maniac_like = PersonalityAnchors(
            baseline_aggression=0.5,
            baseline_looseness=0.5,
            ego=0.7,
            poise=0.3,
            expressiveness=0.8,
            risk_identity=0.8,
            adaptation_bias=0.3,
            baseline_energy=0.8,
            recovery_rate=0.1,
        )
        assert std_of_aggression(maniac_like) > std_of_aggression(lag)
