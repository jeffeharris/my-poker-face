"""
DatabaseContext - Connection management for SQLite repositories.
"""
import sqlite3
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

logger = logging.getLogger(__name__)


class DatabaseContext:
    """Manages SQLite database connections with WAL mode and proper configuration."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._enable_wal_mode()

    def _enable_wal_mode(self) -> None:
        """Enable WAL mode for better concurrent read/write performance.

        WAL (Write-Ahead Logging) mode allows concurrent readers and writers,
        which is important for parallel tournament execution. The 5-second
        busy timeout prevents immediate failures on brief lock contention
        while failing fast on real deadlocks.
        """
        try:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")  # 5 second timeout
                conn.execute("PRAGMA synchronous=NORMAL")  # Good balance of safety/speed
        except Exception as e:
            logger.warning(f"Could not enable WAL mode: {e}")

    def initialize_schema(self) -> None:
        """Create all tables for a fresh database by executing schema SQL files."""
        schema_dir = Path(__file__).parent / "migrations" / "schema"

        with self.connect() as conn:
            for sql_file in sorted(schema_dir.glob("*.sql")):
                logger.info(f"Executing schema file: {sql_file.name}")
                conn.executescript(sql_file.read_text())
            conn.commit()

    def initialize_schema_from_string(self, schema_sql: str) -> None:
        """Initialize schema from a SQL string (for testing or custom schemas)."""
        with self.connect() as conn:
            conn.executescript(schema_sql)
            conn.commit()

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection with row factory configured."""
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a connection within a transaction context."""
        with self.connect() as conn:
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a single SQL statement and return cursor."""
        with self.connect() as conn:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor

    def execute_many(self, sql: str, params_list: list) -> None:
        """Execute a SQL statement for multiple parameter sets."""
        with self.connect() as conn:
            conn.executemany(sql, params_list)
            conn.commit()

    def fetch_one(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        """Execute a query and fetch one row."""
        with self.connect() as conn:
            cursor = conn.execute(sql, params)
            return cursor.fetchone()

    def fetch_all(self, sql: str, params: tuple = ()) -> list:
        """Execute a query and fetch all rows."""
        with self.connect() as conn:
            cursor = conn.execute(sql, params)
            return cursor.fetchall()

    def table_exists(self, table_name: str) -> bool:
        """Check if a table exists in the database."""
        row = self.fetch_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        )
        return row is not None

    def get_schema_version(self) -> int:
        """Get the current schema version, or 0 if not set."""
        if not self.table_exists("schema_version"):
            return 0
        row = self.fetch_one(
            "SELECT MAX(version) as version FROM schema_version"
        )
        return row["version"] if row and row["version"] else 0
