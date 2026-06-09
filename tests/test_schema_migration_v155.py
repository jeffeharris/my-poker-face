"""Tests for schema migration v155 — regard re-baseline 0.5 → 0.35.

The earned-regard neutral moved from the old hardcoded 0.5 to
REGARD_NEUTRAL (0.35). Every pre-existing `relationship_states` row was
created against the 0.5 baseline, so v155 shifts respect/likability DOWN
by 0.15 (clamped to [0, 1]) — preserving each edge's offset-from-neutral
so renown / hints / offers read identically. Heat is untouched.

The migration is a ONE-TIME data transform; correctness rests on the
version gate running it exactly once. These tests craft a pre-155 DB
(fresh build, then roll the recorded version back to 154 and seed
0.5-baseline rows) and assert the forward step transforms them, while a
fresh DB at SCHEMA_VERSION skips the step entirely.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from poker.memory.opponent_model import REGARD_NEUTRAL
from poker.repositories.schema_manager import SchemaManager


@pytest.fixture
def tmp_db_path():
    with tempfile.TemporaryDirectory() as d:
        yield str(Path(d) / "test.db")


def _seed_pre155(path: str, rows):
    """Build a fresh DB, roll the recorded version back to 154, and insert
    `rows` of (observer, opponent, heat, respect, likability) at the OLD
    0.5 baseline — simulating a DB that predates the rebaseline."""
    SchemaManager(path).ensure_schema()
    with sqlite3.connect(path) as conn:
        # Post-squash a fresh DB is stamped only at the baseline; set an explicit
        # pre-v155 version so ensure_schema routes through the legacy chain (which
        # runs the v155 regard rebaseline over the inserted rows).
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version (version, description) VALUES (154, 'pre-v155')")
        conn.executemany(
            "INSERT INTO relationship_states "
            "(observer_id, opponent_id, heat, respect, likability) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()


def _load(path, observer, opponent):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT heat, respect, likability FROM relationship_states "
            "WHERE observer_id = ? AND opponent_id = ?",
            (observer, opponent),
        ).fetchone()


class TestV155Rebaseline:
    def test_neutral_row_shifts_to_new_neutral(self, tmp_db_path):
        # A row that meant "exactly neutral" under 0.5 must mean exactly
        # neutral (REGARD_NEUTRAL) under the new baseline.
        _seed_pre155(tmp_db_path, [("alice", "bob", 0.0, 0.5, 0.5)])
        SchemaManager(tmp_db_path).ensure_schema()  # applies v155
        row = _load(tmp_db_path, "alice", "bob")
        assert row["respect"] == pytest.approx(REGARD_NEUTRAL)
        assert row["likability"] == pytest.approx(REGARD_NEUTRAL)
        assert row["heat"] == 0.0  # heat is not a regard axis — untouched

    def test_offset_from_neutral_is_preserved(self, tmp_db_path):
        # +0.2 above old neutral (0.7) stays +0.2 above new neutral (0.55);
        # −0.3 below (0.2) stays −0.3 below (0.05).
        _seed_pre155(
            tmp_db_path,
            [
                ("a", "earned", 0.3, 0.7, 0.65),
                ("a", "wronged", 0.0, 0.2, 0.25),
            ],
        )
        SchemaManager(tmp_db_path).ensure_schema()
        earned = _load(tmp_db_path, "a", "earned")
        assert earned["respect"] == pytest.approx(0.55)
        assert earned["likability"] == pytest.approx(0.50)
        assert earned["heat"] == pytest.approx(0.3)  # untouched
        wronged = _load(tmp_db_path, "a", "wronged")
        assert wronged["respect"] == pytest.approx(0.05)
        assert wronged["likability"] == pytest.approx(0.10)

    def test_low_values_clamp_to_zero(self, tmp_db_path):
        # Rows already near the floor can't go negative.
        _seed_pre155(
            tmp_db_path,
            [
                ("a", "low", 0.0, 0.10, 0.05),
                ("a", "floor", 0.0, 0.0, 0.0),
            ],
        )
        SchemaManager(tmp_db_path).ensure_schema()
        low = _load(tmp_db_path, "a", "low")
        assert low["respect"] == 0.0  # 0.10 - 0.15 clamped
        assert low["likability"] == 0.0  # 0.05 - 0.15 clamped
        floor = _load(tmp_db_path, "a", "floor")
        assert floor["respect"] == 0.0
        assert floor["likability"] == 0.0

    def test_fresh_db_does_not_shift(self, tmp_db_path):
        # A fresh DB is built at SCHEMA_VERSION and never enters the 154→155
        # step, so a row created at the new baseline (0.35) is NOT shifted
        # again — guards against a double-application.
        SchemaManager(tmp_db_path).ensure_schema()
        with sqlite3.connect(tmp_db_path) as conn:
            conn.execute(
                "INSERT INTO relationship_states "
                "(observer_id, opponent_id, heat, respect, likability) "
                "VALUES ('a', 'fresh', 0.0, ?, ?)",
                (REGARD_NEUTRAL, REGARD_NEUTRAL),
            )
            conn.commit()
        SchemaManager(tmp_db_path).ensure_schema()  # no pending migration
        row = _load(tmp_db_path, "a", "fresh")
        assert row["respect"] == pytest.approx(REGARD_NEUTRAL)
        assert row["likability"] == pytest.approx(REGARD_NEUTRAL)

    def test_migration_recorded_once(self, tmp_db_path):
        # The version row is stamped so a second ensure_schema is a no-op
        # (the transform never runs twice on the same DB).
        _seed_pre155(tmp_db_path, [("a", "b", 0.0, 0.5, 0.5)])
        SchemaManager(tmp_db_path).ensure_schema()
        SchemaManager(tmp_db_path).ensure_schema()  # second call: no re-shift
        row = _load(tmp_db_path, "a", "b")
        assert row["respect"] == pytest.approx(REGARD_NEUTRAL)  # not 0.20
        with sqlite3.connect(tmp_db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM schema_version WHERE version = 155"
            ).fetchone()[0]
        assert count == 1
