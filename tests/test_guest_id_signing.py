"""Tests for T1-26 — signed guest_id cookies.

Previously the guest_id cookie was a plain value with only format
validation, so anyone who knew or guessed a valid-format guest_id
could set their own cookie to that value and impersonate the target.

The fix signs the cookie with the app SECRET_KEY using
itsdangerous.URLSafeTimedSerializer. Forged cookies fail signature
verification and are rejected; signature expiration is enforced
separately.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def auth_manager():
    """A real AuthManager wrapped around a stub Flask app with a known
    SECRET_KEY so signing is deterministic in tests."""
    from poker.auth import AuthManager

    app = MagicMock()
    app.config = {'SECRET_KEY': 'test-secret-for-sign-verify'}
    # Stub the Flask `app.add_url_rule` and route bindings — AuthManager
    # registers a few routes during __init__, but the MagicMock absorbs
    # them harmlessly.
    return AuthManager(app, user_repo=MagicMock(), oauth=None)


class TestSignVerifyRoundTrip:
    def test_sign_then_unsign_returns_original(self, auth_manager):
        original = 'guest_' + 'a' * 32
        signed = auth_manager._sign_guest_id(original)
        unsigned = auth_manager._unsign_guest_id(signed)
        assert unsigned == original

    def test_signed_value_is_different_from_raw(self, auth_manager):
        original = 'guest_' + 'a' * 32
        signed = auth_manager._sign_guest_id(original)
        # The signed payload includes the timestamp + signature
        assert signed != original
        assert '.' in signed  # itsdangerous format


class TestForgeryResistance:
    def test_raw_guest_id_without_signature_is_rejected(self, auth_manager, monkeypatch):
        """An attacker who knows a valid-format guest_id but doesn't
        have the secret can't authenticate by setting that raw value."""
        # Force production behavior so the dev-mode legacy fallback
        # doesn't accept the raw value.
        monkeypatch.setenv('FLASK_ENV', 'production')

        raw_guest_id = 'guest_' + 'b' * 32
        # This is a format-valid guest_id but unsigned
        result = auth_manager._unsign_guest_id(raw_guest_id)
        assert result is None

    def test_tampered_signature_rejected(self, auth_manager, monkeypatch):
        monkeypatch.setenv('FLASK_ENV', 'production')

        signed = auth_manager._sign_guest_id('guest_' + 'c' * 32)
        # Tamper with the last character of the signature
        tampered = signed[:-1] + ('A' if signed[-1] != 'A' else 'B')
        result = auth_manager._unsign_guest_id(tampered)
        assert result is None

    def test_signed_with_different_secret_rejected(self):
        """Cookies signed with a different SECRET_KEY are rejected."""
        from poker.auth import AuthManager

        # First manager signs
        app_a = MagicMock()
        app_a.config = {'SECRET_KEY': 'secret-a'}
        mgr_a = AuthManager(app_a, user_repo=MagicMock(), oauth=None)
        signed_by_a = mgr_a._sign_guest_id('guest_' + 'd' * 32)

        # Second manager (different secret) can't unsign it
        app_b = MagicMock()
        app_b.config = {'SECRET_KEY': 'secret-b'}
        mgr_b = AuthManager(app_b, user_repo=MagicMock(), oauth=None)
        result = mgr_b._unsign_guest_id(signed_by_a)
        assert result is None

    def test_empty_and_none_rejected(self, auth_manager):
        assert auth_manager._unsign_guest_id(None) is None
        assert auth_manager._unsign_guest_id('') is None


class TestExpiration:
    def test_expired_cookie_rejected(self, auth_manager):
        """A cookie signed long enough ago is rejected when max_age
        is shorter than the cookie's age. itsdangerous uses
        integer-second precision on its timestamps, so we use
        max_age=1 and sleep > 2s to guarantee the comparison sees
        age > max_age."""
        signed = auth_manager._sign_guest_id('guest_' + 'e' * 32)
        import time
        time.sleep(2.1)
        result = auth_manager._unsign_guest_id(signed, max_age_seconds=1)
        assert result is None

    def test_fresh_cookie_accepted_with_normal_max_age(self, auth_manager):
        """Sanity: the default 30-day max_age accepts a freshly-signed cookie."""
        signed = auth_manager._sign_guest_id('guest_' + 'f' * 32)
        result = auth_manager._unsign_guest_id(signed)
        assert result == 'guest_' + 'f' * 32


class TestLegacyDevModeCompat:
    def test_dev_mode_accepts_legacy_unsigned_cookies(self, auth_manager, monkeypatch):
        """In dev (non-production), unsigned cookies that match the
        legacy format are accepted so local dev sessions from before
        the signing change keep working. Production rejects them."""
        monkeypatch.setenv('FLASK_ENV', 'development')

        # Legacy format that predates signing
        legacy = 'guest_' + 'f' * 32
        result = auth_manager._unsign_guest_id(legacy)
        assert result == legacy

    def test_production_rejects_legacy_unsigned_cookies(self, auth_manager, monkeypatch):
        monkeypatch.setenv('FLASK_ENV', 'production')

        legacy = 'guest_' + '1' * 32
        result = auth_manager._unsign_guest_id(legacy)
        assert result is None
