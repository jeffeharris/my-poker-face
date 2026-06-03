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


@pytest.fixture
def fixed_sandbox(monkeypatch):
    """Pin the sandbox so register() never creates a sandbox row in the live DB.
    The economy signal then reads an empty (cold) sandbox → NEUTRAL → no chip
    writes for a freeroll, so these route tests stay non-polluting."""
    monkeypatch.setattr(
        'flask_app.routes.tournament_routes._resolve_sandbox_id',
        lambda owner_id: 'test-sandbox-routes',
    )


def test_register_rejects_out_of_range_buy_in(client, fixed_sandbox):
    assert _register(client, buy_in=-1).status_code == 400
    assert _register(client, buy_in=10_000_000).status_code == 400


def test_register_non_integer_buy_in_is_400(client, fixed_sandbox):
    resp = _register(client, buy_in='lots')
    assert resp.status_code == 400


def test_freeroll_register_returns_skipped_economy(client, fixed_sandbox):
    """buy_in=0 in a cold sandbox → NEUTRAL, no overlay/rake, economy block present."""
    resp = _register(client, buy_in=0)
    assert resp.status_code == 201
    econ = resp.get_json()['economy']
    assert econ['buy_in'] == 0
    assert econ['bank_overlay'] == 0
    assert econ['rake'] == 0
    assert econ['prize_pool'] == 0


def test_get_invite_returns_null_when_none(client, fixed_sandbox):
    """A pinned (empty) sandbox is NEUTRAL → the chairman offers nothing, so the
    lobby card payload is {invite: null} and nothing is written."""
    resp = client.get('/api/tournament/invite')
    assert resp.status_code == 200
    assert resp.get_json()['invite'] is None


def test_accept_with_no_invite_is_404(client, fixed_sandbox):
    resp = client.post('/api/tournament/invite/accept')
    assert resp.status_code == 404
    assert resp.get_json()['error'] == 'no_open_invite'


def test_decline_with_no_invite_is_404(client, fixed_sandbox):
    resp = client.post('/api/tournament/invite/decline')
    assert resp.status_code == 404


def test_accept_stands_human_up_then_builds(client, fixed_sandbox, monkeypatch):
    """Accepting an invite leaves the human's cash seat FIRST (the human side of
    the double-presence guard), then builds the tournament."""
    calls = []
    monkeypatch.setattr(
        'flask_app.routes.tournament_routes._leave_cash_if_seated',
        lambda owner_id: calls.append(owner_id) or True,
    )
    monkeypatch.setattr(
        'flask_app.services.tournament_invites.active_invite',
        lambda repo, owner_id: {'invite_id': 'i1', 'status': 'offered', 'owner_id': owner_id},
    )
    monkeypatch.setattr(
        'flask_app.services.tournament_invites.accept',
        lambda **kw: {'tournament_id': 't1', 'human_id': 'human:x', 'entries': {}, 'plan': None},
    )
    resp = client.post('/api/tournament/invite/accept')
    assert resp.status_code == 201
    assert resp.get_json()['tournament_id'] == 't1'
    assert calls == [OWNER['id']]  # human was stood up from cash


def test_accept_no_invite_does_not_stand_human_up(client, fixed_sandbox, monkeypatch):
    """The leave is gated on an open invite — a no-op accept never cashes the
    player out for nothing."""
    calls = []
    monkeypatch.setattr(
        'flask_app.routes.tournament_routes._leave_cash_if_seated',
        lambda owner_id: calls.append(owner_id) or True,
    )
    monkeypatch.setattr(
        'flask_app.services.tournament_invites.active_invite', lambda repo, owner_id: None
    )
    resp = client.post('/api/tournament/invite/accept')
    assert resp.status_code == 404
    assert calls == []  # gated — did NOT stand them up


def test_invite_routes_require_auth(app):
    mock_auth = MagicMock()
    mock_auth.get_current_user.return_value = None
    with patch('flask_app.extensions.auth_manager', mock_auth):
        with app.test_client() as c:
            assert c.get('/api/tournament/invite').status_code == 401
            assert c.post('/api/tournament/invite/accept').status_code == 401
            assert c.post('/api/tournament/invite/decline').status_code == 401


def test_unauthenticated_is_rejected(app):
    mock_auth = MagicMock()
    mock_auth.get_current_user.return_value = None
    with patch('flask_app.extensions.auth_manager', mock_auth):
        with app.test_client() as c:
            assert c.post('/api/tournament/register', json={}).status_code == 401
            assert c.get('/api/tournament/lobby').status_code == 401


def _put_autonomous(owner_id, tid='tourney_auto'):
    """Put an AUTONOMOUS tournament (no human seat) into the registry."""
    from tournament.config import TournamentConfig
    from tournament.director import FakeHandResolver
    from tournament.session import TournamentSession

    entries = {f'persona_{i}': f'persona_{i}' for i in range(6)}
    config = TournamentConfig(field_size=6, table_size=3, starting_stack=10_000, seed=1)
    resolver = FakeHandResolver()
    # human_id is a nominal persona (NOT human:<owner>) → is_autonomous == True.
    session = TournamentSession(
        config, ai_resolver=resolver, human_id=next(iter(entries)), entries=entries
    )
    registry.put(
        tid,
        {
            'session': session,
            'owner_id': owner_id,
            'created_at': 'now',
            'resolver': resolver,
            'resolver_kind': 'fake',
            'game_id': None,
        },
    )
    return tid


def test_autonomous_tournament_rejected_by_play_routes(client):
    """The world ticker owns autonomous advancement — /advance, /play-out, /sit
    must 409 so a route can't race the ticker (data race + prize misattribution)."""
    tid = _put_autonomous(OWNER['id'])
    assert client.post(f'/api/tournament/{tid}/advance').status_code == 409
    assert client.post(f'/api/tournament/{tid}/play-out').status_code == 409
    assert client.post(f'/api/tournament/{tid}/sit').status_code == 409
