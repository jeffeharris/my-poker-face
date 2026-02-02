"""Configuration for the Flask application."""

import os

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

# DB-backed LLM settings — canonical source is core.llm.settings.
# Re-exported here for backwards compatibility with flask_app.routes etc.
from core.llm.settings import (           # noqa: F401
    _get_config_persistence,
    get_default_provider,
    get_default_model,
    get_fast_provider,
    get_fast_model,
    get_assistant_provider,
    get_assistant_model,
    get_image_provider,
    get_image_model,
)

# Database path
from poker.db_utils import get_default_db_path as get_db_path  # noqa: F401 — re-exported for route imports

DB_PATH = get_db_path()
