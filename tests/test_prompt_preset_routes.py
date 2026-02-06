#!/usr/bin/env python3
"""Route tests for prompt preset auth and ownership enforcement."""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from flask_app import create_app
from poker.repositories import create_repos


class TestPromptPresetRoutes(unittest.TestCase):
    """Validate prompt preset route authorization behavior."""

    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        self.repos = create_repos(self.test_db.name)
        self.prompt_preset_repo = self.repos['prompt_preset_repo']

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
    def _auth_manager(user):
        auth_manager = MagicMock()
        auth_manager.get_current_user.return_value = user
        return auth_manager

    @staticmethod
    def _admin_authz(is_admin: bool):
        if not is_admin:
            return None
        authz = MagicMock()
        authz.has_permission.return_value = True
        return authz

    def test_create_requires_authentication(self):
        with patch('flask_app.routes.prompt_preset_routes.prompt_preset_repo', self.prompt_preset_repo), \
             patch('flask_app.routes.prompt_preset_routes.auth_manager', self._auth_manager(None)):
            response = self.client.post('/api/prompt-presets', json={'name': 'no-auth'})

        data = response.get_json()
        self.assertEqual(response.status_code, 401)
        self.assertEqual(data.get('code'), 'AUTH_REQUIRED')

    def test_create_sets_owner_from_current_user(self):
        user = {'id': 'user-1', 'name': 'User One'}
        with patch('flask_app.routes.prompt_preset_routes.prompt_preset_repo', self.prompt_preset_repo), \
             patch('flask_app.routes.prompt_preset_routes.auth_manager', self._auth_manager(user)):
            response = self.client.post('/api/prompt-presets', json={'name': 'my-preset'})

        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data['success'])
        self.assertEqual(data['preset']['owner_id'], 'user-1')

    def test_update_forbidden_for_non_owner(self):
        preset_id = self.prompt_preset_repo.create_prompt_preset(name='owner-only', owner_id='user-1')
        user = {'id': 'user-2', 'name': 'User Two'}

        with patch('flask_app.routes.prompt_preset_routes.prompt_preset_repo', self.prompt_preset_repo), \
             patch('flask_app.routes.prompt_preset_routes.auth_manager', self._auth_manager(user)), \
             patch('flask_app.routes.prompt_preset_routes.get_authorization_service', return_value=self._admin_authz(False)):
            response = self.client.put(f'/api/prompt-presets/{preset_id}', json={'name': 'hijacked'})

        self.assertEqual(response.status_code, 403)
        loaded = self.prompt_preset_repo.get_prompt_preset(preset_id)
        self.assertEqual(loaded['name'], 'owner-only')

    def test_update_allowed_for_owner(self):
        preset_id = self.prompt_preset_repo.create_prompt_preset(name='mine', owner_id='user-1')
        user = {'id': 'user-1', 'name': 'User One'}

        with patch('flask_app.routes.prompt_preset_routes.prompt_preset_repo', self.prompt_preset_repo), \
             patch('flask_app.routes.prompt_preset_routes.auth_manager', self._auth_manager(user)), \
             patch('flask_app.routes.prompt_preset_routes.get_authorization_service', return_value=self._admin_authz(False)):
            response = self.client.put(f'/api/prompt-presets/{preset_id}', json={'name': 'mine-updated'})

        self.assertEqual(response.status_code, 200)
        loaded = self.prompt_preset_repo.get_prompt_preset(preset_id)
        self.assertEqual(loaded['name'], 'mine-updated')

    def test_update_allowed_for_admin_override(self):
        preset_id = self.prompt_preset_repo.create_prompt_preset(name='other-user', owner_id='user-1')
        admin_user = {'id': 'admin-1', 'name': 'Admin'}

        with patch('flask_app.routes.prompt_preset_routes.prompt_preset_repo', self.prompt_preset_repo), \
             patch('flask_app.routes.prompt_preset_routes.auth_manager', self._auth_manager(admin_user)), \
             patch('flask_app.routes.prompt_preset_routes.get_authorization_service', return_value=self._admin_authz(True)):
            response = self.client.put(f'/api/prompt-presets/{preset_id}', json={'name': 'admin-edited'})

        self.assertEqual(response.status_code, 200)
        loaded = self.prompt_preset_repo.get_prompt_preset(preset_id)
        self.assertEqual(loaded['name'], 'admin-edited')

    def test_delete_forbidden_for_non_owner(self):
        preset_id = self.prompt_preset_repo.create_prompt_preset(name='delete-locked', owner_id='user-1')
        user = {'id': 'user-2', 'name': 'User Two'}

        with patch('flask_app.routes.prompt_preset_routes.prompt_preset_repo', self.prompt_preset_repo), \
             patch('flask_app.routes.prompt_preset_routes.auth_manager', self._auth_manager(user)), \
             patch('flask_app.routes.prompt_preset_routes.get_authorization_service', return_value=self._admin_authz(False)):
            response = self.client.delete(f'/api/prompt-presets/{preset_id}')

        self.assertEqual(response.status_code, 403)
        self.assertIsNotNone(self.prompt_preset_repo.get_prompt_preset(preset_id))

    def test_delete_allowed_for_owner(self):
        preset_id = self.prompt_preset_repo.create_prompt_preset(name='delete-mine', owner_id='user-1')
        user = {'id': 'user-1', 'name': 'User One'}

        with patch('flask_app.routes.prompt_preset_routes.prompt_preset_repo', self.prompt_preset_repo), \
             patch('flask_app.routes.prompt_preset_routes.auth_manager', self._auth_manager(user)), \
             patch('flask_app.routes.prompt_preset_routes.get_authorization_service', return_value=self._admin_authz(False)):
            response = self.client.delete(f'/api/prompt-presets/{preset_id}')

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.prompt_preset_repo.get_prompt_preset(preset_id))

    def test_delete_allowed_for_admin_override(self):
        preset_id = self.prompt_preset_repo.create_prompt_preset(name='delete-other', owner_id='user-1')
        admin_user = {'id': 'admin-1', 'name': 'Admin'}

        with patch('flask_app.routes.prompt_preset_routes.prompt_preset_repo', self.prompt_preset_repo), \
             patch('flask_app.routes.prompt_preset_routes.auth_manager', self._auth_manager(admin_user)), \
             patch('flask_app.routes.prompt_preset_routes.get_authorization_service', return_value=self._admin_authz(True)):
            response = self.client.delete(f'/api/prompt-presets/{preset_id}')

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.prompt_preset_repo.get_prompt_preset(preset_id))
