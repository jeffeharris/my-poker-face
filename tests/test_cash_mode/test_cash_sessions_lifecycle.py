"""End-to-end checks for the v108 `cash_sessions` lifecycle.

Pins the contract the leave route depends on:

  1. Sit-down (`record_cash_session_start`) inserts a row with
     `total_buy_in == initial_buy_in` and `ended_at IS NULL`.
  2. Top-up / rebuy (`increment_cash_session_buy_in`) bumps
     `total_buy_in` so leave-time P&L doesn't over-report profit by
     every chip put in mid-session.
  3. Leave (`summarize_cash_session` + `finalise_cash_session`) reads
     buy-in / started_at from the durable row, persists end-of-session
     fields, and produces a staked-aware summary.

These tests exercise the helpers + summary directly. The HTTP route /
lock plumbing is covered by `test_leave_race.py`.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cash_mode.cash_session_persistence import (
    finalise_cash_session,
    increment_cash_session_buy_in,
    record_cash_session_start,
)
from cash_mode.cash_sessions import (
    CLOSED_STATUS_GHOST_CLEANUP,
    CLOSED_STATUS_LEFT,
)
from cash_mode.session_summary import summarize_cash_session
from poker.repositories.cash_session_repository import CashSessionRepository
from poker.repositories.schema_manager import SchemaManager

ANCHOR = datetime(2026, 5, 22, 12, 0, 0)
OWNER_ID = "alice"
GAME_ID = "cash-lifecycle-1"


@pytest.fixture
def repo():
    """Tempdb-backed repo. Each test starts from an empty cash_sessions."""
    with tempfile.TemporaryDirectory() as d:
        db_path = str(Path(d) / "test.db")
        SchemaManager(db_path).ensure_schema()
        yield CashSessionRepository(db_path)


def _summary_from_row(
    repo, game_id, *, cash_out, sponsor_repaid=0, player_take_home=None, now=None
):
    """Build a summary dict the way the leave route does — from the durable row."""
    session = repo.load(game_id)
    is_staked = session.is_staked if session is not None else False
    sponsor_principal = session.sponsor_principal if session is not None else 0
    buy_in = session.total_buy_in if session is not None else 0
    started_at = session.started_at if session is not None else None
    return summarize_cash_session(
        hands=[],
        human_name="Hero",
        buy_in=buy_in,
        cash_out=cash_out,
        started_at=started_at,
        now=now or datetime.utcnow(),
        is_staked=is_staked,
        sponsor_principal=sponsor_principal,
        sponsor_repaid=sponsor_repaid,
        player_take_home=player_take_home,
    )


def test_record_session_start_inserts_self_funded_row(repo):
    """Self-funded sit-down: initial_buy_in == total_buy_in, no stake."""
    record_cash_session_start(
        cash_session_repo=repo,
        game_id=GAME_ID,
        owner_id=OWNER_ID,
        sandbox_id="sbx-1",
        stake_label="$10",
        initial_buy_in=800,
        cash_table_id="lobby_$10",
        cash_seat_index=2,
        now=ANCHOR,
    )

    row = repo.load(GAME_ID)
    assert row is not None
    assert row.initial_buy_in == 800
    assert row.total_buy_in == 800
    assert row.sponsor_principal == 0
    assert row.is_staked is False
    assert row.stake_id is None
    assert row.cash_table_id == "lobby_$10"
    assert row.cash_seat_index == 2
    assert row.started_at == ANCHOR
    assert row.ended_at is None


def test_record_session_start_inserts_staked_row(repo):
    """Staked sit-down: initial_buy_in=0, sponsor_principal=offer_amount."""
    record_cash_session_start(
        cash_session_repo=repo,
        game_id=GAME_ID,
        owner_id=OWNER_ID,
        sandbox_id="sbx-1",
        stake_label="$10",
        initial_buy_in=0,
        sponsor_principal=500,
        is_staked=True,
        stake_id=f"sponsor_{GAME_ID}",
        now=ANCHOR,
    )

    row = repo.load(GAME_ID)
    assert row.initial_buy_in == 0
    assert row.total_buy_in == 0
    assert row.sponsor_principal == 500
    assert row.is_staked is True
    assert row.stake_id == f"sponsor_{GAME_ID}"


def test_increment_buy_in_accumulates_top_ups(repo):
    """Two top-ups push total_buy_in by their sum; initial stays put."""
    record_cash_session_start(
        cash_session_repo=repo,
        game_id=GAME_ID,
        owner_id=OWNER_ID,
        sandbox_id="sbx-1",
        stake_label="$10",
        initial_buy_in=800,
        now=ANCHOR,
    )
    increment_cash_session_buy_in(repo, GAME_ID, 100)
    increment_cash_session_buy_in(repo, GAME_ID, 200)

    row = repo.load(GAME_ID)
    assert row.initial_buy_in == 800
    assert row.total_buy_in == 1100


def test_increment_buy_in_silent_noop_on_missing_row(repo):
    """Legacy session with no cash_sessions row → silent no-op, no raise."""
    increment_cash_session_buy_in(repo, GAME_ID, 100)
    assert repo.load(GAME_ID) is None


def test_increment_buy_in_with_no_repo_is_safe():
    """A missing repo (e.g., tests without DB) must not raise."""
    increment_cash_session_buy_in(None, GAME_ID, 100)


def test_finalise_with_top_ups_in_p_and_l(repo):
    """Self-funded leave: P&L denominator is total_buy_in (initial + top-ups)."""
    record_cash_session_start(
        cash_session_repo=repo,
        game_id=GAME_ID,
        owner_id=OWNER_ID,
        sandbox_id="sbx-1",
        stake_label="$10",
        initial_buy_in=800,
        now=ANCHOR,
    )
    increment_cash_session_buy_in(repo, GAME_ID, 200)  # total = 1000

    leave_at = ANCHOR + timedelta(minutes=15)
    summary = _summary_from_row(
        repo,
        GAME_ID,
        cash_out=1200,
        player_take_home=1200,
        now=leave_at,
    )
    # P&L is cash_out (1200) - total_buy_in (1000); NOT cash_out - initial_buy_in.
    assert summary["buy_in"] == 1000
    assert summary["cash_out"] == 1200
    assert summary["net_pnl"] == 200
    assert summary["is_staked"] is False
    assert summary["duration_seconds"] == 15 * 60

    finalise_cash_session(
        cash_session_repo=repo,
        game_id=GAME_ID,
        now=leave_at,
        final_chips_at_table=1200,
        sponsor_repaid=0,
        player_take_home=1200,
        summary=summary,
        closed_status=CLOSED_STATUS_LEFT,
    )
    finalised = repo.load(GAME_ID)
    assert finalised.ended_at == leave_at
    assert finalised.final_chips_at_table == 1200
    assert finalised.player_take_home == 1200
    assert finalised.duration_seconds == 15 * 60
    assert finalised.closed_status == CLOSED_STATUS_LEFT


def test_staked_leave_summary_uses_player_take_home(repo):
    """Staked leave: headline P&L is take-home; gross table P&L hidden."""
    record_cash_session_start(
        cash_session_repo=repo,
        game_id=GAME_ID,
        owner_id=OWNER_ID,
        sandbox_id="sbx-1",
        stake_label="$10",
        initial_buy_in=0,
        sponsor_principal=500,
        is_staked=True,
        stake_id=f"sponsor_{GAME_ID}",
        now=ANCHOR,
    )

    leave_at = ANCHOR + timedelta(minutes=10)
    # Player ended with $900 on the table; sponsor pulled $700 off the
    # top; player takes home $200.
    summary = _summary_from_row(
        repo,
        GAME_ID,
        cash_out=900,
        sponsor_repaid=700,
        player_take_home=200,
        now=leave_at,
    )
    # Headline must NOT be cash_out - sponsor_principal (=400, gross P&L).
    assert summary["net_pnl"] == 200
    assert summary["is_staked"] is True
    assert summary["sponsor_principal"] == 500
    assert summary["sponsor_repaid"] == 700
    assert summary["player_take_home"] == 200

    finalise_cash_session(
        cash_session_repo=repo,
        game_id=GAME_ID,
        now=leave_at,
        final_chips_at_table=900,
        sponsor_repaid=700,
        player_take_home=200,
        summary=summary,
        closed_status=CLOSED_STATUS_LEFT,
    )
    finalised = repo.load(GAME_ID)
    assert finalised.sponsor_repaid == 700
    assert finalised.player_take_home == 200


def test_memory_miss_leave_can_build_summary_from_persisted_row(repo):
    """Without `state_machine` / `game_data`, the durable row still produces a summary.

    Pre-fix this returned `session_summary: None` because all of
    `cash_buy_in`, `cash_started_at`, and `cash_table_id` lived only
    on the in-memory `game_data` dict.
    """
    record_cash_session_start(
        cash_session_repo=repo,
        game_id=GAME_ID,
        owner_id=OWNER_ID,
        sandbox_id="sbx-1",
        stake_label="$10",
        initial_buy_in=800,
        now=ANCHOR,
    )

    leave_at = ANCHOR + timedelta(hours=1)
    # Simulate the memory-miss path: live stack is unrecoverable, so
    # cash_out=0 / player_take_home=0.
    summary = _summary_from_row(
        repo,
        GAME_ID,
        cash_out=0,
        player_take_home=0,
        now=leave_at,
    )
    assert summary is not None
    assert summary["buy_in"] == 800
    assert summary["cash_out"] == 0
    assert summary["net_pnl"] == -800  # full bust
    assert summary["duration_seconds"] == 3600

    finalise_cash_session(
        cash_session_repo=repo,
        game_id=GAME_ID,
        now=leave_at,
        final_chips_at_table=0,
        sponsor_repaid=0,
        player_take_home=0,
        summary=summary,
        closed_status=CLOSED_STATUS_GHOST_CLEANUP,
    )
    finalised = repo.load(GAME_ID)
    assert finalised.closed_status == CLOSED_STATUS_GHOST_CLEANUP
    assert finalised.ended_at == leave_at


def test_finalise_with_no_repo_is_safe():
    """Missing repo must not raise — wrapper must handle gracefully."""
    finalise_cash_session(
        cash_session_repo=None,
        game_id=GAME_ID,
        now=ANCHOR,
        final_chips_at_table=0,
        sponsor_repaid=0,
        player_take_home=0,
        summary={},
        closed_status=CLOSED_STATUS_LEFT,
    )
