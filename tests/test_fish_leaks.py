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
    POT_COMMITTED_THRESHOLD,
    SPITE_RAISE_PROBABILITY,
    FishLeak,
    _strategy_fish,
)


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
