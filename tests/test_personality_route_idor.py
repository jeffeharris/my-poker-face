"""Regression: PUT/DELETE /api/personality/<name> must not let a non-admin
edit or delete an ownerless (built-in / system) personality.

The owner guard `if owner_id and owner_id != user_id and not is_admin` SHORT-
CIRCUITS when `owner_id is None` (a built-in like Batman), so without the
companion `if not owner_id and not is_admin` guard any authenticated guest
could overwrite or delete the shared catalog (an IDOR). The sibling routes
`/avatar-description` and `/reference-image` already had the second guard;
this locks it in for update + delete too.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from flask_app import create_app
from poker.repositories import create_repos


def _mock_authorization_service(user, has_admin_permission):
    authz = MagicMock()
    authz.auth_manager.get_current_user.return_value = user
    authz.has_permission.return_value = has_admin_permission
    return authz


class _PersonalityRouteBase(unittest.TestCase):
    has_admin_permission = False

    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        repos = create_repos(self.test_db.name)
        self.personality_repo = repos['personality_repo']

        def mock_init_persistence():
            import flask_app.extensions as ext

            for k, v in repos.items():
                if k == 'db_path':
                    ext.persistence_db_path = v
                elif hasattr(ext, k):
                    setattr(ext, k, v)

        with patch('flask_app.extensions.init_persistence', mock_init_persistence):
            self.app = create_app()
        self.app.testing = True
        self.client = self.app.test_client()

        import flask_app.extensions as _ext_mod

        self._ext_mod = _ext_mod
        self._orig_ext_repo = getattr(_ext_mod, 'personality_repo', None)

        self.user = {'id': 'guest-1', 'name': 'Guest'}
        self._authz_patcher = patch(
            'poker.authorization.authorization_service',
            _mock_authorization_service(self.user, self.has_admin_permission),
        )
        self._authz_patcher.start()

        # Routes read auth_manager / personality_repo live off `extensions`.
        auth_mock = MagicMock()
        auth_mock.get_current_user.return_value = self.user
        self._auth_patcher = patch('flask_app.extensions.auth_manager', auth_mock)
        self._auth_patcher.start()
        _ext_mod.personality_repo = self.personality_repo

        # A built-in / system personality: no owner, public.
        self.personality_repo.save_personality('Batman', {'play_style': 'tight'}, source='seed')
        # A personality owned by the requesting guest.
        self.personality_repo.save_personality(
            'Mine', {'play_style': 'loose'}, source='test', owner_id='guest-1', visibility='private'
        )

    def tearDown(self):
        self._auth_patcher.stop()
        self._authz_patcher.stop()
        self._ext_mod.personality_repo = self._orig_ext_repo
        os.unlink(self.test_db.name)


class TestPersonalityIdorNonAdmin(_PersonalityRouteBase):
    has_admin_permission = False

    def test_cannot_update_system_personality(self):
        resp = self.client.put('/api/personality/Batman', json={'play_style': 'pwned'})
        self.assertEqual(resp.status_code, 403)
        # The shared catalog row is untouched.
        self.assertEqual(
            self.personality_repo.load_personality('Batman').get('play_style'), 'tight'
        )

    def test_cannot_delete_system_personality(self):
        resp = self.client.delete('/api/personality/Batman')
        self.assertEqual(resp.status_code, 403)
        # Still present in the catalog.
        self.assertIsNotNone(self.personality_repo.load_personality('Batman'))

    def test_can_still_update_own_personality(self):
        # Guard must not over-restrict: the owner can still edit their own.
        resp = self.client.put('/api/personality/Mine', json={'play_style': 'aggressive'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json().get('success'))

    def test_can_still_delete_own_personality(self):
        resp = self.client.delete('/api/personality/Mine')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json().get('success'))


class TestPersonalityIdorAdmin(_PersonalityRouteBase):
    has_admin_permission = True

    def test_admin_can_update_system_personality(self):
        resp = self.client.put('/api/personality/Batman', json={'play_style': 'balanced'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json().get('success'))

    def test_admin_can_delete_system_personality(self):
        resp = self.client.delete('/api/personality/Batman')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json().get('success'))
