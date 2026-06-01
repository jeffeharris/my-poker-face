"""Double-submit CSRF protection (PRH-36).

Exercises ``flask_app.csrf.init_csrf`` directly on a minimal app with
representative routes. The gate's behavior is purely path/method/cookie/header
based, so a bare app validates it fully — and, unlike the real ``create_app()``,
it carries no prod-DB / ticker / boot-hook footprint, so it stays a clean
xdist citizen (an 8×``create_app()`` fixture perturbs scheduling and surfaces
unrelated tests' isolation fragility).

The test client tracks cookies across requests like a browser, so a GET that
mints the ``csrf_token`` cookie is auto-sent on the following POST.
"""

import pytest
from flask import Flask, jsonify

from flask_app.csrf import init_csrf

pytestmark = pytest.mark.flask


@pytest.fixture
def app():
    app = Flask(__name__)
    app.secret_key = 'csrf-test-secret'
    # Arm the gate for this app; individual tests may flip it off live.
    app.config['CSRF_PROTECTION_ENABLED'] = True
    init_csrf(app)

    # A generic protected mutating route + the real exempt paths.
    @app.route('/api/widget', methods=['POST'])
    def widget():
        return jsonify(ok=True)

    @app.route('/api/auth/me', methods=['GET'])
    def me():
        return jsonify(ok=True)

    @app.route('/api/auth/login', methods=['POST'])
    def login():
        return jsonify(ok=True)

    @app.route('/api/auth/logout', methods=['POST'])
    def logout():
        return jsonify(ok=True)

    @app.route('/api/auth/google/callback', methods=['GET', 'POST'])
    def google_callback():
        return jsonify(ok=True)

    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _establish_csrf_cookie(client) -> str:
    """Hit a GET so after_request mints the csrf_token cookie; return its value.

    The client retains the cookie and auto-sends it on subsequent requests.
    """
    client.get('/api/auth/me')
    cookie = client.get_cookie('csrf_token')
    assert cookie is not None, "after_request should mint a csrf_token cookie"
    return cookie.value


def test_after_request_issues_token_cookie(client):
    token = _establish_csrf_cookie(client)
    assert token  # a non-HttpOnly csrf_token cookie is set on a normal GET


def test_mutating_request_without_header_is_rejected(client):
    _establish_csrf_cookie(client)  # cookie auto-sent, but no header
    resp = client.post('/api/widget')
    assert resp.status_code == 403
    assert resp.get_json()['code'] == 'CSRF_FAILED'


def test_mutating_request_with_matching_header_passes(client):
    token = _establish_csrf_cookie(client)
    resp = client.post('/api/widget', headers={'X-CSRF-Token': token})
    assert resp.status_code == 200


def test_mismatched_header_is_rejected(client):
    _establish_csrf_cookie(client)
    resp = client.post('/api/widget', headers={'X-CSRF-Token': 'not-the-token'})
    assert resp.status_code == 403
    assert resp.get_json()['code'] == 'CSRF_FAILED'


def test_missing_cookie_is_rejected(client):
    # Header present, but no cookie was ever established → still rejected
    # (a forged cross-site request can't satisfy both halves).
    resp = client.post('/api/widget', headers={'X-CSRF-Token': 'anything'})
    assert resp.status_code == 403


def test_login_is_exempt(client):
    """Auth bootstrap (/api/auth/login) is exempt — it can run before a token
    reliably exists, so a missing header must not 403 it."""
    _establish_csrf_cookie(client)
    resp = client.post('/api/auth/login')  # no header
    assert resp.status_code == 200


def test_oauth_callback_is_exempt(client):
    _establish_csrf_cookie(client)
    resp = client.post('/api/auth/google/callback')  # no header
    assert resp.status_code == 200


def test_options_preflight_is_exempt(client):
    _establish_csrf_cookie(client)
    resp = client.open('/api/widget', method='OPTIONS')
    assert resp.status_code != 403


def test_get_requests_are_never_gated(client):
    # No cookie/header at all — a GET must still pass (only mutations are gated).
    resp = client.get('/api/auth/me')
    assert resp.status_code == 200


def test_disabled_flag_allows_unprotected_mutations(client):
    client.application.config['CSRF_PROTECTION_ENABLED'] = False
    resp = client.post('/api/widget')  # no header, no cookie
    assert resp.status_code == 200
