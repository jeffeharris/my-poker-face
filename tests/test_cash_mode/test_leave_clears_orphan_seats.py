"""Regression: `/api/cash/leave` must free orphan human seats too.

Pre-fix, the seat-rebuild loop in `_leave_table_locked` only freed the
seat at the *current* session's `cash_seat_index`. A human seat left
behind by an earlier session that didn't close cleanly (back-arrow,
browser close, crashed Flask) survived in `cash_tables.seats_json`.
On the next `/api/cash/lobby` read the user rendered as still seated
at that ghost slot — the recurring "I left but it still shows me at
the table" bug.

This test pins both halves of the fix:
  - Same table, different index: an orphan human seat owned by the
    user at a different index than the current session must be freed.
  - Different table: a stale human seat on another `cash_tables` row
    in the same sandbox must be freed via the ghost-seat sweep.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from cash_mode.bankroll import PlayerBankrollState
from cash_mode.tables import CashTableState, human_slot, open_slot
from flask_app import create_app
from flask_app.services.sandbox_resolver import resolve_default_sandbox_for
from poker.repositories import create_repos

pytestmark = [pytest.mark.flask, pytest.mark.integration]


OWNER_ID = "orphan-seat-player"
GAME_ID = "cash-orphan-seat-test"


class _StubPlayer:
    def __init__(self, name: str, *, is_human: bool, stack: int):
        self.name = name
        self.is_human = is_human
        self.stack = stack


class _StubGameState:
    def __init__(self, players):
        self.players = players


class _StubStateMachine:
    def __init__(self, players):
        self.game_state = _StubGameState(players)


class TestLeaveClearsOrphanSeats(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        cls.test_db.close()
        cls.repos = create_repos(cls.test_db.name)

        def mock_init_persistence():
            import flask_app.extensions as ext
            for key, repo in cls.repos.items():
                if key == 'db_path':
                    ext.persistence_db_path = repo
                    continue
                setattr(ext, key, repo)

        with patch('flask_app.extensions.init_persistence', mock_init_persistence):
            cls.app = create_app()
        cls.app.testing = True
        cls.sandbox_id = resolve_default_sandbox_for(
            OWNER_ID, sandbox_repo=cls.repos['sandbox_repo'],
        )

    @classmethod
    def tearDownClass(cls):
        try:
            os.unlink(cls.test_db.name)
        except FileNotFoundError:
            pass

    def setUp(self):
        self.client = self.app.test_client()

        user = {'id': OWNER_ID, 'name': 'OrphanTester'}
        authz = MagicMock()
        authz.auth_manager.get_current_user.return_value = user
        authz.has_permission.return_value = True
        self._authz_patcher = patch(
            'poker.authorization.authorization_service', authz,
        )
        self._authz_patcher.start()
        auth_mock = MagicMock()
        auth_mock.get_current_user.return_value = user
        self._auth_patcher = patch(
            'flask_app.extensions.auth_manager', auth_mock,
        )
        self._auth_patcher.start()

        self.repos['bankroll_repo'].save_player_bankroll(
            PlayerBankrollState(
                player_id=OWNER_ID,
                chips=5000,
                starting_bankroll=5000,
            ),
        )

        from flask_app.services import game_state_service
        self.game_state_service = game_state_service
        game_state_service.game_locks.pop(GAME_ID, None)

    def tearDown(self):
        self.game_state_service.games.pop(GAME_ID, None)
        self.game_state_service.game_locks.pop(GAME_ID, None)
        try:
            self.repos['game_repo'].delete_game(GAME_ID)
        except Exception:
            pass
        self._auth_patcher.stop()
        self._authz_patcher.stop()

    def _seated_indices(self, table_id: str) -> list[int]:
        table = self.repos['cash_table_repo'].load_table(
            table_id, sandbox_id=self.sandbox_id,
        )
        return [
            i for i, s in enumerate(table.seats)
            if s.get("kind") == "human"
            and s.get("personality_id") == OWNER_ID
        ]

    def _stub_game_data(self, table_id: str, seat_index: int) -> dict:
        return {
            'state_machine': _StubStateMachine([
                _StubPlayer("You", is_human=True, stack=1000),
            ]),
            'cash_mode': True,
            'owner_id': OWNER_ID,
            'cash_personality_ids': {},
            'cash_table_id': table_id,
            'cash_seat_index': seat_index,
            'sandbox_id': self.sandbox_id,
            'messages': [],
            'ai_controllers': {},
        }

    def test_leave_frees_orphan_seat_at_same_table(self):
        """Active seat at idx 1 + orphan seat at idx 5 on the same
        table → leave must free both.

        Models the production bug: the user sat at this table earlier
        in the day, the session never closed cleanly, the persisted
        human seat survived at idx 5. Today the user sits at idx 1,
        plays, leaves. Pre-fix only idx 1 was freed; idx 5 lingered.
        """
        table_id = "cash-table-200-001"
        seats = [open_slot()] * 6
        seats[1] = human_slot(OWNER_ID, 1000)  # current session seat
        seats[5] = human_slot(OWNER_ID, 800)   # orphan from earlier
        self.repos['cash_table_repo'].save_table(
            CashTableState(
                table_id=table_id, stake_label="$200", seats=seats,
            ),
            sandbox_id=self.sandbox_id,
        )

        self.game_state_service.set_game(
            GAME_ID, self._stub_game_data(table_id, seat_index=1),
        )

        resp = self.client.post('/api/cash/leave')
        self.assertEqual(resp.status_code, 200)

        seated = self._seated_indices(table_id)
        self.assertEqual(
            seated, [],
            f"orphan human seat survived at indices {seated} — lobby "
            "would still render the user as seated",
        )

    def test_leave_frees_orphan_seat_at_different_table(self):
        """Active seat at table A + orphan seat at table B (same
        sandbox) → leave A must free both.

        Cross-table case: the user previously sat at the $50 table,
        exited uncleanly, then today sits at the $200 table and leaves
        cleanly. The $50 orphan is on a different `cash_tables` row
        entirely and is reaped by the ghost-seat sweep.
        """
        active_table = "cash-table-200-001"
        orphan_table = "cash-table-50-001"

        active_seats = [open_slot()] * 6
        active_seats[2] = human_slot(OWNER_ID, 1000)
        self.repos['cash_table_repo'].save_table(
            CashTableState(
                table_id=active_table, stake_label="$200",
                seats=active_seats,
            ),
            sandbox_id=self.sandbox_id,
        )

        orphan_seats = [open_slot()] * 6
        orphan_seats[0] = human_slot(OWNER_ID, 500)
        self.repos['cash_table_repo'].save_table(
            CashTableState(
                table_id=orphan_table, stake_label="$50",
                seats=orphan_seats,
            ),
            sandbox_id=self.sandbox_id,
        )

        self.game_state_service.set_game(
            GAME_ID, self._stub_game_data(active_table, seat_index=2),
        )

        resp = self.client.post('/api/cash/leave')
        self.assertEqual(resp.status_code, 200)

        self.assertEqual(self._seated_indices(active_table), [])
        self.assertEqual(
            self._seated_indices(orphan_table), [],
            "orphan human seat on a different table survived — the "
            "ghost-seat sweep failed to reap it on leave",
        )


if __name__ == '__main__':
    unittest.main()
