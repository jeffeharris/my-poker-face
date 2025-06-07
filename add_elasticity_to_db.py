#!/usr/bin/env python3
"""Add elasticity configuration to personality database schema."""

import sqlite3
import json
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

def add_elasticity_column():
    """Add elasticity_config column to personalities table."""
    
    # Determine database path
    if os.path.exists('/app/data'):
        db_path = '/app/data/poker_games.db'
    else:
        db_path = os.path.join(project_root, 'poker_games.db')
    
    print(f"Updating database at: {db_path}")
    
    with sqlite3.connect(db_path) as conn:
        # Check if column already exists
        cursor = conn.execute("PRAGMA table_info(personalities)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'elasticity_config' not in columns:
            print("Adding elasticity_config column...")
            conn.execute("""
                ALTER TABLE personalities 
                ADD COLUMN elasticity_config TEXT DEFAULT '{}'
            """)
            print("✅ Column added successfully")
        else:
            print("ℹ️  elasticity_config column already exists")
        
        # Generate default elasticity for existing personalities
        print("\nGenerating elasticity values for existing personalities...")
        
        cursor = conn.execute("SELECT name, config_json FROM personalities")
        personalities = cursor.fetchall()
        
        for name, config_json in personalities:
            config = json.loads(config_json)
            traits = config.get('personality_traits', {})
            
            # Generate personality-specific elasticity based on trait values
            elasticity_config = generate_elasticity_for_personality(name, traits)
            
            # Update the database
            conn.execute("""
                UPDATE personalities 
                SET elasticity_config = ? 
                WHERE name = ?
            """, (json.dumps(elasticity_config), name))
            
            print(f"✅ Generated elasticity for {name}")
        
        conn.commit()
        print(f"\n✅ Updated elasticity for {len(personalities)} personalities")

def generate_elasticity_for_personality(name: str, traits: dict) -> dict:
    """Generate personality-specific elasticity values based on traits."""
    
    elasticity_config = {
        "trait_elasticity": {},
        "mood_elasticity": 0.4,
        "recovery_rate": 0.1
    }
    
    # Special cases for specific personalities
    special_cases = {
        "A Mime": {
            "chattiness": 0.0,  # Mimes never talk
            "emoji_usage": 0.4  # But can vary emoji usage
        },
        "Gordon Ramsay": {
            "aggression": 0.2,  # Always aggressive, little variation
            "chattiness": 0.3   # Always vocal
        },
        "Bob Ross": {
            "aggression": 0.1,  # Always peaceful
            "emoji_usage": 0.3  # Consistent positive emojis
        },
        "Eeyore": {
            "chattiness": 0.2,  # Consistently quiet
            "aggression": 0.1   # Never aggressive
        },
        "R2-D2": {
            "chattiness": 0.1,  # Limited communication
        }
    }
    
    # Start with base elasticity for each trait
    for trait_name, trait_value in traits.items():
        # Calculate elasticity based on how extreme the trait is
        # Extreme values (close to 0 or 1) get lower elasticity
        distance_from_center = abs(trait_value - 0.5)
        
        if distance_from_center > 0.4:  # Very extreme trait
            base_elasticity = 0.2
        elif distance_from_center > 0.3:  # Moderately extreme
            base_elasticity = 0.3
        elif distance_from_center > 0.2:  # Somewhat extreme
            base_elasticity = 0.4
        else:  # Balanced trait
            base_elasticity = 0.5
        
        # Adjust based on trait type
        if trait_name == 'chattiness':
            # Chattiness can vary more during game
            base_elasticity = min(0.8, base_elasticity * 1.6)
        elif trait_name == 'aggression':
            # Aggression is more stable
            base_elasticity = base_elasticity * 0.8
        elif trait_name == 'bluff_tendency':
            # Bluffing can be strategic and variable
            base_elasticity = min(0.6, base_elasticity * 1.2)
        elif trait_name == 'emoji_usage':
            # Emoji usage is fairly flexible
            base_elasticity = min(0.5, base_elasticity * 1.1)
        
        elasticity_config["trait_elasticity"][trait_name] = round(base_elasticity, 2)
    
    # Apply special cases
    if name in special_cases:
        for trait, elasticity in special_cases[name].items():
            elasticity_config["trait_elasticity"][trait] = elasticity
    
    # Adjust mood elasticity based on personality
    if any(word in name.lower() for word in ['mime', 'robot', 'r2-d2', 'c3po']):
        elasticity_config["mood_elasticity"] = 0.2  # More rigid moods
    elif any(word in name.lower() for word in ['hulk', 'gordon', 'trump']):
        elasticity_config["mood_elasticity"] = 0.6  # More volatile moods
    
    # Adjust recovery rate
    if 'Bob Ross' in name or 'Buddha' in name:
        elasticity_config["recovery_rate"] = 0.2  # Faster recovery to peaceful state
    elif 'Hulk' in name or 'Gordon Ramsay' in name:
        elasticity_config["recovery_rate"] = 0.05  # Slower recovery from agitation
    
    return elasticity_config

if __name__ == "__main__":
    add_elasticity_column()