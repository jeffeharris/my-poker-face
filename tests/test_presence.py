"""Tests for the cash-mode presence registry.

Covers: active while connected, TTL grace after the last socket drops,
touch as an HTTP-only keepalive, multi-tab sids, and pruning. The clock
is faked (the module reads `time.monotonic()` through `presence.time`).
See `docs/plans/CASH_MODE_REALTIME_TICKER.md`.
"""

from __future__ import annotations

import pytest

from flask_app.services import presence


class FakeClock:
    """Stand-in for the `time` module's `monotonic()`."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    presence.clear()
    clock = FakeClock()
    monkeypatch.setattr(presence, "time", clock)
    yield clock
    presence.clear()


def _owners():
    return {s.owner_id for s in presence.active_sessions()}


def test_mark_active_makes_session_active():
    presence.mark_active("u1", "sbx1", "sid-a")
    assert _owners() == {"u1"}
    assert presence.is_active("u1")


def test_session_carries_sandbox_id():
    presence.mark_active("u1", "sbx1", "sid-a")
    [session] = presence.active_sessions()
    assert session.sandbox_id == "sbx1"


def test_disconnect_keeps_session_within_grace(clean_registry):
    presence.mark_active("u1", "sbx1", "sid-a")
    presence.mark_inactive("sid-a")
    # Within the TTL grace the session is still active.
    clean_registry.advance(presence.ACTIVE_TTL_SECONDS - 1)
    assert presence.is_active("u1")
    assert _owners() == {"u1"}


def test_session_expires_after_grace(clean_registry):
    presence.mark_active("u1", "sbx1", "sid-a")
    presence.mark_inactive("sid-a")
    clean_registry.advance(presence.ACTIVE_TTL_SECONDS + 1)
    assert not presence.is_active("u1")
    assert _owners() == set()  # active_sessions() prunes it


def test_live_socket_never_expires(clean_registry):
    presence.mark_active("u1", "sbx1", "sid-a")
    # No disconnect; even far past the TTL it stays active.
    clean_registry.advance(presence.ACTIVE_TTL_SECONDS * 10)
    assert presence.is_active("u1")


def test_multi_tab_stays_active_until_all_sids_drop(clean_registry):
    presence.mark_active("u1", "sbx1", "sid-a")
    presence.mark_active("u1", "sbx1", "sid-b")
    presence.mark_inactive("sid-a")
    clean_registry.advance(presence.ACTIVE_TTL_SECONDS * 5)
    # sid-b is still live, so the session stays active.
    assert presence.is_active("u1")
    presence.mark_inactive("sid-b")
    clean_registry.advance(presence.ACTIVE_TTL_SECONDS + 1)
    assert not presence.is_active("u1")


def test_touch_keeps_http_only_client_alive(clean_registry):
    # No socket ever connects; the lobby GET touches instead.
    presence.touch("u1", "sbx1")
    assert presence.is_active("u1")
    clean_registry.advance(presence.ACTIVE_TTL_SECONDS - 1)
    presence.touch("u1", "sbx1")  # poll refreshes the grace clock
    clean_registry.advance(presence.ACTIVE_TTL_SECONDS - 1)
    assert presence.is_active("u1")


def test_lobby_room_name():
    assert presence.lobby_room_name("u1") == "lobby:u1"
