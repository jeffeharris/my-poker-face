"""Route tests for the tournament API.

The routes are pure in-memory (no DB), so these use the shared flask_client
fixture plus an auth-manager patch — no tournament-specific DB scaffolding.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from flask_app.services import tournament_registry as registry

pytestmark = [pytest.mark.flask, pytest.mark.integration]

OWNER = {'id': 'tourney-user-1', 'name': 'Tester'}


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.clear()
    yield
    registry.clear()


@pytest.fixture(scope='module')
def app():
    """A real app instance. Tournament routes are DB-free, so no persistence
    scaffolding is needed (the shared conftest flask fixtures are unrelated)."""
    from flask_app import create_app

    application = create_app()
    application.testing = True
    return application


@pytest.fixture
def client(app):
    """Test client with auth_manager.get_current_user → OWNER."""
    mock_auth = MagicMock()
    mock_auth.get_current_user.return_value = OWNER
    with patch('flask_app.extensions.auth_manager', mock_auth):
        with app.test_client() as test_client:
            yield test_client


def _register(client, **body):
    payload = {'field_size': 9, 'table_size': 3, 'resolver': 'fake', 'seed': 1, **body}
    return client.post('/api/tournament/register', json=payload)


def test_register_creates_tournament_and_returns_standings(client):
    resp = _register(client)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data['tournament_id'].startswith('tourney_')
    standings = data['standings']
    assert standings['field_size'] == 9
    assert standings['players_remaining'] == 9
    assert standings['human']['player_id'] == 'P01'
    assert standings['human']['out'] is False


def test_register_rejects_second_active_tournament(client):
    first = _register(client).get_json()['tournament_id']
    resp = _register(client)
    assert resp.status_code == 409
    assert resp.get_json()['tournament_id'] == first


def test_register_validates_params(client):
    assert _register(client, field_size=1).status_code == 400
    assert _register(client, table_size=99).status_code == 400
    assert _register(client, resolver='magic').status_code == 400


def test_lobby_reflects_active_tournament(client):
    assert client.get('/api/tournament/lobby').get_json()['has_active'] is False
    tid = _register(client).get_json()['tournament_id']
    lobby = client.get('/api/tournament/lobby').get_json()
    assert lobby['has_active'] is True
    assert lobby['active']['tournament_id'] == tid


def test_standings_requires_ownership(client):
    tid = _register(client).get_json()['tournament_id']
    assert client.get(f'/api/tournament/{tid}/standings').status_code == 200
    # a different user must not see it
    other = MagicMock()
    other.get_current_user.return_value = {'id': 'someone-else'}
    with patch('flask_app.extensions.auth_manager', other):
        assert client.get(f'/api/tournament/{tid}/standings').status_code == 404


def test_advance_progresses_the_world(client):
    tid = _register(client).get_json()['tournament_id']
    before = client.get(f'/api/tournament/{tid}/standings').get_json()
    after = client.post(f'/api/tournament/{tid}/advance').get_json()
    assert after['rounds'] == before['rounds'] + 1
    # chips conserved across the round
    total = sum(
        seat['stack'] or 0 for t in after['tables'] for seat in t['seats'] if seat['player_id']
    )
    assert total == 9 * 10_000


def test_play_out_completes_and_declares_a_winner(client):
    tid = _register(client).get_json()['tournament_id']
    final = client.post(f'/api/tournament/{tid}/play-out').get_json()
    assert final['complete'] is True
    assert final['winner'] is not None
    assert final['players_remaining'] == 1
    # a completed tournament is no longer the active one
    assert client.get('/api/tournament/lobby').get_json()['has_active'] is False


def test_leave_removes_tournament(client):
    tid = _register(client).get_json()['tournament_id']
    assert client.delete(f'/api/tournament/{tid}').status_code == 200
    assert client.get(f'/api/tournament/{tid}/standings').status_code == 404


def test_unauthenticated_is_rejected(app):
    mock_auth = MagicMock()
    mock_auth.get_current_user.return_value = None
    with patch('flask_app.extensions.auth_manager', mock_auth):
        with app.test_client() as c:
            assert c.post('/api/tournament/register', json={}).status_code == 401
            assert c.get('/api/tournament/lobby').status_code == 401
