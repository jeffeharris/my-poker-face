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
from flask_app.handlers.game_handler import _credit_departed_ai_bankrolls


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


class TestSeatedLeaveSettlesStake(unittest.TestCase):
    """A staked AI leaving the human's seated table must settle its stake
    HERE (at this seat's stack) — the same settlement the unseated path runs.

    Regression: the seated path used to credit the AI its full seat chips and
    leave the stake active, silently transferring the staker's upside to the
    AI's own bankroll and settling the stake later at an unrelated table
    ("AI walks up big, staker gets scraps").
    """

    def test_staked_departure_settles_and_skips_full_credit(self):
        # frida departs up big with a stake; bob departs with no stake.
        changes = [
            BankrollChange(direction='from_seat', personality_id='frida', amount=43600),
            BankrollChange(direction='from_seat', personality_id='bob', amount=500),
        ]
        result = types.SimpleNamespace(bankroll_changes=changes)
        stake_repo = MagicMock()

        # Stub the shared settlement helper: settles frida's stake, none for bob.
        def fake_settle(pid, chips_at_leave, **kwargs):
            return object() if pid == 'frida' else None

        with (
            patch(
                'cash_mode.lobby.settle_departed_ai_stake', side_effect=fake_settle
            ) as mock_settle,
            patch('cash_mode.bankroll.credit_ai_cash_out') as mock_credit,
        ):
            total = _credit_departed_ai_bankrolls(
                result,
                {'frida', 'bob'},
                bankroll_repo=MagicMock(),
                chip_ledger_repo=MagicMock(),
                sandbox_id='sb',
                now=None,
                table_id='cash-table-200-001',
                stake_repo=stake_repo,
                relationship_repo=MagicMock(),
                personality_repo=MagicMock(),
            )

        # frida's stake settled at her leave stack ($43.6k) and table.
        settle_pids = {c.args[0]: c.args[1] for c in mock_settle.call_args_list}
        self.assertEqual(settle_pids['frida'], 43600)
        self.assertEqual(mock_settle.call_args_list[0].kwargs['table_id'], 'cash-table-200-001')

        # frida is NOT double-credited her full seat stack (settlement flows
        # already credited the borrower share); bob (no stake) is credited.
        credited = {c.args[1]: c.args[2] for c in mock_credit.call_args_list}
        self.assertEqual(credited, {'bob': 500})
        self.assertEqual(total, 500)

    def test_no_stake_repo_credits_full_chips_unchanged(self):
        # Backward-compat: with no stake_repo, settlement is skipped and the
        # AI is credited its full seat chips (pre-fix behavior).
        changes = [BankrollChange(direction='from_seat', personality_id='frida', amount=43600)]
        result = types.SimpleNamespace(bankroll_changes=changes)
        with patch('cash_mode.bankroll.credit_ai_cash_out') as mock_credit:
            total = _credit_departed_ai_bankrolls(
                result,
                {'frida'},
                bankroll_repo=MagicMock(),
                chip_ledger_repo=MagicMock(),
                sandbox_id='sb',
                now=None,
                table_id='t-1',
            )
        self.assertEqual(total, 43600)
        self.assertEqual(mock_credit.call_count, 1)


class TestPRH4CashNeverRoutesToTournament(unittest.TestCase):
    """Cash games never reach tournament elimination/completion logic.

    Post-unification (step 3) this is a STRUCTURAL guarantee: tournament
    completion runs only for games carrying a `tournament_session`, and cash
    games never have one (cash builders create no session; cold-load only builds
    a session for `not is_cash_game`). The legacy `handle_eliminations` /
    `check_tournament_complete` no-op guards were deleted with `TournamentTracker`.
    Cash-bust routing to the rebuy/sponsor flow is covered by
    `tests/test_cash_mode/test_human_bust_pause.py`.
    """

    def test_single_table_boundary_requires_a_session(self):
        # The dispatch is gated on `tournament_session is not None`; a cash game
        # (no session) is skipped. The boundary itself assumes a session is
        # present, so it must never be invoked without one.
        from flask_app.handlers.single_table_tournament import single_table_hand_boundary

        with self.assertRaises(KeyError):
            single_table_hand_boundary('cash-x', {}, MagicMock(), ['Alice'], None)


if __name__ == '__main__':
    unittest.main()
