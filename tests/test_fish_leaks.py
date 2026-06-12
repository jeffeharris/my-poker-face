"""Per-leak tests for `_strategy_fish` extensions.

The base fish strategy plays a known calling-station baseline. Each
`FishLeak` value layers one specific deviation. These tests pin each
leak's trigger + non-trigger behavior so future edits can't quietly
break the "designated exploit" property the tourist factory relies on.

Spec: docs/plans/CASH_MODE_EPHEMERAL_TOURISTS.md
"""

from __future__ import annotations

import random

import pytest

from poker.rule_strategies import (
    FISH_BET_BLUFF,
    FISH_BET_MEDIUM,
    FISH_BET_NUTS,
    FISH_BET_STRONG,
    FISH_POP_MEDIUM,
    FISH_POP_NUTS,
    FISH_POP_STRONG,
    POT_COMMITTED_THRESHOLD,
    SPITE_RAISE_PROBABILITY,
    FishLeak,
    _strategy_fish,
)


class _ForceRoll:
    """rng stub whose .random() is always below any leak probability."""

    def random(self):
        return 0.0


class _NoRoll:
    """rng stub whose .random() is always above any leak probability."""

    def random(self):
        return 0.99


def _fish_context(**overrides):
    """Baseline facing-a-large-bet context that the fish would FOLD without a leak.

    Hand is 87o (off-tier), zero equity, no draws, no face cards, no pair.
    cost_in_bb = 100 / 2 = 50 → triggers the large-bet branch. Without
    any leak set, base behavior is fold.
    """
    ctx = {
        'canonical_hand': '87o',
        'hole_cards': ['8h', '7d'],
        'equity': 0.20,
        'cost_to_call': 100,
        'big_blind': 2,
        'min_raise': 200,
        'valid_actions': ['fold', 'call', 'raise'],
        'is_pair': False,
        'is_suited': False,
        'has_flush_draw': False,
        'has_oesd': False,
        'has_face_card': False,
        'has_top_pair_or_better': False,
        'committed_fraction_of_stack': 0.0,
        'is_losing_at_table': False,
        'street': 'turn',
    }
    ctx.update(overrides)
    return ctx


def _free_context(**overrides):
    """Checked-to (free to act) context with pot/sizing fields for value-bet tests.

    Pot is large relative to min_raise so pot-fraction sizing isn't
    swallowed by the min-raise floor — lets us assert the "bigger hand →
    bigger bet" tell. `made_tier` defaults to air; equity is low so the
    equity fallback doesn't fire unless a test sets it.
    """
    ctx = _fish_context(
        cost_to_call=0,
        valid_actions=['check', 'raise'],
        equity=0.30,
        pot_total=2000,
        min_raise=200,
        max_raise=10000,
        made_tier='air',
    )
    ctx.update(overrides)
    return ctx


def _expected_bet(ctx, fraction):
    """Mirror _fish_bet sizing so assertions track the constants, not magic numbers."""
    return max(ctx['min_raise'], min(int(ctx['pot_total'] * fraction), ctx['max_raise']))


class TestBaselineFishBehavior:
    """Sanity: without a leak, the baseline picks fold/call/check correctly."""

    def test_no_leak_folds_air_to_large_bet(self):
        ctx = _fish_context()  # 87o, 50bb call, no leak
        assert _strategy_fish(ctx)['action'] == 'fold'

    def test_no_leak_checks_when_free(self):
        ctx = _fish_context(cost_to_call=0, valid_actions=['check', 'raise'])
        assert _strategy_fish(ctx)['action'] == 'check'

    def test_no_leak_calls_small_bet(self):
        ctx = _fish_context(cost_to_call=4)  # 2bb
        assert _strategy_fish(ctx)['action'] == 'call'


class TestCallsDownTopPair:
    leak = FishLeak.CALLS_DOWN_TOP_PAIR

    def test_calls_large_bet_with_top_pair(self):
        ctx = _fish_context(
            fish_leak=self.leak,
            has_top_pair_or_better=True,
        )
        assert _strategy_fish(ctx)['action'] == 'call'

    def test_base_behavior_without_top_pair(self):
        # Leak set but trigger condition fails → base behavior folds
        ctx = _fish_context(fish_leak=self.leak, has_top_pair_or_better=False)
        assert _strategy_fish(ctx)['action'] == 'fold'


class TestChasesAnyDraw:
    leak = FishLeak.CHASES_ANY_DRAW

    def test_calls_medium_bet_with_flush_draw(self):
        ctx = _fish_context(
            fish_leak=self.leak,
            cost_to_call=12,  # 6bb → medium
            has_flush_draw=True,
        )
        assert _strategy_fish(ctx)['action'] == 'call'

    def test_calls_medium_bet_with_oesd(self):
        ctx = _fish_context(
            fish_leak=self.leak,
            cost_to_call=12,
            has_oesd=True,
        )
        assert _strategy_fish(ctx)['action'] == 'call'

    def test_base_behavior_without_draw(self):
        ctx = _fish_context(
            fish_leak=self.leak,
            cost_to_call=12,
            has_flush_draw=False,
            has_oesd=False,
        )
        # 87o, no draw, no pair, equity 0.20 → base folds the medium bet
        assert _strategy_fish(ctx)['action'] == 'fold'


class TestDoesntBelieveBigBets:
    leak = FishLeak.DOESNT_BELIEVE_BIG_BETS

    def test_calls_large_bet_with_pair(self):
        ctx = _fish_context(
            fish_leak=self.leak,
            is_pair=True,
        )
        assert _strategy_fish(ctx)['action'] == 'call'

    def test_calls_large_bet_with_marginal_equity(self):
        ctx = _fish_context(
            fish_leak=self.leak,
            equity=0.42,
        )
        assert _strategy_fish(ctx)['action'] == 'call'

    def test_folds_very_bad_hand(self):
        # No leak coverage at all — true air, low equity, no pair
        ctx = _fish_context(
            fish_leak=self.leak,
            canonical_hand='72o',
            equity=0.10,
            is_pair=False,
        )
        assert _strategy_fish(ctx)['action'] == 'fold'


class TestLimpsEveryHand:
    leak = FishLeak.LIMPS_EVERY_HAND

    def test_preflop_facing_bet_calls(self):
        ctx = _fish_context(
            fish_leak=self.leak,
            street='preflop',
            cost_to_call=100,
        )
        assert _strategy_fish(ctx)['action'] == 'call'

    def test_preflop_free_checks(self):
        ctx = _fish_context(
            fish_leak=self.leak,
            street='preflop',
            cost_to_call=0,
            valid_actions=['check', 'raise'],
            canonical_hand='AA',  # would normally raise
            equity=0.85,
        )
        # Leak suppresses the monster preflop raise (true passive limper)
        assert _strategy_fish(ctx)['action'] == 'check'

    def test_postflop_unaffected(self):
        # Leak only applies preflop; postflop, base behavior runs
        ctx = _fish_context(
            fish_leak=self.leak,
            street='turn',  # default in baseline already
        )
        assert _strategy_fish(ctx)['action'] == 'fold'


class TestLimpFold:
    leak = FishLeak.LIMP_FOLD

    def test_limp_decision_limps_top_45(self):
        # Facing only the BB (cost_in_bb = 1.0): a top-45% hand limps in.
        ctx = _fish_context(
            fish_leak=self.leak,
            street='preflop',
            cost_to_call=2,
            big_blind=2,
            canonical_hand='KJs',
            equity=0.55,
        )
        assert _strategy_fish(ctx)['action'] == 'call'

    def test_limp_decision_folds_trash(self):
        ctx = _fish_context(
            fish_leak=self.leak,
            street='preflop',
            cost_to_call=2,
            big_blind=2,
            canonical_hand='72o',
            equity=0.20,
        )
        assert _strategy_fish(ctx)['action'] == 'fold'

    def test_facing_jam_folds_bottom_of_range(self):
        # KJs would limp, but folds the bottom of its range to a 9bb jam
        # (not top-10) — the fold equity the never-folding limper lacks.
        ctx = _fish_context(
            fish_leak=self.leak,
            street='preflop',
            cost_to_call=900,
            big_blind=100,
            canonical_hand='KJs',
            equity=0.55,
        )
        assert _strategy_fish(ctx)['action'] == 'fold'

    def test_facing_jam_calls_premium(self):
        ctx = _fish_context(
            fish_leak=self.leak,
            street='preflop',
            cost_to_call=900,
            big_blind=100,
            canonical_hand='AA',
            equity=0.85,
        )
        assert _strategy_fish(ctx)['action'] == 'call'

    def test_postflop_unaffected(self):
        # Preflop-only leak; postflop the base fold runs (87o air vs a large bet).
        ctx = _fish_context(fish_leak=self.leak, street='turn')
        assert _strategy_fish(ctx)['action'] == 'fold'


class TestPotCommittedEarly:
    leak = FishLeak.POT_COMMITTED_EARLY

    def test_calls_when_above_threshold(self):
        ctx = _fish_context(
            fish_leak=self.leak,
            committed_fraction_of_stack=POT_COMMITTED_THRESHOLD + 0.01,
        )
        assert _strategy_fish(ctx)['action'] == 'call'

    def test_base_behavior_below_threshold(self):
        ctx = _fish_context(
            fish_leak=self.leak,
            committed_fraction_of_stack=POT_COMMITTED_THRESHOLD - 0.01,
        )
        assert _strategy_fish(ctx)['action'] == 'fold'

    def test_does_not_force_call_when_free(self):
        """Threshold check requires cost_to_call > 0 — must not turn a check into a call."""
        ctx = _fish_context(
            fish_leak=self.leak,
            committed_fraction_of_stack=0.50,
            cost_to_call=0,
            valid_actions=['check', 'raise'],
        )
        assert _strategy_fish(ctx)['action'] == 'check'


class TestOvervaluesFaceCards:
    leak = FishLeak.OVERVALUES_FACE_CARDS

    def test_calls_medium_bet_with_face_card(self):
        ctx = _fish_context(
            fish_leak=self.leak,
            cost_to_call=12,  # 6bb → medium
            has_face_card=True,
        )
        assert _strategy_fish(ctx)['action'] == 'call'

    def test_base_behavior_without_face(self):
        ctx = _fish_context(
            fish_leak=self.leak,
            cost_to_call=12,
            has_face_card=False,
        )
        assert _strategy_fish(ctx)['action'] == 'fold'


class TestCallsRiverLight:
    leak = FishLeak.CALLS_RIVER_LIGHT

    def test_calls_river_with_marginal_equity(self):
        ctx = _fish_context(
            fish_leak=self.leak,
            street='river',
            equity=0.42,
        )
        assert _strategy_fish(ctx)['action'] == 'call'

    def test_does_not_fire_on_turn(self):
        # Leak is river-only; turn still folds the same equity
        ctx = _fish_context(
            fish_leak=self.leak,
            street='turn',
            equity=0.42,
        )
        assert _strategy_fish(ctx)['action'] == 'fold'

    def test_still_folds_very_bad_on_river(self):
        ctx = _fish_context(
            fish_leak=self.leak,
            street='river',
            equity=0.20,
            canonical_hand='72o',
        )
        assert _strategy_fish(ctx)['action'] == 'fold'


class TestSpiteRaisesWhenLosing:
    leak = FishLeak.SPITE_RAISES_WHEN_LOSING

    def test_raises_when_roll_succeeds(self):
        rng = random.Random()

        # Deterministically force the spite trigger by seeding so .random() < threshold
        # We pin via a stub instead — cleaner than picking a seed
        class _ForceRoll:
            def random(self_inner):
                return 0.0  # always below SPITE_RAISE_PROBABILITY

        ctx = _fish_context(
            fish_leak=self.leak,
            is_losing_at_table=True,
            _rng=_ForceRoll(),
        )
        decision = _strategy_fish(ctx)
        assert decision['action'] == 'raise'
        assert decision['raise_to'] == ctx['min_raise']

    def test_base_behavior_when_roll_fails(self):
        class _NoRoll:
            def random(self_inner):
                return 0.99  # always above threshold

        ctx = _fish_context(
            fish_leak=self.leak,
            is_losing_at_table=True,
            _rng=_NoRoll(),
        )
        assert _strategy_fish(ctx)['action'] == 'fold'

    def test_no_spite_when_winning(self):
        class _ForceRoll:
            def random(self_inner):
                return 0.0

        ctx = _fish_context(
            fish_leak=self.leak,
            is_losing_at_table=False,  # not losing
            _rng=_ForceRoll(),
        )
        assert _strategy_fish(ctx)['action'] == 'fold'


class TestLeakIndependence:
    """An unknown / unset leak must not change base behavior."""

    def test_none_leak_baseline(self):
        ctx = _fish_context(fish_leak=None)
        assert _strategy_fish(ctx)['action'] == 'fold'

    def test_unknown_string_leak_baseline(self):
        # Future-proofing: a leak string the strategy doesn't recognize
        # must silently no-op (factory and strategy can rev independently)
        ctx = _fish_context(fish_leak='made_up_future_leak')
        assert _strategy_fish(ctx)['action'] == 'fold'

    def test_leak_does_not_break_monster_raise(self):
        """The free-to-act monster raise still fires for non-leak-affected hands."""
        ctx = _fish_context(
            fish_leak=FishLeak.CALLS_DOWN_TOP_PAIR,  # leak unrelated to free-to-act
            cost_to_call=0,
            valid_actions=['check', 'raise'],
            canonical_hand='AA',
            equity=0.85,
        )
        assert _strategy_fish(ctx)['action'] == 'raise'


class TestBaselineValueBetting:
    """Checked-to, no leak: honest value betting with size ∝ strength."""

    def test_bets_nuts_when_checked_to(self):
        ctx = _free_context(made_tier='nuts')
        decision = _strategy_fish(ctx)
        assert decision['action'] == 'raise'
        assert decision['raise_to'] == _expected_bet(ctx, FISH_BET_NUTS)

    def test_strong_bets_smaller_than_nuts(self):
        """The tell: a stronger made hand bets bigger."""
        nuts = _strategy_fish(_free_context(made_tier='nuts'))['raise_to']
        strong = _strategy_fish(_free_context(made_tier='strong_made'))['raise_to']
        assert strong == _expected_bet(_free_context(), FISH_BET_STRONG)
        assert strong < nuts

    def test_checks_top_pair_at_baseline(self):
        # Baseline only value-bets strong_made+. Top pair (medium_made) checks.
        ctx = _free_context(made_tier='medium_made')
        assert _strategy_fish(ctx)['action'] == 'check'

    def test_checks_air(self):
        ctx = _free_context(made_tier='air', equity=0.30)
        assert _strategy_fish(ctx)['action'] == 'check'

    def test_equity_fallback_bets_without_made_tier(self):
        # No made_tier signal (preflop / fixture) but high equity → value bet.
        ctx = _free_context(made_tier='air', equity=0.85)
        decision = _strategy_fish(ctx)
        assert decision['action'] == 'raise'
        assert decision['raise_to'] == _expected_bet(ctx, FISH_BET_NUTS)

    def test_checks_when_raise_unavailable(self):
        ctx = _free_context(made_tier='nuts', valid_actions=['check'])
        assert _strategy_fish(ctx)['action'] == 'check'


class TestBetsStrongTransparently:
    leak = FishLeak.BETS_STRONG_TRANSPARENTLY

    def test_bets_top_pair_when_checked_to(self):
        # Widens the value range down to top pair (baseline would check).
        ctx = _free_context(fish_leak=self.leak, made_tier='medium_made')
        decision = _strategy_fish(ctx)
        assert decision['action'] == 'raise'
        assert decision['raise_to'] == _expected_bet(ctx, FISH_BET_MEDIUM)

    def test_value_raises_top_pair_facing_bet(self):
        ctx = _fish_context(
            fish_leak=self.leak,
            has_top_pair_or_better=True,
            made_tier='medium_made',
            pot_total=2000,
            max_raise=10000,
        )
        decision = _strategy_fish(ctx)
        assert decision['action'] == 'raise'
        assert decision['raise_to'] == _expected_bet(ctx, FISH_POP_MEDIUM)

    def test_pops_bigger_with_monster(self):
        ctx = _fish_context(
            fish_leak=self.leak,
            has_top_pair_or_better=True,
            made_tier='nuts',
            pot_total=2000,
            max_raise=10000,
        )
        decision = _strategy_fish(ctx)
        assert decision['raise_to'] == _expected_bet(ctx, FISH_POP_NUTS)
        assert decision['raise_to'] > _expected_bet(ctx, FISH_POP_MEDIUM)

    def test_no_pop_without_made_hand_folds_air(self):
        # Trigger fails (no top pair) → falls through to calling-station ladder.
        ctx = _fish_context(
            fish_leak=self.leak,
            has_top_pair_or_better=False,
            made_tier='air',
        )
        assert _strategy_fish(ctx)['action'] == 'fold'


class TestSpewsBluffs:
    leak = FishLeak.SPEWS_BLUFFS

    def test_bluffs_air_when_roll_succeeds(self):
        ctx = _free_context(fish_leak=self.leak, made_tier='air', equity=0.20, _rng=_ForceRoll())
        decision = _strategy_fish(ctx)
        assert decision['action'] == 'raise'
        assert decision['raise_to'] == _expected_bet(ctx, FISH_BET_BLUFF)

    def test_checks_air_when_roll_fails(self):
        ctx = _free_context(fish_leak=self.leak, made_tier='air', equity=0.20, _rng=_NoRoll())
        assert _strategy_fish(ctx)['action'] == 'check'

    def test_value_bets_strong_even_when_roll_fails(self):
        # Value branch runs before the bluff roll — a real hand still bets.
        ctx = _free_context(fish_leak=self.leak, made_tier='nuts', _rng=_NoRoll())
        decision = _strategy_fish(ctx)
        assert decision['action'] == 'raise'
        assert decision['raise_to'] == _expected_bet(ctx, FISH_BET_NUTS)

    def test_facing_bet_is_still_calling_station(self):
        # Spew is a checked-to behavior; facing a large bet with air → fold.
        ctx = _fish_context(fish_leak=self.leak, _rng=_ForceRoll())
        assert _strategy_fish(ctx)['action'] == 'fold'


class TestStickyThenPops:
    leak = FishLeak.STICKY_THEN_POPS

    def test_pops_nuts_facing_bet(self):
        ctx = _fish_context(
            fish_leak=self.leak,
            made_tier='nuts',
            pot_total=2000,
            max_raise=10000,
        )
        decision = _strategy_fish(ctx)
        assert decision['action'] == 'raise'
        assert decision['raise_to'] == _expected_bet(ctx, FISH_POP_NUTS)

    def test_pops_strong_made_smaller_than_nuts(self):
        ctx = _fish_context(
            fish_leak=self.leak,
            made_tier='strong_made',
            pot_total=2000,
            max_raise=10000,
        )
        decision = _strategy_fish(ctx)
        assert decision['action'] == 'raise'
        assert decision['raise_to'] == _expected_bet(ctx, FISH_POP_STRONG)

    def test_top_pair_does_not_pop_reverts_to_calling(self):
        # medium_made (top pair) is NOT a monster → no pop; reverts to the
        # calling-station ladder, which calls a large bet at equity >= 0.55.
        ctx = _fish_context(
            fish_leak=self.leak,
            made_tier='medium_made',
            has_top_pair_or_better=True,
            equity=0.60,
        )
        assert _strategy_fish(ctx)['action'] == 'call'

    def test_air_folds_facing_bet(self):
        ctx = _fish_context(fish_leak=self.leak, made_tier='air')
        assert _strategy_fish(ctx)['action'] == 'fold'

    def test_free_with_monster_uses_baseline_value_bet(self):
        # When checked to, sticky behaves like baseline (value-bets monsters).
        ctx = _free_context(fish_leak=self.leak, made_tier='nuts')
        decision = _strategy_fish(ctx)
        assert decision['action'] == 'raise'
        assert decision['raise_to'] == _expected_bet(ctx, FISH_BET_NUTS)
