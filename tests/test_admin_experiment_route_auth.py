#!/usr/bin/env python3
"""Auth tests for T1-24 admin-only debug/experiment APIs."""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from flask_app import create_app
from poker.repositories import create_repos


def _mock_authorization_service(user=None, has_admin_permission=False):
    """Build a fake global authorization service for require_permission()."""
    authz = MagicMock()
    authz.auth_manager.get_current_user.return_value = user
    authz.has_permission.return_value = has_admin_permission
    return authz


class TestAdminExperimentRouteAuth(unittest.TestCase):
    """Verify debug/experiment APIs require admin permission."""

    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        self.repos = create_repos(self.test_db.name)

        def mock_init_persistence():
            import flask_app.extensions as ext
            ext.game_repo = self.repos['game_repo']
            ext.user_repo = self.repos['user_repo']
            ext.settings_repo = self.repos['settings_repo']
            ext.personality_repo = self.repos['personality_repo']
            ext.experiment_repo = self.repos['experiment_repo']
            ext.prompt_capture_repo = self.repos['prompt_capture_repo']
            ext.decision_analysis_repo = self.repos['decision_analysis_repo']
            ext.prompt_preset_repo = self.repos['prompt_preset_repo']
            ext.capture_label_repo = self.repos['capture_label_repo']
            ext.replay_experiment_repo = self.repos['replay_experiment_repo']
            ext.llm_repo = self.repos['llm_repo']
            ext.guest_tracking_repo = self.repos['guest_tracking_repo']
            ext.hand_history_repo = self.repos['hand_history_repo']
            ext.tournament_repo = self.repos['tournament_repo']
            ext.coach_repo = self.repos['coach_repo']
            ext.persistence_db_path = self.repos['db_path']

        with patch('flask_app.extensions.init_persistence', mock_init_persistence):
            self.app = create_app()
        self.app.testing = True
        self.client = self.app.test_client()

    def tearDown(self):
        for repo in self.repos.values():
            if hasattr(repo, 'close'):
                repo.close()
        os.unlink(self.test_db.name)

    @staticmethod
    def _auth_patch(user, is_admin):
        return patch(
            'poker.authorization.authorization_service',
            _mock_authorization_service(user=user, has_admin_permission=is_admin),
        )

    def test_admin_tool_routes_require_authentication(self):
        endpoints = [
            '/api/experiments/quick-prompts',
            '/api/prompt-debug/emotions',
            '/api/capture-labels',
            '/api/replay-experiments',
        ]

        with self._auth_patch(None, False):
            for path in endpoints:
                response = self.client.get(path)
                self.assertEqual(response.status_code, 401, f"Expected 401 for GET {path}")
                self.assertEqual(response.get_json().get('code'), 'AUTH_REQUIRED')

    def test_admin_tool_routes_forbid_non_admin_users(self):
        endpoints = [
            '/api/experiments/quick-prompts',
            '/api/prompt-debug/emotions',
            '/api/capture-labels',
            '/api/replay-experiments',
        ]

        with self._auth_patch({'id': 'user-1', 'name': 'User'}, False):
            for path in endpoints:
                response = self.client.get(path)
                self.assertEqual(response.status_code, 403, f"Expected 403 for GET {path}")
                self.assertEqual(response.get_json().get('code'), 'PERMISSION_DENIED')

    def test_experiment_options_preflight_skips_admin_permission_check(self):
        with self._auth_patch(None, False):
            response = self.client.options(
                '/api/experiments/validate',
                headers={
                    'Origin': 'http://localhost:3000',
                    'Access-Control-Request-Method': 'POST',
                    'Access-Control-Request-Headers': 'content-type',
                },
            )

        self.assertIn(response.status_code, [200, 204])

    def test_admin_can_access_experiment_routes(self):
        with self._auth_patch({'id': 'admin-1', 'name': 'Admin'}, True):
            response = self.client.get('/api/experiments/quick-prompts')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json().get('success'))

    def test_admin_can_access_prompt_debug_routes(self):
        with self._auth_patch({'id': 'admin-1', 'name': 'Admin'}, True), \
             patch('flask_app.routes.prompt_debug_routes.prompt_capture_repo') as mock_capture_repo, \
             patch('flask_app.routes.prompt_debug_routes.capture_label_repo') as mock_label_repo:
            mock_capture_repo.list_prompt_captures.return_value = {'captures': [], 'total': 0}
            mock_capture_repo.get_prompt_capture_stats.return_value = {}
            mock_label_repo.get_label_stats.return_value = {}
            response = self.client.get('/api/prompt-debug/captures')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json().get('success'))

    def test_admin_can_access_capture_label_routes(self):
        with self._auth_patch({'id': 'admin-1', 'name': 'Admin'}, True), \
             patch('flask_app.routes.capture_label_routes.capture_label_repo') as mock_repo:
            mock_repo.list_all_labels.return_value = []
            response = self.client.get('/api/capture-labels')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json().get('success'))

    def test_admin_can_access_replay_experiment_routes(self):
        with self._auth_patch({'id': 'admin-1', 'name': 'Admin'}, True), \
             patch('flask_app.routes.replay_experiment_routes.replay_experiment_repo') as mock_repo:
            mock_repo.list_replay_experiments.return_value = {'experiments': [], 'total': 0}
            response = self.client.get('/api/replay-experiments')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json().get('success'))
