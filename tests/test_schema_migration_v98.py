"""Tests for schema migration v98 — stakes table + ledger reason rename.

Covers: fresh DB lands at SCHEMA_VERSION with the stakes table and its
indexes present, re-running the migration is idempotent, and the
ledger reason rename UPDATE flips legacy `house_loan_*` rows to
`house_stake_*` (no-op on fresh installs).
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

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


def _index_names_for(conn: sqlite3.Connection, table: str) -> set:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master " "WHERE type='index' AND tbl_name=?",
            (table,),
        )
    }


class TestV98Migration:
    def test_fresh_db_has_stakes_table(self, tmp_db_path):
        SchemaManager(tmp_db_path).ensure_schema()
        with sqlite3.connect(tmp_db_path) as conn:
            assert _table_exists(conn, 'stakes')
            cols = _table_columns(conn, 'stakes')
            assert {
                'stake_id',
                'session_id',
                'staker_id',
                'staker_kind',
                'borrower_id',
                'borrower_kind',
                'format',
                'principal',
                'match_amount',
                'origination_fee',
                'cut',
                'status',
                'carry_amount',
                'stake_tier',
                'created_at',
                'settled_at',
            }.issubset(cols)

    def test_indexes_created(self, tmp_db_path):
        SchemaManager(tmp_db_path).ensure_schema()
        with sqlite3.connect(tmp_db_path) as conn:
            idxs = _index_names_for(conn, 'stakes')
            assert 'idx_stakes_borrower_carry' in idxs
            assert 'idx_stakes_staker_carry' in idxs
            assert 'idx_stakes_session' in idxs

    def test_schema_version_bumped(self, tmp_db_path):
        SchemaManager(tmp_db_path).ensure_schema()
        with sqlite3.connect(tmp_db_path) as conn:
            version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            assert version == SCHEMA_VERSION
            assert version >= 98

    def test_migration_is_idempotent(self, tmp_db_path):
        # Two ensure_schema passes — the second should be a no-op
        # (CREATE TABLE IF NOT EXISTS keeps the table intact).
        SchemaManager(tmp_db_path).ensure_schema()
        SchemaManager(tmp_db_path).ensure_schema()
        with sqlite3.connect(tmp_db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master " "WHERE type='table' AND name='stakes'"
            ).fetchone()[0]
            assert count == 1

    def test_ledger_reason_rename_house_loan_issue(self, tmp_db_path):
        # Simulate a pre-v98 DB by stopping migrations at v97, inserting
        # a legacy-named row, then running v98 manually.
        sm = SchemaManager(tmp_db_path)
        sm.ensure_schema()  # Lands at SCHEMA_VERSION (v98+) on fresh DB.

        # Directly insert a row with the *old* reason string. This
        # bypasses the LEDGER_REASONS validation in core/economy/ledger
        # (which would reject the old name now). Mimics what a v93-era
        # row looks like on disk.
        with sqlite3.connect(tmp_db_path) as conn:
            conn.execute(
                "INSERT INTO chip_ledger_entries "
                "(source, sink, amount, reason) "
                "VALUES (?, ?, ?, ?)",
                ('central_bank', 'player:legacy', 100, 'house_loan_issue'),
            )
            conn.execute(
                "INSERT INTO chip_ledger_entries "
                "(source, sink, amount, reason) "
                "VALUES (?, ?, ?, ?)",
                ('player:legacy', 'central_bank', 50, 'house_loan_settle'),
            )
            conn.commit()

        # Re-run the migration in isolation; UPDATE renames in place.
        with sqlite3.connect(tmp_db_path) as conn:
            sm._migrate_v98_add_stakes_table(conn)
            conn.commit()

        with sqlite3.connect(tmp_db_path) as conn:
            reasons = {
                row[0] for row in conn.execute("SELECT DISTINCT reason FROM chip_ledger_entries")
            }
            # Old names gone, new names present.
            assert 'house_loan_issue' not in reasons
            assert 'house_loan_settle' not in reasons
            assert 'house_stake_issue' in reasons
            assert 'house_stake_settle' in reasons
