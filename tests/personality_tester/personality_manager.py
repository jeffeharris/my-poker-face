#!/usr/bin/env python3
"""
Web interface for managing AI poker personalities.
Allows editing personality traits and saving back to personalities.json
"""

from flask import Flask, render_template, request, jsonify
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import shutil
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Load environment variables
load_dotenv(override=True)

from poker.personality_generator import PersonalityGenerator

app = Flask(__name__, template_folder='templates', static_folder='static')

# Initialize personality generator
personality_generator = PersonalityGenerator()

# Path to personalities file
PERSONALITIES_FILE = project_root / 'poker' / 'personalities.json'
BACKUP_DIR = project_root / 'poker' / 'personality_backups'

def load_personalities():
    """Load personalities from JSON file"""
    with open(PERSONALITIES_FILE, 'r') as f:
        return json.load(f)

def save_personalities(data):
    """Save personalities to JSON file with backup"""
    # Create backup directory if it doesn't exist
    BACKUP_DIR.mkdir(exist_ok=True)
    
    # Create backup with timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_file = BACKUP_DIR / f'personalities_backup_{timestamp}.json'
    shutil.copy2(PERSONALITIES_FILE, backup_file)
    
    # Save new data
    with open(PERSONALITIES_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    
    return backup_file

@app.route('/')
def index():
    """Main personality manager page"""
    return render_template('personality_manager.html')

@app.route('/api/personalities', methods=['GET'])
def get_personalities():
    """Get all personalities"""
    try:
        data = load_personalities()
        return jsonify({
            'success': True,
            'personalities': data['personalities']
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/personality/<name>', methods=['GET'])
def get_personality(name):
    """Get a specific personality"""
    try:
        data = load_personalities()
        if name in data['personalities']:
            return jsonify({
                'success': True,
                'personality': data['personalities'][name],
                'name': name
            })
        else:
            return jsonify({'success': False, 'error': 'Personality not found'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/personality/<name>', methods=['PUT'])
def update_personality(name):
    """Update a personality"""
    try:
        update_data = request.json
        data = load_personalities()
        
        if name not in data['personalities']:
            return jsonify({'success': False, 'error': 'Personality not found'})
        
        # Update the personality
        data['personalities'][name] = update_data
        
        # Save with backup
        backup_file = save_personalities(data)
        
        return jsonify({
            'success': True,
            'message': f'Personality {name} updated successfully',
            'backup': str(backup_file)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/personality', methods=['POST'])
def create_personality():
    """Create a new personality"""
    try:
        new_data = request.json
        name = new_data.get('name')
        
        if not name:
            return jsonify({'success': False, 'error': 'Name is required'})
        
        data = load_personalities()
        
        if name in data['personalities']:
            return jsonify({'success': False, 'error': 'Personality already exists'})
        
        # Remove name from the data (it's the key, not part of the value)
        personality_data = {k: v for k, v in new_data.items() if k != 'name'}
        
        # Add default traits if missing
        if 'personality_traits' not in personality_data:
            personality_data['personality_traits'] = {
                "bluff_tendency": 0.5,
                "aggression": 0.5,
                "chattiness": 0.5,
                "emoji_usage": 0.3
            }
        
        data['personalities'][name] = personality_data
        
        # Save with backup
        backup_file = save_personalities(data)
        
        return jsonify({
            'success': True,
            'message': f'Personality {name} created successfully',
            'backup': str(backup_file)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/personality/<name>', methods=['DELETE'])
def delete_personality(name):
    """Delete a personality"""
    try:
        data = load_personalities()
        
        if name not in data['personalities']:
            return jsonify({'success': False, 'error': 'Personality not found'})
        
        # Create backup before deleting
        backup_file = save_personalities(data)
        
        # Delete the personality
        del data['personalities'][name]
        
        # Save again
        save_personalities(data)
        
        return jsonify({
            'success': True,
            'message': f'Personality {name} deleted successfully',
            'backup': str(backup_file)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/generate_personality', methods=['POST'])
def generate_personality():
    """Generate a new personality using AI."""
    try:
        data = request.json
        name = data.get('name', '').strip()
        
        if not name:
            return jsonify({'success': False, 'error': 'Name is required'})
        
        # Force generation even if exists
        force_generate = data.get('force', False)
        
        # Generate the personality
        personality_config = personality_generator.get_personality(
            name=name, 
            force_generate=force_generate
        )
        
        # Also save to personalities.json for consistency
        personalities_data = load_personalities()
        personalities_data['personalities'][name] = personality_config
        save_personalities(personalities_data)
        
        return jsonify({
            'success': True,
            'personality': personality_config,
            'name': name,
            'message': f'Successfully generated personality for {name}'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'message': 'Failed to generate personality. Please check your OpenAI API key.'
        })

if __name__ == '__main__':
    print("Starting Personality Manager on http://localhost:5002")
    print(f"Managing personalities at: {PERSONALITIES_FILE}")
    app.run(debug=True, port=5002)