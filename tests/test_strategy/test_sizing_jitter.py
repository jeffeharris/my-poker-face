"""Tests for bet-sizing jitter in the action mapper.

Competitive feel improvement: when the controller passes a non-zero
sizing_jitter to resolve_preflop_sizing / resolve_postflop_sizing, the
returned raise-to amount is sampled uniformly from a band around the
table's target instead of being deterministic. Zero EV cost (symmetric
band) but breaks sizing tells.
"""

from __future__ import annotations

import random
from types import SimpleNamespace
from typing import List

import pytest

from poker.strategy.action_mapper import (
    _compute_raise_to,
    resolve_postflop_sizing,
    resolve_preflop_sizing,
)


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

    def test_with_jitter_amount_varies_within_band(self):
        rng = random.Random(7)
        state = _make_preflop_game_state()
        amounts = set()
        for _ in range(30):
            _, amount = resolve_preflop_sizing(
                'raise_3bb',
                state,
                0,
                rng=rng,
                sizing_jitter=0.15,
            )
            amounts.add(amount)
            # 3 BB = 300; ±15% band: [255, 345]; min_raise=200
            assert 255 <= amount <= 345
        assert len(amounts) > 5, "Expected meaningful variance"

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

    def test_jitter_bet_67_varies_within_band(self):
        rng = random.Random(11)
        state = _make_postflop_game_state(pot_total=1000)
        amounts: List[int] = []
        for _ in range(40):
            _, amount = resolve_postflop_sizing(
                'bet_67',
                state,
                0,
                rng=rng,
                sizing_jitter=0.15,
            )
            amounts.append(amount)
        # 670 ± 15% = [569, 770]
        for a in amounts:
            assert 569 <= a <= 770
        assert len(set(amounts)) > 5

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
