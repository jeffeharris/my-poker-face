#!/usr/bin/env python3
"""Regression tests for two cash-mode hardening Criticals.

PRH-3 — a voluntarily-departing AI at the human's seated table must have its
        seat chips credited back to its bankroll (not destroyed).
PRH-4 — a cash game must never route a bust through tournament-elimination
        logic (no "Nth place" GAME_OVER; the rebuy modal handles it).
"""

import types
import unittest
from unittest.mock import MagicMock, patch

from cash_mode.movement import BankrollChange
from flask_app.handlers.game_handler import (
    _credit_departed_ai_bankrolls,
    check_tournament_complete,
    handle_eliminations,
)


class TestPRH3SeatedDepartureCredit(unittest.TestCase):
    """`_credit_departed_ai_bankrolls` returns departing seat chips to the
    bankroll via `credit_ai_cash_out` — keyed on personality_id, from_seat only."""

    def _run(self, changes, departed):
        result = types.SimpleNamespace(bankroll_changes=changes)
        with patch('cash_mode.bankroll.credit_ai_cash_out') as mock_credit:
            total = _credit_departed_ai_bankrolls(
                result,
                departed,
                bankroll_repo=MagicMock(),
                chip_ledger_repo=MagicMock(),
                sandbox_id='sandbox-1',
                now=None,
                table_id='table-1',
            )
        return total, mock_credit

    def test_credits_from_seat_for_departed_only(self):
        changes = [
            BankrollChange(direction='from_seat', personality_id='alice', amount=500),
            BankrollChange(direction='to_seat', personality_id='bob', amount=300),  # rebuy debit
            BankrollChange(direction='from_seat', personality_id='carol', amount=200),
            BankrollChange(direction='from_seat', personality_id='dave', amount=0),  # no chips
            BankrollChange(direction='from_seat', personality_id='erin', amount=400),  # stayed
        ]
        departed = {'alice', 'carol', 'dave'}  # erin stayed; bob rebought

        total, mock_credit = self._run(changes, departed)

        self.assertEqual(total, 700)
        # credit_ai_cash_out(bankroll_repo, pid, amount, ...) — positional pid/amount
        credited = {(c.args[1], c.args[2]) for c in mock_credit.call_args_list}
        self.assertEqual(credited, {('alice', 500), ('carol', 200)})

    def test_never_credits_to_seat(self):
        # Defensive: even if a to_seat pid is (wrongly) in departed, it is the
        # debit channel (handled by _apply_rebuys) and must never be credited
        # here — that was the documented double-debit hazard.
        changes = [BankrollChange(direction='to_seat', personality_id='bob', amount=300)]
        total, mock_credit = self._run(changes, {'bob'})
        self.assertEqual(total, 0)
        mock_credit.assert_not_called()

    def test_ledger_context_and_repo_threaded(self):
        changes = [BankrollChange(direction='from_seat', personality_id='alice', amount=500)]
        result = types.SimpleNamespace(bankroll_changes=changes)
        fake_repo = MagicMock()
        fake_ledger = MagicMock()
        with patch('cash_mode.bankroll.credit_ai_cash_out') as mock_credit:
            _credit_departed_ai_bankrolls(
                result,
                {'alice'},
                bankroll_repo=fake_repo,
                chip_ledger_repo=fake_ledger,
                sandbox_id='sb',
                now=None,
                table_id='t-9',
            )
        _, kwargs = mock_credit.call_args
        self.assertIs(kwargs['chip_ledger_repo'], fake_ledger)
        self.assertEqual(kwargs['sandbox_id'], 'sb')
        self.assertEqual(kwargs['ledger_context']['site'], 'seated_table_vacate')
        self.assertEqual(kwargs['ledger_context']['table_id'], 't-9')


class TestPRH4CashNeverRoutesToTournament(unittest.TestCase):
    """Cash games never reach tournament elimination/completion logic."""

    def test_handle_eliminations_noops_without_tracker(self):
        # Warm/cold cash builders omit the key → no-op (rebuy modal handles bust).
        result = handle_eliminations('cash-x', {}, MagicMock(), ['Alice'], 100)
        self.assertIsNone(result)

    def test_handle_eliminations_noops_for_cash_even_with_leaked_tracker(self):
        # Belt-and-suspenders: a leaked tracker on a cash game must still no-op.
        tracker = MagicMock()
        game_data = {'cash_mode': True, 'tournament_tracker': tracker}
        result = handle_eliminations('cash-x', game_data, MagicMock(), ['Alice'], 100)
        self.assertIsNone(result)
        tracker.on_hand_complete.assert_not_called()

    def test_check_tournament_complete_noops_for_cash_even_with_leaked_tracker(self):
        tracker = MagicMock()
        game_data = {'cash_mode': True, 'tournament_tracker': tracker}
        self.assertFalse(check_tournament_complete('cash-x', game_data))
        tracker.is_complete.assert_not_called()

    def test_tournament_path_still_active_for_real_tournaments(self):
        # Sanity: a real tournament (tracker present, no cash_mode) passes the
        # guard and exercises the tracker.
        tracker = MagicMock()
        tracker.is_complete.return_value = False
        game_data = {'tournament_tracker': tracker}
        self.assertFalse(check_tournament_complete('g1', game_data))
        tracker.is_complete.assert_called_once()


if __name__ == '__main__':
    unittest.main()
