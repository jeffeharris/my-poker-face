"""Tests for player options clearing during non-betting phases.

Verifies that player_options are correctly cleared during:
- EVALUATING_HAND phase
- HAND_OVER phase
- SHOWDOWN phase
- GAME_OVER phase
- run_it_out mode

And NOT cleared during normal betting phases (PRE_FLOP, FLOP, TURN, RIVER).
"""

import unittest
from unittest.mock import MagicMock, patch

from poker.poker_game import Player, PokerGameState
from poker.poker_state_machine import PokerPhase, ImmutableStateMachine


def create_mock_game_data(phase: PokerPhase, run_it_out: bool = False):
    """Create mock game data with the given phase and run_it_out state."""
    players = (
        Player(name='Human', stack=1000, is_human=True, hand=()),
        Player(name='AI1', stack=1000, is_human=False, hand=()),
    )
    game_state = PokerGameState(
        deck=(),
        players=players,
        community_cards=(),
        current_player_idx=0,
        run_it_out=run_it_out,
    )

    state_machine = MagicMock()
    state_machine.current_phase = phase
    state_machine.game_state = game_state

    return {
        'state_machine': state_machine,
        'game_state': game_state,
    }


def compute_should_clear_options(game_data: dict) -> bool:
    """
    Replicate the should_clear_options logic from game_handler.py.

    This mirrors the logic at game_handler.py:328-332 for testing purposes.
    """
    game_state = game_data['game_state']
    state_machine = game_data.get('state_machine')
    should_clear_options = game_state.run_it_out or (
        state_machine and state_machine.current_phase in (
            PokerPhase.EVALUATING_HAND, PokerPhase.HAND_OVER,
            PokerPhase.SHOWDOWN, PokerPhase.GAME_OVER
        )
    )
    return should_clear_options


class TestShouldClearOptions(unittest.TestCase):
    """Test cases for player options clearing logic."""

    def test_options_cleared_during_evaluating_hand(self):
        """Options should be cleared during EVALUATING_HAND phase."""
        game_data = create_mock_game_data(PokerPhase.EVALUATING_HAND)
        self.assertTrue(compute_should_clear_options(game_data))

    def test_options_cleared_during_hand_over(self):
        """Options should be cleared during HAND_OVER phase."""
        game_data = create_mock_game_data(PokerPhase.HAND_OVER)
        self.assertTrue(compute_should_clear_options(game_data))

    def test_options_cleared_during_showdown(self):
        """Options should be cleared during SHOWDOWN phase."""
        game_data = create_mock_game_data(PokerPhase.SHOWDOWN)
        self.assertTrue(compute_should_clear_options(game_data))

    def test_options_cleared_during_game_over(self):
        """Options should be cleared during GAME_OVER phase."""
        game_data = create_mock_game_data(PokerPhase.GAME_OVER)
        self.assertTrue(compute_should_clear_options(game_data))

    def test_options_cleared_during_run_it_out(self):
        """Options should be cleared when run_it_out is True, regardless of phase."""
        # Even during a betting phase, run_it_out should clear options
        game_data = create_mock_game_data(PokerPhase.RIVER, run_it_out=True)
        self.assertTrue(compute_should_clear_options(game_data))

    def test_options_not_cleared_during_pre_flop(self):
        """Options should NOT be cleared during PRE_FLOP betting phase."""
        game_data = create_mock_game_data(PokerPhase.PRE_FLOP)
        self.assertFalse(compute_should_clear_options(game_data))

    def test_options_not_cleared_during_flop(self):
        """Options should NOT be cleared during FLOP betting phase."""
        game_data = create_mock_game_data(PokerPhase.FLOP)
        self.assertFalse(compute_should_clear_options(game_data))

    def test_options_not_cleared_during_turn(self):
        """Options should NOT be cleared during TURN betting phase."""
        game_data = create_mock_game_data(PokerPhase.TURN)
        self.assertFalse(compute_should_clear_options(game_data))

    def test_options_not_cleared_during_river(self):
        """Options should NOT be cleared during RIVER betting phase."""
        game_data = create_mock_game_data(PokerPhase.RIVER)
        self.assertFalse(compute_should_clear_options(game_data))

    def test_options_cleared_when_state_machine_missing(self):
        """Options should be cleared based on run_it_out when state_machine is missing."""
        game_data = create_mock_game_data(PokerPhase.PRE_FLOP)
        game_data['state_machine'] = None

        # Without state_machine and without run_it_out, should not clear
        self.assertFalse(compute_should_clear_options(game_data))

        # With run_it_out, should clear even without state_machine
        game_data['game_state'] = game_data['game_state'].update(run_it_out=True)
        self.assertTrue(compute_should_clear_options(game_data))


if __name__ == '__main__':
    unittest.main()
