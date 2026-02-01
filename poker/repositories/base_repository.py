"""Base repository with shared connection management.

Provides thread-local connection reuse and WAL mode configuration
for all domain repositories.
"""
import sqlite3
import threading
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class BaseRepository:
    """Base class for SQLite-backed repositories.

    Provides:
    - Thread-local connection reuse (T3-09: avoids creating a new connection per operation)
    - WAL mode with 5s busy timeout for concurrent read/write
    - Explicit close() for clean shutdown (T3-07: prevents connection leaks)

    Usage in subclasses:
        with self._get_connection() as conn:
            conn.execute("SELECT ...")
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()

    @contextmanager
    def _get_connection(self):
        """Get a database connection, reusing the thread-local one if available.

        Connections are reused within the same thread to avoid the overhead
        of creating a new connection per operation. The context manager
        commits on clean exit and rolls back on exception.
        """
        conn = self._ensure_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _ensure_connection(self) -> sqlite3.Connection:
        """Return the thread-local connection, creating one if needed."""
        conn = getattr(self._local, 'connection', None)
        if conn is not None:
            try:
                # Verify connection is still alive
                conn.execute("SELECT 1")
                return conn
            except (sqlite3.ProgrammingError, sqlite3.OperationalError):
                # Connection is closed or broken — recreate
                self._local.connection = None

        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        self._local.connection = conn
        return conn

    def close(self):
        """Close the thread-local connection if open.

        Call this during shutdown or test teardown to prevent connection leaks.
        """
        conn = getattr(self._local, 'connection', None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.connection = None
