"""CSRF: bearer-authenticated (native) requests are exempt; cookie auth is not.

Native iOS/Android clients authenticate with `Authorization: Bearer` and can't
participate in the double-submit-cookie scheme, so mutating calls must skip the
CSRF gate — otherwise every action/chat 403s in production (where CSRF is on).
Cookie-authenticated web requests must still be protected.
"""

import pytest
from flask import Flask

from flask_app.csrf import _is_protected_request

pytestmark = pytest.mark.flask


@pytest.fixture
def app():
    return Flask(__name__)


def test_bearer_mutating_api_request_is_exempt(app):
    with app.test_request_context(
        '/api/game/g1/action', method='POST', headers={'Authorization': 'Bearer tok'}
    ):
        assert _is_protected_request() is False


def test_cookie_mutating_api_request_is_protected(app):
    # No bearer header → the cookie-auth (web SPA) path → still CSRF-protected.
    with app.test_request_context('/api/game/g1/action', method='POST'):
        assert _is_protected_request() is True


def test_non_bearer_authorization_still_protected(app):
    # A non-Bearer scheme isn't the native path; don't widen the hole.
    with app.test_request_context(
        '/api/game/g1/action', method='POST', headers={'Authorization': 'Basic xyz'}
    ):
        assert _is_protected_request() is True


def test_get_is_never_protected(app):
    with app.test_request_context(
        '/api/game/g1/action', method='GET', headers={'Authorization': 'Bearer tok'}
    ):
        assert _is_protected_request() is False
