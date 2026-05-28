#!/usr/bin/env python3
"""Route tests for owner-or-admin enforcement on coach REST endpoints (T1-28)."""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from flask_app import create_app
from flask_app.services import game_state_service
from poker.poker_game import initialize_game_state
from poker.poker_state_machine import PokerStateMachine
from poker.repositories import create_repos


class TestCoachRouteAuth(unittest.TestCase):
    """Validate coach route authorization behavior — owner / admin / non-owner."""

    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        self.repos = create_repos(self.test_db.name)
        self.game_repo = self.repos['game_repo']

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
        self.app.config['RATELIMIT_ENABLED'] = False
        self.client = self.app.test_client()

        self._route_patchers = [
            patch('flask_app.extensions.game_repo', self.repos['game_repo']),
            patch('flask_app.extensions.coach_repo', self.repos['coach_repo']),
        ]
        for patcher in self._route_patchers:
            patcher.start()

        # The @_coach_required decorator goes through poker.authorization.authorization_service.
        # Grant 'can_access_coach' (and not admin) for the default test setup; individual tests
        # may patch for non-coach or admin scenarios.
        self._authz_mock = MagicMock()
        self._authz_mock.has_permission.side_effect = lambda uid, perm: perm == 'can_access_coach'
        # Make get_current_user return whatever the test currently patched on auth_manager.
        self._authz_mock.auth_manager = MagicMock()
        self._authz_patcher = patch('poker.authorization.authorization_service', self._authz_mock)
        self._authz_patcher.start()

    def tearDown(self):
        self._authz_patcher.stop()
        for game_id in game_state_service.list_game_ids():
            game_state_service.delete_game(game_id)
        for patcher in getattr(self, '_route_patchers', []):
            patcher.stop()
        for repo in self.repos.values():
            if hasattr(repo, 'close'):
                repo.close()
        os.unlink(self.test_db.name)

    def _seed_game(self, game_id: str = 'coach-auth-1', owner_id: str = 'owner-1'):
        game_state = initialize_game_state(['AI Opponent'], human_name='Player')
        state_machine = PokerStateMachine(game_state)
        self.game_repo.save_game(game_id, state_machine, owner_id=owner_id, owner_name='Owner One')

        # Also seed the in-memory cache so coach routes' get_game returns it.
        game_state_service.set_game(
            game_id,
            {
                'state_machine': state_machine,
                'owner_id': owner_id,
                'owner_name': 'Owner One',
                'messages': [],
                'game_started': True,
            },
        )
        return game_id

    def _patch_user(self, user, *, admin: bool = False):
        """Patch auth_manager + authorization_service for coach routes."""
        # Reroute the auth manager used by both `_coach_required` and our helpers.
        self._authz_mock.auth_manager.get_current_user.return_value = user
        if admin:
            self._authz_mock.has_permission.side_effect = lambda uid, perm: True
        else:
            self._authz_mock.has_permission.side_effect = (
                lambda uid, perm: perm == 'can_access_coach'
            )
        return [
            patch('flask_app.extensions.auth_manager.get_current_user', return_value=user),
        ]

    def _enter(self, patchers):
        for p in patchers:
            p.start()
        return patchers

    def _exit(self, patchers):
        for p in patchers:
            p.stop()

    def test_non_owner_forbidden_on_coach_endpoints(self):
        """Non-owner with can_access_coach should get 403 on every coach route."""
        game_id = self._seed_game()
        user = {'id': 'intruder-1', 'name': 'Intruder'}

        requests = [
            ('get', f'/api/coach/{game_id}/stats', None),
            ('post', f'/api/coach/{game_id}/ask', {'question': 'test?'}),
            ('get', f'/api/coach/{game_id}/config', None),
            ('post', f'/api/coach/{game_id}/config', {'mode': 'reactive'}),
            ('post', f'/api/coach/{game_id}/hand-review', {}),
            ('get', f'/api/coach/{game_id}/progression', None),
            ('post', f'/api/coach/{game_id}/onboarding', {'level': 'beginner'}),
        ]

        patchers = self._enter(self._patch_user(user, admin=False))
        try:
            for method, path, payload in requests:
                if method == 'post':
                    response = (
                        self.client.post(path, json=payload)
                        if payload is not None
                        else self.client.post(path)
                    )
                else:
                    response = self.client.get(path)
                self.assertEqual(
                    response.status_code,
                    403,
                    f"Expected 403 for {method.upper()} {path}, got {response.status_code}: {response.get_data(as_text=True)}",
                )
                self.assertEqual(response.get_json().get('error'), 'Permission denied')
        finally:
            self._exit(patchers)

    def test_admin_bypass_on_coach_endpoints(self):
        """Admin users (with can_access_admin_tools) get past the owner check."""
        game_id = self._seed_game()
        admin_user = {'id': 'admin-1', 'name': 'Admin'}

        # Admin gets coach config — only endpoint with no LLM dependency to mock.
        patchers = self._enter(self._patch_user(admin_user, admin=True))
        try:
            response = self.client.get(f'/api/coach/{game_id}/config')
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        finally:
            self._exit(patchers)

    def test_owner_allowed_on_coach_config(self):
        """The owning user reaches the handler past the auth check."""
        game_id = self._seed_game(owner_id='owner-1')
        user = {'id': 'owner-1', 'name': 'Owner One'}

        patchers = self._enter(self._patch_user(user, admin=False))
        try:
            response = self.client.get(f'/api/coach/{game_id}/config')
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        finally:
            self._exit(patchers)

    def test_null_owner_denied_for_non_admin(self):
        """A game with NULL owner (legacy / orphaned row) must not be
        usable by an arbitrary authenticated coach-tier user — mirrors
        the deny semantics of game_routes._authorize_game_access.
        """
        game_id = 'coach-null-owner-1'
        # Seed a row with owner_id=None directly via game_repo.
        from poker.poker_game import initialize_game_state
        from poker.poker_state_machine import PokerStateMachine

        sm = PokerStateMachine(initialize_game_state(['AI Opponent'], human_name='Player'))
        self.game_repo.save_game(game_id, sm, owner_id=None, owner_name=None)
        game_state_service.set_game(
            game_id,
            {
                'state_machine': sm,
                'owner_id': None,
                'owner_name': None,
                'messages': [],
                'game_started': True,
            },
        )

        intruder = {'id': 'intruder-2', 'name': 'Intruder Two'}
        patchers = self._enter(self._patch_user(intruder, admin=False))
        try:
            response = self.client.get(f'/api/coach/{game_id}/config')
            self.assertEqual(response.status_code, 403, response.get_data(as_text=True))
        finally:
            self._exit(patchers)


if __name__ == '__main__':
    unittest.main()
