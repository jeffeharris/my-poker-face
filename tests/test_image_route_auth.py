#!/usr/bin/env python3
"""Auth tests for PRH-1: admin-gate the two paid image-generation POST routes.

The `image_bp` blueprint is a deliberate mix — its GET routes *serve*
avatars/grids in-game and MUST stay open, while the two POST *generation*
routes spend money and are now admin-gated per-route. These tests lock both
properties in place.
"""

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


# The two paid POST generation routes guarded by PRH-1.
_REGENERATE_ROUTE = '/api/avatar/TestPersona/regenerate'
_GENERATE_ROUTE = '/api/generate-character-images/TestPersona'


class TestImageRouteAuth(unittest.TestCase):
    """Verify the paid image-generation POST routes require admin permission,
    while the in-game GET serve routes stay open."""

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

        # Disable rate limiting — these are auth tests, and the tight per-hour
        # caps on the image routes (5-10/hr) would otherwise trip on the shared
        # in-memory limiter once the full suite runs in one process. Flip the
        # route module's `limiter` (same singleton the decorators bound to).
        from flask_app.routes import image_routes as ir

        if getattr(ir, 'limiter', None) is not None:
            self._original_limiter_enabled = ir.limiter.enabled
            ir.limiter.enabled = False
        else:
            self._original_limiter_enabled = None

    def tearDown(self):
        # Restore limiter state so we don't leak into other test files.
        from flask_app.routes import image_routes as ir

        if self._original_limiter_enabled is not None and getattr(ir, 'limiter', None) is not None:
            ir.limiter.enabled = self._original_limiter_enabled
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

    # ------------------------------------------------------------------
    # Unauthenticated → 401
    # ------------------------------------------------------------------
    def test_generation_routes_require_authentication(self):
        with self._auth_patch(None, False):
            for path in (_REGENERATE_ROUTE, _GENERATE_ROUTE):
                response = self.client.post(path, json={'emotions': ['confident']})
                self.assertEqual(response.status_code, 401, f"Expected 401 for POST {path}")
                self.assertEqual(response.get_json().get('code'), 'AUTH_REQUIRED')

    # ------------------------------------------------------------------
    # Authenticated non-admin (e.g. a guest) → 403
    # ------------------------------------------------------------------
    def test_generation_routes_forbid_non_admin_users(self):
        with self._auth_patch({'id': 'guest-1', 'name': 'Guest'}, False):
            for path in (_REGENERATE_ROUTE, _GENERATE_ROUTE):
                response = self.client.post(path, json={'emotions': ['confident']})
                self.assertEqual(response.status_code, 403, f"Expected 403 for POST {path}")
                self.assertEqual(response.get_json().get('code'), 'PERMISSION_DENIED')

    # ------------------------------------------------------------------
    # Admin → guard lets the request through (generator is mocked: no paid call)
    # ------------------------------------------------------------------
    def test_admin_can_regenerate_avatar(self):
        fake_generator = MagicMock()
        fake_generator.get_personality.return_value = {'name': 'TestPersona'}
        fake_generator.get_avatar_description.return_value = 'a test persona'

        with (
            self._auth_patch({'id': 'admin-1', 'name': 'Admin'}, True),
            patch('flask_app.extensions.personality_generator', fake_generator),
            patch(
                'flask_app.routes.image_routes.regenerate_avatar_emotion',
                return_value={'success': True, 'message': 'ok'},
            ) as mock_regen,
        ):
            response = self.client.post(_REGENERATE_ROUTE, json={'emotions': ['confident']})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json().get('success'))
        mock_regen.assert_called_once()

    def test_admin_can_generate_character_images(self):
        with (
            self._auth_patch({'id': 'admin-1', 'name': 'Admin'}, True),
            patch('flask_app.routes.image_routes.has_character_images', return_value=False),
            patch(
                'flask_app.routes.image_routes.generate_character_images',
                return_value={'success': True, 'images': {}, 'errors': []},
            ) as mock_gen,
        ):
            response = self.client.post(_GENERATE_ROUTE, json={'emotions': ['confident']})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json().get('status'), 'generated')
        mock_gen.assert_called_once()

    # ------------------------------------------------------------------
    # GET serve routes stay open to unauthenticated callers
    # ------------------------------------------------------------------
    def test_get_serve_routes_open_unauthenticated(self):
        with self._auth_patch(None, False):
            # Emotion list: always served, no auth, limiter-exempt.
            response = self.client.get('/api/avatar/emotions')
            self.assertEqual(response.status_code, 200)
            self.assertIn('emotions', response.get_json())

            # An avatar serve route may 404 when the image is missing, but it
            # must never be auth-gated (401/403).
            serve = self.client.get('/api/avatar/TestPersona/confident')
            self.assertNotIn(serve.status_code, (401, 403))


if __name__ == '__main__':
    unittest.main()
