#!/usr/bin/env python3
"""
One-time migration script for production/dev databases.

This script migrates data from an existing database to a new one with the
clean repository schema. It preserves important historical data like:
- api_usage (LLM cost tracking history)
- prompt_captures (AI decision debugging history)
- model_pricing (Pricing SKUs)
- enabled_models (Model configuration)
- personalities (Generated AI personalities)
- avatar_images (Generated character avatars)
- player_career_stats (Career statistics)

Usage:
    python scripts/migrate_to_new_schema.py --source poker_games.db --target poker_games_v2.db

After verification:
    mv poker_games.db poker_games_backup.db
    mv poker_games_v2.db poker_games.db
"""

import argparse
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from poker.repositories.database import DatabaseContext
from poker.repositories.migrations import SCHEMA_VERSION

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Tables that MUST be preserved (contain important historical data)
PRESERVE_TABLES = [
    'api_usage',           # LLM cost tracking history
    'prompt_captures',     # AI decision debugging history
    'model_pricing',       # Pricing SKUs
    'enabled_models',      # Model configuration
    'player_decision_analysis',  # Decision quality analysis
    'personalities',       # Generated personalities
    'avatar_images',       # Generated avatars
    'player_career_stats', # Career statistics
]

# Tables to optionally migrate (can be regenerated but nice to keep)
OPTIONAL_TABLES = [
    'tournament_results',
    'tournament_standings',
    'users',
    'app_settings',
]


def get_table_columns(conn: sqlite3.Connection, table_name: str) -> list:
    """Get column names for a table."""
    cursor = conn.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cursor.fetchall()]


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Check if a table exists."""
    cursor = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cursor.fetchone() is not None


def get_row_count(conn: sqlite3.Connection, table_name: str) -> int:
    """Get row count for a table."""
    if not table_exists(conn, table_name):
        return 0
    cursor = conn.execute(f"SELECT COUNT(*) FROM {table_name}")
    return cursor.fetchone()[0]


def copy_table_data(
    old_conn: sqlite3.Connection,
    new_db: DatabaseContext,
    table_name: str
) -> int:
    """
    Copy data from old table to new table.

    Handles column mismatches by only copying columns that exist in both tables.
    Returns the number of rows copied.
    """
    if not table_exists(old_conn, table_name):
        logger.warning(f"  Table {table_name} does not exist in source database")
        return 0

    # Get columns from both tables
    old_columns = set(get_table_columns(old_conn, table_name))

    with new_db.connect() as new_conn:
        if not table_exists(new_conn, table_name):
            logger.warning(f"  Table {table_name} does not exist in target database")
            return 0
        new_columns = set(get_table_columns(new_conn, table_name))

    # Find common columns
    common_columns = old_columns & new_columns
    if not common_columns:
        logger.warning(f"  No common columns between source and target for {table_name}")
        return 0

    columns_str = ', '.join(sorted(common_columns))
    placeholders = ', '.join(['?' for _ in common_columns])

    # Read from old
    cursor = old_conn.execute(f"SELECT {columns_str} FROM {table_name}")
    rows = cursor.fetchall()

    if not rows:
        logger.info(f"  Table {table_name}: 0 rows (empty)")
        return 0

    # Write to new
    with new_db.transaction() as new_conn:
        new_conn.executemany(
            f"INSERT OR REPLACE INTO {table_name} ({columns_str}) VALUES ({placeholders})",
            rows
        )

    return len(rows)


def verify_migration(
    old_conn: sqlite3.Connection,
    new_db: DatabaseContext,
    tables: list
) -> bool:
    """
    Verify that migration was successful by comparing row counts.
    Returns True if all counts match.
    """
    all_match = True

    logger.info("\nVerification:")
    logger.info("-" * 50)

    for table in tables:
        old_count = get_row_count(old_conn, table)

        with new_db.connect() as new_conn:
            new_count = get_row_count(new_conn, table)

        status = "OK" if old_count == new_count else "MISMATCH"
        if old_count != new_count:
            all_match = False

        logger.info(f"  {table}: {old_count} -> {new_count} ({status})")

    return all_match


def migrate_database(source_path: str, target_path: str, include_optional: bool = False):
    """
    Migrate data from source database to target with new schema.

    Args:
        source_path: Path to the existing database
        target_path: Path for the new database
        include_optional: Whether to also migrate optional tables
    """
    source_path = Path(source_path)
    target_path = Path(target_path)

    if not source_path.exists():
        logger.error(f"Source database not found: {source_path}")
        sys.exit(1)

    if target_path.exists():
        logger.error(f"Target database already exists: {target_path}")
        logger.error("Remove it first or choose a different target path")
        sys.exit(1)

    logger.info(f"Migrating from: {source_path}")
    logger.info(f"Migrating to:   {target_path}")
    logger.info("")

    # Step 1: Create new database with fresh schema
    logger.info("Step 1: Creating new database with fresh schema...")
    new_db = DatabaseContext(str(target_path))
    new_db.initialize_schema()
    logger.info(f"  Schema version: {SCHEMA_VERSION}")

    # Step 2: Connect to old database
    logger.info("\nStep 2: Connecting to source database...")
    old_conn = sqlite3.connect(str(source_path))
    old_conn.row_factory = sqlite3.Row

    # Step 3: Copy preserved tables
    logger.info("\nStep 3: Copying preserved tables (IMPORTANT DATA)...")
    copied_counts = {}
    for table in PRESERVE_TABLES:
        count = copy_table_data(old_conn, new_db, table)
        copied_counts[table] = count
        logger.info(f"  {table}: {count} rows")

    # Step 4: Optionally copy optional tables
    if include_optional:
        logger.info("\nStep 4: Copying optional tables...")
        for table in OPTIONAL_TABLES:
            count = copy_table_data(old_conn, new_db, table)
            copied_counts[table] = count
            logger.info(f"  {table}: {count} rows")
    else:
        logger.info("\nStep 4: Skipping optional tables (use --include-optional to copy)")

    # Step 5: Verify migration
    tables_to_verify = PRESERVE_TABLES + (OPTIONAL_TABLES if include_optional else [])
    all_match = verify_migration(old_conn, new_db, tables_to_verify)

    # Cleanup
    old_conn.close()

    # Summary
    logger.info("\n" + "=" * 50)
    if all_match:
        logger.info("Migration completed successfully!")
        logger.info(f"\nNew database created at: {target_path}")
        logger.info("\nNext steps:")
        logger.info(f"  1. Verify the new database manually")
        logger.info(f"  2. Backup the original: cp {source_path} {source_path}.backup.{datetime.now().strftime('%Y%m%d')}")
        logger.info(f"  3. Swap the databases: mv {source_path} {source_path}.old && mv {target_path} {source_path}")
    else:
        logger.warning("Migration completed with WARNINGS - some row counts don't match")
        logger.warning("Please investigate before using the new database")

    return all_match


def main():
    parser = argparse.ArgumentParser(
        description="Migrate poker database to new repository schema",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "--source",
        required=True,
        help="Path to source database"
    )
    parser.add_argument(
        "--target",
        required=True,
        help="Path for new target database"
    )
    parser.add_argument(
        "--include-optional",
        action="store_true",
        help="Also migrate optional tables (tournament_results, etc.)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without actually doing it"
    )

    args = parser.parse_args()

    if args.dry_run:
        logger.info("DRY RUN - No changes will be made\n")

        source_path = Path(args.source)
        if not source_path.exists():
            logger.error(f"Source database not found: {source_path}")
            sys.exit(1)

        old_conn = sqlite3.connect(str(source_path))

        logger.info("Tables to preserve (IMPORTANT DATA):")
        for table in PRESERVE_TABLES:
            count = get_row_count(old_conn, table)
            logger.info(f"  {table}: {count} rows")

        if args.include_optional:
            logger.info("\nOptional tables:")
            for table in OPTIONAL_TABLES:
                count = get_row_count(old_conn, table)
                logger.info(f"  {table}: {count} rows")

        old_conn.close()
        logger.info("\nRun without --dry-run to perform migration")
    else:
        success = migrate_database(args.source, args.target, args.include_optional)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
