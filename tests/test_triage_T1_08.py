"""Tests for get_next_active_player_idx behavior when no active players found.

When no active players exist (all folded/all-in), the function returns the
starting index to signal that the betting round should end. Callers handle
this by triggering showdown or winner determination.
"""

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

    def test_returns_starting_idx_when_no_active_players(self):
        """When no active players, returns starting_idx to signal betting should end."""
        players = (
            _make_player("A", is_folded=True),
            _make_player("B", is_all_in=True),
            _make_player("C", is_folded=True),
        )
        # Should return starting_idx (0), not raise
        assert get_next_active_player_idx(players, 0) == 0

    def test_returns_starting_idx_all_acted(self):
        """When all players have acted, returns starting_idx."""
        players = (
            _make_player("A", has_acted=True),
            _make_player("B", has_acted=True),
        )
        assert get_next_active_player_idx(players, 0) == 0

    def test_returns_starting_idx_zero_stack(self):
        """When all players have zero stack, returns starting_idx."""
        players = (
            _make_player("A", stack=0),
            _make_player("B", stack=0),
        )
        assert get_next_active_player_idx(players, 0) == 0
