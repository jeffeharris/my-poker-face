#!/usr/bin/env python3
"""Tests for native (mobile) Google sign-in: /api/auth/google/native.

The endpoint verifies a Google ID token (from the iOS/Android native SDK) and
returns a JWT the app sends as ``Authorization: Bearer``. The token *verification*
(JWKS fetch + RS256) is mocked here so the suite stays offline — these tests
cover the route wiring, the find-or-create user logic, guest linking, the
audience allowlist gate, and that the returned JWT round-trips through
``get_current_user``.
"""

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask, jsonify

from poker.auth import AuthManager

pytestmark = pytest.mark.flask


def _make_app(user_repo):
    """Minimal app with the auth routes registered against a mock user_repo."""
    app = Flask(__name__)
    app.secret_key = 'native-auth-test-secret'
    manager = AuthManager(app, user_repo=user_repo)

    # /api/auth/me lets us prove the returned bearer token authenticates.
    @app.route('/api/auth/me', methods=['GET'])
    def me():
        return jsonify(user=manager.get_current_user())

    return app, manager


def _post_native(client, *, audiences, json_body, verify_return=None, verify_side=None):
    """POST to the native endpoint with audiences + token verifier patched.

    Both are patched only for the duration of the request, because the route
    reads ``flask_app.config.GOOGLE_ALLOWED_AUDIENCES`` and calls
    ``poker.auth.verify_google_id_token`` at request time.
    """
    with ExitStack() as stack:
        stack.enter_context(
            patch('flask_app.config.GOOGLE_ALLOWED_AUDIENCES', audiences, create=True)
        )
        if verify_side is not None:
            stack.enter_context(
                patch('poker.auth.verify_google_id_token', side_effect=verify_side)
            )
        elif verify_return is not None:
            stack.enter_context(
                patch('poker.auth.verify_google_id_token', return_value=verify_return)
            )
        return client.post('/api/auth/google/native', json=json_body)


def _fresh_repo():
    repo = MagicMock()
    repo.get_user_by_email.return_value = None
    repo.create_google_user.return_value = {
        'id': 'google_sub-123',
        'email': 'p@example.com',
        'name': 'Phil',
        'picture': 'http://pic',
        'is_guest': False,
        'created_at': '2026-01-01T00:00:00',
        'linked_guest_id': None,
    }
    repo.transfer_game_ownership.return_value = 0
    return repo


VALID_CLAIMS = {
    'sub': 'sub-123',
    'email': 'p@example.com',
    'email_verified': True,
    'name': 'Phil',
    'picture': 'http://pic',
    'iss': 'https://accounts.google.com',
    'aud': 'ios-client-id',
}


def test_native_login_creates_user_and_returns_bearer_token():
    repo = _fresh_repo()
    app, _ = _make_app(repo)
    client = app.test_client()

    resp = _post_native(
        client,
        audiences=['web-id', 'ios-client-id'],
        json_body={'id_token': 'tok'},
        verify_return=VALID_CLAIMS,
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['success'] is True
    assert body['user']['id'] == 'google_sub-123'
    assert body['user']['is_guest'] is False
    token = body['token']
    assert token

    repo.create_google_user.assert_called_once()

    # The returned JWT must authenticate via the Authorization header.
    me = client.get('/api/auth/me', headers={'Authorization': f'Bearer {token}'})
    assert me.get_json()['user']['id'] == 'google_sub-123'


def test_existing_user_is_reused_not_recreated():
    repo = _fresh_repo()
    repo.get_user_by_email.return_value = {
        'id': 'google_sub-123',
        'email': 'p@example.com',
        'name': 'Phil',
        'is_guest': False,
        'linked_guest_id': None,
    }
    app, _ = _make_app(repo)
    client = app.test_client()

    resp = _post_native(
        client,
        audiences=['ios-client-id'],
        json_body={'id_token': 'tok'},
        verify_return=VALID_CLAIMS,
    )

    assert resp.status_code == 200
    repo.create_google_user.assert_not_called()
    repo.update_user_last_login.assert_called_once_with('google_sub-123')


def test_guest_id_triggers_game_transfer():
    repo = _fresh_repo()
    valid_guest = 'guest_' + 'a' * 32
    app, _ = _make_app(repo)
    client = app.test_client()

    resp = _post_native(
        client,
        audiences=['ios-client-id'],
        json_body={'id_token': 'tok', 'guest_id': valid_guest},
        verify_return=VALID_CLAIMS,
    )

    assert resp.status_code == 200
    repo.transfer_game_ownership.assert_called_once()
    assert repo.transfer_game_ownership.call_args[0][0] == valid_guest


def test_malformed_guest_id_is_ignored():
    repo = _fresh_repo()
    app, _ = _make_app(repo)
    client = app.test_client()

    resp = _post_native(
        client,
        audiences=['ios-client-id'],
        json_body={'id_token': 'tok', 'guest_id': 'not-a-valid-guest'},
        verify_return=VALID_CLAIMS,
    )

    assert resp.status_code == 200
    # create_google_user is called with linked_guest_id=None (forged id dropped).
    assert repo.create_google_user.call_args.kwargs['linked_guest_id'] is None
    repo.transfer_game_ownership.assert_not_called()


def test_missing_id_token_returns_400():
    repo = _fresh_repo()
    app, _ = _make_app(repo)
    client = app.test_client()

    resp = _post_native(client, audiences=['ios-client-id'], json_body={})
    assert resp.status_code == 400
    assert resp.get_json()['success'] is False


def test_unconfigured_audiences_returns_503():
    repo = _fresh_repo()
    app, _ = _make_app(repo)
    client = app.test_client()

    resp = _post_native(
        client, audiences=[], json_body={'id_token': 'tok'}, verify_return=VALID_CLAIMS
    )
    assert resp.status_code == 503


def test_invalid_token_returns_401():
    repo = _fresh_repo()
    app, _ = _make_app(repo)
    client = app.test_client()

    resp = _post_native(
        client,
        audiences=['ios-client-id'],
        json_body={'id_token': 'tok'},
        verify_side=ValueError('bad sig'),
    )

    assert resp.status_code == 401
    assert resp.get_json()['success'] is False
    repo.create_google_user.assert_not_called()


def test_native_route_is_csrf_exempt_by_prefix():
    """The /api/auth/google/ prefix exemption must cover the native POST."""
    from flask_app.csrf import _is_protected_request

    app = Flask(__name__)
    with app.test_request_context('/api/auth/google/native', method='POST'):
        assert _is_protected_request() is False


class TestVerifyGoogleIdToken:
    """Unit tests for the token verifier's claim checks (JWKS/decode mocked)."""

    def _patch_decode(self, claims):
        # Patch both the JWKS client (network) and jwt.decode (crypto).
        return (
            patch('poker.auth._get_google_jwks_client', return_value=MagicMock()),
            patch('poker.auth.jwt.decode', return_value=claims),
        )

    def test_rejects_empty_audiences(self):
        from poker.auth import verify_google_id_token

        with pytest.raises(ValueError):
            verify_google_id_token('tok', [])

    def test_rejects_untrusted_issuer(self):
        from poker.auth import verify_google_id_token

        bad = {**VALID_CLAIMS, 'iss': 'https://evil.example'}
        p_jwks, p_decode = self._patch_decode(bad)
        with p_jwks, p_decode, pytest.raises(ValueError, match='issuer'):
            verify_google_id_token('tok', ['ios-client-id'])

    def test_rejects_unverified_email(self):
        from poker.auth import verify_google_id_token

        bad = {**VALID_CLAIMS, 'email_verified': False}
        p_jwks, p_decode = self._patch_decode(bad)
        with p_jwks, p_decode, pytest.raises(ValueError, match='not verified'):
            verify_google_id_token('tok', ['ios-client-id'])

    def test_accepts_valid_claims(self):
        from poker.auth import verify_google_id_token

        p_jwks, p_decode = self._patch_decode(dict(VALID_CLAIMS))
        with p_jwks, p_decode:
            claims = verify_google_id_token('tok', ['ios-client-id'])
        assert claims['sub'] == 'sub-123'
