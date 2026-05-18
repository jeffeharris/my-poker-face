"""Tests for the v91 schema migration and CashTableRepository.

Covers:
  - Migration v91 creates `cash_tables` with the expected columns.
  - save / load round-trips, including JSON seat serialization.
  - list_all_tables ordering.
  - last_activity_at bumps on every save.
  - created_at preserved across re-saves.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.integration

from cash_mode.tables import (
    CashTableState,
    ai_slot,
    human_slot,
    open_slot,
)
from poker.repositories.cash_table_repository import CashTableRepository
from poker.repositories.schema_manager import SchemaManager


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "cash_tables.db")
    SchemaManager(path).ensure_schema()
    return path


@pytest.fixture
def repo(db_path):
    r = CashTableRepository(db_path)
    yield r
    r.close()


class TestSchemaMigrationV91:
    def test_cash_tables_table_exists(self, db_path):
        with sqlite3.connect(db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(cash_tables)")}
        assert "table_id" in cols
        assert "stake_label" in cols
        assert "seats_json" in cols
        assert "created_at" in cols
        assert "last_activity_at" in cols

    def test_schema_version_at_least_91(self, db_path):
        with sqlite3.connect(db_path) as conn:
            version = conn.execute(
                "SELECT MAX(version) FROM schema_version"
            ).fetchone()[0]
        assert version >= 91

    def test_table_id_is_primary_key(self, db_path):
        with sqlite3.connect(db_path) as conn:
            info = conn.execute("PRAGMA table_info(cash_tables)").fetchall()
        pk_cols = [row[1] for row in info if row[5]]  # row[5] = pk flag
        assert pk_cols == ["table_id"]


class TestRoundtrip:
    def test_empty_table_roundtrip(self, repo):
        state = CashTableState(table_id="cash-table-10-001", stake_label="$10")
        repo.save_table(state)
        loaded = repo.load_table("cash-table-10-001")
        assert loaded is not None
        assert loaded.table_id == "cash-table-10-001"
        assert loaded.stake_label == "$10"
        assert len(loaded.seats) == 6
        assert all(s["kind"] == "open" for s in loaded.seats)

    def test_mixed_seats_roundtrip(self, repo):
        seats = [
            ai_slot("napoleon", 1240),
            ai_slot("zeus", 800),
            human_slot("user-1", 500),
            ai_slot("athena", 200),
            ai_slot("gatsby", 1600),
            open_slot(),
        ]
        state = CashTableState(
            table_id="cash-table-50-001", stake_label="$50", seats=seats,
        )
        repo.save_table(state)
        loaded = repo.load_table("cash-table-50-001")
        assert loaded.seats == seats

    def test_load_missing_returns_none(self, repo):
        assert repo.load_table("does-not-exist") is None


class TestUpsert:
    def test_save_twice_updates(self, repo):
        state = CashTableState(table_id="t1", stake_label="$10")
        repo.save_table(state)
        # Change a seat and re-save.
        new_state = state.with_seat(0, ai_slot("napoleon", 1240))
        repo.save_table(new_state)
        loaded = repo.load_table("t1")
        assert loaded.seats[0]["kind"] == "ai"
        assert loaded.seats[0]["personality_id"] == "napoleon"

    def test_created_at_preserved_across_saves(self, repo):
        state = CashTableState(table_id="t1", stake_label="$10")
        first_time = datetime(2026, 5, 18, 12, 0, 0)
        repo.save_table(state, now=first_time)
        original = repo.load_table("t1")
        assert original.created_at is not None
        first_created = original.created_at

        # Re-save with a later time.
        later = first_time + timedelta(hours=1)
        new_state = state.with_seat(0, ai_slot("napoleon", 1240))
        repo.save_table(new_state, now=later)
        updated = repo.load_table("t1")
        # created_at should not change.
        assert updated.created_at == first_created
        # last_activity_at should have bumped.
        assert updated.last_activity_at >= original.last_activity_at

    def test_last_activity_bumps_on_save(self, repo):
        state = CashTableState(table_id="t1", stake_label="$10")
        t1 = datetime(2026, 5, 18, 12, 0, 0)
        repo.save_table(state, now=t1)
        first = repo.load_table("t1")

        t2 = t1 + timedelta(minutes=30)
        repo.save_table(state, now=t2)
        second = repo.load_table("t1")

        assert second.last_activity_at >= first.last_activity_at
        assert (second.last_activity_at - first.last_activity_at).total_seconds() >= 60


class TestListAllTables:
    def test_empty_lobby_returns_empty(self, repo):
        assert repo.list_all_tables() == []

    def test_ordered_by_table_id(self, repo):
        # Insert out of order so we can verify ORDER BY.
        repo.save_table(CashTableState(table_id="b-table", stake_label="$10"))
        repo.save_table(CashTableState(table_id="a-table", stake_label="$2"))
        repo.save_table(CashTableState(table_id="c-table", stake_label="$50"))
        all_tables = repo.list_all_tables()
        assert [t.table_id for t in all_tables] == ["a-table", "b-table", "c-table"]

    def test_returns_all_seats(self, repo):
        seats = [ai_slot(f"p{i}", 100 * i) for i in range(4)] + [open_slot(), open_slot()]
        repo.save_table(CashTableState(
            table_id="t1", stake_label="$10", seats=seats,
        ))
        all_tables = repo.list_all_tables()
        assert len(all_tables) == 1
        assert all_tables[0].seats == seats
