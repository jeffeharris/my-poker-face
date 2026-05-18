"""Tests for the per-personality bankroll-knobs admin routes.

GET /api/personality/<name>/bankroll-knobs returns current knobs
(with defaults filled in) plus the AI's live bankroll for the
admin UI. PUT merges a partial knob update into config_json. Both
require admin permission.

Pattern mirrors tests/test_experiment_routes.py: tempdb + mocked
init_persistence + create_app + admin-user auth patches.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from flask_app import create_app
from poker.repositories import create_repos

pytestmark = [pytest.mark.flask, pytest.mark.integration]


def _mock_authorization_service(user=None, has_admin_permission=True):
    """Build a fake global authorization service for require_permission()."""
    authz = MagicMock()
    authz.auth_manager.get_current_user.return_value = user
    authz.has_permission.return_value = has_admin_permission
    return authz


class _BankrollKnobsRouteBase(unittest.TestCase):
    """Shared setup: tempdb, create_app, admin auth patches, seeded personality.

    Subclasses pick `has_admin_permission` to test the admin-gating
    branch separately from the happy paths.
    """

    has_admin_permission = True

    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()

        repos = create_repos(self.test_db.name)
        self.personality_repo = repos['personality_repo']
        self.bankroll_repo = repos['bankroll_repo']

        def mock_init_persistence():
            import flask_app.extensions as ext
            ext.game_repo = repos['game_repo']
            ext.user_repo = repos['user_repo']
            ext.settings_repo = repos['settings_repo']
            ext.personality_repo = repos['personality_repo']
            ext.experiment_repo = repos['experiment_repo']
            ext.prompt_capture_repo = repos['prompt_capture_repo']
            ext.decision_analysis_repo = repos['decision_analysis_repo']
            ext.prompt_preset_repo = repos['prompt_preset_repo']
            ext.capture_label_repo = repos['capture_label_repo']
            ext.replay_experiment_repo = repos['replay_experiment_repo']
            ext.llm_repo = repos['llm_repo']
            ext.guest_tracking_repo = repos['guest_tracking_repo']
            ext.hand_history_repo = repos['hand_history_repo']
            ext.tournament_repo = repos['tournament_repo']
            ext.coach_repo = repos['coach_repo']
            ext.relationship_repo = repos['relationship_repo']
            ext.bankroll_repo = repos['bankroll_repo']
            ext.persistence_db_path = repos['db_path']

        with patch('flask_app.extensions.init_persistence', mock_init_persistence):
            self.app = create_app()
        self.app.testing = True
        self.client = self.app.test_client()

        user = {'id': 'test-user', 'name': 'Tester'}
        self._authz_patcher = patch(
            'poker.authorization.authorization_service',
            _mock_authorization_service(
                user=user,
                has_admin_permission=self.has_admin_permission,
            ),
        )
        self._authz_patcher.start()

        auth_mock = MagicMock()
        auth_mock.get_current_user.return_value = user
        self._auth_patcher = patch(
            'flask_app.routes.personality_routes.auth_manager',
            auth_mock,
        )
        self._auth_patcher.start()

        # Also patch the personality_routes' bound bankroll_repo lookup so
        # the route's late-binding `from ..extensions import bankroll_repo`
        # picks up our tempdb repo. The route imports lazily so this only
        # needs to point at the right module attribute.

        # Seed a personality with explicit bankroll_knobs in config_json
        # so we have a known starting state for round-trip tests.
        self.personality_id = self.personality_repo.save_personality(
            'Napoleon',
            {
                'play_style': 'aggressive',
                'bankroll_knobs': {
                    'bankroll_cap': 50_000,
                    'bankroll_rate': 500,
                    'buy_in_multiplier': 2.0,
                    'stop_loss_buy_ins': 3,
                    'stop_win_buy_ins': 5,
                    'stake_comfort_zone': '$10',
                },
            },
            source='test_seed',
        )
        # Also seed a personality WITHOUT bankroll_knobs to test defaults.
        self.rookie_id = self.personality_repo.save_personality(
            'Rookie',
            {'play_style': 'tight'},
            source='test_seed',
        )

    def tearDown(self):
        self._auth_patcher.stop()
        self._authz_patcher.stop()
        os.unlink(self.test_db.name)


class TestBankrollKnobsRoutesAdmin(_BankrollKnobsRouteBase):
    """Admin happy paths: GET + PUT + round-trip semantics."""

    has_admin_permission = True

    def test_get_returns_seeded_knobs(self):
        response = self.client.get('/api/personality/Napoleon/bankroll-knobs')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['name'], 'Napoleon')
        self.assertEqual(data['personality_id'], self.personality_id)
        self.assertEqual(data['knobs']['bankroll_cap'], 50_000)
        self.assertEqual(data['knobs']['bankroll_rate'], 500)
        self.assertEqual(data['knobs']['buy_in_multiplier'], 2.0)
        self.assertEqual(data['knobs']['stake_comfort_zone'], '$10')
        # Defaults block surfaced for the UI's hint text.
        self.assertIn('bankroll_cap', data['defaults'])
        # No bankroll row yet → None.
        self.assertIsNone(data['current_bankroll'])

    def test_get_returns_defaults_for_unseeded_personality(self):
        response = self.client.get('/api/personality/Rookie/bankroll-knobs')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data['success'])
        # Defaults fill in.
        self.assertEqual(data['knobs']['bankroll_cap'], 10_000)
        self.assertEqual(data['knobs']['bankroll_rate'], 500)
        self.assertEqual(data['knobs']['buy_in_multiplier'], 1.0)
        self.assertEqual(data['knobs']['stake_comfort_zone'], '$10')

    def test_get_returns_404_for_unknown_personality(self):
        response = self.client.get('/api/personality/NoSuchOne/bankroll-knobs')
        self.assertEqual(response.status_code, 404)
        data = response.get_json()
        self.assertFalse(data['success'])

    def test_get_surfaces_live_bankroll_when_row_exists(self):
        # Seed a bankroll row so current_bankroll comes through.
        from cash_mode.bankroll import AIBankrollState
        from datetime import datetime
        self.bankroll_repo.save_ai_bankroll(AIBankrollState(
            personality_id=self.personality_id,
            chips=8_000,
            last_regen_tick=datetime.utcnow(),
        ))
        response = self.client.get('/api/personality/Napoleon/bankroll-knobs')
        data = response.get_json()
        # Live bankroll surfaces (close to 8_000 — no regen elapsed).
        self.assertIsNotNone(data['current_bankroll'])
        self.assertGreaterEqual(data['current_bankroll'], 8_000)

    def test_put_round_trips_partial_update(self):
        # Send only bankroll_cap; other fields should preserve.
        response = self.client.put(
            '/api/personality/Napoleon/bankroll-knobs',
            json={'bankroll_cap': 75_000},
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['knobs']['bankroll_cap'], 75_000)
        # Preserved.
        self.assertEqual(data['knobs']['bankroll_rate'], 500)
        self.assertEqual(data['knobs']['buy_in_multiplier'], 2.0)

        # Re-fetch via GET to confirm persistence.
        get_response = self.client.get('/api/personality/Napoleon/bankroll-knobs')
        get_data = get_response.get_json()
        self.assertEqual(get_data['knobs']['bankroll_cap'], 75_000)
        self.assertEqual(get_data['knobs']['bankroll_rate'], 500)

    def test_put_replaces_all_fields(self):
        new_knobs = {
            'bankroll_cap': 100_000,
            'bankroll_rate': 1_000,
            'buy_in_multiplier': 5.0,
            'stop_loss_buy_ins': 10,
            'stop_win_buy_ins': 20,
            'stake_comfort_zone': '$100',
        }
        response = self.client.put(
            '/api/personality/Napoleon/bankroll-knobs',
            json=new_knobs,
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['knobs'], new_knobs)

    def test_put_rejects_negative_cap(self):
        response = self.client.put(
            '/api/personality/Napoleon/bankroll-knobs',
            json={'bankroll_cap': -100},
        )
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertFalse(data['success'])
        self.assertIn('bankroll_cap', data['error'])

    def test_put_rejects_zero_multiplier(self):
        response = self.client.put(
            '/api/personality/Napoleon/bankroll-knobs',
            json={'buy_in_multiplier': 0},
        )
        self.assertEqual(response.status_code, 400)

    def test_put_rejects_non_numeric_value(self):
        response = self.client.put(
            '/api/personality/Napoleon/bankroll-knobs',
            json={'bankroll_cap': 'fifty thousand'},
        )
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertFalse(data['success'])

    def test_put_404_for_unknown_personality(self):
        response = self.client.put(
            '/api/personality/NoSuchOne/bankroll-knobs',
            json={'bankroll_cap': 50_000},
        )
        self.assertEqual(response.status_code, 404)


class TestBankrollKnobsRoutesNonAdmin(_BankrollKnobsRouteBase):
    """Non-admin users get 403 on both endpoints."""

    has_admin_permission = False

    def test_get_blocked_for_non_admin(self):
        response = self.client.get('/api/personality/Napoleon/bankroll-knobs')
        self.assertEqual(response.status_code, 403)

    def test_put_blocked_for_non_admin(self):
        response = self.client.put(
            '/api/personality/Napoleon/bankroll-knobs',
            json={'bankroll_cap': 99_999},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == '__main__':
    unittest.main()
