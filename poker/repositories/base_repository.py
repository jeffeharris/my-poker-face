"""Base repository with shared connection management.

Provides thread-local connection reuse and WAL mode configuration
for all domain repositories.
"""
import sqlite3
import threading
import logging
import time
import functools
from contextlib import contextmanager
from typing import TypeVar, Callable

logger = logging.getLogger(__name__)

F = TypeVar('F', bound=Callable)


def retry_on_lock(max_retries: int = 3, base_delay: float = 0.1) -> Callable[[F], F]:
    """Decorator to retry a function on database lock errors.

    Uses exponential backoff: base_delay, base_delay*2, base_delay*4, etc.

    Args:
        max_retries: Maximum number of retry attempts (default 3)
        base_delay: Initial delay in seconds (default 0.1)

    Returns:
        Decorated function that retries on sqlite3.OperationalError with 'locked' message
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    error_msg = str(e).lower()
                    if 'locked' in error_msg or 'busy' in error_msg:
                        last_exception = e
                        if attempt < max_retries:
                            delay = base_delay * (2 ** attempt)
                            logger.warning(
                                f"Database lock detected in {func.__name__}, "
                                f"retry {attempt + 1}/{max_retries} after {delay:.2f}s"
                            )
                            time.sleep(delay)
                            continue
                    raise
            # Exhausted retries
            logger.error(f"Database lock persisted after {max_retries} retries in {func.__name__}")
            raise last_exception
        return wrapper  # type: ignore
    return decorator


class BaseRepository:
    """Base class for SQLite-backed repositories.

    Provides:
    - Thread-local connection reuse (avoids creating a new connection per operation)
    - WAL mode with 5s busy timeout for concurrent read/write
    - Explicit close() for clean shutdown (prevents connection leaks)

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

    @contextmanager
    def _get_connection_with_retry(self, max_retries: int = 3, base_delay: float = 0.1):
        """Get a database connection with retry logic for lock contention.

        Same as _get_connection but retries on lock/busy errors with
        exponential backoff.

        Args:
            max_retries: Maximum retry attempts (default 3)
            base_delay: Initial delay in seconds (default 0.1)
        """
        last_exception = None
        for attempt in range(max_retries + 1):
            try:
                with self._get_connection() as conn:
                    yield conn
                    return  # Success - exit retry loop
            except sqlite3.OperationalError as e:
                error_msg = str(e).lower()
                if 'locked' in error_msg or 'busy' in error_msg:
                    last_exception = e
                    if attempt < max_retries:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            f"Database lock in {self.__class__.__name__}, "
                            f"retry {attempt + 1}/{max_retries} after {delay:.2f}s"
                        )
                        time.sleep(delay)
                        continue
                raise
        # Exhausted retries
        logger.error(f"Database lock persisted after {max_retries} retries")
        raise last_exception

    def close(self):
        """Close the thread-local connection if open.

        Call this during shutdown or test teardown to prevent connection leaks.
        """
        conn = getattr(self._local, 'connection', None)
        if conn is not None:
            try:
                conn.close()
            except Exception as e:
                logger.debug(f"Error closing connection for {self.db_path}: {e}")
            self._local.connection = None
