#!/usr/bin/env python3
"""
Test suite for SQL query security in experiment routes.

Tests the _execute_sql_query function to ensure:
1. Only allowed tables can be queried
2. Dangerous keywords are blocked
3. LIMIT is properly enforced (including comment bypass prevention)
4. Only SELECT and specific PRAGMAs are allowed
"""
import os
import sys
import unittest
import tempfile
import json
import re
import sqlite3
from unittest.mock import MagicMock, patch

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from poker.persistence import GamePersistence


# Copy the security logic from experiment_routes for standalone testing
# This avoids Flask app initialization issues while testing the core security logic

ALLOWED_SQL_TABLES = {
    'prompt_captures', 'capture_labels', 'player_decision_analysis',
    'prompt_presets', 'personalities', 'replay_experiments',
    'replay_results', 'api_usage'
}


def _execute_sql_query_standalone(sql: str, db_path: str) -> str:
    """Execute a read-only SQL query against allowed tables.

    This is a standalone version of the function for testing purposes,
    matching the security logic in experiment_routes.py.
    """
    # Validate: must be SELECT or read-only PRAGMA
    normalized = sql.strip().upper()
    is_select = normalized.startswith('SELECT')
    is_pragma = normalized.startswith('PRAGMA')

    if not is_select and not is_pragma:
        return json.dumps({"error": "Only SELECT and PRAGMA queries allowed"})

    # For PRAGMA, only allow specific read-only commands for schema discovery
    if is_pragma:
        # Extract pragma name (e.g., "PRAGMA TABLE_INFO(foo)" -> "TABLE_INFO")
        pragma_match = re.match(r'PRAGMA\s+(\w+)', normalized)
        if not pragma_match:
            return json.dumps({"error": "Invalid PRAGMA syntax"})

        pragma_name = pragma_match.group(1)
        allowed_pragmas = {'TABLE_INFO', 'TABLE_LIST', 'INDEX_LIST', 'INDEX_INFO', 'DATABASE_LIST'}
        if pragma_name not in allowed_pragmas:
            return json.dumps({"error": f"PRAGMA {pragma_name} not allowed. Use: TABLE_INFO, TABLE_LIST, INDEX_LIST"})

    # Validate: no dangerous keywords (even in subqueries)
    forbidden = ['INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE', 'TRUNCATE', 'REPLACE', 'ATTACH', 'DETACH']
    for kw in forbidden:
        # Check for keyword as a standalone word (not part of another word)
        if re.search(rf'\b{kw}\b', normalized):
            return json.dumps({"error": f"Query contains forbidden keyword: {kw}"})

    # Validate: only allowed tables can be queried (for SELECT queries)
    if is_select:
        # Extract table names from FROM and JOIN clauses
        # Matches: FROM table, JOIN table, LEFT JOIN table, etc.
        table_pattern = r'\bFROM\s+(\w+)|\bJOIN\s+(\w+)'
        matches = re.findall(table_pattern, normalized)
        # Flatten matches (each match is a tuple with one empty string)
        tables = {t.lower() for match in matches for t in match if t}

        # Check against whitelist (case-insensitive)
        allowed_lower = {t.lower() for t in ALLOWED_SQL_TABLES}
        disallowed = tables - allowed_lower
        if disallowed:
            return json.dumps({"error": f"Tables not allowed: {', '.join(sorted(disallowed))}. Allowed: {', '.join(sorted(ALLOWED_SQL_TABLES))}"})

    # Execute with row limit for SELECT queries
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Add LIMIT if SELECT and not present
            if is_select and 'LIMIT' not in normalized:
                # Strip comments before adding LIMIT to prevent bypass
                # Handles both -- comments and /* */ comments
                sql_clean = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)
                sql_clean = re.sub(r'/\*.*?\*/', '', sql_clean, flags=re.DOTALL)
                sql_clean = sql_clean.rstrip('; \t\n')
                sql = sql_clean + " LIMIT 100"
            cursor = conn.execute(sql)
            rows = [dict(row) for row in cursor.fetchall()]

        return json.dumps({"rows": rows, "count": len(rows)})
    except sqlite3.Error as e:
        return json.dumps({"error": f"SQL error: {str(e)}"})


class TestSQLQuerySecurity(unittest.TestCase):
    """Test cases for SQL query security."""

    def setUp(self):
        """Create a test database with sample data."""
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        self.db_path = self.test_db.name

        # Create tables that match the allowed list
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS personalities (
                id INTEGER PRIMARY KEY,
                name TEXT,
                config_json TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prompt_captures (
                id INTEGER PRIMARY KEY,
                game_id TEXT,
                player_name TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS admin_tokens (
                id INTEGER PRIMARY KEY,
                token TEXT,
                secret TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                password_hash TEXT
            )
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        """Clean up temporary database."""
        os.unlink(self.test_db.name)

    def _execute(self, sql: str) -> dict:
        """Helper to execute and parse result."""
        result = _execute_sql_query_standalone(sql, self.db_path)
        return json.loads(result)

    # ============================================================
    # Table Whitelist Tests
    # ============================================================

    def test_allowed_table_query_succeeds(self):
        """Verify queries to whitelisted tables succeed."""
        data = self._execute("SELECT * FROM personalities LIMIT 1")
        self.assertIn("rows", data)
        self.assertNotIn("error", data)

    def test_disallowed_table_blocked(self):
        """Verify queries to non-whitelisted tables are rejected."""
        data = self._execute("SELECT * FROM admin_tokens")
        self.assertIn("error", data)
        self.assertIn("not allowed", data["error"].lower())

    def test_disallowed_table_users_blocked(self):
        """Verify queries to users table are rejected."""
        data = self._execute("SELECT * FROM users")
        self.assertIn("error", data)
        self.assertIn("not allowed", data["error"].lower())

    def test_join_with_disallowed_table_blocked(self):
        """Verify JOINs with non-whitelisted tables are rejected."""
        data = self._execute(
            "SELECT * FROM prompt_captures JOIN admin_tokens ON 1=1"
        )
        self.assertIn("error", data)
        self.assertIn("not allowed", data["error"].lower())

    def test_left_join_with_disallowed_table_blocked(self):
        """Verify LEFT JOINs with non-whitelisted tables are rejected."""
        data = self._execute(
            "SELECT * FROM personalities LEFT JOIN users ON 1=1"
        )
        self.assertIn("error", data)
        self.assertIn("not allowed", data["error"].lower())

    def test_subquery_with_disallowed_table_blocked(self):
        """Verify subqueries with non-whitelisted tables are rejected."""
        data = self._execute(
            "SELECT * FROM personalities WHERE id IN (SELECT id FROM admin_tokens)"
        )
        self.assertIn("error", data)
        self.assertIn("not allowed", data["error"].lower())

    # ============================================================
    # Dangerous Keyword Tests
    # ============================================================

    def test_insert_blocked(self):
        """Verify INSERT statements are blocked."""
        data = self._execute(
            "INSERT INTO personalities (name) VALUES ('test')"
        )
        self.assertIn("error", data)

    def test_update_blocked(self):
        """Verify UPDATE statements are blocked."""
        data = self._execute(
            "UPDATE personalities SET name='hacked'"
        )
        self.assertIn("error", data)

    def test_delete_blocked(self):
        """Verify DELETE statements are blocked."""
        data = self._execute("DELETE FROM personalities")
        self.assertIn("error", data)

    def test_drop_blocked(self):
        """Verify DROP statements are blocked."""
        data = self._execute("DROP TABLE personalities")
        self.assertIn("error", data)

    def test_alter_blocked(self):
        """Verify ALTER statements are blocked."""
        data = self._execute(
            "ALTER TABLE personalities ADD COLUMN pwned TEXT"
        )
        self.assertIn("error", data)

    def test_attach_blocked(self):
        """Verify ATTACH statements are blocked (potential security risk)."""
        data = self._execute("ATTACH DATABASE ':memory:' AS temp")
        self.assertIn("error", data)

    # ============================================================
    # LIMIT Enforcement Tests
    # ============================================================

    def test_limit_auto_added(self):
        """Verify LIMIT is automatically added to queries without it."""
        # Insert test data
        conn = sqlite3.connect(self.db_path)
        for i in range(150):
            conn.execute(
                "INSERT INTO personalities (name, config_json) VALUES (?, ?)",
                (f"test_personality_{i}", "{}")
            )
        conn.commit()
        conn.close()

        # Query without LIMIT
        data = self._execute("SELECT * FROM personalities")
        self.assertIn("rows", data)
        # Should be capped at 100
        self.assertLessEqual(len(data["rows"]), 100)

    def test_comment_limit_bypass_prevented(self):
        """Verify LIMIT is applied even with trailing comments."""
        # Insert test data
        conn = sqlite3.connect(self.db_path)
        for i in range(150):
            conn.execute(
                "INSERT INTO personalities (name, config_json) VALUES (?, ?)",
                (f"comment_test_{i}", "{}")
            )
        conn.commit()
        conn.close()

        # Try to bypass with comment
        data = self._execute("SELECT * FROM personalities -- bypass")
        self.assertIn("rows", data)
        # Should still be capped at 100
        self.assertLessEqual(len(data["rows"]), 100)

    def test_block_comment_limit_bypass_prevented(self):
        """Verify LIMIT is applied even with block comments."""
        # Insert test data
        conn = sqlite3.connect(self.db_path)
        for i in range(150):
            conn.execute(
                "INSERT INTO personalities (name, config_json) VALUES (?, ?)",
                (f"block_comment_test_{i}", "{}")
            )
        conn.commit()
        conn.close()

        # Try to bypass with block comment
        data = self._execute("SELECT * FROM personalities /* bypass */")
        self.assertIn("rows", data)
        # Should still be capped at 100
        self.assertLessEqual(len(data["rows"]), 100)

    def test_explicit_limit_respected(self):
        """Verify explicit LIMIT in query is respected."""
        data = self._execute("SELECT * FROM personalities LIMIT 5")
        self.assertIn("rows", data)
        self.assertLessEqual(len(data["rows"]), 5)

    # ============================================================
    # Query Type Tests
    # ============================================================

    def test_only_select_allowed(self):
        """Verify only SELECT statements are allowed."""
        data = self._execute("EXPLAIN SELECT * FROM personalities")
        # EXPLAIN is not SELECT or PRAGMA, so should be blocked
        self.assertIn("error", data)

    def test_pragma_table_info_allowed(self):
        """Verify PRAGMA TABLE_INFO is allowed."""
        data = self._execute("PRAGMA table_info(personalities)")
        self.assertIn("rows", data)

    def test_pragma_table_list_allowed(self):
        """Verify PRAGMA TABLE_LIST is allowed."""
        data = self._execute("PRAGMA table_list")
        self.assertIn("rows", data)

    def test_dangerous_pragma_blocked(self):
        """Verify dangerous PRAGMAs are blocked."""
        # PRAGMA writable_schema can be used to bypass security
        data = self._execute("PRAGMA writable_schema = ON")
        self.assertIn("error", data)

    def test_pragma_integrity_check_blocked(self):
        """Verify PRAGMA integrity_check is blocked (can be slow)."""
        data = self._execute("PRAGMA integrity_check")
        self.assertIn("error", data)

    # ============================================================
    # Edge Case Tests
    # ============================================================

    def test_case_insensitive_keyword_blocking(self):
        """Verify keyword blocking is case insensitive."""
        data = self._execute("DeLeTe FROM personalities")
        self.assertIn("error", data)

    def test_empty_query_rejected(self):
        """Verify empty queries are rejected."""
        data = self._execute("")
        self.assertIn("error", data)

    def test_whitespace_only_query_rejected(self):
        """Verify whitespace-only queries are rejected."""
        data = self._execute("   \n\t  ")
        self.assertIn("error", data)

    def test_case_insensitive_table_whitelist(self):
        """Verify table whitelist is case insensitive."""
        data = self._execute("SELECT * FROM PERSONALITIES LIMIT 1")
        self.assertIn("rows", data)
        self.assertNotIn("error", data)


if __name__ == '__main__':
    unittest.main()
