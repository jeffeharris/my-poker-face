"""P4 device registration route.

POST /api/devices/register upserts a push token for the authed user;
/unregister drops it. Builds the app manually with temp-DB repos + patched
auth, mirroring tests/test_async_game_routes.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from flask_app import create_app
from poker.repositories import create_repos

pytestmark = [pytest.mark.flask, pytest.mark.integration]

USER = {'id': 'user-1', 'name': 'Alice'}


@pytest.fixture
def client():
    db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    db.close()
    repos = create_repos(db.name)

    import flask_app.extensions as ext

    ext_keys = [k for k in repos if k != 'db_path'] + ['persistence_db_path']
    snapshot = {k: getattr(ext, k, None) for k in ext_keys}

    def mock_init_persistence():
        for key, val in repos.items():
            if key == 'db_path':
                continue
            setattr(ext, key, val)
        ext.persistence_db_path = repos['db_path']

    with patch('flask_app.extensions.init_persistence', mock_init_persistence):
        app = create_app()
    app.testing = True

    auth_mock = MagicMock()
    auth_mock.get_current_user.return_value = USER
    with patch('flask_app.extensions.auth_manager', auth_mock):
        yield app.test_client(), repos

    for k, v in snapshot.items():
        setattr(ext, k, v)
    try:
        os.unlink(db.name)
    except FileNotFoundError:
        pass


def test_register_device(client):
    test_client, repos = client
    resp = test_client.post('/api/devices/register', json={'platform': 'ios', 'token': 'tok-A'})
    assert resp.status_code == 200
    devices = repos['device_repo'].list_devices(USER['id'])
    assert [d.token for d in devices] == ['tok-A']


def test_register_requires_token(client):
    test_client, _ = client
    resp = test_client.post('/api/devices/register', json={'platform': 'ios'})
    assert resp.status_code == 400


def test_register_rejects_unknown_platform(client):
    test_client, _ = client
    resp = test_client.post('/api/devices/register', json={'platform': 'blackberry', 'token': 'x'})
    assert resp.status_code == 400


def test_unregister_device(client):
    test_client, repos = client
    test_client.post('/api/devices/register', json={'platform': 'ios', 'token': 'tok-A'})
    resp = test_client.post('/api/devices/unregister', json={'token': 'tok-A'})
    assert resp.status_code == 200
    assert repos['device_repo'].list_devices(USER['id']) == []
