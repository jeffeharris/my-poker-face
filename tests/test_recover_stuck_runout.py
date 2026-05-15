"""Tests for recover_stuck_runout — the restore-path fix for games
persisted mid-all-in-runout.

The scenario this guards against: a server crash while
`game_state.run_it_out` is True. On reload the state machine sets
awaiting_action=True with run_it_out=True; the live progress_game loop
that normally consumes the flag is never re-engaged, and the UI
clears action options whenever run_it_out is set — so the player sees
no buttons and the game freezes.

`recover_stuck_runout` fast-forwards through the run-out without
animations and lands at the next stable point (showdown completes, a
new hand begins).
"""
import unittest
from unittest.mock import MagicMock

from poker.poker_game import initialize_game_state
from poker.poker_state_machine import PokerStateMachine, PokerPhase
from flask_app.game_adapter import StateMachineAdapter
from flask_app.handlers.game_handler import recover_stuck_runout


def _stuck_runout_state_machine_at_river():
    """Build a state machine in the exact stuck shape we saw in production.

    Mirrors the recovered game (gmhlFMYdCCXE72DxuzUGCA): one player
    matched another's all-in on the flop, turn + river dealt out,
    state persisted with run_it_out=True awaiting_action=True on RIVER.
    """
    gs = initialize_game_state(['Jeff', 'Whoopi', 'Gordon', 'Buddha'])

    # Match the production stuck shape: 4 players, two folded, one all-in,
    # one (Jeff) the "current_player" but with no real opponent to bet
    # against. 5 community cards already on the board.
    community = [
        {'rank': 'K', 'suit': 'Clubs', 'suit_symbol': '♣', 'value': 13},
        {'rank': '10', 'suit': 'Spades', 'suit_symbol': '♠', 'value': 10},
        {'rank': '5', 'suit': 'Hearts', 'suit_symbol': '♥', 'value': 5},
        {'rank': '4', 'suit': 'Clubs', 'suit_symbol': '♣', 'value': 4},
        {'rank': '2', 'suit': 'Spades', 'suit_symbol': '♠', 'value': 2},
    ]
    players = tuple([
        gs.players[0].update(stack=10008, bet=7008),
        gs.players[1].update(stack=0, bet=7008, is_all_in=True),
        gs.players[2].update(stack=18936, bet=1800, is_folded=True),
        gs.players[3].update(stack=13876, bet=0, is_folded=True),
    ])
    gs = gs.update(
        players=players,
        pot={'total': 15816, 'highest_bet': 7008,
             'Jeff': 7008, 'Whoopi': 7008, 'Gordon': 1800},
        community_cards=community,
        current_player_idx=0,
        current_ante=1800,
        awaiting_action=True,
        run_it_out=True,
    )
    return PokerStateMachine.from_saved_state(gs, PokerPhase.RIVER)


class TestRecoverStuckRunout(unittest.TestCase):

    def test_healthy_game_is_not_modified(self):
        """recover_stuck_runout returns False when run_it_out is unset."""
        gs = initialize_game_state(['Alice', 'Bob'])
        sm = StateMachineAdapter(PokerStateMachine(gs))
        # Sanity — fresh game has run_it_out=False
        self.assertFalse(sm.game_state.run_it_out)
        before_phase = sm.current_phase

        applied = recover_stuck_runout(sm)

        self.assertFalse(applied)
        self.assertEqual(sm.current_phase, before_phase)

    def test_stuck_river_runout_recovers(self):
        """Stuck RIVER + run_it_out advances past RIVER and clears the flag."""
        sm = StateMachineAdapter(_stuck_runout_state_machine_at_river())
        self.assertTrue(sm.game_state.run_it_out)
        self.assertEqual(sm.current_phase, PokerPhase.RIVER)

        applied = recover_stuck_runout(sm)

        self.assertTrue(applied)
        self.assertFalse(sm.game_state.run_it_out)
        # Should have settled past RIVER — either at the next acting
        # player on a new hand, or terminal phase. Either way, not
        # RIVER with the stuck flag.
        self.assertNotEqual(
            (sm.current_phase, sm.game_state.run_it_out),
            (PokerPhase.RIVER, True),
        )

    def test_recovered_state_is_playable(self):
        """After recovery, a human is awaiting action with real options."""
        sm = StateMachineAdapter(_stuck_runout_state_machine_at_river())

        recover_stuck_runout(sm)

        # Should have advanced through showdown into a new hand
        self.assertTrue(sm.game_state.awaiting_action)
        # Options should not be empty (the UI-clearing bug only fires
        # when run_it_out=True, which is now cleared)
        options = sm.game_state.current_player_options
        self.assertTrue(len(options) > 0,
                        f"Expected playable options, got {options}")

    def test_safety_iteration_cap(self):
        """The recovery loop terminates even if run_it_out can't be cleared."""
        # Construct a mock state_machine whose run_it_out flag never
        # clears. The safety cap inside recover_stuck_runout should
        # break the loop after a bounded number of iterations rather
        # than hanging forever.
        from poker.poker_state_machine import PokerPhase as _Phase

        inner = MagicMock()
        # Stubs for the _state_machine surface
        inner.game_state = MagicMock(run_it_out=True)
        inner.game_state.update = MagicMock(return_value=MagicMock(run_it_out=True))
        inner.with_game_state = MagicMock(return_value=inner)
        inner.with_phase = MagicMock(return_value=inner)

        adapter = MagicMock()
        adapter._state_machine = inner
        # The adapter's game_state property delegates to inner
        type(adapter).game_state = property(lambda self: self._state_machine.game_state)
        type(adapter).current_phase = property(lambda self: _Phase.RIVER)
        adapter.run_until_player_action = MagicMock()

        # If the cap doesn't fire we'd hang — pytest's timeout would
        # catch it but explicit cap test is clearer.
        recover_stuck_runout(adapter)

        # Mock's update was called repeatedly but bounded
        self.assertLess(inner.game_state.update.call_count, 50)


if __name__ == '__main__':
    unittest.main()
