#!/usr/bin/env python3
"""Restore personalities from a backup JSON file to the database."""

import json
import os
import sys
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from poker.persistence import GamePersistence

def restore_personalities(backup_file=None):
    """Restore personalities from backup JSON file to database."""
    
    # Initialize persistence
    if os.path.exists('/app/data'):
        db_path = '/app/data/poker_games.db'
    else:
        db_path = os.path.join(project_root, 'poker_games.db')
    
    persistence = GamePersistence(db_path)
    
    # Determine backup file to use
    backup_dir = project_root / 'poker' / 'personality_backups'
    
    if backup_file:
        backup_path = Path(backup_file)
        if not backup_path.exists():
            # Try in backup directory
            backup_path = backup_dir / backup_file
    else:
        # Use latest backup
        backup_path = backup_dir / 'personalities_backup_latest.json'
    
    if not backup_path.exists():
        print(f"❌ Error: Backup file not found: {backup_path}")
        print("\nAvailable backups:")
        if backup_dir.exists():
            for f in sorted(backup_dir.glob('personalities_backup_*.json')):
                print(f"  - {f.name}")
        return
    
    # Load backup data
    print(f"Loading backup from: {backup_path}")
    with open(backup_path, 'r') as f:
        backup_data = json.load(f)
    
    metadata = backup_data.get('metadata', {})
    personalities = backup_data.get('personalities', {})
    
    print(f"\nBackup Information:")
    print(f"  📅 Backup date: {metadata.get('backup_date', 'Unknown')}")
    print(f"  📊 Total personalities: {metadata.get('total_personalities', len(personalities))}")
    print(f"  💾 Original database: {metadata.get('database_path', 'Unknown')}")
    
    # Confirm restoration
    response = input(f"\nRestore {len(personalities)} personalities to database? (yes/no): ")
    if response.lower() != 'yes':
        print("Restoration cancelled.")
        return
    
    # Restore personalities
    print(f"\nRestoring {len(personalities)} personalities...")
    print("=" * 60)
    
    success_count = 0
    skip_count = 0
    failed_count = 0
    
    for name, data in personalities.items():
        try:
            # Check if already exists
            existing = persistence.load_personality(name)
            if existing:
                skip_count += 1
                print(f"⏭️  {name} - Already exists, skipping")
                continue
            
            # Extract config and metadata
            config = data.get('config', data)  # Handle both old and new format
            source = data.get('source', 'restored')
            
            # Save to database
            persistence.save_personality(name, config, source=f"{source}_restored")
            success_count += 1
            print(f"✅ {name} - Restored successfully")
            
        except Exception as e:
            failed_count += 1
            print(f"❌ {name} - Failed: {e}")
    
    print("\n" + "=" * 60)
    print(f"Restoration Summary:")
    print(f"  ✅ Successfully restored: {success_count}")
    print(f"  ⏭️  Skipped (already exist): {skip_count}")
    print(f"  ❌ Failed: {failed_count}")
    print(f"  📊 Total processed: {len(personalities)}")
    
    # List all personalities in DB
    db_personalities = persistence.list_personalities(limit=500)
    print(f"\nTotal personalities now in database: {len(db_personalities)}")

if __name__ == "__main__":
    # Check if a specific backup file was provided
    if len(sys.argv) > 1:
        restore_personalities(sys.argv[1])
    else:
        restore_personalities()