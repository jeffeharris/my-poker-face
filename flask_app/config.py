"""Configuration for the Flask application."""

import os
from functools import lru_cache

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv(override=True)

# Environment detection
flask_env = os.environ.get('FLASK_ENV', 'production')
flask_debug = os.environ.get('FLASK_DEBUG', '0')
is_development = (flask_env == 'development' or flask_debug == '1')

# AI Debug mode - enables LLM stats on player cards
enable_ai_debug = os.environ.get('ENABLE_AI_DEBUG', 'false').lower() == 'true'

# Secret key
if is_development:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-not-for-production')
else:
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        raise RuntimeError(
            "SECRET_KEY environment variable is required in production. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )

# Google OAuth configuration
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
FRONTEND_URL = os.environ.get('FRONTEND_URL', 'http://localhost:5173')

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
from core.llm import ASSISTANT_MODEL, ASSISTANT_PROVIDER
from core.llm.config import DEFAULT_MODEL, DEFAULT_PROVIDER, FAST_MODEL, FAST_PROVIDER, IMAGE_PROVIDER, IMAGE_MODEL


@lru_cache(maxsize=1)
def _get_config_persistence():
    """Get a cached GamePersistence instance for config lookups.

    Note: This caches a single shared instance across all callers. This is safe
    because GamePersistence uses context managers for all DB operations and
    maintains no connection state between calls. Each operation opens and closes
    its own connection.

    If GamePersistence is ever modified to maintain persistent state (connection
    pools, cached transactions, etc.), this caching pattern must be revisited.
    """
    from poker.persistence import GamePersistence
    return GamePersistence(DB_PATH)


def _get_setting(key: str, default: str) -> str:
    """Get a setting value from DB, falling back to the provided default.

    Priority: 1. Database (app_settings), 2. default (from core.llm.config / env)
    """
    p = _get_config_persistence()
    db_value = p.get_setting(key, '')
    return db_value if db_value else default


def get_default_provider() -> str:
    return _get_setting('DEFAULT_PROVIDER', DEFAULT_PROVIDER)


def get_default_model() -> str:
    return _get_setting('DEFAULT_MODEL', DEFAULT_MODEL)


def get_fast_provider() -> str:
    return _get_setting('FAST_PROVIDER', FAST_PROVIDER)


def get_fast_model() -> str:
    return _get_setting('FAST_MODEL', FAST_MODEL)


def get_assistant_provider() -> str:
    return _get_setting('ASSISTANT_PROVIDER', ASSISTANT_PROVIDER)


def get_assistant_model() -> str:
    return _get_setting('ASSISTANT_MODEL', ASSISTANT_MODEL)


def get_image_provider() -> str:
    return _get_setting('IMAGE_PROVIDER', IMAGE_PROVIDER)


def get_image_model() -> str:
    return _get_setting('IMAGE_MODEL', IMAGE_MODEL)


# Database path
def get_db_path():
    """Get the database path based on environment."""
    if os.path.exists('/app/data'):
        return '/app/data/poker_games.db'
    else:
        return os.path.join(os.path.dirname(__file__), '..', 'poker_games.db')

DB_PATH = get_db_path()
