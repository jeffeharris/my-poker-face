"""PRH-41: rate-limit key binds real accounts per-user (not per-IP)."""

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.flask


def _key_with(user):
    """Call get_rate_limit_key with a stubbed auth_manager + remote address."""
    from flask_app import extensions

    am = MagicMock()
    am.get_current_user.return_value = user
    with (
        patch.object(extensions, "auth_manager", am),
        patch.object(extensions, "get_remote_address", return_value="9.9.9.9"),
    ):
        return extensions.get_rate_limit_key()


def test_oauth_account_keyed_per_user():
    assert _key_with({"id": "google_42", "is_guest": False}) == "user:google_42"


def test_guest_falls_back_to_ip():
    # Guest ids are cookie-resettable → IP-keyed (minting is throttled per IP).
    assert _key_with({"id": "guest_abc", "is_guest": True}) == "9.9.9.9"


def test_anonymous_falls_back_to_ip():
    assert _key_with(None) == "9.9.9.9"


def test_auth_failure_falls_back_to_ip():
    from flask_app import extensions

    am = MagicMock()
    am.get_current_user.side_effect = RuntimeError("boom")
    with (
        patch.object(extensions, "auth_manager", am),
        patch.object(extensions, "get_remote_address", return_value="9.9.9.9"),
    ):
        assert extensions.get_rate_limit_key() == "9.9.9.9"
