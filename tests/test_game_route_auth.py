#!/usr/bin/env python3
"""Route tests for owner-or-admin enforcement on game REST endpoints."""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from flask_app import create_app
from flask_app.game_adapter import StateMachineAdapter
from flask_app.services import game_state_service
from poker.poker_game import initialize_game_state
from poker.poker_state_machine import PokerStateMachine
from poker.repositories import create_repos


class _AIControllerStub:
    """Minimal controller stub for route tests that avoids MagicMock leakage."""

    def __init__(self, *args, **kwargs):
        self.emotional_state = None
        self.llm_config = kwargs.get('llm_config', {})
        self.session_memory = {}
        self.opponent_model_manager = {}
        self.ai_player = MagicMock()
        self.ai_player.personality_config = {'nickname': 'Stub'}


class TestGameRouteAuth(unittest.TestCase):
    """Validate game route authorization behavior."""

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

        # Patch game_routes module-level repo globals to this test's fresh repos.
        # game_routes imports these by value at module import time, so without this
        # tests can hit stale/closed repo objects from other test modules.
        self._route_patchers = [
            patch('flask_app.routes.game_routes.game_repo', self.repos['game_repo']),
            patch('flask_app.routes.game_routes.user_repo', self.repos['user_repo']),
            patch('flask_app.routes.game_routes.prompt_preset_repo', self.repos['prompt_preset_repo']),
            patch('flask_app.routes.game_routes.guest_tracking_repo', self.repos['guest_tracking_repo']),
            patch('flask_app.routes.game_routes.hand_history_repo', self.repos['hand_history_repo']),
            patch('flask_app.routes.game_routes.tournament_repo', self.repos['tournament_repo']),
            patch('flask_app.routes.game_routes.llm_repo', self.repos['llm_repo']),
            patch('flask_app.routes.game_routes.decision_analysis_repo', self.repos['decision_analysis_repo']),
            patch('flask_app.routes.game_routes.capture_label_repo', self.repos['capture_label_repo']),
            patch('flask_app.routes.game_routes.coach_repo', self.repos['coach_repo']),
            patch('flask_app.routes.game_routes.persistence_db_path', self.repos['db_path']),
        ]
        for patcher in self._route_patchers:
            patcher.start()

    def tearDown(self):
        for game_id in game_state_service.list_game_ids():
            game_state_service.delete_game(game_id)
        for patcher in getattr(self, '_route_patchers', []):
            patcher.stop()
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

    @staticmethod
    def _memory_manager_mock():
        memory_manager = MagicMock()
        memory_manager.hand_count = 0
        memory_manager.get_session_memory.return_value = {}
        memory_manager.get_opponent_model_manager.return_value = {}
        return memory_manager

    def _seed_game(self, game_id: str = 'game-auth-1', owner_id: str = 'owner-1'):
        game_state = initialize_game_state(['AI Opponent'], human_name='Player')
        state_machine = PokerStateMachine(game_state)
        self.game_repo.save_game(game_id, state_machine, owner_id=owner_id, owner_name='Owner One')
        return game_id

    def _count_games(self) -> int:
        return len(self.game_repo.list_games(owner_id=None, limit=1000))

    def _post_new_game(self, payload: dict, remote_addr: str):
        """POST /api/new-game with explicit client IP to avoid shared limiter buckets."""
        return self.client.post(
            '/api/new-game',
            json=payload,
            environ_overrides={'REMOTE_ADDR': remote_addr},
        )

    def test_game_state_requires_authentication(self):
        game_id = self._seed_game()

        with patch('flask_app.routes.game_routes.auth_manager', self._auth_manager(None)):
            response = self.client.get(f'/api/game-state/{game_id}')

        data = response.get_json()
        self.assertEqual(response.status_code, 401)
        self.assertEqual(data.get('code'), 'AUTH_REQUIRED')

    def test_game_state_forbidden_for_non_owner(self):
        game_id = self._seed_game()
        user = {'id': 'intruder-1', 'name': 'Intruder'}

        with patch('flask_app.routes.game_routes.auth_manager', self._auth_manager(user)), \
             patch('flask_app.routes.game_routes.get_authorization_service', return_value=self._admin_authz(False)):
            response = self.client.get(f'/api/game-state/{game_id}')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('error'), 'Permission denied')

    def test_game_state_allows_new_owner_after_db_transfer_with_stale_cache(self):
        game_id = 'game-auth-transfer-1'
        guest_owner = 'guest_owner_1'
        new_owner = 'google_owner_1'
        self._seed_game(game_id=game_id, owner_id=guest_owner)

        stale_cached_state = StateMachineAdapter(
            PokerStateMachine(initialize_game_state(['AI Opponent'], human_name='Player'))
        )
        game_state_service.set_game(game_id, {
            'state_machine': stale_cached_state,
            'owner_id': guest_owner,
            'owner_name': 'Guest Owner',
            'messages': [],
            'game_started': True,
        })

        transferred = self.repos['user_repo'].transfer_game_ownership(
            guest_owner,
            new_owner,
            'New Owner',
        )
        self.assertEqual(transferred, 1)

        user = {'id': new_owner, 'name': 'New Owner'}
        with patch('flask_app.routes.game_routes.auth_manager', self._auth_manager(user)), \
             patch('flask_app.routes.game_routes.get_authorization_service', return_value=self._admin_authz(False)):
            response = self.client.get(f'/api/game-state/{game_id}')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(game_state_service.get_game(game_id).get('owner_id'), new_owner)

    def test_delete_forbidden_for_non_owner(self):
        game_id = self._seed_game()
        user = {'id': 'intruder-1', 'name': 'Intruder'}

        with patch('flask_app.routes.game_routes.auth_manager', self._auth_manager(user)), \
             patch('flask_app.routes.game_routes.get_authorization_service', return_value=self._admin_authz(False)):
            response = self.client.delete(f'/api/game/{game_id}')

        self.assertEqual(response.status_code, 403)
        self.assertIsNotNone(self.game_repo.get_game_owner_info(game_id))

    def test_delete_allowed_for_admin_override(self):
        game_id = self._seed_game()
        admin_user = {'id': 'admin-1', 'name': 'Admin'}

        with patch('flask_app.routes.game_routes.auth_manager', self._auth_manager(admin_user)), \
             patch('flask_app.routes.game_routes.get_authorization_service', return_value=self._admin_authz(True)):
            response = self.client.delete(f'/api/game/{game_id}')

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.game_repo.get_game_owner_info(game_id))

    def test_non_owner_forbidden_on_rest_game_endpoints(self):
        game_id = self._seed_game()
        user = {'id': 'intruder-1', 'name': 'Intruder'}
        requests = [
            ('post', f'/api/game/{game_id}/action', {'action': 'call', 'amount': 0}),
            ('post', f'/api/game/{game_id}/message', {'message': 'hello', 'sender': 'Player'}),
            ('post', f'/api/game/{game_id}/retry', None),
            ('post', f'/api/end_game/{game_id}', None),
            ('get', f'/messages/{game_id}', None),
            ('get', f'/api/game/{game_id}/llm-configs', None),
        ]

        with patch('flask_app.routes.game_routes.auth_manager', self._auth_manager(user)), \
             patch('flask_app.routes.game_routes.get_authorization_service', return_value=self._admin_authz(False)):
            for method, path, payload in requests:
                if method == 'post':
                    response = self.client.post(path, json=payload) if payload is not None else self.client.post(path)
                else:
                    response = self.client.get(path)

                self.assertEqual(
                    response.status_code,
                    403,
                    f"Expected 403 for {method.upper()} {path}, got {response.status_code}",
                )

    def test_new_game_requires_authentication(self):
        games_before = self._count_games()
        with patch('flask_app.routes.game_routes.auth_manager', self._auth_manager(None)):
            response = self._post_new_game({'playerName': 'Anon'}, remote_addr='10.0.0.11')

        data = response.get_json()
        self.assertEqual(response.status_code, 401)
        self.assertEqual(data.get('code'), 'AUTH_REQUIRED')
        self.assertEqual(self._count_games(), games_before)

    def test_new_game_allows_guest_session_and_sets_owner(self):
        guest_user = {'id': 'guest_tester', 'name': 'Tester', 'is_guest': True}
        with patch('flask_app.routes.game_routes.auth_manager', self._auth_manager(guest_user)), \
             patch('flask_app.routes.game_routes.AIPlayerController', side_effect=_AIControllerStub), \
             patch('flask_app.routes.game_routes.AIMemoryManager', return_value=self._memory_manager_mock()), \
             patch('poker.repositories.sqlite_repositories.PressureEventRepository', return_value=MagicMock()), \
             patch('flask_app.routes.game_routes.start_background_avatar_generation'):
            response = self._post_new_game({'playerName': 'Tester'}, remote_addr='10.0.0.12')

        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertIn('game_id', data)

        owner_info = self.game_repo.get_game_owner_info(data['game_id'])
        self.assertIsNotNone(owner_info)
        self.assertEqual(owner_info['owner_id'], 'guest_tester')

    def test_guest_can_access_game_after_creation(self):
        guest_user = {'id': 'guest_tester', 'name': 'Tester', 'is_guest': True}
        with patch('flask_app.routes.game_routes.auth_manager', self._auth_manager(guest_user)), \
             patch('flask_app.routes.game_routes.AIPlayerController', side_effect=_AIControllerStub), \
             patch('flask_app.routes.game_routes.AIMemoryManager', return_value=self._memory_manager_mock()), \
             patch('poker.repositories.sqlite_repositories.PressureEventRepository', return_value=MagicMock()), \
             patch('flask_app.routes.game_routes.start_background_avatar_generation'):
            create_response = self._post_new_game({'playerName': 'Tester'}, remote_addr='10.0.0.13')

        create_data = create_response.get_json()
        self.assertEqual(create_response.status_code, 200)
        game_id = create_data['game_id']

        with patch('flask_app.routes.game_routes.auth_manager', self._auth_manager(guest_user)):
            state_response = self.client.get(f'/api/game-state/{game_id}')

        self.assertEqual(state_response.status_code, 200)
