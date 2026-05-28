"""Tier 3: the sit guard respects the explicit session_state.

`_find_active_cash_game_id` / `_cash_session_blocks`
(`flask_app/routes/cash_routes.py`) used to treat *any* `cash-*` games
row (in memory or DB) as an active session. Post-Tier-3 they consult
`cash_sessions.session_state`: a `closed`/`broken` session whose row
lingers no longer wedges new sits, while `active`/`paused` and
legacy-no-row sessions still block (fail-safe).

Driven with fakes patched onto `flask_app.extensions` +
`flask_app.services.game_state_service` so it stays hermetic.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration

# `cash_routes` binds `flask_app.extensions.limiter` at import time for its
# @limiter.limit(...) route decorators; that's None until create_app() runs.
# Install a no-op limiter so this module can import cash_routes standalone
# (a full create_app would drag in the boot hook + real DB side effects we
# don't want for a pure-function guard test). No-op if a real limiter is
# already in place (another test created the app first).
import flask_app.extensions as _ext  # noqa: E402


class _NoopLimiter:
    """Identity limiter. `limit`/`shared_limit` are decorator factories;
    `exempt` is a direct decorator. All just return the function."""

    def limit(self, *_a, **_k):
        def _deco(fn):
            return fn

        return _deco

    shared_limit = limit

    def exempt(self, fn):
        return fn


if _ext.limiter is None:
    _ext.limiter = _NoopLimiter()

import flask_app.services.game_state_service as gss_module  # noqa: E402
from flask_app.routes import cash_routes  # noqa: E402


class _FakeCashSessionRepo:
    def __init__(self, sessions, blocking_id=None):
        # game_id -> session_state string (or KeyError → None/missing row)
        self._states = sessions
        # What find_blocking_session_id_for_owner returns (the authoritative
        # DB lookup). None → no blocking session, fall through to the net.
        self._blocking_id = blocking_id

    def load(self, session_id):
        if session_id not in self._states:
            return None
        return SimpleNamespace(session_state=self._states[session_id])

    def find_blocking_session_id_for_owner(self, owner_id):
        return self._blocking_id


class _FakeGameRepo:
    def __init__(self, rows):
        self._rows = rows

    def list_games(self, owner_id=None, limit=50, offset=0):
        return list(self._rows)


def _row(game_id, owner_id="u1"):
    return SimpleNamespace(game_id=game_id, owner_id=owner_id)


# --- _cash_session_blocks ------------------------------------------------


def _blocks(game_id, states):
    with patch("flask_app.extensions.cash_session_repo", _FakeCashSessionRepo(states), create=True):
        return cash_routes._cash_session_blocks(game_id)


def test_active_session_blocks():
    assert _blocks("cash-a", {"cash-a": "active"}) is True


def test_paused_session_blocks():
    assert _blocks("cash-a", {"cash-a": "paused"}) is True


def test_closed_session_does_not_block():
    assert _blocks("cash-a", {"cash-a": "closed"}) is False


def test_broken_session_does_not_block():
    assert _blocks("cash-a", {"cash-a": "broken"}) is False


def test_missing_session_row_blocks_failsafe():
    # No cash_sessions row at all (legacy / sit that errored before the
    # row landed) → block, so a real frozen session is never lost.
    assert _blocks("cash-a", {}) is True


# --- _find_active_cash_game_id -------------------------------------------


def test_find_active_skips_closed_in_memory_game():
    """A resurrected closed session sitting in memory must NOT count as
    active — the guard returns None so the player can sit elsewhere."""
    games = {"cash-closed": {"cash_mode": True, "owner_id": "u1"}}
    with (
        patch.dict(gss_module.games, games, clear=True),
        patch(
            "flask_app.extensions.cash_session_repo",
            _FakeCashSessionRepo({"cash-closed": "closed"}),
            create=True,
        ),
        patch("flask_app.extensions.game_repo", _FakeGameRepo([]), create=True),
    ):
        assert cash_routes._find_active_cash_game_id("u1") is None


def test_find_active_returns_active_in_memory_game():
    games = {"cash-live": {"cash_mode": True, "owner_id": "u1"}}
    with (
        patch.dict(gss_module.games, games, clear=True),
        patch(
            "flask_app.extensions.cash_session_repo",
            _FakeCashSessionRepo({"cash-live": "active"}),
            create=True,
        ),
        patch("flask_app.extensions.game_repo", _FakeGameRepo([]), create=True),
    ):
        assert cash_routes._find_active_cash_game_id("u1") == "cash-live"


def test_find_active_uses_direct_query_not_capped_scan():
    """Codex #4: a blocking session is found via the unbounded direct
    cash_sessions query even when the (capped) games scan would miss it
    (here game_repo returns nothing)."""
    repo = _FakeCashSessionRepo({}, blocking_id="cash-db-active")
    with (
        patch.dict(gss_module.games, {}, clear=True),
        patch("flask_app.extensions.cash_session_repo", repo, create=True),
        patch("flask_app.extensions.game_repo", _FakeGameRepo([]), create=True),
    ):
        assert cash_routes._find_active_cash_game_id("u1") == "cash-db-active"


def test_find_active_legacy_net_catches_rowless_orphan():
    """When the direct query finds nothing (None), a cash-* games row with
    NO cash_sessions record still blocks via the fail-safe net — a real
    frozen session is never lost to a missing-row read."""
    repo = _FakeCashSessionRepo({}, blocking_id=None)  # direct query → None
    with (
        patch.dict(gss_module.games, {}, clear=True),
        patch("flask_app.extensions.cash_session_repo", repo, create=True),
        patch(
            "flask_app.extensions.game_repo",
            _FakeGameRepo([_row("cash-rowless-orphan")]),
            create=True,
        ),
    ):
        assert cash_routes._find_active_cash_game_id("u1") == "cash-rowless-orphan"


def test_find_active_skips_broken_db_row():
    """A broken session lingering only in the DB must not block sits."""
    with (
        patch.dict(gss_module.games, {}, clear=True),
        patch(
            "flask_app.extensions.cash_session_repo",
            _FakeCashSessionRepo({"cash-broken": "broken"}),
            create=True,
        ),
        patch(
            "flask_app.extensions.game_repo",
            _FakeGameRepo([_row("cash-broken")]),
            create=True,
        ),
    ):
        assert cash_routes._find_active_cash_game_id("u1") is None


def test_find_active_returns_active_db_row():
    with (
        patch.dict(gss_module.games, {}, clear=True),
        patch(
            "flask_app.extensions.cash_session_repo",
            _FakeCashSessionRepo({"cash-paused": "paused"}),
            create=True,
        ),
        patch(
            "flask_app.extensions.game_repo",
            _FakeGameRepo([_row("cash-paused")]),
            create=True,
        ),
    ):
        assert cash_routes._find_active_cash_game_id("u1") == "cash-paused"
