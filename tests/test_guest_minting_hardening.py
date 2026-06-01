"""Tests for PRH-26 guest-minting hardening.

Covers the security-critical auth-layer logic:
- the signed guest_tracking_id cookie + IP-derived fallback resolver (so a
  forged/cleared cookie can't mint a fresh hand quota),
- the "is this a fresh guest mint?" predicate the rate-limit keys on,
- the username/password login stub now refusing (was minting limit-free
  sessions).

The bot-coercion (guests forced to 'sharp') lives inline in the new-game route
and is exercised at the route level, not here.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def auth_manager():
    """Real AuthManager over a stub app with a known SECRET_KEY (deterministic
    signing), mirroring tests/test_guest_id_signing.py."""
    from poker.auth import AuthManager

    app = MagicMock()
    app.config = {'SECRET_KEY': 'test-secret-prh26'}
    return AuthManager(app, user_repo=MagicMock(), oauth=None)


class FakeRequest:
    """Minimal stand-in for flask.request for the cookie/IP/json reads."""

    def __init__(self, *, cookies=None, remote_addr='198.51.100.7', json_data=None):
        self.cookies = cookies or {}
        self.remote_addr = remote_addr
        self._json = json_data

    def get_json(self, silent=False):
        return self._json


def _patch_request(monkeypatch, req):
    monkeypatch.setattr('poker.auth.request', req)


# --- signed tracking cookie round trip --------------------------------------


class TestTrackingIdSigning:
    def test_sign_unsign_round_trip(self, auth_manager):
        raw = 'ipguest_' + 'a' * 32
        signed = auth_manager._sign_tracking_id(raw)
        assert signed != raw and '.' in signed
        assert auth_manager._unsign_tracking_id(signed) == raw

    def test_forged_cookie_rejected_in_prod(self, auth_manager, monkeypatch):
        monkeypatch.setenv('FLASK_ENV', 'production')
        # A random value the attacker made up — no valid signature.
        assert auth_manager._unsign_tracking_id('not-a-signed-value') is None

    def test_absent_cookie_is_none(self, auth_manager):
        assert auth_manager._unsign_tracking_id(None) is None


# --- resolve_guest_tracking_id ---------------------------------------------


class TestResolveTrackingId:
    def test_valid_signed_cookie_wins(self, auth_manager, monkeypatch):
        raw = 'ipguest_' + 'b' * 32
        signed = auth_manager._sign_tracking_id(raw)
        _patch_request(monkeypatch, FakeRequest(cookies={'guest_tracking_id': signed}))
        assert auth_manager.resolve_guest_tracking_id() == raw

    def test_forged_cookie_falls_back_to_ip_derived(self, auth_manager, monkeypatch):
        monkeypatch.setenv('FLASK_ENV', 'production')
        _patch_request(
            monkeypatch,
            FakeRequest(cookies={'guest_tracking_id': 'forged'}, remote_addr='203.0.113.9'),
        )
        resolved = auth_manager.resolve_guest_tracking_id()
        assert resolved is not None and resolved.startswith('ipguest_')

    def test_forged_cookie_same_ip_resolves_same_bucket(self, auth_manager, monkeypatch):
        """The whole point: rotating a forged cookie can't mint a new quota —
        same IP -> same id."""
        monkeypatch.setenv('FLASK_ENV', 'production')
        _patch_request(monkeypatch, FakeRequest(cookies={'guest_tracking_id': 'forged-A'}))
        a = auth_manager.resolve_guest_tracking_id()
        _patch_request(monkeypatch, FakeRequest(cookies={'guest_tracking_id': 'forged-B'}))
        b = auth_manager.resolve_guest_tracking_id()
        assert a == b

    def test_absent_cookie_uses_ip_derived(self, auth_manager, monkeypatch):
        _patch_request(monkeypatch, FakeRequest(cookies={}, remote_addr='192.0.2.5'))
        resolved = auth_manager.resolve_guest_tracking_id()
        assert resolved is not None and resolved.startswith('ipguest_')

    def test_no_cookie_no_ip_is_none(self, auth_manager, monkeypatch):
        _patch_request(monkeypatch, FakeRequest(cookies={}, remote_addr=None))
        assert auth_manager.resolve_guest_tracking_id() is None


# --- minting predicate (rate-limit key) ------------------------------------


class TestGuestMintingRequest:
    def test_guest_without_cookie_is_minting(self, auth_manager, monkeypatch):
        _patch_request(monkeypatch, FakeRequest(cookies={}, json_data={'guest': True}))
        assert auth_manager._guest_minting_request() is True

    def test_returning_guest_is_not_minting(self, auth_manager, monkeypatch):
        guest_id = 'guest_' + 'c' * 32
        signed = auth_manager._sign_guest_id(guest_id)
        _patch_request(
            monkeypatch,
            FakeRequest(cookies={'guest_id': signed}, json_data={'guest': True}),
        )
        assert auth_manager._guest_minting_request() is False

    def test_password_login_is_not_minting(self, auth_manager, monkeypatch):
        _patch_request(
            monkeypatch,
            FakeRequest(json_data={'username': 'x', 'password': 'y'}),
        )
        assert auth_manager._guest_minting_request() is False


# --- guest bot coercion -----------------------------------------------------


class TestGuestBotCoercion:
    def test_guest_forced_to_sharp(self):
        from flask_app.routes.game_routes import _guest_safe_bot_types

        out = _guest_safe_bot_types({'Bot1': 'chaos', 'Bot2': 'standard'}, enforce_guest=True)
        assert out == {'Bot1': 'sharp', 'Bot2': 'sharp'}

    def test_member_selection_preserved(self):
        from flask_app.routes.game_routes import _guest_safe_bot_types

        original = {'Bot1': 'chaos', 'Bot2': 'sharp'}
        assert _guest_safe_bot_types(original, enforce_guest=False) == original

    def test_empty_is_empty(self):
        from flask_app.routes.game_routes import _guest_safe_bot_types

        assert _guest_safe_bot_types({}, enforce_guest=True) == {}
