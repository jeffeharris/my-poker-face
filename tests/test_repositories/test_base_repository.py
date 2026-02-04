"""Tests for BaseRepository connection management."""
import os
import tempfile
import threading
import unittest

from poker.repositories.base_repository import BaseRepository
from poker.repositories.schema_manager import SchemaManager


class TestBaseRepository(unittest.TestCase):
    """Test BaseRepository connection management and WAL mode."""

    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        # Initialize schema so tables exist
        self.schema_manager = SchemaManager(self.test_db.name)
        self.schema_manager.ensure_schema()
        self.repo = BaseRepository(self.test_db.name)

    def tearDown(self):
        self.repo.close()
        os.unlink(self.test_db.name)

    def test_get_connection_returns_working_connection(self):
        with self.repo._get_connection() as conn:
            cursor = conn.execute("SELECT 1")
            self.assertEqual(cursor.fetchone()[0], 1)

    def test_connection_has_row_factory(self):
        with self.repo._get_connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS test_t (id INTEGER, name TEXT)")
            conn.execute("INSERT INTO test_t VALUES (1, 'alice')")
        with self.repo._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM test_t WHERE id = 1")
            row = cursor.fetchone()
            # row_factory=sqlite3.Row allows dict-like access
            self.assertEqual(row['name'], 'alice')

    def test_wal_mode_enabled(self):
        with self.repo._get_connection() as conn:
            cursor = conn.execute("PRAGMA journal_mode")
            mode = cursor.fetchone()[0]
            self.assertEqual(mode, 'wal')

    def test_connection_reused_within_thread(self):
        """Same thread should get the same connection object."""
        with self.repo._get_connection() as conn1:
            id1 = id(conn1)
        with self.repo._get_connection() as conn2:
            id2 = id(conn2)
        self.assertEqual(id1, id2)

    def test_different_threads_get_different_connections(self):
        """Different threads should get different connection objects."""
        connections = []
        barrier = threading.Barrier(2)  # Ensure both threads hold connections simultaneously

        def get_conn():
            with self.repo._get_connection() as conn:
                connections.append(conn)
                barrier.wait()  # Wait for both threads to have their connections
            self.repo.close()  # Clean up thread-local connection

        t1 = threading.Thread(target=get_conn)
        t2 = threading.Thread(target=get_conn)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(len(connections), 2)
        # Compare IDs while both connections are still referenced (not freed)
        self.assertNotEqual(id(connections[0]), id(connections[1]))

    def test_commit_on_success(self):
        with self.repo._get_connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS test_commit (val TEXT)")
            conn.execute("INSERT INTO test_commit VALUES ('hello')")

        # Verify data persisted
        with self.repo._get_connection() as conn:
            cursor = conn.execute("SELECT val FROM test_commit")
            self.assertEqual(cursor.fetchone()[0], 'hello')

    def test_rollback_on_exception(self):
        with self.repo._get_connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS test_rollback (val TEXT)")
            conn.execute("INSERT INTO test_rollback VALUES ('keep')")

        try:
            with self.repo._get_connection() as conn:
                conn.execute("INSERT INTO test_rollback VALUES ('discard')")
                raise ValueError("force rollback")
        except ValueError:
            pass

        with self.repo._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM test_rollback")
            self.assertEqual(cursor.fetchone()[0], 1)

    def test_close_releases_connection(self):
        with self.repo._get_connection() as conn:
            conn.execute("SELECT 1")

        self.repo.close()
        # After close, next call should create a new connection
        with self.repo._get_connection() as conn:
            cursor = conn.execute("SELECT 1")
            self.assertEqual(cursor.fetchone()[0], 1)

    def test_close_idempotent(self):
        """Calling close() multiple times should not raise."""
        self.repo.close()
        self.repo.close()
        self.repo.close()


if __name__ == '__main__':
    unittest.main()
