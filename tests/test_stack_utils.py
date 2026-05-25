"""Unit tests for poker/stack_utils.py."""

from types import SimpleNamespace

import pytest

from poker.stack_utils import (
    ANTE_FALLBACK_BB,
    big_blind_of,
    effective_stack_bb,
    effective_stack_chips,
    spr,
)


def _player(name: str, stack: int, is_folded: bool = False):
    return SimpleNamespace(name=name, stack=stack, is_folded=is_folded)


def _state(players, current_ante: int = 50, pot_total: int = 0):
    return SimpleNamespace(
        players=tuple(players),
        current_ante=current_ante,
        pot={'total': pot_total},
    )


class TestBigBlindOf:
    def test_reads_current_ante(self):
        state = _state([_player("a", 1000)], current_ante=50)
        assert big_blind_of(state) == 50

    def test_falls_back_when_missing(self):
        state = SimpleNamespace(players=())
        assert big_blind_of(state) == ANTE_FALLBACK_BB

    def test_falls_back_when_zero(self):
        state = _state([_player("a", 1000)], current_ante=0)
        assert big_blind_of(state) == ANTE_FALLBACK_BB

    def test_respects_override_default(self):
        state = SimpleNamespace(players=())
        assert big_blind_of(state, default=200) == 200


class TestEffectiveStackChips:
    def test_min_of_hero_and_largest_opponent(self):
        hero = _player("hero", 5000)
        state = _state([hero, _player("opp1", 3000), _player("opp2", 8000)])
        assert effective_stack_chips(state, hero) == 5000

    def test_hero_smaller_than_all(self):
        hero = _player("hero", 1000)
        state = _state([hero, _player("opp1", 3000), _player("opp2", 8000)])
        assert effective_stack_chips(state, hero) == 1000

    def test_ignores_folded_opponents(self):
        hero = _player("hero", 5000)
        state = _state(
            [
                hero,
                _player("opp_big", 8000, is_folded=True),
                _player("opp_small", 1500),
            ]
        )
        assert effective_stack_chips(state, hero) == 1500

    def test_no_active_opponents_returns_hero_stack(self):
        hero = _player("hero", 5000)
        state = _state([hero, _player("opp", 8000, is_folded=True)])
        assert effective_stack_chips(state, hero) == 5000


class TestEffectiveStackBB:
    def test_uses_current_ante_when_present(self):
        # Regression: TieredBot previously read game_state.big_blind, which
        # does not exist, so it always divided by 100 instead of the real
        # BB. effective_stack_bb must read current_ante.
        hero = _player("hero", 1000)
        state = _state([hero, _player("opp", 2000)], current_ante=50)
        assert effective_stack_bb(state, hero) == pytest.approx(20.0)

    def test_explicit_bb_overrides_state(self):
        hero = _player("hero", 1000)
        state = _state([hero, _player("opp", 2000)], current_ante=50)
        assert effective_stack_bb(state, hero, big_blind=100) == pytest.approx(10.0)

    def test_falls_back_when_state_bb_missing(self):
        hero = _player("hero", 1000)
        state = SimpleNamespace(
            players=(hero, _player("opp", 2000)),
            pot={'total': 0},
        )
        # No current_ante → fallback to ANTE_FALLBACK_BB (50)
        assert effective_stack_bb(state, hero) == pytest.approx(20.0)


class TestSPR:
    def test_uses_effective_stack_over_pot(self):
        hero = _player("hero", 1000)
        state = _state([hero, _player("opp", 2000)], pot_total=200)
        assert spr(state, hero) == pytest.approx(5.0)

    def test_empty_pot_is_inf(self):
        hero = _player("hero", 1000)
        state = _state([hero, _player("opp", 2000)], pot_total=0)
        assert spr(state, hero) == float('inf')

    def test_pot_override(self):
        hero = _player("hero", 1000)
        state = _state([hero, _player("opp", 2000)], pot_total=999_999)
        assert spr(state, hero, pot_total=500) == pytest.approx(2.0)
