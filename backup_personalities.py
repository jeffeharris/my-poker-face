#!/usr/bin/env python3
"""Backup all personalities from database to a JSON file."""

import json
import os
import sys
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from poker.persistence import GamePersistence

def backup_personalities():
    """Backup all personalities from database to JSON file."""
    
    # Initialize persistence
    if os.path.exists('/app/data'):
        db_path = '/app/data/poker_games.db'
    else:
        db_path = os.path.join(project_root, 'poker_games.db')
    
    persistence = GamePersistence(db_path)
    
    # Get all personalities from database
    print("Loading personalities from database...")
    db_personalities = persistence.list_personalities(limit=500)
    print(f"Found {len(db_personalities)} personalities in database")
    
    # Create backup data structure
    backup_data = {
        "metadata": {
            "backup_date": datetime.now().isoformat(),
            "total_personalities": len(db_personalities),
            "database_path": db_path
        },
        "personalities": {}
    }
    
    # Process each personality
    for p in db_personalities:
        name = p['name']
        
        # Load the full config from database
        config = persistence.load_personality(name)
        if not config:
            print(f"⚠️  Warning: Could not load config for {name}")
            continue
        
        # Add metadata
        backup_data["personalities"][name] = {
            "config": config,
            "source": p.get('source', 'unknown'),
            "created_at": p.get('created_at', ''),
            "updated_at": p.get('updated_at', ''),
            "usage_count": p.get('times_used', 0),
            "is_generated": p.get('is_generated', False)
        }
    
    # Create backups directory if it doesn't exist
    backup_dir = project_root / 'poker' / 'personality_backups'
    backup_dir.mkdir(exist_ok=True)
    
    # Generate backup filename with timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_file = backup_dir / f'personalities_backup_{timestamp}.json'
    
    # Save backup
    with open(backup_file, 'w') as f:
        json.dump(backup_data, f, indent=2)
    
    print(f"\n✅ Backup complete!")
    print(f"📁 Saved to: {backup_file}")
    print(f"📊 Total personalities backed up: {len(db_personalities)}")
    
    # Also create a "latest" symlink for easy access
    latest_link = backup_dir / 'personalities_backup_latest.json'
    if latest_link.exists():
        latest_link.unlink()
    
    # Copy instead of symlink for better compatibility
    with open(backup_file, 'r') as src:
        with open(latest_link, 'w') as dst:
            dst.write(src.read())
    
    print(f"📎 Also saved as: {latest_link}")
    
    return backup_file

if __name__ == "__main__":
    backup_personalities()