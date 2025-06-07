#!/usr/bin/env python3
"""Generate and populate database with personalities for all celebrities."""

import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv(override=True)

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from poker.persistence import GamePersistence
from poker.personality_generator import PersonalityGenerator
from poker.utils import ALL_CELEBRITIES_LIST

def populate_all_personalities():
    """Generate personalities for all celebrities and save to database."""
    
    # Initialize persistence
    if os.path.exists('/app/data'):
        db_path = '/app/data/poker_games.db'
    else:
        db_path = os.path.join(project_root, 'poker_games.db')
    
    persistence = GamePersistence(db_path)
    
    # Initialize personality generator
    generator = PersonalityGenerator()
    
    print(f"Generating personalities for {len(ALL_CELEBRITIES_LIST)} celebrities...")
    print("=" * 60)
    
    success_count = 0
    existing_count = 0
    failed_count = 0
    
    for name in ALL_CELEBRITIES_LIST:
        try:
            # Check if already exists in database
            existing = persistence.load_personality(name)
            if existing:
                existing_count += 1
                print(f"⏭️  {name} - Already exists in database")
                continue
            
            # Generate new personality
            print(f"🤖 Generating {name}...", end='', flush=True)
            personality = generator.get_personality(name, force_generate=True)
            
            # Save to database
            persistence.save_personality(name, personality, source='ai_generated')
            success_count += 1
            print(f" ✅ Success!")
            
        except Exception as e:
            failed_count += 1
            print(f" ❌ Failed: {e}")
    
    print("\n" + "=" * 60)
    print(f"Generation Summary:")
    print(f"  ✅ Successfully generated: {success_count}")
    print(f"  ⏭️  Already existed: {existing_count}")
    print(f"  ❌ Failed: {failed_count}")
    print(f"  📊 Total: {len(ALL_CELEBRITIES_LIST)}")
    
    # List all personalities in DB
    db_personalities = persistence.list_personalities(limit=200)
    print(f"\nTotal personalities in database: {len(db_personalities)}")
    
    # Also update personalities.json with all personalities
    print("\nUpdating personalities.json with all personalities...")
    personalities_file = project_root / 'poker' / 'personalities.json'
    
    # Create a new dict with all personalities
    all_personalities = {}
    for p in db_personalities:
        config = p['config'] if isinstance(p['config'], dict) else json.loads(p['config'])
        all_personalities[p['name']] = config
    
    # Save to personalities.json
    with open(personalities_file, 'w') as f:
        json.dump({"personalities": all_personalities}, f, indent=2)
    
    print(f"✅ Updated personalities.json with {len(all_personalities)} personalities")

if __name__ == "__main__":
    if not os.getenv('OPENAI_API_KEY'):
        print("❌ Error: OPENAI_API_KEY not found in environment variables!")
        print("Please ensure your .env file contains: OPENAI_API_KEY=your_key_here")
        sys.exit(1)
    
    populate_all_personalities()