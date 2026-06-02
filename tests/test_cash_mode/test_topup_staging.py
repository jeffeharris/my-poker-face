"""Regression: a mid-hand cash top-up is staged, then flushed at the next deal.

Bug (observed in the field): a human folds, clicks "Top up", and the
request hangs then 400s with "Top up is only allowed during the hand".
The top-up route took the per-game lock that `progress_game` was already
holding while it auto-advanced the hand; by the time the lock freed, the
next hand had dealt and `is_folded` had reset, so the route saw an active
player mid-hand and rejected.

Fix: mid-hand top-ups (folded OR active) park the amount in
`game_data['pending_topup']` and `_flush_pending_topup` applies it right
after the next hand is dealt — debiting the bankroll only at that point,
so a leave/bust/drop before the deal can't strand committed chips.

These tests pin the flush helper's money movement and conservation.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from cash_mode.bankroll import PlayerBankrollState
from poker.poker_game import Player, PokerGameState


class _FakeBankrollRepo:
    def __init__(self, chips: int):
        self.state = PlayerBankrollState(
            player_id='You', chips=chips, starting_bankroll=chips
        )
        self.saves = 0

    def load_player_bankroll(self, owner_id):
        return self.state

    def save_player_bankroll(self, new_state):
        self.state = new_state
        self.saves += 1


class TestTopUpStaging(unittest.TestCase):
    def _state(self, human_stack: int, with_human: bool = True):
        players = []
        if with_human:
            players.append(Player(name='You', stack=human_stack, is_human=True))
        players.append(Player(name='Bob', stack=1000, is_human=False))
        return PokerGameState(deck=(), players=tuple(players), current_ante=10)

    def _flush(self, game_data, game_state, bankroll_chips):
        """Run _flush_pending_topup with the bankroll + accounting stubbed.
        Returns (state_machine, fake_bankroll_repo, increment_calls)."""
        from flask_app.handlers import game_handler

        sm = MagicMock()
        sm.game_state = game_state

        fake_repo = _FakeBankrollRepo(bankroll_chips)
        increment_calls = []

        with (
            patch('flask_app.extensions.bankroll_repo', fake_repo),
            patch(
                'flask_app.routes.cash_routes._increment_cash_session_buy_in',
                MagicMock(side_effect=lambda gid, amt: increment_calls.append((gid, amt))),
            ),
            patch.object(game_handler, 'send_message', MagicMock()),
            patch.object(
                game_handler.game_state_service,
                'get_game_owner_info',
                MagicMock(return_value=('You', 'You')),
            ),
        ):
            game_handler._flush_pending_topup('g1', game_data, sm)

        return sm, fake_repo, increment_calls

    def test_flush_credits_stack_and_debits_bankroll(self):
        gs = self._state(human_stack=500)
        game_data = {'pending_topup': 800}

        sm, repo, increments = self._flush(game_data, gs, bankroll_chips=2000)

        human = next(p for p in sm.game_state.players if p.is_human)
        self.assertEqual(human.stack, 1300)          # 500 + 800
        self.assertEqual(repo.state.chips, 1200)      # 2000 - 800
        self.assertNotIn('pending_topup', game_data)  # stage cleared
        self.assertEqual(increments, [('g1', 800)])   # counted as buy-in

    def test_conservation_chips_neither_minted_nor_lost(self):
        gs = self._state(human_stack=500)
        game_data = {'pending_topup': 800}
        before = 500 + 2000

        sm, repo, _ = self._flush(game_data, gs, bankroll_chips=2000)

        human = next(p for p in sm.game_state.players if p.is_human)
        self.assertEqual(human.stack + repo.state.chips, before)

    def test_flush_caps_at_available_bankroll(self):
        # Bankroll shrank below the staged amount (shouldn't happen given
        # the route's guard, but the flush must never overdraw).
        gs = self._state(human_stack=500)
        game_data = {'pending_topup': 800}

        sm, repo, increments = self._flush(game_data, gs, bankroll_chips=300)

        human = next(p for p in sm.game_state.players if p.is_human)
        self.assertEqual(human.stack, 800)           # 500 + min(800, 300)
        self.assertEqual(repo.state.chips, 0)
        self.assertEqual(increments, [('g1', 300)])

    def test_no_pending_is_a_noop(self):
        gs = self._state(human_stack=500)
        game_data = {}

        sm, repo, increments = self._flush(game_data, gs, bankroll_chips=2000)

        human = next(p for p in sm.game_state.players if p.is_human)
        self.assertEqual(human.stack, 500)
        self.assertEqual(repo.saves, 0)               # bankroll untouched
        self.assertEqual(increments, [])

    def test_human_not_seated_leaves_stage_parked(self):
        # Human isn't in the new deal (e.g. just left). Nothing was
        # debited, so the stage must stay parked — no chips at risk.
        gs = self._state(human_stack=0, with_human=False)
        game_data = {'pending_topup': 800}

        _sm, repo, increments = self._flush(game_data, gs, bankroll_chips=2000)

        self.assertEqual(game_data.get('pending_topup'), 800)
        self.assertEqual(repo.saves, 0)
        self.assertEqual(increments, [])


if __name__ == '__main__':
    unittest.main()
