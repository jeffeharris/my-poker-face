"""Tests for `cash_mode.aspiration` trigger helpers.

Pure-function unit tests pinning the calibration of each multiplier
plus the compound probability. The integration layer (Commit 4) and
cooldown layer (Commit 3) have their own test files.

Spec: docs/plans/CASH_MODE_AI_ASPIRATION_ASK.md Commit 2.
"""

from __future__ import annotations

import pytest

from cash_mode.aspiration import (
    DEFAULT_BASE_RATE,
    MAX_ASPIRATION_PROB,
    aspiration_bias_factor,
    compute_aspiration_probability,
    wealth_gap_factor,
)

# --- aspiration_bias_factor --------------------------------------------------


class TestAspirationBiasFactor:
    def test_zero_bias_zero_factor(self):
        # The locked decision: willing=False ⟹ aspiration_bias=0 ⟹
        # no asks. The factor short-circuit lives here.
        assert aspiration_bias_factor(0.0) == 0.0

    def test_baseline_yields_one(self):
        # Default aspiration_bias=0.5 → factor 1.0 (the baseline).
        assert aspiration_bias_factor(0.5) == 1.0

    def test_max_bias_yields_two(self):
        # Eager climber doubles the trigger rate.
        assert aspiration_bias_factor(1.0) == 2.0

    def test_clamps_above_one(self):
        # Defensive: malformed values past the loader's clamp don't
        # explode the formula.
        assert aspiration_bias_factor(1.5) == 2.0

    def test_clamps_below_zero(self):
        assert aspiration_bias_factor(-0.3) == 0.0


# --- wealth_gap_factor -------------------------------------------------------


class TestWealthGapFactor:
    """Pins the calibration against SAFE_BUY_IN_COUNT=5. All test
    inputs use target_min_buy_in=10_000, so the implicit target is
    50_000 chips (5 × 10k buy-ins of cushion at the next tier).
    """

    def test_zero_at_ratio_zero(self):
        # Empty bankroll → no aspiration. Can't commit to anything.
        assert wealth_gap_factor(0, 10_000) == 0.0

    def test_peak_at_half_of_safe_roll(self):
        # 25_000 / (5 × 10_000) = 0.5 → peak.
        assert wealth_gap_factor(25_000, 10_000) == 2.0

    def test_zero_at_or_above_safe_roll(self):
        # 50_000 / 50_000 = 1.0 — well-rolled, no need to ask.
        assert wealth_gap_factor(50_000, 10_000) == 0.0
        assert wealth_gap_factor(80_000, 10_000) == 0.0

    def test_one_buy_in_below_safe_roll_still_in_band(self):
        # 10_000 / 50_000 = 0.2 → |0.2 - 0.5| × 4 = 1.2 → factor = 0.
        # An AI with ONE buy-in of next-tier is too thin to commit.
        assert wealth_gap_factor(10_000, 10_000) == 0.0

    def test_three_buy_ins_meaningful_contribution(self):
        # 30_000 / 50_000 = 0.6 → |0.6 - 0.5| × 4 = 0.4, height = 0.6
        # → factor = 1.2. Three buy-ins is in the "leverage helps" zone.
        factor = wealth_gap_factor(30_000, 10_000)
        assert factor == pytest.approx(1.2, abs=0.01)

    def test_symmetry_around_peak(self):
        # 15_000 (ratio 0.3) and 35_000 (ratio 0.7) give equal factors.
        below = wealth_gap_factor(15_000, 10_000)
        above = wealth_gap_factor(35_000, 10_000)
        assert below == pytest.approx(above)

    def test_handles_zero_target_buy_in(self):
        # Degenerate input — must not divide by zero.
        assert wealth_gap_factor(5_000, 0) == 0.0

    def test_handles_negative_bankroll(self):
        # Defensive: negative bankroll shouldn't be possible but
        # the formula handles it gracefully.
        assert wealth_gap_factor(-100, 10_000) == 0.0


# --- compute_aspiration_probability ------------------------------------------


class TestComputeAspirationProbability:
    def test_zero_bias_short_circuits(self):
        # The most important invariant: willing=False (bias=0) AIs
        # NEVER fire the trigger, regardless of other inputs.
        prob = compute_aspiration_probability(
            aspiration_bias=0.0,
            bankroll=5_000,
            target_min_buy_in=10_000,
        )
        assert prob == 0.0

    def test_zero_wealth_gap_short_circuits(self):
        # Even an eager climber, when already safe-rolled at the next
        # tier, doesn't ask — they just self-fund via normal stake_up.
        prob = compute_aspiration_probability(
            aspiration_bias=1.0,
            bankroll=60_000,  # > 5 × 10_000 (safe-rolled at next tier)
            target_min_buy_in=10_000,
        )
        assert prob == 0.0

    def test_baseline_input_matches_floor(self):
        # bias=0.5 (factor 1.0), bankroll=25_000 (peak wealth_gap=2.0)
        # → 2× base_rate.
        prob = compute_aspiration_probability(
            aspiration_bias=0.5,
            bankroll=25_000,
            target_min_buy_in=10_000,
        )
        assert prob == pytest.approx(2.0 * DEFAULT_BASE_RATE)

    def test_napoleon_class_peak(self):
        # Aspiration 0.876 × bias_factor 1.752, wealth_gap 2.0 (peak),
        # base 0.002 → ~0.007 per tick.
        prob = compute_aspiration_probability(
            aspiration_bias=0.876,
            bankroll=25_000,
            target_min_buy_in=10_000,
        )
        assert prob == pytest.approx(0.002 * 1.752 * 2.0, abs=1e-5)

    def test_max_clamp(self):
        # Pushing all inputs to their max + bumping base_rate by 50×
        # would exceed the ceiling if uncapped — must clamp.
        prob = compute_aspiration_probability(
            aspiration_bias=1.0,
            bankroll=25_000,
            target_min_buy_in=10_000,
            base_rate=0.10,  # 50× the default
        )
        assert prob == MAX_ASPIRATION_PROB

    def test_degenerate_inputs_yield_zero(self):
        # No target tier → no probability.
        assert (
            compute_aspiration_probability(
                aspiration_bias=1.0,
                bankroll=25_000,
                target_min_buy_in=0,
            )
            == 0.0
        )
        # Negative bankroll → no probability.
        assert (
            compute_aspiration_probability(
                aspiration_bias=1.0,
                bankroll=-5_000,
                target_min_buy_in=10_000,
            )
            == 0.0
        )

    def test_lincoln_class_low_but_nonzero(self):
        # Lincoln-class (bias 0.37) at the wealth-gap peak still gets
        # *some* probability — they're a grinder, not a refuser.
        prob = compute_aspiration_probability(
            aspiration_bias=0.37,
            bankroll=25_000,
            target_min_buy_in=10_000,
        )
        assert prob > 0
        # But meaningfully below Napoleon's rate.
        napoleon = compute_aspiration_probability(
            aspiration_bias=0.876,
            bankroll=25_000,
            target_min_buy_in=10_000,
        )
        assert prob < 0.5 * napoleon
