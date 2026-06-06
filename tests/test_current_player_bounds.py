"""Regression: an out-of-range current_player_idx must not 500-storm the API.

When the players tuple shrinks at a between-hands pause (AI departures, a
tournament reconcile) without re-clamping current_player_idx, the index can point
past the end. The read-only API/emit path (to_dict, BettingContext.from_game_state)
then IndexError-ed on every frontend poll — a continuous 500 storm while the game
sat paused. The accessors now degrade to empty/benign values instead.
"""

from poker.betting_context import BettingContext
from poker.poker_game import Player, PokerGameState


def _state_with_out_of_range_idx() -> PokerGameState:
    players = (
        Player(name="A", stack=100, is_human=False),
        Player(name="B", stack=100, is_human=True),
    )
    # idx 5 is past the 2-player tuple, as if seats were pruned without re-clamp.
    return PokerGameState(
        players=players, deck=(), current_ante=100, last_raise_amount=100, current_player_idx=5
    )


def test_current_player_options_empty_when_idx_out_of_range():
    assert _state_with_out_of_range_idx().current_player_options == []


def test_to_dict_does_not_raise_when_idx_out_of_range():
    # to_dict() computes current_player_options unconditionally on the emit path.
    d = _state_with_out_of_range_idx().to_dict()
    assert d["current_player_options"] == []


def test_betting_context_benign_when_idx_out_of_range():
    ctx = BettingContext.from_game_state(_state_with_out_of_range_idx())  # must not raise
    assert ctx.available_actions == ()
    assert ctx.player_stack == 0
