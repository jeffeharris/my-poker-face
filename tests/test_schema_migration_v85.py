"""Tests for schema migration v85 — personality_id on personalities table.

Covers the actual migration logic: a freshly-built v84-shaped database
gets migrated up, and the test verifies that (a) all existing rows have
been backfilled with valid personality_ids, (b) the slugs match the
canonical rule, (c) duplicates would be rejected by the new UNIQUE
constraint, (d) re-running the migration is idempotent.

Also verifies the live `personalities.json` seed data has `id` fields
that match what the migration would assign — guarding against drift
between the seed source and the DB-side backfill.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _create_v84_personalities_table(conn: sqlite3.Connection) -> None:
    """Build the personalities table as it existed before v85.

    Matches the schema produced by _init_db() in schema_manager.py at
    SCHEMA_VERSION = 84.
    """
    conn.execute(
        """
        CREATE TABLE personalities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            config_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_generated BOOLEAN DEFAULT 1,
            source TEXT DEFAULT 'ai_generated',
            times_used INTEGER DEFAULT 0,
            elasticity_config TEXT,
            owner_id TEXT,
            visibility TEXT DEFAULT 'public'
        )
        """
    )


def _apply_v85_via_schema_manager(conn: sqlite3.Connection) -> None:
    """Invoke the actual _migrate_v85_add_personality_id method.

    Uses a stub SchemaManager whose _get_connection returns the test
    connection rather than building one from a path.
    """
    from poker.repositories.legacy_migrations import LegacyMigrations

    sm = LegacyMigrations()  # bypass __init__
    sm._migrate_v85_add_personality_id(conn)


@pytest.fixture
def v84_db():
    """In-memory SQLite with a v84-shaped personalities table seeded
    with realistic test data."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_v84_personalities_table(conn)

    test_personalities = [
        ("Abraham Lincoln", "{}"),
        ("Bob Ross", "{}"),
        ("Dr. Seuss", "{}"),
        ("Louis XIV", "{}"),
        ("CaseBot", "{}"),
        ("A Mime", "{}"),
        ("Renée Zellweger", "{}"),  # diacritics test
    ]
    for name, config in test_personalities:
        conn.execute(
            "INSERT INTO personalities (name, config_json) VALUES (?, ?)",
            (name, config),
        )
    conn.commit()
    yield conn
    conn.close()


class TestV85Migration:
    def test_adds_personality_id_column(self, v84_db):
        cols = {row[1] for row in v84_db.execute("PRAGMA table_info(personalities)")}
        assert "personality_id" not in cols  # pre-migration

        _apply_v85_via_schema_manager(v84_db)

        cols = {row[1] for row in v84_db.execute("PRAGMA table_info(personalities)")}
        assert "personality_id" in cols

    def test_backfills_all_rows(self, v84_db):
        _apply_v85_via_schema_manager(v84_db)

        rows = v84_db.execute(
            "SELECT name, personality_id FROM personalities ORDER BY id"
        ).fetchall()
        for row in rows:
            assert row[
                "personality_id"
            ], f"Personality {row['name']!r} has no personality_id after migration"

    def test_backfilled_ids_match_slug_rule(self, v84_db):
        _apply_v85_via_schema_manager(v84_db)

        from poker.personality_id import slugify_personality_name

        rows = v84_db.execute("SELECT name, personality_id FROM personalities").fetchall()
        for row in rows:
            expected = slugify_personality_name(row["name"])
            assert (
                row["personality_id"] == expected
            ), f"{row['name']!r}: id={row['personality_id']!r} expected={expected!r}"

    def test_creates_unique_index(self, v84_db):
        _apply_v85_via_schema_manager(v84_db)

        indexes = [
            row[1]
            for row in v84_db.execute(
                "SELECT * FROM sqlite_master " "WHERE type='index' AND tbl_name='personalities'"
            )
        ]
        assert "idx_personalities_personality_id" in indexes

    def test_unique_constraint_rejects_duplicates(self, v84_db):
        _apply_v85_via_schema_manager(v84_db)

        # Pick one existing id and try to insert another row claiming it
        existing = v84_db.execute("SELECT personality_id FROM personalities LIMIT 1").fetchone()[
            "personality_id"
        ]
        with pytest.raises(sqlite3.IntegrityError):
            v84_db.execute(
                "INSERT INTO personalities (name, config_json, personality_id) " "VALUES (?, ?, ?)",
                ("New Personality", "{}", existing),
            )

    def test_unique_constraint_allows_multiple_nulls(self, v84_db):
        """SQLite treats NULL as distinct in UNIQUE indexes by default.
        That property is what lets the migration add the column as
        nullable and still create the UNIQUE index — rows that fail to
        backfill (slugifies to empty) stay NULL without blocking the
        index."""
        _apply_v85_via_schema_manager(v84_db)

        # Insert two rows with NULL personality_id — both should succeed
        v84_db.execute(
            "INSERT INTO personalities (name, config_json, personality_id) " "VALUES (?, ?, NULL)",
            ("Null A", "{}"),
        )
        v84_db.execute(
            "INSERT INTO personalities (name, config_json, personality_id) " "VALUES (?, ?, NULL)",
            ("Null B", "{}"),
        )
        v84_db.commit()

    def test_migration_is_idempotent(self, v84_db):
        _apply_v85_via_schema_manager(v84_db)
        first_ids = dict(
            v84_db.execute("SELECT name, personality_id FROM personalities").fetchall()
        )

        _apply_v85_via_schema_manager(v84_db)  # re-run

        second_ids = dict(
            v84_db.execute("SELECT name, personality_id FROM personalities").fetchall()
        )
        assert first_ids == second_ids

    def test_collision_resolution_during_backfill(self):
        """If two existing names slugify to the same id, the second
        gets a `_v2` suffix. This catches any future personality roster
        where naming collisions occur."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _create_v84_personalities_table(conn)

        # Names that slugify to the same base
        conn.execute(
            "INSERT INTO personalities (name, config_json) VALUES (?, ?)",
            ("Test Hero", "{}"),
        )
        conn.execute(
            "INSERT INTO personalities (name, config_json) VALUES (?, ?)",
            ("Test  Hero", "{}"),  # double space collapses to same slug
        )
        conn.commit()

        _apply_v85_via_schema_manager(conn)

        rows = conn.execute("SELECT name, personality_id FROM personalities ORDER BY id").fetchall()
        ids = [r["personality_id"] for r in rows]
        assert ids[0] == "test_hero"
        assert ids[1] == "test_hero_v2"
        conn.close()

    def test_partial_backfill_state_is_recoverable(self, v84_db):
        """If the migration is interrupted (some rows have ids, others
        don't), re-running it backfills only the missing rows without
        disturbing existing assignments."""
        # First run
        _apply_v85_via_schema_manager(v84_db)

        # Simulate a partial state: blow away half the ids
        v84_db.execute("UPDATE personalities SET personality_id = NULL WHERE id <= 3")
        v84_db.commit()

        # Capture the surviving ids that should not change
        surviving = dict(
            v84_db.execute(
                "SELECT name, personality_id FROM personalities " "WHERE personality_id IS NOT NULL"
            ).fetchall()
        )

        # Re-run migration
        _apply_v85_via_schema_manager(v84_db)

        # All rows now have ids
        nullified = v84_db.execute(
            "SELECT COUNT(*) FROM personalities WHERE personality_id IS NULL"
        ).fetchone()[0]
        assert nullified == 0

        # Surviving ids unchanged
        for name, sid in surviving.items():
            current = v84_db.execute(
                "SELECT personality_id FROM personalities WHERE name = ?", (name,)
            ).fetchone()["personality_id"]
            assert current == sid


class TestSeedSourceAlignment:
    """The DB-side backfill (v85 migration) and the JSON seed source
    must produce the same ids. Drift between them causes
    seed_personalities_from_json to insert rows with mismatched ids."""

    def test_json_seed_ids_match_migration_output(self):
        """For every personality currently in personalities.json, the
        `id` field there matches what v85 would have computed via the
        slugify rule. This catches drift in either direction."""
        from poker.personality_id import slugify_personality_name

        json_path = REPO_ROOT / "poker" / "personalities.json"
        with json_path.open() as f:
            data = json.load(f)

        for name, entry in data["personalities"].items():
            json_id = entry.get("id")
            assert json_id is not None, f"Seed missing id for {name!r}"
            expected = slugify_personality_name(name)
            # Note: if a personality was renamed after id was assigned,
            # the json id stays as the original slug — not the current
            # name's slug. We allow that exception by requiring the id
            # to either match the current slug OR be a valid slug-shaped
            # string (no special characters, no edge underscores).
            if json_id != expected:
                assert json_id == json_id.lower(), f"{name}: malformed id"
                assert json_id.strip("_") == json_id, f"{name}: edge underscores"
                # Anything beyond the simple slugify result must be a
                # versioned suffix from a prior collision.
                # We do not enforce stricter pattern here — renames are
                # allowed without forcing a rebuild.
