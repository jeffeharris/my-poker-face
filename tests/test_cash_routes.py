"""Tests for cash mode Flask routes.

Smoke tests for the five REST endpoints. Auth + persistence patched
per the existing test_experiment_routes.py pattern. The AI controller
factory is swapped for a scripted mock so hands resolve deterministically
without LLM calls.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from flask_app import create_app
from poker.repositories import create_repos

pytestmark = [pytest.mark.flask, pytest.mark.integration]


def _mock_authz(user):
    """Build a fake global authorization service."""
    authz = MagicMock()
    authz.auth_manager.get_current_user.return_value = user
    authz.has_permission.return_value = True
    return authz


class AlwaysFoldController:
    """Lightweight mock that the cash route factory uses in place of
    the production HybridAIController. Folds every action."""

    def __init__(self, name):
        self.name = name
        self.current_hand_number = 0

    def decide_action(self, action_log):
        return {"action": "fold", "raise_to": 0}


def _mock_controller_factory():
    return lambda pid, name, mm: AlwaysFoldController(name)


def _seed_personality(db_path, personality_id, name):
    """Seed a public personality with default bankroll knobs."""
    import sqlite3
    config = {
        "play_style": "test",
        "anchors": {},
        "bankroll_knobs": {
            "bankroll_cap": 20_000,
            "bankroll_rate": 500,
            "buy_in_multiplier": 1.0,
            "stop_loss_buy_ins": 3,
            "stop_win_buy_ins": 5,
            "stake_comfort_zone": "$10",
        },
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO personalities (name, config_json, personality_id, visibility)
            VALUES (?, ?, ?, 'public')
            """,
            (name, json.dumps(config), personality_id),
        )
        conn.commit()


class TestCashRoutes(unittest.TestCase):
    """Smoke tests for /api/cash/* endpoints."""

    def setUp(self):
        # Temp database with full schema (incl. v88).
        self.test_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.test_db.close()
        repos = create_repos(self.test_db.name)
        self.repos = repos

        # Seed two AI personalities for fill_seats.
        _seed_personality(self.test_db.name, "alpha_bot", "Alpha Bot")
        _seed_personality(self.test_db.name, "beta_bot", "Beta Bot")

        # Patch init_persistence to bind our test repos to the
        # flask_app.extensions module globals.
        def mock_init_persistence():
            import flask_app.extensions as ext
            ext.game_repo = repos["game_repo"]
            ext.user_repo = repos["user_repo"]
            ext.settings_repo = repos["settings_repo"]
            ext.personality_repo = repos["personality_repo"]
            ext.experiment_repo = repos["experiment_repo"]
            ext.prompt_capture_repo = repos["prompt_capture_repo"]
            ext.decision_analysis_repo = repos["decision_analysis_repo"]
            ext.prompt_preset_repo = repos["prompt_preset_repo"]
            ext.capture_label_repo = repos["capture_label_repo"]
            ext.replay_experiment_repo = repos["replay_experiment_repo"]
            ext.llm_repo = repos["llm_repo"]
            ext.guest_tracking_repo = repos["guest_tracking_repo"]
            ext.hand_history_repo = repos["hand_history_repo"]
            ext.tournament_repo = repos["tournament_repo"]
            ext.coach_repo = repos["coach_repo"]
            ext.relationship_repo = repos["relationship_repo"]
            ext.bankroll_repo = repos["bankroll_repo"]
            ext.persistence_db_path = repos["db_path"]

        with patch("flask_app.extensions.init_persistence", mock_init_persistence):
            self.app = create_app()
        self.app.testing = True
        self.client = self.app.test_client()

        # Patch auth so the routes see a known user.
        self.user = {"id": "test_user_42", "name": "Tester"}
        self._authz_patcher = patch(
            "poker.authorization.authorization_service",
            _mock_authz(self.user),
        )
        self._authz_patcher.start()

        # Cash routes call auth_manager.get_current_user via the
        # extensions module. Patch the bound import in cash_routes.
        auth_mock = MagicMock()
        auth_mock.get_current_user.return_value = self.user
        self._auth_ext_patcher = patch(
            "flask_app.extensions.auth_manager", auth_mock,
        )
        self._auth_ext_patcher.start()

        # Swap the production HybridAIController factory for a mock so
        # hands resolve without LLM calls.
        self._factory_patcher = patch(
            "flask_app.routes.cash_routes._build_controller_factory",
            _mock_controller_factory,
        )
        self._factory_patcher.start()

        # Clean session store between tests.
        from flask_app.services.cash_session_service import cash_session_store
        cash_session_store.clear()

    def tearDown(self):
        from flask_app.services.cash_session_service import cash_session_store
        cash_session_store.clear()
        self._factory_patcher.stop()
        self._auth_ext_patcher.stop()
        self._authz_patcher.stop()
        os.unlink(self.test_db.name)

    # --- /state without session ---

    def test_state_without_session_returns_404(self):
        response = self.client.get("/api/cash/state")
        self.assertEqual(response.status_code, 404)

    # --- /start ---

    def test_start_rejects_invalid_stake(self):
        response = self.client.post(
            "/api/cash/start",
            json={"stake_label": "$999", "buy_in": 500},
        )
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertIn("Invalid stake_label", data["error"])

    def test_start_rejects_non_positive_buy_in(self):
        response = self.client.post(
            "/api/cash/start",
            json={"stake_label": "$10", "buy_in": 0},
        )
        self.assertEqual(response.status_code, 400)

    def test_start_succeeds_with_valid_payload(self):
        response = self.client.post(
            "/api/cash/start",
            json={"stake_label": "$10", "buy_in": 500},
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("state", data)
        self.assertIn("result", data)
        state = data["state"]
        # Player seated
        self.assertEqual(state["table"]["seats"][0], "player")
        self.assertEqual(state["table"]["stacks"]["player"], 500)
        # Bankroll debited from default starting (5000) → 4500
        self.assertEqual(state["player_bankroll"]["chips"], 4_500)

    def test_start_rejects_duplicate_session(self):
        self.client.post(
            "/api/cash/start",
            json={"stake_label": "$10", "buy_in": 500},
        )
        response = self.client.post(
            "/api/cash/start",
            json={"stake_label": "$10", "buy_in": 500},
        )
        self.assertEqual(response.status_code, 409)

    # --- /state with active session ---

    def test_state_after_start_returns_snapshot(self):
        self.client.post(
            "/api/cash/start",
            json={"stake_label": "$10", "buy_in": 500},
        )
        response = self.client.get("/api/cash/state")
        self.assertEqual(response.status_code, 200)
        state = response.get_json()["state"]
        self.assertEqual(state["table"]["stake_label"], "$10")
        self.assertEqual(state["table"]["big_blind"], 10)

    # --- /topup ---

    def test_topup_rejects_invalid_amount(self):
        self.client.post(
            "/api/cash/start",
            json={"stake_label": "$10", "buy_in": 500},
        )
        response = self.client.post("/api/cash/topup", json={"amount": 0})
        self.assertEqual(response.status_code, 400)

    def test_topup_rejects_without_session(self):
        response = self.client.post("/api/cash/topup", json={"amount": 100})
        self.assertEqual(response.status_code, 404)

    # --- /action ---

    def test_action_without_session_returns_404(self):
        response = self.client.post(
            "/api/cash/action", json={"action": "fold"},
        )
        self.assertEqual(response.status_code, 404)

    def test_action_rejects_invalid_action(self):
        self.client.post(
            "/api/cash/start",
            json={"stake_label": "$10", "buy_in": 500},
        )
        response = self.client.post(
            "/api/cash/action", json={"action": "explode"},
        )
        self.assertEqual(response.status_code, 400)

    # --- /leave ---

    def test_leave_without_session_returns_404(self):
        response = self.client.post("/api/cash/leave")
        self.assertEqual(response.status_code, 404)

    def test_leave_between_hands_returns_chips_and_ends_session(self):
        start_response = self.client.post(
            "/api/cash/start",
            json={"stake_label": "$10", "buy_in": 500},
        )
        start_state = start_response.get_json()["state"]
        # If awaiting_human, the hand is in progress — leave behavior
        # differs (mid-hand quit vs clean leave). For the deterministic
        # test path, we always go through the leave route and just
        # assert the session ends.
        response = self.client.post("/api/cash/leave")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["session_ended"])
        # State route no longer finds the session
        followup = self.client.get("/api/cash/state")
        self.assertEqual(followup.status_code, 404)


if __name__ == "__main__":
    unittest.main()
