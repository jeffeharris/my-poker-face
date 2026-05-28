"""Tests for the guest free-text chat lock (PRH-27).

`check_guest_free_chat` blocks anonymous (guest) users from sending free
typed text — which would reach the AI prompt verbatim — while leaving
structured quick-chat (a recognized tone) allowed. Enforcement is gated on
the module-level `GUEST_LIMITS_ENABLED` / `GUEST_FREE_CHAT_ENABLED` flags,
which we patch here so the test is independent of dev-vs-prod mode.
"""

import pytest

from poker import guest_limits
from poker.guest_limits import check_guest_free_chat

GUEST = {"id": "guest_abc", "is_guest": True}
MEMBER = {"id": "user_xyz", "is_guest": False}


@pytest.fixture
def enforced(monkeypatch):
    """Force guest-limit enforcement on with free chat locked (prod default)."""
    monkeypatch.setattr(guest_limits, "GUEST_LIMITS_ENABLED", True)
    monkeypatch.setattr(guest_limits, "GUEST_FREE_CHAT_ENABLED", False)


def test_guest_free_text_is_blocked(enforced):
    allowed, msg = check_guest_free_chat(GUEST, has_structured_tone=False)
    assert allowed is False
    assert msg and "Sign in" in msg


def test_guest_structured_tone_is_allowed(enforced):
    # Quick-chat (a recognized tone) is bounded, not free text.
    allowed, msg = check_guest_free_chat(GUEST, has_structured_tone=True)
    assert allowed is True
    assert msg is None


def test_anonymous_no_user_is_treated_as_guest(enforced):
    # is_guest(None) -> True, so a missing user is gated like a guest.
    allowed, _ = check_guest_free_chat(None, has_structured_tone=False)
    assert allowed is False


def test_member_free_text_is_allowed(enforced):
    allowed, msg = check_guest_free_chat(MEMBER, has_structured_tone=False)
    assert allowed is True
    assert msg is None


def test_dev_mode_bypass(monkeypatch):
    # When guest limits are disabled (dev default), nothing is gated.
    monkeypatch.setattr(guest_limits, "GUEST_LIMITS_ENABLED", False)
    monkeypatch.setattr(guest_limits, "GUEST_FREE_CHAT_ENABLED", False)
    allowed, msg = check_guest_free_chat(GUEST, has_structured_tone=False)
    assert allowed is True
    assert msg is None


def test_env_optin_reopens_free_chat(monkeypatch):
    # GUEST_FREE_CHAT_ENABLED=true is the escape hatch to allow guest free chat.
    monkeypatch.setattr(guest_limits, "GUEST_LIMITS_ENABLED", True)
    monkeypatch.setattr(guest_limits, "GUEST_FREE_CHAT_ENABLED", True)
    allowed, msg = check_guest_free_chat(GUEST, has_structured_tone=False)
    assert allowed is True
    assert msg is None
