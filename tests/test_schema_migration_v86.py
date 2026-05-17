"""Tests for schema migration v86 — observer_id + opponent_id on opponent_models.

Covers the migration logic: a freshly-built v85-shape database (including
the v85 personality_id column) gets migrated up to v86, and the test
verifies that (a) the new columns are added, (b) existing rows are
backfilled via name lookup against personalities.personality_id, (c)
unmatched names stay NULL, (d) re-running is idempotent.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


def _create_v85_schema_subset(conn: sqlite3.Connection) -> None:
    """Build personalities + opponent_models as they existed at v85.

    Only the columns this migration touches. Enough to exercise the
    backfill join.
    """
    conn.execute(
        """
        CREATE TABLE personalities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            config_json TEXT NOT NULL DEFAULT '{}',
            personality_id TEXT
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX idx_personalities_personality_id "
        "ON personalities(personality_id)"
    )
    conn.execute(
        """
        CREATE TABLE opponent_models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT,
            observer_name TEXT NOT NULL,
            opponent_name TEXT NOT NULL,
            hands_observed INTEGER DEFAULT 0,
            vpip REAL DEFAULT 0.5,
            UNIQUE(game_id, observer_name, opponent_name)
        )
        """
    )


def _apply_v86(conn: sqlite3.Connection) -> None:
    from poker.repositories.schema_manager import SchemaManager
    sm = SchemaManager.__new__(SchemaManager)
    sm._migrate_v86_add_opponent_model_ids(conn)


@pytest.fixture
def v85_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_v85_schema_subset(conn)

    # Seed personalities (Lincoln + Bob Ross have ids; Casper does not)
    conn.executemany(
        "INSERT INTO personalities (name, personality_id) VALUES (?, ?)",
        [
            ("Abraham Lincoln", "abraham_lincoln"),
            ("Bob Ross", "bob_ross"),
            ("Casper", None),  # personality with no id (pre-v85 leftover)
        ],
    )

    # Seed opponent_models rows that reference the personalities by name
    conn.executemany(
        "INSERT INTO opponent_models (game_id, observer_name, opponent_name) "
        "VALUES (?, ?, ?)",
        [
            ("game_1", "Abraham Lincoln", "Bob Ross"),
            ("game_1", "Bob Ross", "Abraham Lincoln"),
            ("game_2", "Abraham Lincoln", "Some Guest Player"),  # not in personalities
            ("game_2", "Casper", "Bob Ross"),  # observer has no id
        ],
    )
    conn.commit()
    yield conn
    conn.close()


class TestV86Migration:
    def test_adds_id_columns(self, v85_db):
        cols = {row[1] for row in v85_db.execute("PRAGMA table_info(opponent_models)")}
        assert "observer_id" not in cols
        assert "opponent_id" not in cols

        _apply_v86(v85_db)

        cols = {row[1] for row in v85_db.execute("PRAGMA table_info(opponent_models)")}
        assert "observer_id" in cols
        assert "opponent_id" in cols

    def test_backfills_matched_names(self, v85_db):
        _apply_v86(v85_db)
        rows = v85_db.execute(
            "SELECT observer_name, opponent_name, observer_id, opponent_id "
            "FROM opponent_models ORDER BY id"
        ).fetchall()
        # Row 1: Lincoln observes Bob — both should resolve
        assert rows[0]["observer_id"] == "abraham_lincoln"
        assert rows[0]["opponent_id"] == "bob_ross"
        # Row 2: Bob observes Lincoln — both should resolve
        assert rows[1]["observer_id"] == "bob_ross"
        assert rows[1]["opponent_id"] == "abraham_lincoln"

    def test_unmatched_names_stay_null(self, v85_db):
        _apply_v86(v85_db)
        rows = v85_db.execute(
            "SELECT observer_id, opponent_id, opponent_name "
            "FROM opponent_models WHERE game_id = 'game_2'"
        ).fetchall()
        # Row 3: opponent is a guest with no personalities row → NULL
        guest_row = next(r for r in rows if r["opponent_name"] == "Some Guest Player")
        assert guest_row["observer_id"] == "abraham_lincoln"  # observer matched
        assert guest_row["opponent_id"] is None  # guest didn't match
        # Row 4: observer Casper has a personality row but no personality_id → NULL
        casper_row = next(r for r in rows if r["opponent_name"] == "Bob Ross")
        assert casper_row["observer_id"] is None  # Casper has no personality_id
        assert casper_row["opponent_id"] == "bob_ross"  # Bob still resolves

    def test_indexes_created(self, v85_db):
        _apply_v86(v85_db)
        idxs = [
            row[1] for row in v85_db.execute(
                "SELECT * FROM sqlite_master "
                "WHERE type='index' AND tbl_name='opponent_models'"
            )
        ]
        assert "idx_opponent_models_observer_id" in idxs
        assert "idx_opponent_models_opponent_id" in idxs

    def test_idempotent(self, v85_db):
        _apply_v86(v85_db)
        first = dict(
            v85_db.execute(
                "SELECT id, observer_id || '|' || COALESCE(opponent_id, 'NULL') "
                "FROM opponent_models"
            ).fetchall()
        )
        _apply_v86(v85_db)  # re-run
        second = dict(
            v85_db.execute(
                "SELECT id, observer_id || '|' || COALESCE(opponent_id, 'NULL') "
                "FROM opponent_models"
            ).fetchall()
        )
        assert first == second

    def test_existing_ids_not_overwritten(self, v85_db):
        """If a row already has observer_id / opponent_id set (e.g. from
        live writes by a newer codepath running between migration
        attempts), the backfill must leave them alone."""
        _apply_v86(v85_db)
        # Manually replace one row's opponent_id with a different value
        v85_db.execute(
            "UPDATE opponent_models SET opponent_id = ? WHERE id = ?",
            ("custom_override", 1),
        )
        v85_db.commit()

        _apply_v86(v85_db)  # re-run

        result = v85_db.execute(
            "SELECT opponent_id FROM opponent_models WHERE id = 1"
        ).fetchone()
        assert result["opponent_id"] == "custom_override"

    def test_nulls_rebackfill_on_rerun(self, v85_db):
        """Rows that initially failed to match (NULL after first run)
        get another chance on subsequent runs — useful if a missing
        personality is added between migration attempts."""
        _apply_v86(v85_db)

        # Add the previously missing personality
        v85_db.execute(
            "INSERT INTO personalities (name, personality_id) VALUES (?, ?)",
            ("Some Guest Player", "some_guest_player"),
        )
        v85_db.commit()

        _apply_v86(v85_db)  # re-run

        result = v85_db.execute(
            "SELECT opponent_id FROM opponent_models "
            "WHERE opponent_name = 'Some Guest Player'"
        ).fetchone()
        assert result["opponent_id"] == "some_guest_player"
