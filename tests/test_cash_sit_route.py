"""Tests for `POST /api/cash/sit` — the lobby-v1.5 sit-down route.

Replaces `/api/cash/start` for lobby flows. Validates table existence,
seat openness, affordability + sponsor-eligibility branching, and
double-sit rejection. The roster used to build the game comes from
the persisted `cash_tables` row, not a fresh sample.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from cash_mode.bankroll import AIBankrollState, PlayerBankrollState
from cash_mode.tables import CashTableState, ai_slot, open_slot
from flask_app import create_app
from poker.repositories import create_repos

pytestmark = [pytest.mark.flask, pytest.mark.integration]


PLAYER_OWNER_ID = "test-player-1"


def _mock_authorization_service(user, has_admin_permission=True):
    authz = MagicMock()
    authz.auth_manager.get_current_user.return_value = user
    authz.has_permission.return_value = has_admin_permission
    return authz


def _seat_napoleon():
    """Build a $10 table with napoleon + 5 open seats."""
    seats = [
        ai_slot("napoleon", 400),
        open_slot(),
        open_slot(),
        open_slot(),
        open_slot(),
        open_slot(),
    ]
    return CashTableState(
        table_id="cash-table-10-001",
        stake_label="$10",
        seats=seats,
    )


class _CashSitRouteBase(unittest.TestCase):
    """Shared tempdb across all tests in the file.

    Module-level repo binding in `flask_app.routes.game_routes` (the
    `prompt_preset_repo` import at module load) captures the FIRST
    create_app's tempdb path. Subsequent setUps creating fresh tempdbs
    leave the old one dangling, and any code that touches
    `prompt_preset_repo` crashes with "no such table".

    Workaround: use `setUpClass`/`tearDownClass` so all tests in this
    module share the same tempdb + app instance. Tests must clean up
    after themselves (reset bankroll, reset seats) to avoid cross-test
    pollution.
    """

    @classmethod
    def setUpClass(cls):
        cls.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        cls.test_db.close()

        repos = create_repos(cls.test_db.name)
        cls.repos = repos
        cls.bankroll_repo = repos['bankroll_repo']
        cls.personality_repo = repos['personality_repo']
        cls.cash_table_repo = repos['cash_table_repo']

        cls.napoleon_id = cls.personality_repo.save_personality(
            'Napoleon',
            {
                'play_style': 'aggressive',
                'bankroll_knobs': {
                    'bankroll_cap': 5_000_000, 'bankroll_rate': 0,
                    'buy_in_multiplier': 1.0,
                    'stop_loss_buy_ins': 3, 'stop_win_buy_ins': 5,
                    'stake_comfort_zone': '$10',
                },
            },
        )
        cls.bankroll_repo.save_ai_bankroll(AIBankrollState(
            personality_id=cls.napoleon_id, chips=4_000_000,
            last_regen_tick=datetime(2026, 5, 18, 12, 0, 0),
        ))
        for i in range(30):
            pid = cls.personality_repo.save_personality(
                f'AI {i}',
                {
                    'bankroll_knobs': {
                        'bankroll_cap': 5_000_000, 'bankroll_rate': 0,
                        'buy_in_multiplier': 1.0,
                        'stop_loss_buy_ins': 3, 'stop_win_buy_ins': 5,
                        'stake_comfort_zone': '$10',
                    },
                },
            )
            cls.bankroll_repo.save_ai_bankroll(AIBankrollState(
                personality_id=pid, chips=4_000_000,
                last_regen_tick=datetime(2026, 5, 18, 12, 0, 0),
            ))

        def mock_init_persistence():
            import flask_app.extensions as ext
            for key in (
                'game_repo', 'user_repo', 'settings_repo', 'personality_repo',
                'experiment_repo', 'prompt_capture_repo',
                'decision_analysis_repo', 'prompt_preset_repo',
                'capture_label_repo', 'replay_experiment_repo',
                'llm_repo', 'guest_tracking_repo', 'hand_history_repo',
                'tournament_repo', 'coach_repo', 'relationship_repo',
                'bankroll_repo', 'cash_table_repo',
            ):
                if key in repos:
                    setattr(ext, key, repos[key])
            ext.persistence_db_path = repos['db_path']

        with patch('flask_app.extensions.init_persistence', mock_init_persistence):
            cls.app = create_app()
        cls.app.testing = True
        cls.client = cls.app.test_client()

        # game_routes.py captures `prompt_preset_repo` at module-import
        # time (this is the documented flake in handoff caveats). For
        # the test harness — where multiple files create fresh tempdbs —
        # we explicitly rebind the module-level attribute to OUR repo so
        # the route doesn't query a closed connection.
        import flask_app.routes.game_routes as _gr
        for key in (
            'prompt_preset_repo', 'game_repo', 'user_repo',
            'guest_tracking_repo', 'llm_repo', 'tournament_repo',
            'hand_history_repo', 'decision_analysis_repo',
            'capture_label_repo', 'coach_repo', 'relationship_repo',
            'persistence_db_path', 'personality_repo',
        ):
            if key in repos:
                setattr(_gr, key, repos[key])
            elif key == 'persistence_db_path':
                setattr(_gr, key, repos['db_path'])

    @classmethod
    def tearDownClass(cls):
        try:
            os.unlink(cls.test_db.name)
        except FileNotFoundError:
            pass

    def setUp(self):
        user = {'id': PLAYER_OWNER_ID, 'name': 'Tester'}
        self._authz_patcher = patch(
            'poker.authorization.authorization_service',
            _mock_authorization_service(user=user),
        )
        self._authz_patcher.start()
        auth_mock = MagicMock()
        auth_mock.get_current_user.return_value = user
        self._auth_patcher = patch(
            'flask_app.extensions.auth_manager',
            auth_mock,
        )
        self._auth_patcher.start()

        # Reset game_state_service for a clean slate per test.
        from flask_app.services import game_state_service
        for gid in list(game_state_service.games.keys()):
            game_state_service.delete_game(gid)

    def tearDown(self):
        self._auth_patcher.stop()
        self._authz_patcher.stop()
        # Clear any cash sessions left over from this test.
        from flask_app.services import game_state_service
        for gid in list(game_state_service.games.keys()):
            game_state_service.delete_game(gid)
        # Re-seed the lobby to undo any seat mutations this test made.
        from cash_mode.lobby import ensure_lobby_seeded
        # Wipe and reseed: drop every table, then run the boot seeder.
        with self.cash_table_repo._get_connection() as conn:
            conn.execute("DELETE FROM cash_tables")
            conn.execute("DELETE FROM cash_idle_pool")
        ensure_lobby_seeded(
            cash_table_repo=self.cash_table_repo,
            personality_repo=self.personality_repo,
            bankroll_repo=self.bankroll_repo,
        )
        # Reset player bankroll.
        self.bankroll_repo.save_player_bankroll(PlayerBankrollState(
            player_id=PLAYER_OWNER_ID, chips=200, starting_bankroll=200,
        ))


class TestSitAll(_CashSitRouteBase):
    def test_missing_table_id_400(self):
        resp = self.client.post("/api/cash/sit", json={"seat_index": 1})
        assert resp.status_code == 400

    def test_unknown_table_id_404(self):
        resp = self.client.post("/api/cash/sit", json={
            "table_id": "does-not-exist", "seat_index": 1,
        })
        assert resp.status_code == 404

    def test_seat_out_of_range_400(self):
        # Use the lobby-seeded $2 table.
        resp = self.client.post("/api/cash/sit", json={
            "table_id": "cash-table-2-001", "seat_index": 99,
        })
        assert resp.status_code == 400

    def test_occupied_seat_409(self):
        # Place napoleon at seat 0 and try to sit there.
        table = self.cash_table_repo.load_table("cash-table-2-001")
        new_seats = list(table.seats)
        new_seats[0] = ai_slot(self.napoleon_id, 80)
        self.cash_table_repo.save_table(CashTableState(
            table_id=table.table_id,
            stake_label=table.stake_label,
            seats=new_seats,
        ))
        resp = self.client.post("/api/cash/sit", json={
            "table_id": "cash-table-2-001", "seat_index": 0,
        })
        assert resp.status_code == 409


    # --- Affordability tests (rolled into the same class to avoid
    # per-class setUpClass creating multiple tempdbs).

    def _set_bankroll(self, chips):
        self.bankroll_repo.save_player_bankroll(PlayerBankrollState(
            player_id=PLAYER_OWNER_ID, chips=chips, starting_bankroll=200,
        ))

    def test_unaffordable_at_lowest_tier_returns_sponsor_required(self):
        # Bankroll 0; sponsor-eligible at $2 (lowest tier).
        self._set_bankroll(0)
        # Find an open seat on $2 table.
        table = self.cash_table_repo.load_table("cash-table-2-001")
        open_idx = next(
            i for i, s in enumerate(table.seats) if s["kind"] == "open"
        )
        resp = self.client.post("/api/cash/sit", json={
            "table_id": "cash-table-2-001", "seat_index": open_idx,
        })
        assert resp.status_code == 402
        data = resp.get_json()
        assert data.get("requires_sponsor") is True
        assert data.get("stake_label") == "$2"
        assert data.get("bankroll") == 0

    def test_unaffordable_at_high_tier_400(self):
        # Bankroll 0; $1000 table is locked (not sponsor-eligible).
        self._set_bankroll(0)
        table = self.cash_table_repo.load_table("cash-table-1000-001")
        open_idx = next(
            i for i, s in enumerate(table.seats) if s["kind"] == "open"
        )
        resp = self.client.post("/api/cash/sit", json={
            "table_id": "cash-table-1000-001", "seat_index": open_idx,
        })
        assert resp.status_code == 400


    # --- Happy-path + double-sit combined into one method since
    # the tempdb is class-scoped and we want the second sit-attempt
    # to see the first sit's session still in game_state_service.

    def test_happy_path_and_double_sit(self):
        # Phase 1: happy-path sit.
        self.bankroll_repo.save_player_bankroll(PlayerBankrollState(
            player_id=PLAYER_OWNER_ID, chips=10_000, starting_bankroll=10_000,
        ))
        table = self.cash_table_repo.load_table("cash-table-10-001")
        open_idx = next(
            i for i, s in enumerate(table.seats) if s["kind"] == "open"
        )
        resp = self.client.post("/api/cash/sit", json={
            "table_id": "cash-table-10-001",
            "seat_index": open_idx,
            "buy_in": 400,
        })
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data.get("table_id") == "cash-table-10-001"
        assert data.get("seat_index") == open_idx
        assert data.get("game_id", "").startswith("cash-")

        # Persisted table: human seat at that index.
        updated = self.cash_table_repo.load_table("cash-table-10-001")
        assert updated.seats[open_idx]["kind"] == "human"
        assert updated.seats[open_idx]["personality_id"] == PLAYER_OWNER_ID
        assert updated.seats[open_idx]["chips"] == 400

        # Player bankroll debited.
        bankroll = self.bankroll_repo.load_player_bankroll(PLAYER_OWNER_ID)
        assert bankroll.chips == 9600

        # Phase 2: double-sit attempt → 409.
        table2 = self.cash_table_repo.load_table("cash-table-50-001")
        open_idx2 = next(
            i for i, s in enumerate(table2.seats) if s["kind"] == "open"
        )
        resp2 = self.client.post("/api/cash/sit", json={
            "table_id": "cash-table-50-001",
            "seat_index": open_idx2,
            "buy_in": 2000,
        })
        assert resp2.status_code == 409
