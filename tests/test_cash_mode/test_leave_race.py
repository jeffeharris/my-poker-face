"""Regression: `/api/cash/leave` race with `progress_game`.

Pre-fix, `leave_table` did not acquire the per-game lock that
`progress_game` holds during its main loop (including AI LLM calls).
The interleaving that broke the user:

  T0  progress_game holds lock, mid-`run_until` (slow LLM call)
  T1  user clicks Leave → leave_table runs (no lock acquired)
  T2  leave_table deletes the game from memory and DB
  T3  progress_game finishes run_until, calls set_game + save_game
  T4  the (stale) state machine is back in memory and DB

The next `/api/cash/state` call then redirected the player back to the
table they thought they'd left. On a loan-leave this also enabled a
free-money exploit: a second leave returned the full table stack with
no sponsor cut, because the loan had been cleared on the first leave.

This test pins the lock contract: leave_table waits for the per-game
lock and tears the row down even after a concurrent "progress_game"
re-saved it.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from cash_mode.bankroll import PlayerBankrollState
from flask_app import create_app
from poker.repositories import create_repos

pytestmark = [pytest.mark.flask, pytest.mark.integration]


OWNER_ID = "race-test-player"
GAME_ID = "cash-race-test-1"


class _StubPlayer:
    """Minimal player shape that `_leave_table_locked` reads."""
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


def _stub_game_data() -> dict:
    """Build the in-memory game_data shape that leave_table reads.

    cash_table_id=None skips the seat-freeing block (we're not exercising
    cash_tables here — the race is about game_state_service + game_repo).
    """
    return {
        'state_machine': _StubStateMachine([
            _StubPlayer("You", is_human=True, stack=1000),
        ]),
        'cash_mode': True,
        'owner_id': OWNER_ID,
        'cash_personality_ids': {},
        'cash_table_id': None,
        'cash_seat_index': None,
        'messages': [],
        'ai_controllers': {},
    }


class TestLeaveTableRace(unittest.TestCase):
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

    @classmethod
    def tearDownClass(cls):
        try:
            os.unlink(cls.test_db.name)
        except FileNotFoundError:
            pass

    def setUp(self):
        # Each test gets its own client + fresh seeded state.
        self.client = self.app.test_client()

        user = {'id': OWNER_ID, 'name': 'RaceTester'}
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
        # Drop any leftover lock from a prior run so each test starts with
        # a fresh lock object (a `with lock: ...` left in a weird state
        # from a flaky run shouldn't poison the next test).
        game_state_service.game_locks.pop(GAME_ID, None)
        game_state_service.set_game(GAME_ID, _stub_game_data())

    def tearDown(self):
        self.game_state_service.games.pop(GAME_ID, None)
        self.game_state_service.game_locks.pop(GAME_ID, None)
        try:
            self.repos['game_repo'].delete_game(GAME_ID)
        except Exception:
            pass
        self._auth_patcher.stop()
        self._authz_patcher.stop()

    def _row_exists(self, game_id: str) -> bool:
        with sqlite3.connect(self.test_db.name) as conn:
            cur = conn.execute(
                "SELECT 1 FROM games WHERE game_id = ?", (game_id,),
            )
            return cur.fetchone() is not None

    def test_leave_blocks_while_progress_game_holds_lock(self):
        """leave_table must wait for the per-game lock before tearing down.

        Acquires the lock from a "saboteur" thread (standing in for
        progress_game mid-iteration), then POSTs /api/cash/leave from
        another thread and verifies the request is still in flight after
        a short pause. Pre-fix, the route ran without acquiring the lock
        and would complete immediately.
        """
        lock = self.game_state_service.get_game_lock(GAME_ID)
        lock_held = threading.Event()
        release_lock = threading.Event()

        def hold_lock():
            lock.acquire()
            try:
                lock_held.set()
                release_lock.wait(timeout=5)
            finally:
                lock.release()

        holder = threading.Thread(target=hold_lock)
        holder.start()
        assert lock_held.wait(timeout=2), "saboteur never acquired lock"

        leave_response: list = []

        def call_leave():
            r = self.client.post('/api/cash/leave')
            leave_response.append(r)

        leaver = threading.Thread(target=call_leave)
        leaver.start()

        # Pre-fix the request would complete in well under 300ms;
        # post-fix it must still be blocking on the lock.
        time.sleep(0.3)
        self.assertTrue(
            leaver.is_alive(),
            "leave_table returned without acquiring the per-game lock — "
            "progress_game can resurrect the row between the deletes "
            "and a follow-up state read.",
        )
        self.assertEqual(leave_response, [])

        release_lock.set()
        holder.join(timeout=5)
        leaver.join(timeout=5)

        self.assertEqual(len(leave_response), 1)
        self.assertEqual(leave_response[0].status_code, 200)
        self.assertIsNone(self.game_state_service.get_game(GAME_ID))

    def test_leave_cleans_up_row_resurrected_under_the_lock(self):
        """A concurrent save under the lock must not survive the leave.

        Simulates the exact bug: progress_game holds the lock, leave is
        invoked and blocks, progress_game (the saboteur here) saves the
        row to the DB just before releasing the lock. Post-fix, leave
        then acquires the lock and tears down the resurrected row.
        """
        lock = self.game_state_service.get_game_lock(GAME_ID)
        lock_held = threading.Event()
        proceed = threading.Event()

        def saboteur():
            lock.acquire()
            try:
                lock_held.set()
                proceed.wait(timeout=5)
                # Stand in for progress_game's save_game by writing
                # directly. We don't need a real serialized state — only
                # the row's existence matters for `_find_active_cash_game_id`'s
                # DB fallback.
                with sqlite3.connect(self.test_db.name) as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO games ("
                        "game_id, phase, num_players, pot_size, "
                        "game_state_json, owner_id, owner_name"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (GAME_ID, 'PRE_FLOP', 1, 0.0, '{}', OWNER_ID, 'RaceTester'),
                    )
                    conn.commit()
            finally:
                lock.release()

        sab = threading.Thread(target=saboteur)
        sab.start()
        assert lock_held.wait(timeout=2), "saboteur never acquired lock"

        leave_response: list = []

        def call_leave():
            r = self.client.post('/api/cash/leave')
            leave_response.append(r)

        leaver = threading.Thread(target=call_leave)
        leaver.start()

        # Give leave_table a moment to enter the blocking lock acquire.
        time.sleep(0.2)
        self.assertTrue(
            leaver.is_alive(),
            "leave_table did not block on the per-game lock",
        )

        # Let the saboteur save the row and release.
        proceed.set()
        sab.join(timeout=5)
        leaver.join(timeout=5)

        self.assertEqual(len(leave_response), 1)
        self.assertEqual(leave_response[0].status_code, 200)
        self.assertIsNone(self.game_state_service.get_game(GAME_ID))
        self.assertFalse(
            self._row_exists(GAME_ID),
            "leave_table left a resurrected row in `games` — the row "
            "would surface via `/api/cash/state` and redirect the user "
            "back to the table.",
        )


if __name__ == '__main__':
    unittest.main()
