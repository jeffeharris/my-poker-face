"""
Migration support for the repository layer.
"""
from pathlib import Path
from typing import List, Tuple
import logging

from ..database import DatabaseContext

logger = logging.getLogger(__name__)

# Current schema version - increment when adding new schema files
SCHEMA_VERSION = 1


class MigrationRunner:
    """Runs schema migrations for fresh databases."""

    def __init__(self, db: DatabaseContext):
        self.db = db
        self.schema_dir = Path(__file__).parent / "schema"

    def get_schema_files(self) -> List[Path]:
        """Get all schema SQL files in order."""
        return sorted(self.schema_dir.glob("*.sql"))

    def initialize_fresh_database(self) -> None:
        """Initialize a fresh database with all schema files."""
        schema_files = self.get_schema_files()

        if not schema_files:
            logger.warning("No schema files found in %s", self.schema_dir)
            return

        with self.db.transaction() as conn:
            for sql_file in schema_files:
                logger.info(f"Executing schema: {sql_file.name}")
                conn.executescript(sql_file.read_text())

            # Record schema version
            conn.execute(
                """
                INSERT OR REPLACE INTO schema_version (version, description)
                VALUES (?, ?)
                """,
                (SCHEMA_VERSION, f"Fresh install with {len(schema_files)} schema files"),
            )

    def get_current_version(self) -> int:
        """Get the current schema version, or 0 if not set."""
        return self.db.get_schema_version()

    def needs_initialization(self) -> bool:
        """Check if the database needs initialization."""
        return not self.db.table_exists("schema_version")


def initialize_database(db_path: str) -> DatabaseContext:
    """
    Initialize a database, creating schema if needed.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        The initialized DatabaseContext.
    """
    db = DatabaseContext(db_path)
    runner = MigrationRunner(db)

    if runner.needs_initialization():
        logger.info(f"Initializing fresh database at {db_path}")
        runner.initialize_fresh_database()
    else:
        logger.debug(f"Database already initialized (version {runner.get_current_version()})")

    return db
