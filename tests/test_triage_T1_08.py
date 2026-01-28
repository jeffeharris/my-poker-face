"""Tests for get_next_active_player_idx behavior when no active players found.

When no active players exist (all folded/all-in), the function returns None
to signal that the betting round should end. Callers handle this by
triggering showdown or winner determination.
"""

from poker.poker_game import (
    get_next_active_player_idx,
    advance_to_next_active_player,
    set_betting_round_start_player,
    Player,
    PokerGameState,
    create_deck,
)


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

    def test_returns_none_when_no_active_players(self):
        """When no active players exist (all folded/all-in), returns None."""
        players = (
            _make_player("A", is_folded=True),
            _make_player("B", is_all_in=True),
            _make_player("C", is_folded=True),
        )
        # Should return None to signal betting round should end
        assert get_next_active_player_idx(players, 0) is None

    def test_returns_none_when_all_acted(self):
        """When all players have acted, returns None."""
        players = (
            _make_player("A", has_acted=True),
            _make_player("B", has_acted=True),
        )
        assert get_next_active_player_idx(players, 0) is None

    def test_returns_none_when_zero_stack(self):
        """When all players have zero stack, returns None."""
        players = (
            _make_player("A", stack=0),
            _make_player("B", stack=0),
        )
        assert get_next_active_player_idx(players, 0) is None


class TestAdvanceToNextActivePlayer:
    """Tests for advance_to_next_active_player returning Optional[PokerGameState]."""

    def _make_game_state(self, players, current_player_idx=0):
        return PokerGameState(
            players=tuple(players),
            deck=create_deck(shuffled=False),
            current_player_idx=current_player_idx,
        )

    def test_advances_to_next_active(self):
        """Returns game state with updated current_player_idx when active player found."""
        players = (
            _make_player("A"),
            _make_player("B"),
        )
        game_state = self._make_game_state(players, current_player_idx=0)
        result = advance_to_next_active_player(game_state)
        assert result is not None
        assert result.current_player_idx == 1

    def test_returns_none_when_no_active_players(self):
        """Returns None when all players are folded/all-in."""
        players = (
            _make_player("A", is_all_in=True),
            _make_player("B", is_folded=True),
        )
        game_state = self._make_game_state(players, current_player_idx=0)
        result = advance_to_next_active_player(game_state)
        assert result is None


class TestSetBettingRoundStartPlayer:
    """Tests for set_betting_round_start_player returning Optional[PokerGameState]."""

    def _make_game_state(self, players, community_cards=(), dealer_idx=0):
        return PokerGameState(
            players=tuple(players),
            deck=create_deck(shuffled=False),
            community_cards=community_cards,
            current_dealer_idx=dealer_idx,
        )

    def test_sets_start_player_preflop(self):
        """Returns game state with start player set for pre-flop (no community cards)."""
        players = (
            _make_player("A"),
            _make_player("B"),
            _make_player("C"),
        )
        game_state = self._make_game_state(players, dealer_idx=0)
        result = set_betting_round_start_player(game_state)
        assert result is not None
        # Pre-flop starts 3 positions from dealer (after blinds)
        assert result.current_player_idx is not None

    def test_returns_none_when_no_active_players_preflop(self):
        """Returns None when all players are folded/all-in at pre-flop."""
        players = (
            _make_player("A", is_all_in=True),
            _make_player("B", is_all_in=True),
        )
        game_state = self._make_game_state(players, dealer_idx=0)
        result = set_betting_round_start_player(game_state)
        assert result is None

    def test_returns_none_when_no_active_players_postflop(self):
        """Returns None when all players are folded/all-in at post-flop."""
        from core.card import Card
        players = (
            _make_player("A", is_all_in=True),
            _make_player("B", is_folded=True),
        )
        community_cards = (Card("A", "Spades"), Card("K", "Hearts"), Card("Q", "Diamonds"))
        game_state = self._make_game_state(players, community_cards=community_cards, dealer_idx=0)
        result = set_betting_round_start_player(game_state)
        assert result is None
