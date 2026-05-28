"""Regression: a busted human pauses the cash table at the hand boundary.

Bug (observed in the field): the human busts (stack 0) at a cash table
that still has 2+ AI chip-holders. `_detect_human_cash_bust` emits
`cash_rebuy_needed` and the BustModal opens — but the old pause guard
only fired when fewer than 2 chip-holders remained. With quorum intact
the table dealt the next hand without the human, advancing the phase out
of HAND_OVER. The rebuy POST then hit the between-hands gate and was
rejected with "Rebuy is only allowed between hands" (400).

`handle_evaluating_hand_phase` must pause the table in HAND_OVER whenever
the human is busted, regardless of how many AIs still hold chips, so the
rebuy/leave has a clean between-hands window to land in.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from core.card import Card
from poker.poker_game import Player, PokerGameState


class TestHumanBustPause(unittest.TestCase):
    def _build_game_state(self, human_stack: int):
        """Hand just ended: an AI (Bob) wins uncontested; the others are
        folded. `human_stack` lets a test toggle the busted vs not-busted
        precondition while keeping 3 AI chip-holders (quorum intact)."""
        you = Player(
            name='You',
            stack=human_stack,
            is_human=True,
            bet=0,
            hand=(),
            is_folded=True,
        )
        bob = Player(
            name='Bob',
            stack=1000,
            is_human=False,
            bet=10,
            hand=(Card('A', 'Spades'), Card('K', 'Hearts')),
            is_folded=False,
        )
        cleo = Player(name='Cleo', stack=1000, is_human=False, bet=0, hand=(), is_folded=True)
        dan = Player(name='Dan', stack=1000, is_human=False, bet=0, hand=(), is_folded=True)
        return PokerGameState(
            deck=(),
            players=(you, bob, cleo, dan),
            community_cards=(
                Card('7', 'Diamonds'),
                Card('8', 'Clubs'),
                Card('9', 'Spades'),
                Card('Q', 'Hearts'),
                Card('2', 'Spades'),
            ),
            pot={'total': 10, 'Bob': 10},
            current_ante=10,
        )

    def _run(self, human_stack: int):
        """Drive handle_evaluating_hand_phase with the cash helpers stubbed
        out. Returns (result_tuple, state_machine, game_data)."""
        from flask_app.handlers import game_handler
        from poker.memory.memory_manager import AIMemoryManager

        game_id = 'bust-pause-test'
        game_state = self._build_game_state(human_stack)
        mm = AIMemoryManager(game_id)
        mm.on_hand_start(game_state, hand_number=1)

        state_machine = MagicMock()
        state_machine.game_state = game_state
        state_machine.current_phase = None
        state_machine._state_machine = MagicMock()

        game_data = {
            'memory_manager': mm,
            'ai_controllers': {},
            'state_machine': state_machine,
            'owner_id': '',
            'cash_mode': True,
            'cash_stake_label': 'low',
            'hand_start_stacks': {},
            'short_stack_players': set(),
            'last_announced_phase': None,
        }

        patches = [
            patch.object(
                game_handler,
                'socketio',
                MagicMock(
                    emit=MagicMock(),
                    start_background_task=MagicMock(),
                    sleep=MagicMock(),
                ),
            ),
            patch.object(game_handler, 'send_message', MagicMock()),
            patch.object(game_handler, 'hand_history_repo', MagicMock()),
            patch.object(game_handler, 'game_repo', MagicMock()),
            patch.object(game_handler, 'event_repository', MagicMock()),
            patch.object(game_handler, 'coach_repo', MagicMock()),
            patch.object(game_handler, 'handle_eliminations', MagicMock(return_value=False)),
            patch.object(game_handler, 'check_tournament_complete', MagicMock(return_value=False)),
            patch.object(game_handler, 'update_and_emit_game_state', MagicMock()),
            # Cash helpers: isolate the pause decision from the world sim.
            patch.object(
                game_handler,
                '_apply_player_table_rake',
                MagicMock(side_effect=lambda **kw: kw['game_state']),
            ),
            patch.object(game_handler, '_refill_cash_seats', MagicMock()),
            patch.object(game_handler, '_detect_human_cash_bust', MagicMock()),
            patch.object(game_handler, '_refresh_lobby_table_for_session', MagicMock()),
            patch.object(game_handler, 'select_rejoin_candidates', MagicMock(return_value=[])),
            patch.object(game_handler.game_state_service, 'set_game', MagicMock()),
            patch.object(
                game_handler.game_state_service,
                'get_game_owner_info',
                MagicMock(return_value=('', '')),
            ),
            patch.object(
                game_handler,
                'config',
                MagicMock(ENABLE_AI_COMMENTARY=False, ANIMATION_SPEED=0),
            ),
            patch.object(game_handler, '_track_guest_hand', MagicMock(return_value=False)),
        ]
        for p in patches:
            p.start()
        try:
            result = game_handler.handle_evaluating_hand_phase(
                game_id, game_data, state_machine, game_state
            )
        finally:
            for p in patches:
                p.stop()
        return result, state_machine, game_data

    def test_busted_human_pauses_even_with_quorum(self):
        """Human stack 0 + 3 AI chip-holders → pause, don't deal on."""
        result, state_machine, game_data = self._run(human_stack=0)

        # The table must NOT deal the next hand — that's the phase advance
        # that broke rebuy.
        state_machine.run_until_player_action.assert_not_called()
        # Paused-and-returned contract: should_return is True.
        self.assertTrue(result[1])
        # It's a bust, not the "everyone left" solo case.
        self.assertFalse(game_data.get('cash_solo_paused'))

    def test_active_human_with_quorum_deals_on(self):
        """Human still has chips + quorum intact → no over-pause; deal on."""
        result, state_machine, game_data = self._run(human_stack=1000)

        state_machine.run_until_player_action.assert_called_once()
        self.assertFalse(result[1])


if __name__ == '__main__':
    unittest.main()
