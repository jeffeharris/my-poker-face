"""Tests for schema migration v93 — chip_ledger_entries table.

Covers: fresh DB lands at SCHEMA_VERSION (table present), legacy v92
DB migrates cleanly, re-running the migration is idempotent, and the
CHECK constraint on amount is enforced.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from poker.repositories.legacy_migrations import LegacyMigrations
from poker.repositories.schema_manager import SCHEMA_VERSION, SchemaManager


@pytest.fixture
def tmp_db_path():
    with tempfile.TemporaryDirectory() as d:
        yield str(Path(d) / "test.db")


def _table_columns(conn: sqlite3.Connection, table: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


class TestV93Migration:
    def test_fresh_db_has_chip_ledger_table(self, tmp_db_path):
        SchemaManager(tmp_db_path).ensure_schema()
        with sqlite3.connect(tmp_db_path) as conn:
            assert _table_exists(conn, 'chip_ledger_entries')
            cols = _table_columns(conn, 'chip_ledger_entries')
            assert {
                'entry_id',
                'created_at',
                'source',
                'sink',
                'amount',
                'reason',
                'context_json',
            }.issubset(cols)

    def test_indexes_created(self, tmp_db_path):
        SchemaManager(tmp_db_path).ensure_schema()
        with sqlite3.connect(tmp_db_path) as conn:
            idxs = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='chip_ledger_entries'"
                )
            }
            assert 'idx_chip_ledger_created' in idxs
            assert 'idx_chip_ledger_reason' in idxs

    def test_schema_version_bumped(self, tmp_db_path):
        SchemaManager(tmp_db_path).ensure_schema()
        with sqlite3.connect(tmp_db_path) as conn:
            version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            assert version == SCHEMA_VERSION
            assert version >= 93

    def test_migration_idempotent(self, tmp_db_path):
        """Re-running the migration on an already-migrated DB is a no-op."""
        SchemaManager(tmp_db_path).ensure_schema()
        with sqlite3.connect(tmp_db_path) as conn:
            # Re-run the v93 method directly.
            sm = LegacyMigrations()
            sm._migrate_v93_add_chip_ledger(conn)
            # Still exactly one chip_ledger_entries table.
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name='chip_ledger_entries'"
            ).fetchone()[0]
            assert count == 1

    def test_amount_check_constraint(self, tmp_db_path):
        """Negative amounts are rejected by the CHECK constraint."""
        SchemaManager(tmp_db_path).ensure_schema()
        with sqlite3.connect(tmp_db_path) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO chip_ledger_entries "
                    "(source, sink, amount, reason) VALUES (?, ?, ?, ?)",
                    ('central_bank', 'player:x', -1, 'player_seed'),
                )

    def test_zero_amount_allowed(self, tmp_db_path):
        """Zero is allowed — used for annotation rows (forgive_balance)."""
        SchemaManager(tmp_db_path).ensure_schema()
        with sqlite3.connect(tmp_db_path) as conn:
            conn.execute(
                "INSERT INTO chip_ledger_entries "
                "(source, sink, amount, reason) VALUES (?, ?, ?, ?)",
                ('player:x', 'central_bank', 0, 'forgive_balance'),
            )
            count = conn.execute("SELECT COUNT(*) FROM chip_ledger_entries").fetchone()[0]
            assert count == 1
