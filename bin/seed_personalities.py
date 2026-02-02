#!/usr/bin/env python3
"""Seed personalities from JSON file if database is empty.

This script should be run on first deployment to populate the personalities table.
It will only add personalities that don't already exist in the database.

Usage:
    python scripts/seed_personalities.py

In Docker:
    docker compose exec backend python scripts/seed_personalities.py
"""

import json
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from poker.repositories import create_repos


def get_db_path() -> str:
    """Get the appropriate database path based on environment.

    Note: This duplicates flask_app.config.get_db_path() to allow standalone execution.
    The canonical version is in flask_app/config.py.
    """
    if os.path.exists('/app/data'):
        return '/app/data/poker_games.db'
    return str(project_root / 'data' / 'poker_games.db')


def get_json_path() -> str:
    """Get the path to personalities.json."""
    if os.path.exists('/app/poker/personalities.json'):
        return '/app/poker/personalities.json'
    return str(project_root / 'poker' / 'personalities.json')


def seed_personalities(force: bool = False) -> dict:
    """
    Seed personalities from JSON file into the database.

    Args:
        force: If True, update existing personalities to match JSON.
               If False, only add missing personalities.

    Returns:
        dict with counts: added, skipped, updated, total
    """
    db_path = get_db_path()
    json_path = get_json_path()

    print(f"Database: {db_path}")
    print(f"JSON source: {json_path}")
    print()

    # Load personalities from JSON
    with open(json_path) as f:
        json_data = json.load(f)

    personalities = json_data.get('personalities', {})
    print(f"Found {len(personalities)} personalities in JSON file")

    # Initialize repositories
    repos = create_repos(db_path)
    personality_repo = repos['personality_repo']

    # Check current count
    existing = personality_repo.list_personalities(limit=200)
    existing_names = {p['name'] for p in existing}
    print(f"Found {len(existing_names)} personalities in database")
    print()

    added = 0
    skipped = 0
    updated = 0

    for name, config in personalities.items():
        if name in existing_names:
            if force:
                # Update existing personality
                personality_repo.save_personality(name, config, source='json_import')
                updated += 1
                print(f"  Updated: {name}")
            else:
                skipped += 1
        else:
            # Add new personality
            personality_repo.save_personality(name, config, source='json_import')
            added += 1
            print(f"  Added: {name}")

    # Final count
    final = personality_repo.list_personalities(limit=200)

    print()
    print("=" * 50)
    print(f"Summary:")
    print(f"  Added:   {added}")
    print(f"  Updated: {updated}")
    print(f"  Skipped: {skipped} (already existed)")
    print(f"  Total in database: {len(final)}")
    print("=" * 50)

    return {
        'added': added,
        'updated': updated,
        'skipped': skipped,
        'total': len(final)
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='Seed personalities from JSON file into database'
    )
    parser.add_argument(
        '--force', '-f',
        action='store_true',
        help='Update existing personalities to match JSON file'
    )
    parser.add_argument(
        '--check', '-c',
        action='store_true',
        help='Only check counts, do not modify database'
    )

    args = parser.parse_args()

    if args.check:
        db_path = get_db_path()
        json_path = get_json_path()

        with open(json_path) as f:
            json_data = json.load(f)
        json_count = len(json_data.get('personalities', {}))

        repos = create_repos(db_path)
        personality_repo = repos['personality_repo']
        db_count = len(personality_repo.list_personalities(limit=200))

        print(f"JSON file: {json_count} personalities")
        print(f"Database:  {db_count} personalities")

        if db_count == 0:
            print("\nDatabase is empty - run without --check to seed")
            sys.exit(1)
        elif db_count < json_count:
            print(f"\nDatabase is missing {json_count - db_count} personalities")
            sys.exit(1)
        else:
            print("\nDatabase is fully seeded")
            sys.exit(0)
    else:
        result = seed_personalities(force=args.force)

        if result['added'] == 0 and result['updated'] == 0:
            print("\nNo changes made - database already seeded")
        else:
            print("\nSeeding complete!")


if __name__ == '__main__':
    main()
