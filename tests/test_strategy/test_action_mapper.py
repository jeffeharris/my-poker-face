"""Tests for poker.strategy.action_mapper."""

import pytest
from types import SimpleNamespace

from poker.strategy.action_mapper import resolve_preflop_sizing, _compute_raise_to


def _make_game_state(stack, bet, current_ante, highest_bet, player_idx=0):
    """Build a minimal mock game state for action mapper tests."""
    player = SimpleNamespace(stack=stack, bet=bet)
    gs = SimpleNamespace(
        players=[player],
        current_ante=current_ante,
        highest_bet=highest_bet,
    )
    # Support multiple player slots when player_idx > 0
    while len(gs.players) <= player_idx:
        gs.players.append(SimpleNamespace(stack=0, bet=0))
    return gs


# --- Simple actions ---

def test_fold():
    gs = _make_game_state(stack=10000, bet=0, current_ante=100, highest_bet=100)
    assert resolve_preflop_sizing('fold', gs, 0) == ('fold', 0)


def test_call():
    gs = _make_game_state(stack=10000, bet=0, current_ante=100, highest_bet=100)
    assert resolve_preflop_sizing('call', gs, 0) == ('call', 0)


def test_check():
    gs = _make_game_state(stack=10000, bet=100, current_ante=100, highest_bet=100)
    assert resolve_preflop_sizing('check', gs, 0) == ('check', 0)


# --- BB-relative raises ---

def test_raise_2_5bb_at_100bb():
    """raise_2.5bb with big_blind=100 → raise to 250."""
    gs = _make_game_state(stack=10000, bet=0, current_ante=100, highest_bet=100)
    action, amount = resolve_preflop_sizing('raise_2.5bb', gs, 0)
    assert action == 'raise'
    assert amount == 250


def test_raise_3bb_from_sb():
    """raise_3bb with big_blind=100 → raise to 300."""
    gs = _make_game_state(stack=9950, bet=50, current_ante=100, highest_bet=100)
    action, amount = resolve_preflop_sizing('raise_3bb', gs, 0)
    assert action == 'raise'
    assert amount == 300


# --- Multiplier-of-bet raises ---

def test_raise_3x_vs_open():
    """raise_3x when highest_bet=250 (2.5bb open) → raise to 750."""
    gs = _make_game_state(stack=10000, bet=0, current_ante=100, highest_bet=250)
    action, amount = resolve_preflop_sizing('raise_3x', gs, 0)
    assert action == 'raise'
    assert amount == 750


def test_raise_4x_vs_open():
    """raise_4x when highest_bet=250 → raise to 1000."""
    gs = _make_game_state(stack=10000, bet=0, current_ante=100, highest_bet=250)
    action, amount = resolve_preflop_sizing('raise_4x', gs, 0)
    assert action == 'raise'
    assert amount == 1000


def test_raise_2_2x_vs_3bet():
    """raise_2.2x when highest_bet=750 → raise to 1650."""
    gs = _make_game_state(stack=10000, bet=0, current_ante=100, highest_bet=750)
    action, amount = resolve_preflop_sizing('raise_2.2x', gs, 0)
    assert action == 'raise'
    assert amount == 1650


# --- Jam ---

def test_jam():
    """jam → all_in with player's total stack."""
    gs = _make_game_state(stack=5000, bet=250, current_ante=100, highest_bet=250)
    action, amount = resolve_preflop_sizing('jam', gs, 0)
    assert action == 'all_in'
    assert amount == 5250  # stack + bet


# --- Edge cases ---

def test_raise_clamped_to_min_raise():
    """When computed raise < min_raise, clamp up to min_raise.

    big_blind=200, highest_bet=200, min_raise = 200+200 = 400.
    raise_1.5bb → 1.5*200 = 300, which is below min_raise of 400.
    """
    gs = _make_game_state(stack=10000, bet=0, current_ante=200, highest_bet=200)
    action, amount = resolve_preflop_sizing('raise_1.5bb', gs, 0)
    assert action == 'raise'
    assert amount == 400  # clamped up to min_raise


def test_raise_converts_to_all_in():
    """When computed raise >= player total stack → all_in."""
    # Player has 500 stack + 0 bet = 500 total
    # raise_3x with highest_bet=250 → 750, exceeds 500
    gs = _make_game_state(stack=500, bet=0, current_ante=100, highest_bet=250)
    action, amount = resolve_preflop_sizing('raise_3x', gs, 0)
    assert action == 'all_in'
    assert amount == 500


def test_short_stack_raise_2_5bb_converts_to_all_in():
    """Player with less than 2.5bb → raise converts to all_in."""
    # Player has 200 stack + 0 bet = 200 total, raise_2.5bb → 250, exceeds 200
    gs = _make_game_state(stack=200, bet=0, current_ante=100, highest_bet=100)
    action, amount = resolve_preflop_sizing('raise_2.5bb', gs, 0)
    assert action == 'all_in'
    assert amount == 200


def test_raise_exactly_equals_stack():
    """When raise_to == player total → all_in."""
    # Player has 750 stack + 0 bet = 750. raise_3x with highest=250 → 750 exactly
    gs = _make_game_state(stack=750, bet=0, current_ante=100, highest_bet=250)
    action, amount = resolve_preflop_sizing('raise_3x', gs, 0)
    assert action == 'all_in'
    assert amount == 750


# --- _compute_raise_to helper ---

def test_compute_raise_to_normal():
    assert _compute_raise_to(2.5, 100, 200, 10000) == 250


def test_compute_raise_to_clamps_to_min():
    assert _compute_raise_to(1.0, 100, 200, 10000) == 200


def test_compute_raise_to_clamps_to_max():
    assert _compute_raise_to(10.0, 100, 200, 500) == 500


def test_compute_raise_to_rounds():
    # 2.5 * 33 = 82.5 → rounds to 82
    assert _compute_raise_to(2.5, 33, 50, 10000) == 82


# --- Error handling ---

def test_unknown_action_raises_error():
    gs = _make_game_state(stack=10000, bet=0, current_ante=100, highest_bet=100)
    with pytest.raises(ValueError, match="Unknown abstract action"):
        resolve_preflop_sizing('limp', gs, 0)
