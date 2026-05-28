"""Tests for the ticker's stale-session watchdog (T2.3).

The watchdog (`flask_app.services.ticker_service._maybe_run_stale_session_watchdog`)
periodically reaps abandoned cash-* rows so an orphan created between
reboots self-clears instead of wedging the sit guard. It must:

  - be rate-limited to WATCHDOG_INTERVAL_SECONDS,
  - skip cash games currently in memory (a live copy would re-save a
    deleted row — the resurrection race — and the player may still be
    seated),
  - leave fresh (within-TTL) rows alone.

Driven with fakes patched onto `flask_app.extensions` +
`flask_app.services.game_state_service` so it stays hermetic. Row ages
use the real `datetime.utcnow()` clock (a 2h-old row is reliably stale,
a 1-min-old row reliably fresh), so there's no need to pin the clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration

import flask_app.services.game_state_service as gss_module
from flask_app.services import ticker_service


class _FakeGameRepo:
    def __init__(self, rows):
        self._rows = list(rows)
        self.deleted = []

    def list_games(self, owner_id=None, limit=10000, offset=0):
        return list(self._rows)

    def delete_game(self, game_id):
        self.deleted.append(game_id)
        self._rows = [r for r in self._rows if r.game_id != game_id]


class _FakeCashSessionRepo:
    def __init__(self, sessions):
        self._sessions = sessions
        self.finalised = []

    def load(self, session_id):
        return self._sessions.get(session_id)

    def finalise(self, session_id, *, ended_at, closed_status, **_ignored):
        self.finalised.append((session_id, closed_status))
        s = self._sessions.get(session_id)
        if s is not None:
            s.ended_at = ended_at
        return True


def _row(game_id, age_seconds, *, owner_id="u1"):
    return SimpleNamespace(
        game_id=game_id,
        owner_id=owner_id,
        updated_at=datetime.utcnow() - timedelta(seconds=age_seconds),
    )


def _session(session_id):
    return SimpleNamespace(
        session_id=session_id,
        ended_at=None,
        sandbox_id="sb",
        hands_played=0,
        hands_won=0,
        biggest_pot_won=0,
    )


def _run_watchdog(*, rows, sessions, in_memory, mono):
    """Drive one watchdog pass with fakes patched in. Returns (swept, game_repo, sessions_repo)."""
    game_repo = _FakeGameRepo(rows)
    cash_session_repo = _FakeCashSessionRepo(sessions)

    with patch.object(ticker_service, "_last_watchdog_at", None), patch.dict(
        gss_module.games, in_memory, clear=True
    ), patch(
        "flask_app.extensions.cash_session_repo", cash_session_repo, create=True
    ), patch(
        "flask_app.extensions.game_repo", game_repo, create=True
    ), patch(
        "flask_app.extensions.stake_repo", None, create=True
    ), patch(
        "flask_app.extensions.chip_ledger_repo", None, create=True
    ):
        swept = ticker_service._maybe_run_stale_session_watchdog(now_monotonic=mono)
    return swept, game_repo, cash_session_repo


def test_sweeps_stale_cold_orphan():
    swept, game_repo, sessions = _run_watchdog(
        rows=[_row("cash-cold", 7200)],
        sessions={"cash-cold": _session("cash-cold")},
        in_memory={},
        mono=1000.0,
    )
    assert swept == 1
    assert "cash-cold" in game_repo.deleted
    # The watchdog tags its sweeps `stale_swept` (vs the boot hook's
    # `boot_swept`) so ops can tell the two reconcilers apart.
    assert ("cash-cold", "stale_swept") in sessions.finalised


def test_skips_in_memory_game_even_if_row_is_stale():
    swept, game_repo, _ = _run_watchdog(
        rows=[_row("cash-live", 7200)],
        sessions={"cash-live": _session("cash-live")},
        # Same id is in memory → must be skipped (resurrection guard).
        in_memory={"cash-live": {"cash_mode": True, "owner_id": "u1"}},
        mono=1000.0,
    )
    assert swept == 0
    assert game_repo.deleted == []


def test_leaves_fresh_row_alone():
    swept, game_repo, _ = _run_watchdog(
        rows=[_row("cash-fresh", 60)],
        sessions={"cash-fresh": _session("cash-fresh")},
        in_memory={},
        mono=1000.0,
    )
    assert swept == 0
    assert game_repo.deleted == []


def test_rate_limited_within_interval():
    """A second call inside WATCHDOG_INTERVAL_SECONDS is a no-op."""
    game_repo = _FakeGameRepo([])
    cash_session_repo = _FakeCashSessionRepo({})
    with patch.dict(gss_module.games, {}, clear=True), patch(
        "flask_app.extensions.cash_session_repo", cash_session_repo, create=True
    ), patch("flask_app.extensions.game_repo", game_repo, create=True), patch(
        "flask_app.extensions.stake_repo", None, create=True
    ), patch(
        "flask_app.extensions.chip_ledger_repo", None, create=True
    ), patch.object(
        ticker_service, "_last_watchdog_at", None
    ):
        # First call at t=1000 runs (stamps _last_watchdog_at).
        ticker_service._maybe_run_stale_session_watchdog(now_monotonic=1000.0)
        first_stamp = ticker_service._last_watchdog_at
        assert first_stamp == 1000.0
        # Second call 10s later is inside the interval → no-op.
        result = ticker_service._maybe_run_stale_session_watchdog(now_monotonic=1010.0)
        assert result == 0
        assert ticker_service._last_watchdog_at == first_stamp
