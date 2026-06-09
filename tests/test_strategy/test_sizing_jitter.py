"""Tests for bet-sizing jitter in the action mapper.

Competitive feel improvement: when the controller passes a non-zero
sizing_jitter to resolve_preflop_sizing / resolve_postflop_sizing, the
returned raise-to amount is sampled uniformly from a band around the
table's target instead of being deterministic. Zero EV cost (symmetric
band) but breaks sizing tells. The jittered amount is then snapped to a
natural chip increment (round_to_human_bet) so it reads like a person bet
(300, not 287) — variety on round numbers, not a bot tell. Both are
live-only (jitter>0); the deterministic sim/Baseline path stays exact.
"""

from __future__ import annotations

import random
from types import SimpleNamespace
from typing import List

import pytest

from poker.strategy.action_mapper import (
    _compute_raise_to,
    _nice_step,
    resolve_postflop_sizing,
    resolve_preflop_sizing,
    round_to_human_bet,
)


class TestHumanRounding:
    """The live (jittered) amount is snapped to a natural chip increment so it
    reads like a person bet (300, not 287), not a bot tell."""

    def test_rounds_to_clean_amounts_at_bb_100(self):
        assert round_to_human_bet(287, 100) == 275  # open: BB/4 = 25 step
        assert round_to_human_bet(312, 100) == 300
        assert round_to_human_bet(743, 100) == 750  # 3-bet: BB/2 = 50 step
        assert round_to_human_bet(1837, 100) == 1800  # 4-bet: BB step = 100

    def test_clean_steps_across_blind_levels(self):
        # BB/4 = 12.5 must snap to a CLEAN denomination (10), not 12.
        assert round_to_human_bet(143, 50) == 140
        assert round_to_human_bet(372, 50) == 375
        assert round_to_human_bet(563, 200) == 550
        # micro stakes: step floors at 1 chip
        assert round_to_human_bet(7, 2) == 7

    def test_nice_step_ladder(self):
        assert _nice_step(12.5) == 10
        assert _nice_step(25) == 25
        assert _nice_step(50) == 50
        assert _nice_step(0.5) == 1

    def test_noop_without_big_blind(self):
        # big_blind<=0 → plain integer rounding (the deterministic path stays exact)
        assert round_to_human_bet(287.0, 0) == 287

    def test_deterministic_path_is_exact_unchanged(self):
        # No rng/jitter → no rounding even though the code path exists. The
        # GTO/sim reference sizing must stay byte-exact.
        assert _compute_raise_to(2.5, 100, 50, 10000, big_blind=100) == 250
        assert _compute_raise_to(3.0, 250, 50, 10000, big_blind=100) == 750


class TestComputeRaiseToJitter:
    def test_no_jitter_preserves_exact_value(self):
        # multiplier=2.5, base=100, no jitter → exactly 250
        result = _compute_raise_to(
            multiplier=2.5,
            base_amount=100,
            min_raise=50,
            max_raise=10000,
        )
        assert result == 250

    def test_jitter_with_no_rng_is_noop(self):
        """Even if jitter > 0, if no RNG is provided the path is
        deterministic. Lets callers opt in by passing rng *and* jitter."""
        result = _compute_raise_to(
            multiplier=2.5,
            base_amount=100,
            min_raise=50,
            max_raise=10000,
            jitter=0.15,
        )
        assert result == 250

    def test_jitter_with_rng_produces_values_in_band(self):
        rng = random.Random(42)
        # Sample 50 values with 15% jitter around target=250
        samples = [
            _compute_raise_to(
                multiplier=2.5,
                base_amount=100,
                min_raise=50,
                max_raise=10000,
                rng=rng,
                jitter=0.15,
            )
            for _ in range(50)
        ]
        # All should be within [250*0.85, 250*1.15] = [212.5, 287.5]
        for s in samples:
            assert 212 <= s <= 288, f"Value {s} out of jitter band"
        # And they should NOT all be the same value (would defeat the purpose)
        assert len(set(samples)) > 5

    def test_jitter_clamped_to_min_raise(self):
        """If the jitter samples below the legal minimum, clamp to it."""
        rng = random.Random(42)
        # target=200, min_raise=180, jitter=0.5 → low end is 100
        result = _compute_raise_to(
            multiplier=2.0,
            base_amount=100,
            min_raise=180,
            max_raise=10000,
            rng=rng,
            jitter=0.5,
        )
        assert result >= 180

    def test_jitter_clamped_to_max_raise(self):
        """If the jitter samples above the player's stack, clamp."""
        rng = random.Random(42)
        result = _compute_raise_to(
            multiplier=10.0,
            base_amount=100,
            min_raise=50,
            max_raise=500,
            rng=rng,
            jitter=0.5,
        )
        assert result <= 500

    def test_jitter_is_reproducible_with_seed(self):
        rng_a = random.Random(99)
        rng_b = random.Random(99)
        samples_a = [
            _compute_raise_to(2.5, 100, 50, 10000, rng=rng_a, jitter=0.15) for _ in range(10)
        ]
        samples_b = [
            _compute_raise_to(2.5, 100, 50, 10000, rng=rng_b, jitter=0.15) for _ in range(10)
        ]
        assert samples_a == samples_b


# ── Integration: through resolve_preflop_sizing ────────────────────────


def _make_preflop_game_state(player_stack=10000, highest_bet=200, big_blind=100):
    """Minimal SimpleNamespace state that the resolver needs."""
    player = SimpleNamespace(stack=player_stack, bet=0)
    return SimpleNamespace(
        players=[player],
        current_ante=big_blind,
        highest_bet=highest_bet,
        min_raise_amount=big_blind,
        pot={'total': 300},
    )


class TestResolvePreflopJitter:
    def test_no_jitter_path_unchanged(self):
        state = _make_preflop_game_state()
        action, amount = resolve_preflop_sizing('raise_3bb', state, 0)
        # 3 BB = 300 chips
        assert action == 'raise'
        assert amount == 300

    def test_with_jitter_amount_varies_and_rounds(self):
        rng = random.Random(7)
        state = _make_preflop_game_state()  # BB=100, min_raise=300
        amounts = set()
        for _ in range(60):
            _, amount = resolve_preflop_sizing(
                'raise_3bb',
                state,
                0,
                rng=rng,
                sizing_jitter=0.15,
            )
            amounts.add(amount)
            # 3 BB = 300; ±15% band [255, 345] → snapped to 25-chip steps and
            # clamped up to min_raise=300 → {300, 325, 350}. Live amounts are
            # human-round multiples of the BB/4 step (no 287-style tells).
            assert amount % 25 == 0, f"{amount} not a round 25-chip increment"
            assert 300 <= amount <= 350
        assert len(amounts) > 1, "Expected size variety (just on round numbers)"

    def test_fold_and_call_unaffected_by_jitter(self):
        state = _make_preflop_game_state()
        rng = random.Random(7)
        assert resolve_preflop_sizing('fold', state, 0, rng=rng, sizing_jitter=0.15) == ('fold', 0)
        assert resolve_preflop_sizing('call', state, 0, rng=rng, sizing_jitter=0.15) == ('call', 0)
        assert resolve_preflop_sizing('check', state, 0, rng=rng, sizing_jitter=0.15) == (
            'check',
            0,
        )


def _make_postflop_game_state(player_stack=10000, highest_bet=0, pot_total=1000, player_bet=0):
    player = SimpleNamespace(stack=player_stack, bet=player_bet)
    return SimpleNamespace(
        players=[player],
        current_ante=100,
        highest_bet=highest_bet,
        min_raise_amount=100,
        pot={'total': pot_total},
    )


class TestResolvePostflopJitter:
    def test_no_jitter_bet_67_is_exact(self):
        state = _make_postflop_game_state(pot_total=1000)
        action, amount = resolve_postflop_sizing('bet_67', state, 0)
        # 67% of 1000 = 670
        assert action == 'raise'
        assert amount == 670

    def test_jitter_bet_67_varies_and_rounds(self):
        rng = random.Random(11)
        state = _make_postflop_game_state(pot_total=1000)  # BB=100
        amounts: List[int] = []
        for _ in range(60):
            _, amount = resolve_postflop_sizing(
                'bet_67',
                state,
                0,
                rng=rng,
                sizing_jitter=0.15,
            )
            amounts.append(amount)
        # 67% of 1000 = 670; ±15% band [569, 770] → snapped to 50-chip steps
        # (the ~half-BB tier for a 5–15 BB bet): {550 … 800}. Human-round, varied.
        for a in amounts:
            assert a % 50 == 0, f"{a} not a round 50-chip increment"
            assert 500 <= a <= 850
        assert len(set(amounts)) > 1

    def test_jitter_raise_postflop_clamps_to_legal_min(self):
        """If jitter would sample under min_raise, the clamp catches it."""
        rng = random.Random(11)
        state = _make_postflop_game_state(
            pot_total=200,
            highest_bet=100,
            player_bet=0,
        )
        for _ in range(20):
            _, amount = resolve_postflop_sizing(
                'raise_50',
                state,
                0,
                rng=rng,
                sizing_jitter=0.5,
            )
            # Legal min_raise = highest_bet + min_raise_amount = 200
            assert amount >= 200
