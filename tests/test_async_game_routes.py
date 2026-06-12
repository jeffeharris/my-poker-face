"""P3 async-friends lifecycle: create / invite / join / list.

End-to-end through the Flask routes: the owner creates an async game (owner +
AI fill), invites a friend, the friend claims an open seat (an AI seat becomes
their human seat), and both can list their async games. Builds the app manually
with temp-DB repos + patched auth, mirroring tests/test_cash_career_lobby_route.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from flask_app import create_app
from poker.repositories import create_repos

pytestmark = [pytest.mark.flask, pytest.mark.integration]

OWNER = {'id': 'owner-1', 'name': 'Alice'}
FRIEND = {'id': 'friend-2', 'name': 'Bob'}


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

    yield app.test_client(), repos

    for k, v in snapshot.items():
        setattr(ext, k, v)
    try:
        os.unlink(db.name)
    except FileNotFoundError:
        pass


def _as_user(user):
    """Patch auth + authorization to act as `user` for the duration of a block."""
    authz = MagicMock()
    authz.has_permission.return_value = False
    auth_mock = MagicMock()
    auth_mock.get_current_user.return_value = user
    return (
        patch('flask_app.extensions.auth_manager', auth_mock),
        patch('poker.authorization.authorization_service', authz),
    )


def _create_async_game(client):
    p1, p2 = _as_user(OWNER)
    with p1, p2:
        resp = client.post('/api/async-game/new', json={'opponent_count': 3})
        assert resp.status_code == 200, resp.get_json()
        return resp.get_json()['game_id']


def test_create_async_game_seeds_owner_membership(client):
    test_client, repos = client
    game_id = _create_async_game(test_client)

    # Game is flagged async and the owner is a seated member.
    meta = repos['game_repo'].get_async_meta(game_id)
    assert meta['is_async'] is True
    assert repos['membership_repo'].is_member(game_id, OWNER['id']) is True
    owner_member = repos['membership_repo'].get_member(game_id, OWNER['id'])
    assert owner_member.role == 'owner'


def test_invite_and_join_claims_a_seat(client):
    test_client, repos = client
    game_id = _create_async_game(test_client)

    # Owner mints an invite.
    p1, p2 = _as_user(OWNER)
    with p1, p2:
        resp = test_client.post(f'/api/async-game/{game_id}/invite', json={})
        assert resp.status_code == 200, resp.get_json()
        code = resp.get_json()['code']

    # Count human seats before the friend joins.
    sm_before = repos['game_repo'].load_game(game_id)
    humans_before = sum(1 for p in sm_before.game_state.players if p.is_human)
    assert humans_before == 1

    # Friend joins via the code.
    p1, p2 = _as_user(FRIEND)
    with p1, p2:
        resp = test_client.post('/api/async-game/join', json={'code': code})
        assert resp.status_code == 200, resp.get_json()
        seat_index = resp.get_json()['seat_index']

    # The friend is now a seated member, and an AI seat became their human seat.
    assert repos['membership_repo'].is_member(game_id, FRIEND['id']) is True
    sm_after = repos['game_repo'].load_game(game_id)
    humans_after = sum(1 for p in sm_after.game_state.players if p.is_human)
    assert humans_after == 2
    claimed = sm_after.game_state.players[seat_index]
    assert claimed.is_human is True
    assert claimed.name == FRIEND['name']
    assert claimed.seat_id.owner_id == FRIEND['id']


def test_join_is_idempotent_for_existing_member(client):
    test_client, repos = client
    game_id = _create_async_game(test_client)
    p1, p2 = _as_user(OWNER)
    with p1, p2:
        code = test_client.post(f'/api/async-game/{game_id}/invite', json={}).get_json()['code']
        # Owner is already a member -> join short-circuits, no seat consumed.
        resp = test_client.post('/api/async-game/join', json={'code': code})
        assert resp.status_code == 200
        assert resp.get_json().get('already_member') is True


def test_invalid_invite_code_rejected(client):
    test_client, _ = client
    _create_async_game(test_client)
    p1, p2 = _as_user(FRIEND)
    with p1, p2:
        resp = test_client.post('/api/async-game/join', json={'code': 'nope'})
        assert resp.status_code == 404


def test_mine_lists_async_games_with_turn_flag(client):
    test_client, repos = client
    game_id = _create_async_game(test_client)
    p1, p2 = _as_user(OWNER)
    with p1, p2:
        resp = test_client.get('/api/async-game/mine')
        assert resp.status_code == 200
        games = resp.get_json()['games']
        assert any(g['game_id'] == game_id for g in games)


def test_non_member_cannot_invite(client):
    test_client, _ = client
    game_id = _create_async_game(test_client)
    p1, p2 = _as_user(FRIEND)
    with p1, p2:
        resp = test_client.post(f'/api/async-game/{game_id}/invite', json={})
        assert resp.status_code == 403
