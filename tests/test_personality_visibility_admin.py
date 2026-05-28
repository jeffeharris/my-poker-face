"""PRH-27: publishing a personality is admin-only.

Two layers:
- Repo: ``save_personality`` must preserve an existing row's visibility +
  owner on a re-save (an avatar / visual-identity edit passes neither), so a
  private personality can't be silently published or orphaned.
- Route: ``PUT /api/personality/<name>/visibility`` lets a non-admin owner set
  only 'private'; 'public'/'disabled' require admin.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pytest

from flask_app import create_app
from poker.repositories import create_repos

# ===========================================================================
# Repo layer: preserve visibility/owner on re-save
# ===========================================================================

@pytest.fixture
def prepo(tmp_path):
    from poker.repositories.personality_repository import PersonalityRepository
    from poker.repositories.schema_manager import SchemaManager

    db = str(tmp_path / "p.db")
    SchemaManager(db).ensure_schema()
    return PersonalityRepository(db), db


def _row(db, name):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT visibility, owner_id FROM personalities WHERE name = ?", (name,)
        ).fetchone()
    finally:
        conn.close()


def test_resave_preserves_private_and_owner(prepo):
    repo, db = prepo
    repo.save_personality('Hero', {'play_style': 'x'}, owner_id='u1', visibility='private')
    # Simulate an avatar-description edit: re-save passing neither owner nor visibility.
    repo.save_personality('Hero', {'play_style': 'x', 'avatar_description': 'cape'}, source='updated')
    row = _row(db, 'Hero')
    assert row['visibility'] == 'private'
    assert row['owner_id'] == 'u1'


def test_new_row_defaults_public_and_ownerless(prepo):
    repo, db = prepo
    repo.save_personality('Builtin', {'play_style': 'y'})  # built-in seed: no owner/visibility
    row = _row(db, 'Builtin')
    assert row['visibility'] == 'public'
    assert row['owner_id'] is None


def test_explicit_visibility_override_still_wins_and_owner_preserved(prepo):
    repo, db = prepo
    repo.save_personality('Hero', {'a': 1}, owner_id='u1', visibility='private')
    # Admin explicitly publishes (visibility passed); owner not passed -> preserved.
    repo.save_personality('Hero', {'a': 1}, visibility='public')
    row = _row(db, 'Hero')
    assert row['visibility'] == 'public'
    assert row['owner_id'] == 'u1'


# ===========================================================================
# Route layer: admin-only publish
# ===========================================================================

def _mock_authorization_service(user, has_admin_permission):
    authz = MagicMock()
    authz.auth_manager.get_current_user.return_value = user
    authz.has_permission.return_value = has_admin_permission
    return authz


class _VisibilityRouteBase(unittest.TestCase):
    has_admin_permission = True

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
        import flask_app.routes.personality_routes as _routes_mod

        self._ext_mod = _ext_mod
        self._routes_mod = _routes_mod
        self._orig_ext_repo = getattr(_ext_mod, 'personality_repo', None)
        self._orig_route_repo = getattr(_routes_mod, 'personality_repo', None)

        self.user = {'id': 'owner-1', 'name': 'Owner'}
        self._authz_patcher = patch(
            'poker.authorization.authorization_service',
            _mock_authorization_service(self.user, self.has_admin_permission),
        )
        self._authz_patcher.start()

        auth_mock = MagicMock()
        auth_mock.get_current_user.return_value = self.user
        self._auth_patcher = patch(
            'flask_app.routes.personality_routes.auth_manager', auth_mock
        )
        self._auth_patcher.start()

        self._routes_mod.personality_repo = self.personality_repo

        # A personality owned by the test user, currently private.
        self.personality_repo.save_personality(
            'Mine', {'play_style': 'tight'}, source='test', owner_id='owner-1', visibility='private'
        )

    def tearDown(self):
        self._auth_patcher.stop()
        self._authz_patcher.stop()
        self._ext_mod.personality_repo = self._orig_ext_repo
        self._routes_mod.personality_repo = self._orig_route_repo
        os.unlink(self.test_db.name)

    def _set_visibility(self, value):
        return self.client.put('/api/personality/Mine/visibility', json={'visibility': value})


class TestVisibilityNonAdmin(_VisibilityRouteBase):
    has_admin_permission = False

    def test_cannot_publish_own_personality(self):
        resp = self._set_visibility('public')
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.get_json().get('code'), 'ADMIN_REQUIRED_FOR_PUBLIC')

    def test_cannot_disable(self):
        resp = self._set_visibility('disabled')
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.get_json().get('code'), 'ADMIN_REQUIRED_FOR_PUBLIC')

    def test_can_set_private(self):
        resp = self._set_visibility('private')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json().get('success'))


class TestVisibilityAdmin(_VisibilityRouteBase):
    has_admin_permission = True

    def test_admin_can_publish(self):
        resp = self._set_visibility('public')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json().get('success'))
