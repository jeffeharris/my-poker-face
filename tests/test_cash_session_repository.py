"""Tests for the v108 CashSessionRepository.

CRUD round-trip + buy-in mutation + finalise + active-session lookup +
recent-history list. Uses a tempdb fixture per test (same pattern as
`tests/test_stake_repository.py`).
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cash_mode.cash_sessions import (
    CLOSED_STATUS_GHOST_CLEANUP,
    CLOSED_STATUS_LEFT,
    CashSession,
)
from poker.repositories.cash_session_repository import CashSessionRepository
from poker.repositories.schema_manager import SchemaManager

ANCHOR = datetime(2026, 5, 22, 12, 0, 0)


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory() as d:
        db_path = str(Path(d) / "test.db")
        SchemaManager(db_path).ensure_schema()
        yield CashSessionRepository(db_path)


def _self_funded(
    *,
    session_id: str = "cash-sess-1",
    owner_id: str = "alice",
    initial_buy_in: int = 800,
    cash_table_id=None,
    cash_seat_index=None,
    started_at: datetime = ANCHOR,
) -> CashSession:
    return CashSession(
        session_id=session_id,
        owner_id=owner_id,
        sandbox_id="sbx-1",
        stake_label="$10",
        is_staked=False,
        stake_id=None,
        initial_buy_in=initial_buy_in,
        total_buy_in=initial_buy_in,
        sponsor_principal=0,
        cash_table_id=cash_table_id,
        cash_seat_index=cash_seat_index,
        started_at=started_at,
    )


def _staked(
    *,
    session_id: str = "cash-sess-staked",
    owner_id: str = "alice",
    sponsor_principal: int = 500,
    stake_id: str = "sponsor_cash-sess-staked",
    started_at: datetime = ANCHOR,
) -> CashSession:
    return CashSession(
        session_id=session_id,
        owner_id=owner_id,
        sandbox_id="sbx-1",
        stake_label="$10",
        is_staked=True,
        stake_id=stake_id,
        initial_buy_in=0,
        total_buy_in=0,
        sponsor_principal=sponsor_principal,
        cash_table_id="lobby_$10",
        cash_seat_index=3,
        started_at=started_at,
    )


def test_create_and_load_round_trip(repo):
    """Every field survives the DB round-trip."""
    session = _self_funded(cash_table_id="lobby_$10", cash_seat_index=2)
    repo.create(session)
    loaded = repo.load(session.session_id)
    assert loaded is not None
    assert loaded == session


def test_load_active_for_owner_returns_only_unfinalised(repo):
    """Finalised rows shouldn't show up as active."""
    finalised = _self_funded(session_id="cash-sess-old", started_at=ANCHOR - timedelta(hours=2))
    repo.create(finalised)
    repo.finalise(
        finalised.session_id,
        ended_at=ANCHOR - timedelta(hours=1),
        final_chips_at_table=900,
        sponsor_repaid=0,
        player_take_home=900,
        hands_played=20,
        hands_won=8,
        biggest_pot_won=200,
        duration_seconds=3600,
        closed_status=CLOSED_STATUS_LEFT,
    )
    active = _self_funded(session_id="cash-sess-active", started_at=ANCHOR)
    repo.create(active)
    # Both rows are owned by 'alice' but only one is active.
    result = repo.load_active_for_owner("alice")
    assert result is not None
    assert result.session_id == "cash-sess-active"


def test_load_active_for_owner_returns_none_when_none_active(repo):
    """No active row → None."""
    assert repo.load_active_for_owner("alice") is None


def test_update_total_buy_in_increments(repo):
    """Top-up + rebuy flow: total_buy_in is replaced (caller adds the delta)."""
    session = _self_funded(initial_buy_in=800)
    repo.create(session)
    # Simulate two top-ups: 800 → 900 → 1100.
    assert repo.update_total_buy_in(session.session_id, 900) is True
    loaded = repo.load(session.session_id)
    assert loaded.total_buy_in == 900
    # initial_buy_in stays put — only total_buy_in moves.
    assert loaded.initial_buy_in == 800
    assert repo.update_total_buy_in(session.session_id, 1100) is True
    assert repo.load(session.session_id).total_buy_in == 1100


def test_update_total_buy_in_skips_finalised_rows(repo):
    """Don't let a late-arriving top-up corrupt a finalised session."""
    session = _self_funded()
    repo.create(session)
    repo.finalise(
        session.session_id,
        ended_at=ANCHOR + timedelta(minutes=30),
        final_chips_at_table=1200,
        sponsor_repaid=0,
        player_take_home=1200,
        hands_played=15,
        hands_won=6,
        biggest_pot_won=180,
        duration_seconds=1800,
        closed_status=CLOSED_STATUS_LEFT,
    )
    # Update after finalise must no-op.
    assert repo.update_total_buy_in(session.session_id, 5000) is False
    assert repo.load(session.session_id).total_buy_in == session.total_buy_in


def test_finalise_is_idempotent(repo):
    """Second finalise call (e.g., retry) must not overwrite the first's numbers."""
    session = _self_funded()
    repo.create(session)
    assert (
        repo.finalise(
            session.session_id,
            ended_at=ANCHOR + timedelta(minutes=15),
            final_chips_at_table=950,
            sponsor_repaid=0,
            player_take_home=950,
            hands_played=10,
            hands_won=4,
            biggest_pot_won=120,
            duration_seconds=900,
            closed_status=CLOSED_STATUS_LEFT,
        )
        is True
    )
    # Retry with different numbers.
    assert (
        repo.finalise(
            session.session_id,
            ended_at=ANCHOR + timedelta(minutes=20),
            final_chips_at_table=1,
            sponsor_repaid=999,
            player_take_home=1,
            hands_played=1,
            hands_won=0,
            biggest_pot_won=1,
            duration_seconds=1,
            closed_status=CLOSED_STATUS_GHOST_CLEANUP,
        )
        is False
    )
    loaded = repo.load(session.session_id)
    assert loaded.final_chips_at_table == 950
    assert loaded.closed_status == CLOSED_STATUS_LEFT


def test_staked_session_round_trip(repo):
    """Staked-session shape preserves principal + stake_id + is_staked."""
    session = _staked(sponsor_principal=500, stake_id="sponsor_cash-sess-staked")
    repo.create(session)
    loaded = repo.load(session.session_id)
    assert loaded.is_staked is True
    assert loaded.sponsor_principal == 500
    assert loaded.stake_id == "sponsor_cash-sess-staked"
    assert loaded.initial_buy_in == 0
    assert loaded.total_buy_in == 0


def test_list_for_owner_orders_by_started_at_desc(repo):
    """History view: most recent first."""
    old = _self_funded(session_id="s-old", started_at=ANCHOR - timedelta(days=3))
    mid = _self_funded(session_id="s-mid", started_at=ANCHOR - timedelta(days=1))
    new = _self_funded(session_id="s-new", started_at=ANCHOR)
    for s in (old, new, mid):
        repo.create(s)
    rows = repo.list_for_owner("alice")
    assert [r.session_id for r in rows] == ["s-new", "s-mid", "s-old"]


def test_list_for_owner_respects_limit(repo):
    """Default limit caps at 50; explicit limit overrides."""
    for i in range(5):
        repo.create(
            _self_funded(
                session_id=f"s-{i}",
                started_at=ANCHOR - timedelta(minutes=i),
            )
        )
    assert len(repo.list_for_owner("alice", limit=3)) == 3


def test_delete_removes_row(repo):
    session = _self_funded()
    repo.create(session)
    assert repo.delete(session.session_id) is True
    assert repo.load(session.session_id) is None
