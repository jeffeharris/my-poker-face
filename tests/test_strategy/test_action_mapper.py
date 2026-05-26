"""Tests for poker.strategy.action_mapper."""

from types import SimpleNamespace

import pytest

from poker.strategy.action_mapper import (
    _compute_raise_to,
    resolve_postflop_sizing,
    resolve_preflop_sizing,
)


def _make_game_state(stack, bet, current_ante, highest_bet, player_idx=0, min_raise_amount=None):
    """Build a minimal mock game state for action mapper tests.

    `min_raise_amount` defaults to `current_ante` (BB) which matches the
    engine's reset behaviour at the start of every betting round; tests
    that exercise re-raise spots can pass a larger value to mirror the
    last raise size.
    """
    player = SimpleNamespace(stack=stack, bet=bet)
    gs = SimpleNamespace(
        players=[player],
        current_ante=current_ante,
        highest_bet=highest_bet,
        min_raise_amount=min_raise_amount if min_raise_amount is not None else current_ante,
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


def test_raise_min_clamps_to_prior_raise_size():
    """T2-38 regression: in a 3-bet spot the minimum legal raise must
    use the prior raise size, not the big blind. Without the fix, a
    sub-minimum sample is clamped to BB+highest_bet and the engine
    silently sanitizes upward, over-committing chips."""
    # Villain raised to 300, prior open was 100, so last_raise_amount=200
    gs = _make_game_state(
        stack=10000,
        bet=0,
        current_ante=100,
        highest_bet=300,
        min_raise_amount=200,
    )
    # raise_2.5bb wants raise_to=250 — below legal min — so clamps to min
    action, amount = resolve_preflop_sizing('raise_2.5bb', gs, 0)
    assert action == 'raise'
    # Correct min = 300 + 200 = 500 (NOT 300 + 100 = 400)
    assert amount == 500


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


def _make_postflop_state(
    stack,
    bet,
    current_ante,
    highest_bet,
    pot_total,
    other_players=None,
    player_idx=0,
    min_raise_amount=None,
):
    """Build a minimal mock game state for postflop action mapper tests.

    `min_raise_amount` defaults to `current_ante` (BB) which mirrors the
    engine's reset at the start of every betting round.
    """
    player = SimpleNamespace(stack=stack, bet=bet)
    players = [player]
    if other_players:
        for s, b in other_players:
            players.append(SimpleNamespace(stack=s, bet=b))
    gs = SimpleNamespace(
        players=players,
        current_ante=current_ante,
        highest_bet=highest_bet,
        min_raise_amount=min_raise_amount if min_raise_amount is not None else current_ante,
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
    """raise_67 facing a flop bet, realistic cumulative state.

    HU flop: hero committed 300 preflop (player.bet=300); villain committed
    300 preflop then bet 400 on the flop (player.bet=700). This engine keeps
    `pot['total']` == sum(player.bet) == 1000 (bets are cumulative, never
    reset per street).

        true_pot       = pot['total']     = 1000
        call_amount    = 700 - 300        = 400
        pot_after_call = 1000 + 400       = 1400
        raise_to       = 700 + int(1400 * 0.67) = 700 + 938 = 1638
    """
    gs = _make_postflop_state(
        stack=9700,
        bet=300,
        current_ante=100,
        highest_bet=700,
        pot_total=1000,
        other_players=[(9300, 700)],
        min_raise_amount=400,
    )
    action, amount = resolve_postflop_sizing('raise_67', gs, 0)
    assert action == 'raise'
    assert amount == 1638


def test_raise_150():
    """raise_150 facing a flop bet, realistic cumulative state.

    HU flop: hero committed 200 preflop; villain committed 200 then bet 200 on
    the flop (player.bet=400). pot['total'] == sum(bets) == 600.

        true_pot       = 600
        call_amount    = 400 - 200 = 200
        pot_after_call = 600 + 200 = 800
        raise_to       = 400 + int(800 * 1.5) = 400 + 1200 = 1600
    """
    gs = _make_postflop_state(
        stack=9600,
        bet=200,
        current_ante=100,
        highest_bet=400,
        pot_total=600,
        other_players=[(9600, 400)],
        min_raise_amount=200,
    )
    action, amount = resolve_postflop_sizing('raise_150', gs, 0)
    assert action == 'raise'
    assert amount == 1600


def test_bet_adds_correct_fraction_of_true_pot():
    """Regression for the 2026-05-24 postflop sizing fix.

    A bet must ADD `pct` of the TRUE pot on top of the hero's cumulative
    commitment — not set the total bet to `pct` of a double-counted pot.

    Realistic cumulative state: HU flop, both committed 300 preflop, no flop
    bet yet, so pot['total'] == sum(bets) == 600 (engine invariant).

        true_pot = 600
        raise_to = player.bet(300) + int(600 * 0.67) = 300 + 402 = 702
        added    = 702 - 300 = 402  == 67% of the TRUE pot

    Pre-fix code computed pot_total = 600 + 600 = 1200 AND omitted the
    `player.bet +`, giving raise_to = int(1200 * 0.67) = 804 (adding 504 ≈
    84% of the true pot). This test fails on the old code, passes on the new.
    """
    gs = _make_postflop_state(
        stack=9700,
        bet=300,
        current_ante=100,
        highest_bet=300,
        pot_total=600,
        other_players=[(9700, 300)],
        min_raise_amount=100,
    )
    action, amount = resolve_postflop_sizing('bet_67', gs, 0)
    assert action == 'raise'
    assert amount == 702
    assert amount - 300 == int(600 * 0.67)  # added chips == 67% of the TRUE pot


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
        stack=3000,
        bet=0,
        current_ante=100,
        highest_bet=500,
        pot_total=1500,
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
