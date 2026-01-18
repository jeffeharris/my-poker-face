#!/usr/bin/env python3
"""
Verify that a database migration was successful by comparing row counts
and spot-checking recent records.

Usage:
    python scripts/verify_migration.py --old poker_games_backup.db --new poker_games.db
"""

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Tables to verify
CRITICAL_TABLES = [
    'api_usage',
    'prompt_captures',
    'model_pricing',
    'enabled_models',
    'personalities',
    'avatar_images',
    'player_career_stats',
    'player_decision_analysis',
]


def get_row_count(conn: sqlite3.Connection, table_name: str) -> int:
    """Get row count for a table."""
    try:
        cursor = conn.execute(f"SELECT COUNT(*) FROM {table_name}")
        return cursor.fetchone()[0]
    except sqlite3.OperationalError:
        return -1  # Table doesn't exist


def get_recent_records(conn: sqlite3.Connection, table_name: str, limit: int = 3) -> list:
    """Get recent records from a table for spot-checking."""
    try:
        # Try common timestamp columns
        for ts_col in ['timestamp', 'created_at', 'last_updated', 'ended_at']:
            try:
                cursor = conn.execute(
                    f"SELECT * FROM {table_name} ORDER BY {ts_col} DESC LIMIT ?",
                    (limit,)
                )
                return [dict(row) for row in cursor.fetchall()]
            except sqlite3.OperationalError:
                continue

        # Fall back to id column
        try:
            cursor = conn.execute(
                f"SELECT * FROM {table_name} ORDER BY id DESC LIMIT ?",
                (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.OperationalError:
            # Just get any records
            cursor = conn.execute(f"SELECT * FROM {table_name} LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]

    except sqlite3.OperationalError:
        return []


def verify_databases(old_path: str, new_path: str) -> bool:
    """
    Verify that the new database has the expected data from the old one.
    Returns True if verification passes.
    """
    old_path = Path(old_path)
    new_path = Path(new_path)

    if not old_path.exists():
        logger.error(f"Old database not found: {old_path}")
        return False

    if not new_path.exists():
        logger.error(f"New database not found: {new_path}")
        return False

    old_conn = sqlite3.connect(str(old_path))
    old_conn.row_factory = sqlite3.Row
    new_conn = sqlite3.connect(str(new_path))
    new_conn.row_factory = sqlite3.Row

    all_passed = True

    logger.info("=" * 60)
    logger.info("Database Migration Verification Report")
    logger.info("=" * 60)
    logger.info(f"Old database: {old_path}")
    logger.info(f"New database: {new_path}")
    logger.info("")

    # Row count comparison
    logger.info("Row Count Comparison:")
    logger.info("-" * 60)
    logger.info(f"{'Table':<30} {'Old':>10} {'New':>10} {'Status':>10}")
    logger.info("-" * 60)

    for table in CRITICAL_TABLES:
        old_count = get_row_count(old_conn, table)
        new_count = get_row_count(new_conn, table)

        if old_count == -1:
            status = "N/A (old)"
        elif new_count == -1:
            status = "MISSING"
            all_passed = False
        elif old_count == new_count:
            status = "OK"
        else:
            status = "MISMATCH"
            all_passed = False

        logger.info(f"{table:<30} {old_count:>10} {new_count:>10} {status:>10}")

    logger.info("")

    # Spot check recent records
    logger.info("Spot Check (most recent records):")
    logger.info("-" * 60)

    for table in ['api_usage', 'prompt_captures', 'personalities']:
        old_count = get_row_count(old_conn, table)
        if old_count <= 0:
            continue

        logger.info(f"\n{table}:")
        old_recent = get_recent_records(old_conn, table, 2)
        new_recent = get_recent_records(new_conn, table, 2)

        if not old_recent:
            logger.info("  No records in old database")
            continue

        if not new_recent:
            logger.warning("  WARNING: No records in new database!")
            all_passed = False
            continue

        # Compare first record
        old_rec = old_recent[0]
        new_rec = new_recent[0]

        # Check if they match (by key fields)
        key_fields = ['id', 'game_id', 'name', 'player_name']
        matched = False
        for key in key_fields:
            if key in old_rec and key in new_rec:
                if old_rec[key] == new_rec[key]:
                    matched = True
                    logger.info(f"  Latest record matches by {key}: {old_rec[key]}")
                    break

        if not matched and old_recent:
            logger.warning(f"  WARNING: Could not verify latest record match")
            # Still show what we have
            sample_key = list(old_rec.keys())[0] if old_rec else None
            if sample_key:
                logger.info(f"    Old: {sample_key}={old_rec.get(sample_key)}")
                logger.info(f"    New: {sample_key}={new_rec.get(sample_key) if new_rec else 'N/A'}")

    # Cleanup
    old_conn.close()
    new_conn.close()

    # Summary
    logger.info("")
    logger.info("=" * 60)
    if all_passed:
        logger.info("VERIFICATION PASSED - All checks OK")
    else:
        logger.warning("VERIFICATION FAILED - Some checks did not pass")
        logger.warning("Please investigate before using the new database")
    logger.info("=" * 60)

    return all_passed


def main():
    parser = argparse.ArgumentParser(
        description="Verify database migration by comparing old and new databases"
    )
    parser.add_argument(
        "--old",
        required=True,
        help="Path to old/backup database"
    )
    parser.add_argument(
        "--new",
        required=True,
        help="Path to new database"
    )

    args = parser.parse_args()
    success = verify_databases(args.old, args.new)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
