"""Tests for T1-17: Shared input validation for player actions.

Validates that the validate_player_action function correctly rejects
invalid actions, wrong turn, and bad amounts before they reach play_turn().
"""
import pytest
from unittest.mock import MagicMock

from flask_app.validation import validate_player_action, VALID_ACTIONS


def _make_game_state(is_human=True, options=None):
    """Create a mock game state for validation testing."""
    if options is None:
        options = ['fold', 'call', 'raise', 'all_in']

    game_state = MagicMock()
    game_state.current_player.is_human = is_human
    game_state.current_player_options = options
    return game_state


class TestValidatePlayerAction:
    """Tests for the shared validate_player_action function."""

    def test_valid_action_passes(self):
        game_state = _make_game_state(options=['fold', 'call', 'raise', 'all_in'])
        is_valid, error = validate_player_action(game_state, 'call', 0)
        assert is_valid is True
        assert error == ""

    def test_valid_raise_passes(self):
        game_state = _make_game_state(options=['fold', 'call', 'raise', 'all_in'])
        is_valid, error = validate_player_action(game_state, 'raise', 100)
        assert is_valid is True
        assert error == ""

    def test_invalid_action_string_rejected(self):
        game_state = _make_game_state()
        is_valid, error = validate_player_action(game_state, 'hack', 0)
        assert is_valid is False
        assert "Invalid action" in error

    def test_empty_action_rejected(self):
        game_state = _make_game_state()
        is_valid, error = validate_player_action(game_state, '', 0)
        assert is_valid is False
        assert "Invalid action" in error

    def test_action_not_in_current_options_rejected(self):
        """Action is valid overall but not available in current game state."""
        game_state = _make_game_state(options=['fold', 'call'])
        is_valid, error = validate_player_action(game_state, 'raise', 100)
        assert is_valid is False
        assert "not available" in error

    def test_ai_turn_rejected(self):
        game_state = _make_game_state(is_human=False)
        is_valid, error = validate_player_action(game_state, 'call', 0)
        assert is_valid is False
        assert "human" in error.lower()

    def test_negative_raise_amount_rejected(self):
        game_state = _make_game_state(options=['fold', 'call', 'raise', 'all_in'])
        is_valid, error = validate_player_action(game_state, 'raise', -50)
        assert is_valid is False
        assert "Invalid raise amount" in error

    def test_non_numeric_raise_amount_rejected(self):
        game_state = _make_game_state(options=['fold', 'call', 'raise', 'all_in'])
        is_valid, error = validate_player_action(game_state, 'raise', 'abc')
        assert is_valid is False
        assert "Invalid raise amount" in error

    def test_fold_action_valid(self):
        game_state = _make_game_state(options=['fold', 'call', 'all_in'])
        is_valid, error = validate_player_action(game_state, 'fold', 0)
        assert is_valid is True

    def test_check_action_valid(self):
        game_state = _make_game_state(options=['check', 'raise', 'all_in'])
        is_valid, error = validate_player_action(game_state, 'check', 0)
        assert is_valid is True

    def test_all_in_action_valid(self):
        game_state = _make_game_state(options=['fold', 'call', 'all_in'])
        is_valid, error = validate_player_action(game_state, 'all_in', 0)
        assert is_valid is True

    def test_valid_actions_constant_contains_expected(self):
        expected = {'fold', 'check', 'call', 'raise', 'all_in'}
        assert VALID_ACTIONS == expected
