"""Tests for SchemaManager."""

import os
import re
import sqlite3
import tempfile
import unittest

from poker.repositories.schema_manager import SCHEMA_VERSION, SchemaManager


class TestSchemaManager(unittest.TestCase):
    """Test schema initialization and migrations."""

    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()

    def tearDown(self):
        os.unlink(self.test_db.name)

    def test_ensure_schema_creates_tables(self):
        """Fresh database should have all tables after ensure_schema."""
        sm = SchemaManager(self.test_db.name)
        sm.ensure_schema()

        conn = sqlite3.connect(self.test_db.name)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        # Check core tables exist
        expected_tables = {
            'schema_version',
            'games',
            'game_messages',
            'ai_player_state',
            'personalities',
            'hand_history',
            'tournament_results',
            'avatar_images',
            'api_usage',
            'prompt_captures',
            'experiments',
            'experiment_games',
            'users',
            'app_settings',
            'prompt_presets',
            'guest_usage_tracking',
        }
        for table in expected_tables:
            self.assertIn(table, tables, f"Missing table: {table}")

    def test_schema_version_is_current(self):
        """Schema version should match SCHEMA_VERSION after init."""
        sm = SchemaManager(self.test_db.name)
        sm.ensure_schema()

        conn = sqlite3.connect(self.test_db.name)
        cursor = conn.execute("SELECT MAX(version) FROM schema_version")
        version = cursor.fetchone()[0]
        conn.close()

        self.assertEqual(version, SCHEMA_VERSION)

    def test_ensure_schema_is_idempotent(self):
        """Calling ensure_schema twice should not fail."""
        sm = SchemaManager(self.test_db.name)
        sm.ensure_schema()
        sm.ensure_schema()  # Should not raise

    def test_wal_mode_enabled(self):
        """WAL mode should be enabled after ensure_schema."""
        sm = SchemaManager(self.test_db.name)
        sm.ensure_schema()

        conn = sqlite3.connect(self.test_db.name)
        cursor = conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        conn.close()

        self.assertEqual(mode, 'wal')

    def test_fresh_db_avatar_images_is_pid_only(self):
        """v147: a fresh DB builds avatar_images keyed solely on personality_id —
        no legacy personality_name column."""
        sm = SchemaManager(self.test_db.name)
        sm.ensure_schema()

        conn = sqlite3.connect(self.test_db.name)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(avatar_images)")}
        idx = {row[1] for row in conn.execute("PRAGMA index_list(avatar_images)")}
        conn.close()

        self.assertIn('personality_id', cols)
        self.assertNotIn('personality_name', cols)
        self.assertIn('idx_avatar_pid', idx)

    def test_v147_drops_name_column_and_orphans(self):
        """v147 migration: rebuild a pre-v147 (post-v146, dual-column) avatar_images
        keyed on personality_id — drop the personality_name column, preserve rows
        with a backfilled pid, drop NULL-pid orphans."""
        # Build the COMPLETE current schema first, then regress ONLY avatar_images
        # to the post-v146/pre-v147 dual-column shape and stamp the version at 146.
        # Building the full schema (not a minimal hand-crafted one) keeps the
        # renumber self-heal's `limp_count` sentinel satisfied so it doesn't fire —
        # we're isolating the v147 avatar drop, not the 132–138 re-assert.
        SchemaManager(self.test_db.name).ensure_schema()
        conn = sqlite3.connect(self.test_db.name)
        conn.executescript(
            """
            DROP TABLE avatar_images;
            CREATE TABLE avatar_images (id INTEGER PRIMARY KEY AUTOINCREMENT,
                personality_name TEXT NOT NULL, personality_id TEXT, emotion TEXT NOT NULL,
                image_data BLOB NOT NULL, content_type TEXT DEFAULT 'image/png',
                width INTEGER, height INTEGER, file_size INTEGER, full_image_data BLOB,
                full_width INTEGER, full_height INTEGER, full_file_size INTEGER,
                created_at TIMESTAMP, updated_at TIMESTAMP, UNIQUE(personality_name, emotion));
            INSERT OR IGNORE INTO personalities (name, config_json, personality_id)
                VALUES ('Zz Drop Test', '{}', 'zz_drop_test');
            INSERT INTO avatar_images (personality_name, personality_id, emotion, image_data)
                VALUES ('Zz Drop Test', 'zz_drop_test', 'happy', X'AABB');
            INSERT INTO avatar_images (personality_name, personality_id, emotion, image_data)
                VALUES ('GhostName', NULL, 'angry', X'CCDD');
            DELETE FROM schema_version WHERE version > 146;
            INSERT OR REPLACE INTO schema_version (version, description) VALUES (146, 'pre-v147 stamp');
            """
        )
        conn.commit()
        conn.close()

        SchemaManager(self.test_db.name).ensure_schema()

        conn = sqlite3.connect(self.test_db.name)
        version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        cols = {row[1] for row in conn.execute("PRAGMA table_info(avatar_images)")}
        rows = conn.execute(
            "SELECT personality_id, emotion FROM avatar_images ORDER BY emotion"
        ).fetchall()
        conn.close()

        self.assertEqual(version, SCHEMA_VERSION)
        self.assertNotIn('personality_name', cols)  # legacy column dropped
        self.assertEqual(rows, [('zz_drop_test', 'happy')])  # matched kept, orphan dropped

    def test_games_table_has_coach_mode(self):
        """Games table should have coach_mode column (v62 migration)."""
        sm = SchemaManager(self.test_db.name)
        sm.ensure_schema()

        conn = sqlite3.connect(self.test_db.name)
        cursor = conn.execute("PRAGMA table_info(games)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()

        self.assertIn('coach_mode', columns)

    def test_hand_history_has_deck_seed(self):
        """Hand history should include deck_seed for deterministic replay."""
        sm = SchemaManager(self.test_db.name)
        sm.ensure_schema()

        conn = sqlite3.connect(self.test_db.name)
        cursor = conn.execute("PRAGMA table_info(hand_history)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()

        self.assertIn('deck_seed', columns)

    def test_rbac_tables_exist(self):
        """RBAC tables from v52 migration should exist."""
        sm = SchemaManager(self.test_db.name)
        sm.ensure_schema()

        conn = sqlite3.connect(self.test_db.name)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('groups', 'user_groups', 'permissions', 'group_permissions')"
        )
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        self.assertEqual(tables, {'groups', 'user_groups', 'permissions', 'group_permissions'})


class TestMigrationRegistryContiguity(unittest.TestCase):
    """Guard the renumbering hazard. Migration version numbers must form a
    gapless, duplicate-free 1..SCHEMA_VERSION. A gap, a reused number, or a
    migration numbered below a deployed DB's current version is how a DB ends up
    stamped vN yet missing a migration the walk skipped (the dev DB hit exactly
    this — v148 missing the v139 entity_kind column; see PROD_MERGE_PLAN.md
    root-cause finding 2026-06-03). The runtime catch-all is
    scripts/schema_completeness_check.py; this catches the registry-shape half at
    CI time so a future renumber can't silently leave a hole."""

    def _migration_versions(self):
        from poker.repositories.legacy_migrations import LegacyMigrations

        versions = []
        for name in dir(LegacyMigrations):
            m = re.match(r"_migrate_v(\d+)_", name)
            if m:
                versions.append(int(m.group(1)))
        return versions

    def test_no_duplicate_version_numbers(self):
        versions = self._migration_versions()
        dupes = sorted({v for v in versions if versions.count(v) > 1})
        self.assertEqual(dupes, [], f"duplicate migration version numbers: {dupes}")

    def test_versions_are_gapless_1_to_schema_version(self):
        versions = sorted(set(self._migration_versions()))
        self.assertEqual(
            versions,
            list(range(1, SCHEMA_VERSION + 1)),
            "migration version numbers must be a gapless 1..SCHEMA_VERSION "
            "(no gaps, none beyond SCHEMA_VERSION) — a hole here means a renumber "
            "left a migration unreachable",
        )


if __name__ == '__main__':
    unittest.main()
