"""The lobby refresh sweeps expired sponsorship seat-holds back to open.

A `"reserved"` seat is the transient hold placed when a player taps a
seat they can only afford via sponsorship (see `/api/cash/sit` 402 path).
The frontend releases it on SponsorModal-close, but for the abandoned
case (closed tab, dropped network) `refresh_unseated_tables` is the
safety net: any hold past its TTL is freed so the seat returns to the
live-fill pool instead of being stranded against AI seating.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from cash_mode.lobby import refresh_unseated_tables
from cash_mode.tables import (
    SEAT_RESERVATION_TTL_SECONDS,
    CashTableState,
    open_slot,
    reserved_slot,
)
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.cash_table_repository import CashTableRepository
from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.schema_manager import SchemaManager

SB = "sb-reservation-expiry"

pytestmark = pytest.mark.integration


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "reservation_expiry.db")
    SchemaManager(path).ensure_schema()
    return path


def _refresh(cash_table_repo, db_path, now):
    refresh_unseated_tables(
        cash_table_repo=cash_table_repo,
        personality_repo=PersonalityRepository(db_path),
        bankroll_repo=BankrollRepository(db_path),
        now=now,
        sandbox_id=SB,
    )


def test_expired_hold_is_freed(db_path):
    cash_table_repo = CashTableRepository(db_path)
    placed_at = datetime(2026, 5, 29, 12, 0, 0)
    table = CashTableState(
        table_id="cash-t",
        stake_label="$2",
        seats=[reserved_slot("ghost-player", placed_at)] + [open_slot() for _ in range(5)],
    )
    cash_table_repo.save_table(table, sandbox_id=SB)

    # Refresh well past the TTL → the abandoned hold is reclaimed.
    later = placed_at + timedelta(seconds=SEAT_RESERVATION_TTL_SECONDS + 5)
    _refresh(cash_table_repo, db_path, later)

    after = cash_table_repo.load_table("cash-t", sandbox_id=SB)
    assert after.seats[0]["kind"] == "open"


def test_fresh_hold_survives_refresh(db_path):
    cash_table_repo = CashTableRepository(db_path)
    placed_at = datetime(2026, 5, 29, 12, 0, 0)
    table = CashTableState(
        table_id="cash-t",
        stake_label="$2",
        seats=[reserved_slot("active-player", placed_at)] + [open_slot() for _ in range(5)],
    )
    cash_table_repo.save_table(table, sandbox_id=SB)

    # Refresh one second later — still inside the SponsorModal window.
    soon = placed_at + timedelta(seconds=1)
    _refresh(cash_table_repo, db_path, soon)

    after = cash_table_repo.load_table("cash-t", sandbox_id=SB)
    assert after.seats[0]["kind"] == "reserved"
    assert after.seats[0]["personality_id"] == "active-player"
