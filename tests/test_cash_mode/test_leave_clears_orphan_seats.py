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
            OWNER_ID,
            sandbox_repo=cls.repos['sandbox_repo'],
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
            'poker.authorization.authorization_service',
            authz,
        )
        self._authz_patcher.start()
        auth_mock = MagicMock()
        auth_mock.get_current_user.return_value = user
        self._auth_patcher = patch(
            'flask_app.extensions.auth_manager',
            auth_mock,
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
            table_id,
            sandbox_id=self.sandbox_id,
        )
        return [
            i
            for i, s in enumerate(table.seats)
            if s.get("kind") == "human" and s.get("personality_id") == OWNER_ID
        ]

    def _stub_game_data(self, table_id: str, seat_index: int) -> dict:
        return {
            'state_machine': _StubStateMachine(
                [
                    _StubPlayer("You", is_human=True, stack=1000),
                ]
            ),
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
        seats[5] = human_slot(OWNER_ID, 800)  # orphan from earlier
        self.repos['cash_table_repo'].save_table(
            CashTableState(
                table_id=table_id,
                stake_label="$200",
                seats=seats,
            ),
            sandbox_id=self.sandbox_id,
        )

        self.game_state_service.set_game(
            GAME_ID,
            self._stub_game_data(table_id, seat_index=1),
        )

        resp = self.client.post('/api/cash/leave')
        self.assertEqual(resp.status_code, 200)

        seated = self._seated_indices(table_id)
        self.assertEqual(
            seated,
            [],
            f"orphan human seat survived at indices {seated} — lobby "
            "would still render the user as seated",
        )

    def test_leave_cold_session_settles_instead_of_ghosting(self):
        """A DB-only session (not in memory) must be cold-loaded and
        SETTLED on leave — the player's real table chips return to
        bankroll — not zeroed by the ghost-cleanup path.

        Pre-hardening, the memory-miss branch always took ghost cleanup
        (chips_at_table=0), so a player whose game fell out of memory on
        a server restart lost every chip at the table on leave. The
        warm-load (`_warm_cash_game_for_leave`) rehydrates just enough
        to run the normal settlement. We patch the helper to inject a
        stub state machine (a real PokerStateMachine isn't needed to
        prove the route now settles vs zeroes).
        """
        from flask_app.services import game_state_service

        # Game is NOT in memory — this is the cold path.
        game_state_service.games.pop(GAME_ID, None)

        start_chips = self.repos['bankroll_repo'].load_player_bankroll(OWNER_ID).chips

        def _fake_warm(game_id, *, owner_id, persisted_cash_session=None):
            data = {
                'state_machine': _StubStateMachine(
                    [_StubPlayer("You", is_human=True, stack=1000)]
                ),
                'cash_mode': True,
                'owner_id': owner_id,
                'cash_personality_ids': {},
                'cash_table_id': None,
                'cash_seat_index': None,
                'sandbox_id': self.sandbox_id,
                'messages': [],
                'ai_controllers': {},
            }
            game_state_service.set_game(game_id, data)
            return data

        with patch(
            'flask_app.routes.cash_routes._find_active_cash_game_id',
            return_value=GAME_ID,
        ), patch(
            'flask_app.routes.cash_routes._warm_cash_game_for_leave',
            side_effect=_fake_warm,
        ):
            resp = self.client.post('/api/cash/leave')

        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(
            body['chips_at_table'],
            1000,
            "cold session was ghost-cleaned (chips zeroed) instead of "
            "settled with the real stack",
        )
        self.assertEqual(body['returned_chips'], 1000)
        end_chips = self.repos['bankroll_repo'].load_player_bankroll(OWNER_ID).chips
        self.assertEqual(
            end_chips,
            start_chips + 1000,
            "table chips were not returned to bankroll on cold leave",
        )

    def test_leave_on_finalized_session_does_not_resettle(self):
        """Idempotency guard (T2.1): a leave on an already-finalized
        cash_sessions row must NOT re-credit any bankroll.

        This is the 2026-05-28 phantom-chip incident in miniature: a
        session was settled once (ended_at set), then the game got
        resurrected into memory and a second leave ran — the stake was
        already non-active, so settlement fell into the no-stake branch
        and refunded the full table stack a SECOND time. With the guard,
        the second leave is cleanup-only: bankroll is untouched and
        chips_at_table comes back 0.
        """
        from datetime import datetime

        from cash_mode.cash_sessions import CashSession
        from flask_app.services import game_state_service

        # A finalized session row for this owner.
        self.repos['cash_session_repo'].create(
            CashSession(
                session_id=GAME_ID,
                owner_id=OWNER_ID,
                sandbox_id=self.sandbox_id,
                stake_label="$200",
                is_staked=False,
                stake_id=None,
                initial_buy_in=8000,
                total_buy_in=8000,
                sponsor_principal=0,
                cash_table_id=None,
                cash_seat_index=None,
                started_at=datetime.utcnow(),
                ended_at=datetime.utcnow(),  # ALREADY finalized
                final_chips_at_table=12000,
                sponsor_repaid=0,
                player_take_home=12000,
                closed_status="left",
            )
        )

        # Simulate a resurrected in-memory game with a fat stack — if the
        # guard fails, the no-stake branch would credit this to bankroll.
        game_state_service.set_game(
            GAME_ID,
            {
                'state_machine': _StubStateMachine(
                    [_StubPlayer("You", is_human=True, stack=12000)]
                ),
                'cash_mode': True,
                'owner_id': OWNER_ID,
                'cash_personality_ids': {},
                'cash_table_id': None,
                'cash_seat_index': None,
                'sandbox_id': self.sandbox_id,
                'messages': [],
                'ai_controllers': {},
            },
        )

        start_chips = self.repos['bankroll_repo'].load_player_bankroll(OWNER_ID).chips

        with patch(
            'flask_app.routes.cash_routes._find_active_cash_game_id',
            return_value=GAME_ID,
        ):
            resp = self.client.post('/api/cash/leave')

        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(
            body['chips_at_table'],
            0,
            "finalized session re-settled — the double-settle guard "
            "didn't fire",
        )
        end_chips = self.repos['bankroll_repo'].load_player_bankroll(OWNER_ID).chips
        self.assertEqual(
            end_chips,
            start_chips,
            f"bankroll changed ({start_chips} -> {end_chips}) on a leave "
            "of an already-finalized session — phantom chips injected",
        )

    def test_leave_falls_back_to_ghost_when_warm_load_fails(self):
        """When the DB row can't be loaded (corrupt / already gone), the
        leave path must still fall back to ghost cleanup and return a
        coherent ended-session response rather than 500.
        """
        from flask_app.services import game_state_service

        game_state_service.games.pop(GAME_ID, None)

        with patch(
            'flask_app.routes.cash_routes._find_active_cash_game_id',
            return_value=GAME_ID,
        ), patch(
            'flask_app.routes.cash_routes._warm_cash_game_for_leave',
            return_value=None,
        ):
            resp = self.client.post('/api/cash/leave')

        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body['session_ended'])
        self.assertEqual(body['chips_at_table'], 0)

    def test_leave_frees_ghost_seat_when_cash_table_id_none(self):
        """A session with cash_table_id=None (the sponsor-flow gap)
        must still free the player's lobby seat on leave.

        Regression for the nested-if bug: the cross-table ghost-seat
        sweep used to live inside `if cash_table_id is not None:`, so a
        sponsor session — which wrote cash_sessions.cash_table_id=NULL
        — skipped the sweep entirely and stranded the human seat on the
        lobby table. With game_data['cash_table_id'] = None AND no
        persisted cash_sessions row to fall back on, the seat-specific
        free can't run; the now-unconditional sweep is the only thing
        that frees the seat.
        """
        table_id = "cash-table-200-001"
        seats = [open_slot()] * 6
        seats[3] = human_slot(OWNER_ID, 900)
        self.repos['cash_table_repo'].save_table(
            CashTableState(
                table_id=table_id,
                stake_label="$200",
                seats=seats,
            ),
            sandbox_id=self.sandbox_id,
        )

        game_data = self._stub_game_data(table_id, seat_index=3)
        # Simulate the sponsor-session gap: the game knows nothing about
        # which lobby table/seat it occupies.
        game_data['cash_table_id'] = None
        game_data['cash_seat_index'] = None
        self.game_state_service.set_game(GAME_ID, game_data)

        resp = self.client.post('/api/cash/leave')
        self.assertEqual(resp.status_code, 200)

        self.assertEqual(
            self._seated_indices(table_id),
            [],
            "ghost human seat survived a leave whose session had "
            "cash_table_id=None — the unconditional sweep didn't run",
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
                table_id=active_table,
                stake_label="$200",
                seats=active_seats,
            ),
            sandbox_id=self.sandbox_id,
        )

        orphan_seats = [open_slot()] * 6
        orphan_seats[0] = human_slot(OWNER_ID, 500)
        self.repos['cash_table_repo'].save_table(
            CashTableState(
                table_id=orphan_table,
                stake_label="$50",
                seats=orphan_seats,
            ),
            sandbox_id=self.sandbox_id,
        )

        self.game_state_service.set_game(
            GAME_ID,
            self._stub_game_data(active_table, seat_index=2),
        )

        resp = self.client.post('/api/cash/leave')
        self.assertEqual(resp.status_code, 200)

        self.assertEqual(self._seated_indices(active_table), [])
        self.assertEqual(
            self._seated_indices(orphan_table),
            [],
            "orphan human seat on a different table survived — the "
            "ghost-seat sweep failed to reap it on leave",
        )


if __name__ == '__main__':
    unittest.main()
