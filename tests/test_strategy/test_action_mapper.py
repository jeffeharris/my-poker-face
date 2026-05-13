"""Tests for poker.strategy.action_mapper."""

import pytest
from types import SimpleNamespace

from poker.strategy.action_mapper import resolve_preflop_sizing, resolve_postflop_sizing, _compute_raise_to


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


# =====================================================================
# resolve_postflop_sizing tests
# =====================================================================

def _make_postflop_state(stack, bet, current_ante, highest_bet, pot_total,
                         other_players=None, player_idx=0):
    """Build a minimal mock game state for postflop action mapper tests."""
    player = SimpleNamespace(stack=stack, bet=bet)
    players = [player]
    if other_players:
        for s, b in other_players:
            players.append(SimpleNamespace(stack=s, bet=b))
    gs = SimpleNamespace(
        players=players,
        current_ante=current_ante,
        highest_bet=highest_bet,
        pot={'total': pot_total},
    )
    while len(gs.players) <= player_idx:
        gs.players.append(SimpleNamespace(stack=0, bet=0))
    return gs


# --- Simple postflop actions ---

def test_postflop_fold():
    gs = _make_postflop_state(stack=10000, bet=0, current_ante=100, highest_bet=0, pot_total=1000)
    assert resolve_postflop_sizing('fold', gs, 0) == ('fold', 0)


def test_postflop_check():
    gs = _make_postflop_state(stack=10000, bet=0, current_ante=100, highest_bet=0, pot_total=1000)
    assert resolve_postflop_sizing('check', gs, 0) == ('check', 0)


def test_postflop_call():
    gs = _make_postflop_state(stack=10000, bet=0, current_ante=100, highest_bet=500, pot_total=1000)
    assert resolve_postflop_sizing('call', gs, 0) == ('call', 0)


# --- Bet actions (first to act, no bet to face) ---

def test_bet_33():
    """bet_33 with pot=1000 → raise to 330."""
    gs = _make_postflop_state(stack=10000, bet=0, current_ante=100, highest_bet=0, pot_total=1000)
    action, amount = resolve_postflop_sizing('bet_33', gs, 0)
    assert action == 'raise'
    assert amount == 330


def test_bet_67():
    """bet_67 with pot=1000 → raise to 670."""
    gs = _make_postflop_state(stack=10000, bet=0, current_ante=100, highest_bet=0, pot_total=1000)
    action, amount = resolve_postflop_sizing('bet_67', gs, 0)
    assert action == 'raise'
    assert amount == 670


def test_bet_100():
    """bet_100 with pot=1000 → raise to 1000 (pot-size bet)."""
    gs = _make_postflop_state(stack=10000, bet=0, current_ante=100, highest_bet=0, pot_total=1000)
    action, amount = resolve_postflop_sizing('bet_100', gs, 0)
    assert action == 'raise'
    assert amount == 1000


# --- Raise actions (facing a bet) ---

def test_raise_67():
    """raise_67 facing bet of 500, pot=1500 total.

    call_amount = 500 - 0 = 500
    pot_after_call = 1500 + 500 = 2000
    raise_to = 500 + int(2000 * 0.67) = 500 + 1340 = 1840
    """
    # pot['total']=1000, villain bet=500, hero bet=0 → pot_total=1500
    gs = _make_postflop_state(
        stack=10000, bet=0, current_ante=100, highest_bet=500, pot_total=1000,
        other_players=[(9500, 500)],
    )
    action, amount = resolve_postflop_sizing('raise_67', gs, 0)
    assert action == 'raise'
    assert amount == 1840


def test_raise_150():
    """raise_150 facing bet of 200, pot=600 total.

    call_amount = 200 - 0 = 200
    pot_after_call = 600 + 200 = 800
    raise_to = 200 + int(800 * 1.5) = 200 + 1200 = 1400
    """
    # pot['total']=400, villain bet=200, hero bet=0 → pot_total=600
    gs = _make_postflop_state(
        stack=10000, bet=0, current_ante=100, highest_bet=200, pot_total=400,
        other_players=[(9800, 200)],
    )
    action, amount = resolve_postflop_sizing('raise_150', gs, 0)
    assert action == 'raise'
    assert amount == 1400


# --- Jam ---

def test_postflop_jam():
    """jam → all_in with player's total stack."""
    gs = _make_postflop_state(stack=5000, bet=200, current_ante=100, highest_bet=200, pot_total=800)
    action, amount = resolve_postflop_sizing('jam', gs, 0)
    assert action == 'all_in'
    assert amount == 5200  # stack + bet


# --- Clamping ---

def test_postflop_min_raise_clamping():
    """bet_33 with tiny pot → clamp up to min_raise.

    pot_total = 60, bet_33 → int(60 * 0.33) = 19
    min_raise = 0 + 100 = 100
    19 < 100 → clamp to 100
    """
    gs = _make_postflop_state(stack=10000, bet=0, current_ante=100, highest_bet=0, pot_total=60)
    action, amount = resolve_postflop_sizing('bet_33', gs, 0)
    assert action == 'raise'
    assert amount == 100


def test_postflop_stack_clamping():
    """bet_100 when pot > stack → all_in.

    pot_total = 2000, bet_100 → 2000
    player_total = 1000 + 0 = 1000
    min(2000, 1000) = 1000 → >= player_total → all_in
    """
    gs = _make_postflop_state(stack=1000, bet=0, current_ante=100, highest_bet=0, pot_total=2000)
    action, amount = resolve_postflop_sizing('bet_100', gs, 0)
    assert action == 'all_in'
    assert amount == 1000


def test_postflop_raise_converts_to_all_in():
    """raise_150 when computed raise exceeds stack → all_in.

    pot_total = 2000, highest_bet = 500, call_amount = 500
    pot_after_call = 2000 + 500 = 2500
    raise_to = 500 + int(2500 * 1.5) = 500 + 3750 = 4250
    player_total = 3000 → 4250 > 3000 → clamp → all_in
    """
    gs = _make_postflop_state(
        stack=3000, bet=0, current_ante=100, highest_bet=500, pot_total=1500,
        other_players=[(9500, 500)],
    )
    action, amount = resolve_postflop_sizing('raise_150', gs, 0)
    assert action == 'all_in'
    assert amount == 3000


# --- Error handling ---

def test_postflop_unknown_action_raises_error():
    gs = _make_postflop_state(stack=10000, bet=0, current_ante=100, highest_bet=0, pot_total=1000)
    with pytest.raises(ValueError, match="Unknown abstract action"):
        resolve_postflop_sizing('limp', gs, 0)
