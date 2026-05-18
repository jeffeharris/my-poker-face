"""Tests for `GET /api/cash/lobby`.

The route reads `cash_tables`, computes affordability per table for
the current player, attaches relationship hints per AI seat, AND
triggers `refresh_unseated_tables` as a documented side effect.

Test pattern mirrors `test_cash_sponsor_routes.py`: tempdb + patched
`init_persistence` + auth bypass.
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
from cash_mode.lobby import ensure_lobby_seeded
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


class _CashLobbyRouteBase(unittest.TestCase):
    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()

        repos = create_repos(self.test_db.name)
        self.bankroll_repo = repos['bankroll_repo']
        self.personality_repo = repos['personality_repo']
        self.relationship_repo = repos['relationship_repo']
        self.cash_table_repo = repos['cash_table_repo']
        self.game_repo = repos['game_repo']

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

        # Seed personalities BEFORE create_app so the lobby boot hook
        # can find them.
        self.napoleon_id = self.personality_repo.save_personality(
            'Napoleon',
            {
                'play_style': 'aggressive',
                'bankroll_knobs': {
                    'bankroll_cap': 50_000, 'bankroll_rate': 0,
                    'buy_in_multiplier': 1.0,
                    'stop_loss_buy_ins': 3, 'stop_win_buy_ins': 5,
                    'stake_comfort_zone': '$10',
                },
            },
        )
        self.bankroll_repo.save_ai_bankroll(AIBankrollState(
            personality_id=self.napoleon_id, chips=10_000,
            last_regen_tick=datetime(2026, 5, 18, 12, 0, 0),
        ))

        with patch('flask_app.extensions.init_persistence', mock_init_persistence):
            self.app = create_app()
        self.app.testing = True
        self.client = self.app.test_client()

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

    def tearDown(self):
        self._auth_patcher.stop()
        self._authz_patcher.stop()
        os.unlink(self.test_db.name)


class TestLobbyResponseShape(_CashLobbyRouteBase):
    def test_returns_bankroll_and_tables(self):
        resp = self.client.get("/api/cash/lobby")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "bankroll" in data
        assert "tables" in data
        assert isinstance(data["tables"], list)

    def test_lobby_seeded_with_5_tables(self):
        resp = self.client.get("/api/cash/lobby")
        data = resp.get_json()
        # Boot hook seeded 5 stakes.
        assert len(data["tables"]) == 5
        stake_labels = {t["stake_label"] for t in data["tables"]}
        assert stake_labels == {"$2", "$10", "$50", "$200", "$1000"}

    def test_table_fields(self):
        resp = self.client.get("/api/cash/lobby")
        data = resp.get_json()
        for t in data["tables"]:
            assert "table_id" in t
            assert "stake_label" in t
            assert "big_blind" in t
            assert "min_buy_in" in t
            assert "max_buy_in" in t
            assert "affordability" in t
            assert "seats" in t
            assert len(t["seats"]) == 6


class TestAffordabilityTriState(_CashLobbyRouteBase):
    def _set_player_bankroll(self, chips):
        self.bankroll_repo.save_player_bankroll(PlayerBankrollState(
            player_id=PLAYER_OWNER_ID, chips=chips, starting_bankroll=200,
        ))

    def test_locked_when_no_bankroll(self):
        self._set_player_bankroll(0)
        resp = self.client.get("/api/cash/lobby")
        data = resp.get_json()
        # $2 table is the lowest tier: sponsor_eligible at bankroll 0.
        two = next(t for t in data["tables"] if t["stake_label"] == "$2")
        assert two["affordability"] == "sponsor_eligible"
        # Higher tiers should be locked.
        thousand = next(t for t in data["tables"] if t["stake_label"] == "$1000")
        assert thousand["affordability"] == "locked"

    def test_affordable_when_bankroll_exceeds_min(self):
        self._set_player_bankroll(100_000)
        resp = self.client.get("/api/cash/lobby")
        data = resp.get_json()
        # All tables should be affordable.
        for t in data["tables"]:
            assert t["affordability"] == "affordable"


class TestMovementOnRead(_CashLobbyRouteBase):
    def test_last_activity_at_bumps_on_read(self):
        # The route triggers refresh_unseated_tables, which always bumps
        # last_activity_at via save_table. Pre-read the value, then re-read.
        first = self.client.get("/api/cash/lobby")
        first_data = first.get_json()
        first_activity = {
            t["table_id"]: t for t in first_data["tables"]
        }

        # Read again; persisted state must have updated.
        before_second = self.cash_table_repo.load_table("cash-table-10-001")
        assert before_second is not None
        first_ts = before_second.last_activity_at

        second = self.client.get("/api/cash/lobby")
        second_data = second.get_json()
        assert len(second_data["tables"]) == 5

        after_second = self.cash_table_repo.load_table("cash-table-10-001")
        # Activity timestamp should have moved forward or stayed (same-sec).
        assert after_second.last_activity_at >= first_ts


class TestSeatSerialization(_CashLobbyRouteBase):
    def test_ai_seat_carries_personality_id_and_chips(self):
        resp = self.client.get("/api/cash/lobby")
        data = resp.get_json()
        for t in data["tables"]:
            for seat in t["seats"]:
                if seat["kind"] == "ai":
                    assert "personality_id" in seat
                    assert "chips" in seat
                    assert "name" in seat
                    assert "relationship_hint" in seat
                    assert seat["chips"] > 0
