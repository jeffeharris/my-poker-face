"""Configuration for the Flask application."""

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv(override=True)

# Environment detection
flask_env = os.environ.get('FLASK_ENV', 'production')
flask_debug = os.environ.get('FLASK_DEBUG', '0')
is_development = (flask_env == 'development' or flask_debug == '1')

# Secret key
SECRET_KEY = os.environ.get('SECRET_KEY', os.urandom(32).hex())

# CORS configuration
CORS_ORIGINS_ENV = os.environ.get('CORS_ORIGINS', '*')

# Rate limiting configuration
RATE_LIMIT_DEFAULT = ['10000 per day', '1000 per hour', '100 per minute']
RATE_LIMIT_NEW_GAME = os.environ.get('RATE_LIMIT_NEW_GAME', '10 per hour')
RATE_LIMIT_GAME_ACTION = os.environ.get('RATE_LIMIT_GAME_ACTION', '60 per minute')
RATE_LIMIT_CHAT_SUGGESTIONS = os.environ.get('RATE_LIMIT_CHAT_SUGGESTIONS', '100 per hour')
RATE_LIMIT_GENERATE_PERSONALITY = os.environ.get('RATE_LIMIT_GENERATE_PERSONALITY', '15 per hour')

# Redis configuration
REDIS_URL = os.environ.get('REDIS_URL')

# AI model configuration - import from centralized config
from core.llm import FAST_MODEL as FAST_AI_MODEL

# Database path
def get_db_path():
    """Get the database path based on environment."""
    if os.path.exists('/app/data'):
        return '/app/data/poker_games.db'
    else:
        return os.path.join(os.path.dirname(__file__), '..', 'poker_games.db')

DB_PATH = get_db_path()
