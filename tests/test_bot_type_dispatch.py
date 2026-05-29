"""Tests for /api/new-game bot_type dispatch and legacy alias mapping.

After the 4-mode bot controller refactor (chaos/standard/lean/sharp), the
route picks a controller class per player from `bot_types`. Legacy values
`hybrid` / `tiered` are accepted at the boundary and remapped to
`standard` / `sharp` before storage.

These tests guard against regressions where a new bot_type silently falls
through to the default (TieredBotController — the core engine) — the kind of
silent miss that showed up in the experiment runner during this refactor.
"""

import os
import sys
import tempfile
import unittest
import unittest.mock
from unittest.mock import patch

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from flask_app import create_app
from poker.controllers import AIPlayerController
from poker.hybrid_ai_controller import HybridAIController
from poker.lean_bounded_controller import LeanBoundedController
from poker.repositories import create_repos
from poker.tiered_bot_controller import TieredBotController


class TestBotTypeDispatch(unittest.TestCase):
    """POST /api/new-game with each bot_type and verify controller wiring."""

    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()

        repos = create_repos(self.test_db.name)

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
            ext.persistence_db_path = repos['db_path']

        with patch('flask_app.extensions.init_persistence', mock_init_persistence):
            self.app = create_app()
        self.app.testing = True
        self.client = self.app.test_client()

        # Disable rate limiting — this test creates several games in sequence
        # to exercise every bot_type and would otherwise hit the per-hour cap.
        # The route module imports `limiter` by value at module load time, so
        # the *route's* limiter (not extensions.limiter, which may have been
        # swapped) is the one we have to flip.
        from flask_app.routes import game_routes as gr

        if getattr(gr, 'limiter', None) is not None:
            self._original_limiter_enabled = gr.limiter.enabled
            gr.limiter.enabled = False
        else:
            self._original_limiter_enabled = None

        self._route_patchers = [
            patch('flask_app.extensions.game_repo', repos['game_repo']),
            patch('flask_app.extensions.user_repo', repos['user_repo']),
            patch('flask_app.extensions.prompt_preset_repo', repos['prompt_preset_repo']),
            patch('flask_app.extensions.guest_tracking_repo', repos['guest_tracking_repo']),
            patch('flask_app.extensions.hand_history_repo', repos['hand_history_repo']),
            patch('flask_app.extensions.tournament_repo', repos['tournament_repo']),
            patch('flask_app.extensions.llm_repo', repos['llm_repo']),
            patch(
                'flask_app.extensions.decision_analysis_repo',
                repos['decision_analysis_repo'],
            ),
            patch('flask_app.extensions.capture_label_repo', repos['capture_label_repo']),
            patch('flask_app.extensions.coach_repo', repos['coach_repo']),
            patch('flask_app.extensions.persistence_db_path', repos['db_path']),
        ]
        for patcher in self._route_patchers:
            patcher.start()

        # Unique user id per test: PRH-41 keys the rate limiter per authenticated
        # user, and this suite POSTs /api/new-game (10/hr) many times — a shared
        # id would accumulate into one bucket and 429. A per-test id gives each
        # its own empty bucket.
        mock_auth = unittest.mock.MagicMock()
        mock_auth.get_current_user.return_value = {
            'id': f'test-user-{self.id()}',
            'name': 'TestUser',
        }
        self._auth_patcher = patch('flask_app.extensions.auth_manager', mock_auth)
        self._auth_patcher.start()

    def tearDown(self):
        self._auth_patcher.stop()
        for patcher in self._route_patchers:
            patcher.stop()
        # Restore limiter state so we don't leak into other test files.
        from flask_app.routes import game_routes as gr

        if self._original_limiter_enabled is not None and getattr(gr, 'limiter', None) is not None:
            gr.limiter.enabled = self._original_limiter_enabled
        os.unlink(self.test_db.name)

    def _create_game(self, bot_types: dict):
        """POST /api/new-game with two AI players + given bot_types. Return (game_id, ai_controllers)."""
        response = self.client.post(
            '/api/new-game',
            json={
                'playerName': 'TestPlayer',
                'personalities': ['Batman', 'Yoda'],
                'bot_types': bot_types,
            },
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        game_id = response.get_json()['game_id']

        from flask_app.services import game_state_service

        game_data = game_state_service.get_game(game_id)
        self.assertIsNotNone(game_data, 'game_data missing from game_state_service')
        return game_id, game_data['ai_controllers']

    def test_bot_type_chaos_uses_ai_player_controller(self):
        _, controllers = self._create_game({'Batman': 'chaos', 'Yoda': 'chaos'})
        # AIPlayerController is the parent of HybridAIController, so check exact type.
        self.assertIs(type(controllers['Batman']), AIPlayerController)
        self.assertIs(type(controllers['Yoda']), AIPlayerController)

    def test_bot_type_standard_uses_hybrid_controller(self):
        _, controllers = self._create_game({'Batman': 'standard', 'Yoda': 'standard'})
        self.assertIs(type(controllers['Batman']), HybridAIController)
        self.assertIs(type(controllers['Yoda']), HybridAIController)

    def test_bot_type_lean_uses_lean_bounded_controller(self):
        _, controllers = self._create_game({'Batman': 'lean', 'Yoda': 'lean'})
        self.assertIs(type(controllers['Batman']), LeanBoundedController)
        self.assertIs(type(controllers['Yoda']), LeanBoundedController)

    def test_bot_type_sharp_uses_tiered_bot_controller(self):
        _, controllers = self._create_game({'Batman': 'sharp', 'Yoda': 'sharp'})
        # build_tiered_controller wires expression generators on top; the
        # controller itself is still TieredBotController.
        self.assertIsInstance(controllers['Batman'], TieredBotController)
        self.assertIsInstance(controllers['Yoda'], TieredBotController)

    def test_legacy_hybrid_maps_to_standard(self):
        """bot_type='hybrid' is accepted but normalized to 'standard' (HybridAIController)."""
        _, controllers = self._create_game({'Batman': 'hybrid', 'Yoda': 'hybrid'})
        self.assertIs(type(controllers['Batman']), HybridAIController)
        # Legacy alias must not produce the lean variant
        self.assertNotIsInstance(controllers['Batman'], LeanBoundedController)

    def test_legacy_tiered_maps_to_sharp(self):
        """bot_type='tiered' is accepted but normalized to 'sharp' (TieredBotController)."""
        _, controllers = self._create_game({'Batman': 'tiered', 'Yoda': 'tiered'})
        self.assertIsInstance(controllers['Batman'], TieredBotController)
        self.assertIsInstance(controllers['Yoda'], TieredBotController)

    def test_unknown_bot_type_returns_400(self):
        response = self.client.post(
            '/api/new-game',
            json={
                'playerName': 'TestPlayer',
                'personalities': ['Batman'],
                'bot_types': {'Batman': 'wizard'},
            },
        )
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertIn('valid_bot_types', payload)
        # Error response must NOT advertise legacy values to new clients
        self.assertNotIn('hybrid', payload['valid_bot_types'])
        self.assertNotIn('tiered', payload['valid_bot_types'])
        self.assertIn('standard', payload['valid_bot_types'])
        self.assertIn('lean', payload['valid_bot_types'])

    def test_omitted_bot_type_defaults_to_tiered(self):
        """Players not listed in bot_types default to the tiered solver bot.

        Tiered ('sharp') is the core engine: instant table-lookup decisions,
        LLM only for narration. The LLM-driven bots (standard/chaos/lean) are
        opt-in via Custom Game.
        """
        _, controllers = self._create_game({})  # No bot_types specified
        self.assertIsInstance(controllers['Batman'], TieredBotController)
        self.assertIsInstance(controllers['Yoda'], TieredBotController)

    def test_ai_chat_on_solver_narrates_and_is_not_instant(self):
        """Default (AI Chat on): Solver opponents get an expression generator
        (one narration LLM call), so the game is NOT in instant mode."""
        from flask_app.handlers.game_handler import _all_ai_no_llm

        _, controllers = self._create_game({})  # ai_chat defaults on
        for c in controllers.values():
            self.assertIsInstance(c, TieredBotController)
            self.assertIsNotNone(getattr(c, 'expression_generator', None))
        self.assertFalse(_all_ai_no_llm(controllers))

    def test_ai_chat_off_makes_solver_instant(self):
        """AI Chat off: Solver opponents make ZERO LLM calls (no expression
        generator), so the game reports instant mode (FF button hidden)."""
        from flask_app.handlers.game_handler import _all_ai_no_llm
        from flask_app.services import game_state_service

        response = self.client.post(
            '/api/new-game',
            json={
                'playerName': 'TestPlayer',
                'personalities': ['Batman', 'Yoda'],
                'ai_chat': False,
            },
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        controllers = game_state_service.get_game(response.get_json()['game_id'])['ai_controllers']
        for c in controllers.values():
            self.assertIsInstance(c, TieredBotController)
            self.assertIsNone(getattr(c, 'expression_generator', None))
        self.assertTrue(_all_ai_no_llm(controllers))


if __name__ == '__main__':
    unittest.main()
