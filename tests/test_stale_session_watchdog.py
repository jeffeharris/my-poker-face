"""Tests for the ticker's stale-session watchdog (T2.3).

The watchdog (`flask_app.services.ticker_service._maybe_run_stale_session_watchdog`)
periodically GC's *dead* abandoned cash-* rows so an orphan created
between reboots self-clears instead of wedging the sit guard. It must:

  - be rate-limited to WATCHDOG_INTERVAL_SECONDS,
  - skip cash games currently in memory (a live copy would re-save a
    deleted row — the resurrection race — and the player may still be
    seated),
  - leave fresh (within-TTL) rows alone,
  - **NEVER touch a resumable session** (active/paused/abandoning) — the
    freeze-forever guard (CASH_MODE_STATE_MODEL.md §5.4, §10 Cut 1). A
    blocking session IS the player's frozen table; zeroing its chips and
    deleting its games row was the silent-forfeiture bug. The watchdog
    only GC's genuinely-dead rows: closed/broken sessions, or sessionless
    orphans (a sit that errored before its session row landed), both of
    which carry no live chips.

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

    def set_session_state(self, session_id, state):
        s = self._sessions.get(session_id)
        if s is not None:
            s.session_state = state
        return True


def _row(game_id, age_seconds, *, owner_id="u1"):
    return SimpleNamespace(
        game_id=game_id,
        owner_id=owner_id,
        updated_at=datetime.utcnow() - timedelta(seconds=age_seconds),
    )


def _session(session_id, *, session_state="active", ended_at=None):
    return SimpleNamespace(
        session_id=session_id,
        ended_at=ended_at,
        sandbox_id="sb",
        hands_played=0,
        hands_won=0,
        biggest_pot_won=0,
        session_state=session_state,
    )


def _run_watchdog(*, rows, sessions, in_memory, mono):
    """Drive one watchdog pass with fakes patched in. Returns (swept, game_repo, sessions_repo)."""
    game_repo = _FakeGameRepo(rows)
    cash_session_repo = _FakeCashSessionRepo(sessions)

    with (
        patch.object(ticker_service, "_last_watchdog_at", None),
        patch.dict(gss_module.games, in_memory, clear=True),
        patch("flask_app.extensions.cash_session_repo", cash_session_repo, create=True),
        patch("flask_app.extensions.game_repo", game_repo, create=True),
        patch("flask_app.extensions.stake_repo", None, create=True),
        patch("flask_app.extensions.chip_ledger_repo", None, create=True),
    ):
        swept = ticker_service._maybe_run_stale_session_watchdog(now_monotonic=mono)
    return swept, game_repo, cash_session_repo


# Comfortably past the watchdog's (4h) TTL, tracked off the constant so a
# future TTL change doesn't silently turn "stale" into "fresh" here.
_STALE_AGE = int(ticker_service.STALE_SESSION_TTL_SECONDS) + 3600


def test_preserves_stale_active_session():
    """Freeze-forever guard: a stale but RESUMABLE (active) session is the
    player's frozen table — it must never be swept, finalised, or deleted,
    no matter how cold. This is the regression test for the silent-
    forfeiture bug that zeroed real buy-ins (CASH_MODE_STATE_MODEL.md §5.4).
    """
    swept, game_repo, sessions = _run_watchdog(
        rows=[_row("cash-frozen", _STALE_AGE)],
        sessions={"cash-frozen": _session("cash-frozen", session_state="active")},
        in_memory={},
        mono=1000.0,
    )
    assert swept == 0
    assert game_repo.deleted == []
    assert sessions.finalised == []


@pytest.mark.parametrize("state", ["paused", "abandoning"])
def test_preserves_other_blocking_states(state):
    """paused / abandoning are also resumable-blocking — never swept."""
    swept, game_repo, sessions = _run_watchdog(
        rows=[_row("cash-block", _STALE_AGE)],
        sessions={"cash-block": _session("cash-block", session_state=state)},
        in_memory={},
        mono=1000.0,
    )
    assert swept == 0
    assert game_repo.deleted == []
    assert sessions.finalised == []


def test_sweeps_dead_closed_session_row():
    """A `closed` session whose games row lingers is dead weight — GC it.
    It's already finalised (ended_at set), so no re-finalise; just delete
    the row. Carries no live chips.
    """
    swept, game_repo, sessions = _run_watchdog(
        rows=[_row("cash-dead", _STALE_AGE)],
        sessions={
            "cash-dead": _session("cash-dead", session_state="closed", ended_at=datetime.utcnow())
        },
        in_memory={},
        mono=1000.0,
    )
    assert swept == 1
    assert "cash-dead" in game_repo.deleted
    # Already finalised → the ended_at guard means no second finalise.
    assert sessions.finalised == []


def test_sweeps_sessionless_orphan():
    """A cash-* games row with NO cash_sessions record (a sit that errored
    before its session row landed) wedges the sit guard's fail-safe and
    carries no chips — GC it.
    """
    swept, game_repo, sessions = _run_watchdog(
        rows=[_row("cash-orphan", _STALE_AGE)],
        sessions={},  # no session row at all
        in_memory={},
        mono=1000.0,
    )
    assert swept == 1
    assert "cash-orphan" in game_repo.deleted


def test_skips_in_memory_game_even_if_row_is_stale():
    swept, game_repo, _ = _run_watchdog(
        rows=[_row("cash-live", _STALE_AGE)],
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
    with (
        patch.dict(gss_module.games, {}, clear=True),
        patch("flask_app.extensions.cash_session_repo", cash_session_repo, create=True),
        patch("flask_app.extensions.game_repo", game_repo, create=True),
        patch("flask_app.extensions.stake_repo", None, create=True),
        patch("flask_app.extensions.chip_ledger_repo", None, create=True),
        patch.object(ticker_service, "_last_watchdog_at", None),
    ):
        # First call at t=1000 runs (stamps _last_watchdog_at).
        ticker_service._maybe_run_stale_session_watchdog(now_monotonic=1000.0)
        first_stamp = ticker_service._last_watchdog_at
        assert first_stamp == 1000.0
        # Second call 10s later is inside the interval → no-op.
        result = ticker_service._maybe_run_stale_session_watchdog(now_monotonic=1010.0)
        assert result == 0
        assert ticker_service._last_watchdog_at == first_stamp
