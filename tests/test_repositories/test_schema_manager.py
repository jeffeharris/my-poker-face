"""Tests for SchemaManager."""

import os
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
        # Build a minimal dual-column (post-v146) schema and stamp the version at
        # 146 so only the v147 drop runs forward.
        conn = sqlite3.connect(self.test_db.name)
        conn.executescript(
            """
            CREATE TABLE schema_version (version INTEGER PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, description TEXT);
            CREATE TABLE personalities (id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL, config_json TEXT NOT NULL,
                created_at TIMESTAMP, updated_at TIMESTAMP, is_generated BOOLEAN DEFAULT 1,
                source TEXT, times_used INTEGER DEFAULT 0, elasticity_config TEXT,
                owner_id TEXT, visibility TEXT, personality_id TEXT UNIQUE);
            CREATE TABLE avatar_images (id INTEGER PRIMARY KEY AUTOINCREMENT,
                personality_name TEXT NOT NULL, personality_id TEXT, emotion TEXT NOT NULL,
                image_data BLOB NOT NULL, content_type TEXT DEFAULT 'image/png',
                width INTEGER, height INTEGER, file_size INTEGER, full_image_data BLOB,
                full_width INTEGER, full_height INTEGER, full_file_size INTEGER,
                created_at TIMESTAMP, updated_at TIMESTAMP, UNIQUE(personality_name, emotion));
            INSERT INTO personalities (name, config_json, personality_id)
                VALUES ('Napoleon', '{}', 'napoleon');
            INSERT INTO avatar_images (personality_name, personality_id, emotion, image_data)
                VALUES ('Napoleon', 'napoleon', 'happy', X'AABB');
            INSERT INTO avatar_images (personality_name, personality_id, emotion, image_data)
                VALUES ('GhostName', NULL, 'angry', X'CCDD');
            INSERT INTO schema_version (version, description) VALUES (146, 'pre-v147 stamp');
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
        self.assertEqual(rows, [('napoleon', 'happy')])  # matched kept, orphan dropped

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


if __name__ == '__main__':
    unittest.main()
