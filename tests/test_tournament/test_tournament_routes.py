"""Route tests for the tournament API.

These exercise route logic against the in-memory registry. The autouse
fixture forces the registry memory-only (tournament_session_repo = None), so
no tournament-specific DB scaffolding is needed; durable write-through is
covered separately by test_registry_persistence.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from flask_app.services import tournament_registry as registry

pytestmark = [pytest.mark.flask, pytest.mark.integration]

OWNER = {'id': 'tourney-user-1', 'name': 'Tester'}


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    # These tests exercise route logic against the in-memory registry. Layer C
    # made the registry write-through (get / find_active_for_owner fall back to
    # the persisted repo), so without this an active tournament would leak
    # across tests via the DB. Force memory-only here — write-through itself is
    # covered by test_registry_persistence.py.
    monkeypatch.setattr('flask_app.extensions.tournament_session_repo', None)
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


def test_sit_builds_a_live_game(client):
    """The live path: register, then sit builds a real single-table game tagged
    with the tournament session. Exercises the builder + tiered controllers +
    memory wiring end to end."""
    from flask_app.services import game_state_service

    tid = _register(client).get_json()['tournament_id']
    resp = client.post(f'/api/tournament/{tid}/sit')
    assert resp.status_code in (200, 201)
    game_id = resp.get_json()['game_id']
    assert game_id.startswith('tourney-')

    game_data = game_state_service.get_game(game_id)
    assert game_data is not None
    assert game_data['tournament_session'] is not None
    assert game_data['tournament_id'] == tid
    # exactly one human seat at the live table, plus AI controllers for the rest
    players = game_data['state_machine'].game_state.players
    assert sum(1 for p in players if p.is_human) == 1
    ai_names = {p.name for p in players if not p.is_human}
    assert set(game_data['ai_controllers']) == ai_names

    # sitting again returns the same live game (idempotent)
    again = client.post(f'/api/tournament/{tid}/sit')
    assert again.get_json()['game_id'] == game_id


def test_unauthenticated_is_rejected(app):
    mock_auth = MagicMock()
    mock_auth.get_current_user.return_value = None
    with patch('flask_app.extensions.auth_manager', mock_auth):
        with app.test_client() as c:
            assert c.post('/api/tournament/register', json={}).status_code == 401
            assert c.get('/api/tournament/lobby').status_code == 401
