"""Tests for T1-08: get_next_active_player_idx raises ValueError when no active players found."""

import pytest
from poker.poker_game import get_next_active_player_idx, Player


def _make_player(name="P", stack=1000, is_folded=False, is_all_in=False, has_acted=False):
    return Player(name=name, stack=stack, is_human=False, is_folded=is_folded,
                  is_all_in=is_all_in, has_acted=has_acted)


class TestGetNextActivePlayerIdx:
    def test_finds_next_active_player(self):
        players = (
            _make_player("A", has_acted=True),
            _make_player("B"),  # active
            _make_player("C", has_acted=True),
        )
        assert get_next_active_player_idx(players, 0) == 1

    def test_wraps_around(self):
        players = (
            _make_player("A"),  # active
            _make_player("B", is_folded=True),
            _make_player("C", is_folded=True),
        )
        # Starting from index 1, should wrap around to index 0
        assert get_next_active_player_idx(players, 1) == 0

    def test_skips_folded_and_all_in(self):
        players = (
            _make_player("A", is_folded=True),
            _make_player("B", is_all_in=True),
            _make_player("C"),  # active
            _make_player("D", is_folded=True),
        )
        assert get_next_active_player_idx(players, 0) == 2

    def test_raises_valueerror_when_no_active_players(self):
        players = (
            _make_player("A", is_folded=True),
            _make_player("B", is_all_in=True),
            _make_player("C", is_folded=True),
        )
        with pytest.raises(ValueError, match="No active players found"):
            get_next_active_player_idx(players, 0)

    def test_raises_valueerror_all_acted(self):
        players = (
            _make_player("A", has_acted=True),
            _make_player("B", has_acted=True),
        )
        with pytest.raises(ValueError, match="No active players found"):
            get_next_active_player_idx(players, 0)

    def test_raises_valueerror_zero_stack(self):
        players = (
            _make_player("A", stack=0),
            _make_player("B", stack=0),
        )
        with pytest.raises(ValueError, match="No active players found"):
            get_next_active_player_idx(players, 0)
