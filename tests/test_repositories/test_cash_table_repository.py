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
    IDLE_REASONS,
    IdlePoolEntry,
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


# --- Idle pool ---


class TestSchemaMigrationV92:
    def test_cash_idle_pool_exists(self, db_path):
        with sqlite3.connect(db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(cash_idle_pool)")}
        assert "personality_id" in cols
        assert "left_at" in cols
        assert "reason" in cols
        assert "target_stake" in cols

    def test_schema_version_at_least_92(self, db_path):
        with sqlite3.connect(db_path) as conn:
            version = conn.execute(
                "SELECT MAX(version) FROM schema_version"
            ).fetchone()[0]
        assert version >= 92

    def test_personality_id_is_primary_key(self, db_path):
        with sqlite3.connect(db_path) as conn:
            info = conn.execute("PRAGMA table_info(cash_idle_pool)").fetchall()
        pk_cols = [row[1] for row in info if row[5]]
        assert pk_cols == ["personality_id"]


class TestIdlePoolRoundtrip:
    def test_save_and_load(self, repo):
        now = datetime(2026, 5, 18, 12, 0, 0)
        entry = IdlePoolEntry(
            personality_id="napoleon",
            left_at=now,
            reason="forced_leave",
        )
        repo.save_idle(entry)
        loaded = repo.load_idle("napoleon")
        assert loaded is not None
        assert loaded.personality_id == "napoleon"
        assert loaded.left_at == now
        assert loaded.reason == "forced_leave"
        assert loaded.target_stake is None

    def test_save_with_target_stake(self, repo):
        now = datetime(2026, 5, 18, 12, 0, 0)
        entry = IdlePoolEntry(
            personality_id="zeus",
            left_at=now,
            reason="stake_up_queued",
            target_stake="$50",
        )
        repo.save_idle(entry)
        loaded = repo.load_idle("zeus")
        assert loaded.target_stake == "$50"
        assert loaded.reason == "stake_up_queued"

    def test_load_missing_returns_none(self, repo):
        assert repo.load_idle("nobody") is None

    def test_upsert(self, repo):
        t1 = datetime(2026, 5, 18, 12, 0, 0)
        t2 = datetime(2026, 5, 18, 13, 0, 0)
        repo.save_idle(IdlePoolEntry(
            personality_id="napoleon", left_at=t1, reason="bored_move",
        ))
        repo.save_idle(IdlePoolEntry(
            personality_id="napoleon", left_at=t2, reason="forced_leave",
        ))
        loaded = repo.load_idle("napoleon")
        # Last write wins.
        assert loaded.left_at == t2
        assert loaded.reason == "forced_leave"


class TestIdlePoolList:
    def test_empty_returns_empty(self, repo):
        assert repo.list_idle() == []

    def test_ordered_by_left_at_asc(self, repo):
        # Insert out of order; should come back oldest-first.
        repo.save_idle(IdlePoolEntry(
            personality_id="newest", left_at=datetime(2026, 5, 18, 15, 0),
            reason="bored_move",
        ))
        repo.save_idle(IdlePoolEntry(
            personality_id="oldest", left_at=datetime(2026, 5, 18, 9, 0),
            reason="forced_leave",
        ))
        repo.save_idle(IdlePoolEntry(
            personality_id="middle", left_at=datetime(2026, 5, 18, 12, 0),
            reason="take_break",
        ))
        ids = [e.personality_id for e in repo.list_idle()]
        assert ids == ["oldest", "middle", "newest"]


class TestIdlePoolDelete:
    def test_delete_existing_returns_true(self, repo):
        repo.save_idle(IdlePoolEntry(
            personality_id="napoleon",
            left_at=datetime(2026, 5, 18, 12, 0),
            reason="bored_move",
        ))
        assert repo.delete_idle("napoleon") is True
        assert repo.load_idle("napoleon") is None

    def test_delete_missing_returns_false(self, repo):
        assert repo.delete_idle("ghost") is False


class TestIdleReasonEnum:
    @pytest.mark.parametrize("reason", IDLE_REASONS)
    def test_all_reasons_roundtrip(self, repo, reason):
        entry = IdlePoolEntry(
            personality_id=f"p-{reason}",
            left_at=datetime(2026, 5, 18, 12, 0),
            reason=reason,
        )
        repo.save_idle(entry)
        loaded = repo.load_idle(f"p-{reason}")
        assert loaded.reason == reason

    def test_unknown_reason_rejected_in_dataclass(self):
        with pytest.raises(ValueError, match="Unknown reason"):
            IdlePoolEntry(
                personality_id="napoleon",
                left_at=datetime(2026, 5, 18, 12, 0),
                reason="alien_abduction",
            )
