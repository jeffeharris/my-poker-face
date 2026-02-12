"""Tests for SchemaManager."""
import os
import tempfile
import unittest
import sqlite3

from poker.repositories.schema_manager import SchemaManager, SCHEMA_VERSION


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
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        # Check core tables exist
        expected_tables = {
            'schema_version', 'games', 'game_messages', 'ai_player_state',
            'personalities', 'hand_history', 'tournament_results',
            'avatar_images', 'api_usage', 'prompt_captures',
            'experiments', 'experiment_games', 'users',
            'app_settings', 'prompt_presets', 'guest_usage_tracking',
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
