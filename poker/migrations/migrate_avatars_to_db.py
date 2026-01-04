#!/usr/bin/env python3
"""
Migration script to import personalities and avatar images into the database.

This script:
1. Seeds personalities from poker/personalities.json into the database
2. Imports existing avatar images from generated_images/grid/icons/ into the database

Usage:
    python -m poker.migrations.migrate_avatars_to_db [options]

Options:
    --seed-only          Only seed personalities, skip avatar import
    --avatars-only       Only import avatars, skip personality seeding
    --verify             Verify migration without making changes
    --db-path PATH       Path to database (default: poker_games.db)
    --images-dir PATH    Path to images directory (default: generated_images/grid/icons)
    --overwrite          Overwrite existing personalities in database
"""

import argparse
import os
import sys
from pathlib import Path

# Add project root to path (poker/migrations -> poker -> project_root)
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from poker.persistence import GamePersistence


def get_default_db_path() -> str:
    """Get the default database path."""
    if Path('/app/data').exists():
        return '/app/data/poker_games.db'
    return str(project_root / 'poker_games.db')


def get_default_images_dir() -> Path:
    """Get the default images directory."""
    return project_root / 'generated_images' / 'grid' / 'icons'


def get_personalities_json_path() -> Path:
    """Get the path to personalities.json."""
    return project_root / 'poker' / 'personalities.json'


def seed_personalities(persistence: GamePersistence, overwrite: bool = False) -> dict:
    """Seed database with personalities from JSON file.

    Args:
        persistence: GamePersistence instance
        overwrite: Whether to overwrite existing personalities

    Returns:
        Dict with statistics
    """
    json_path = get_personalities_json_path()
    print(f"\nSeeding personalities from: {json_path}")

    result = persistence.seed_personalities_from_json(str(json_path), overwrite=overwrite)

    print(f"  Added: {result.get('added', 0)}")
    print(f"  Updated: {result.get('updated', 0)}")
    print(f"  Skipped: {result.get('skipped', 0)}")

    if result.get('error'):
        print(f"  Error: {result['error']}")

    return result


def import_avatars(persistence: GamePersistence, images_dir: Path) -> dict:
    """Import avatar images from filesystem to database.

    Args:
        persistence: GamePersistence instance
        images_dir: Path to icons directory

    Returns:
        Dict with statistics
    """
    print(f"\nImporting avatar images from: {images_dir}")

    if not images_dir.exists():
        print(f"  Error: Directory does not exist: {images_dir}")
        return {'error': 'Directory not found', 'imported': 0, 'skipped': 0, 'failed': 0}

    stats = {'imported': 0, 'skipped': 0, 'failed': 0, 'total_bytes': 0}

    # Get all PNG files
    png_files = list(images_dir.glob('*.png'))
    print(f"  Found {len(png_files)} PNG files")

    for png_file in png_files:
        try:
            # Parse personality name and emotion from filename
            # Format: personality_name_emotion.png
            stem = png_file.stem
            parts = stem.rsplit('_', 1)

            if len(parts) != 2:
                print(f"  Warning: Skipping {png_file.name} (cannot parse name/emotion)")
                stats['skipped'] += 1
                continue

            personality_slug, emotion = parts

            # Convert slug back to proper name
            # e.g., "bob_ross" -> "Bob Ross"
            personality_name = ' '.join(word.title() for word in personality_slug.split('_'))

            # Check if already exists in database
            if persistence.has_avatar_image(personality_name, emotion):
                stats['skipped'] += 1
                continue

            # Read image bytes
            with open(png_file, 'rb') as f:
                image_data = f.read()

            # Save to database
            persistence.save_avatar_image(
                personality_name=personality_name,
                emotion=emotion,
                image_data=image_data,
                width=256,
                height=256
            )

            stats['imported'] += 1
            stats['total_bytes'] += len(image_data)

        except Exception as e:
            print(f"  Error importing {png_file.name}: {e}")
            stats['failed'] += 1

    print(f"  Imported: {stats['imported']}")
    print(f"  Skipped (already exist): {stats['skipped']}")
    print(f"  Failed: {stats['failed']}")
    print(f"  Total size: {stats['total_bytes'] / 1024 / 1024:.2f} MB")

    return stats


def verify_migration(persistence: GamePersistence, images_dir: Path) -> None:
    """Verify the migration by comparing filesystem and database counts.

    Args:
        persistence: GamePersistence instance
        images_dir: Path to icons directory
    """
    print("\n=== Migration Verification ===\n")

    # Count filesystem images
    if images_dir.exists():
        fs_count = len(list(images_dir.glob('*.png')))
    else:
        fs_count = 0
    print(f"Filesystem images: {fs_count}")

    # Get database stats
    db_stats = persistence.get_avatar_stats()
    print(f"Database images: {db_stats['total_images']}")
    print(f"Database size: {db_stats['total_size_mb']} MB")
    print(f"Personalities with avatars: {db_stats['personality_count']}")
    print(f"Complete personalities (6 emotions): {db_stats['complete_personality_count']}")

    # List personalities in database
    db_personalities = persistence.list_personalities(limit=200)
    print(f"\nPersonalities in database: {len(db_personalities)}")

    # Compare
    if db_stats['total_images'] >= fs_count:
        print("\n✓ All filesystem images are in the database")
    else:
        print(f"\n⚠ Some images may be missing from database ({fs_count - db_stats['total_images']})")


def main():
    parser = argparse.ArgumentParser(
        description='Migrate personalities and avatar images to SQLite database',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('--seed-only', action='store_true',
                       help='Only seed personalities, skip avatar import')
    parser.add_argument('--avatars-only', action='store_true',
                       help='Only import avatars, skip personality seeding')
    parser.add_argument('--verify', action='store_true',
                       help='Verify migration without making changes')
    parser.add_argument('--db-path', type=str, default=None,
                       help='Path to database file')
    parser.add_argument('--images-dir', type=str, default=None,
                       help='Path to images directory')
    parser.add_argument('--overwrite', action='store_true',
                       help='Overwrite existing personalities in database')

    args = parser.parse_args()

    # Set up paths
    db_path = args.db_path or get_default_db_path()
    images_dir = Path(args.images_dir) if args.images_dir else get_default_images_dir()

    print("=== Avatar and Personality Migration ===")
    print(f"Database: {db_path}")
    print(f"Images directory: {images_dir}")

    # Initialize persistence (this runs schema migrations)
    persistence = GamePersistence(db_path)

    if args.verify:
        verify_migration(persistence, images_dir)
        return

    # Run migrations
    if not args.avatars_only:
        seed_personalities(persistence, overwrite=args.overwrite)

    if not args.seed_only:
        import_avatars(persistence, images_dir)

    # Show final stats
    verify_migration(persistence, images_dir)

    print("\n=== Migration Complete ===")


if __name__ == '__main__':
    main()
