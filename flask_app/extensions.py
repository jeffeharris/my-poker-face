"""Flask extensions initialization.

Extensions are created here without being initialized to an app.
They get initialized in the app factory via init_app().
"""

import logging
import re

from flask import Flask
from flask_socketio import SocketIO
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from authlib.integrations.flask_client import OAuth

from poker.repositories.factory import RepositoryFactory
from poker.personality_generator import PersonalityGenerator
from poker.character_images import init_character_image_service
from poker.pricing_loader import sync_pricing_from_yaml

from . import config

logger = logging.getLogger(__name__)

# SocketIO instance - initialized without app
socketio = SocketIO(cors_allowed_origins="*", async_mode='threading')

# Limiter instance - will be initialized with app
limiter = None

# Repository factory (new architecture)
repository_factory = None

# Auth manager - will be set after app creation
auth_manager = None

# OAuth instance - will be initialized with app
oauth = OAuth()

# Personality generator
personality_generator = None


def get_rate_limit_key():
    """Get IP address for rate limiting."""
    return get_remote_address() or "127.0.0.1"


def init_cors(app: Flask) -> None:
    """Initialize CORS configuration."""
    cors_origins_env = config.CORS_ORIGINS_ENV

    if cors_origins_env == '*':
        if config.is_development:
            # Development: Allow all origins WITH credentials using regex
            CORS(app, supports_credentials=True, origins=re.compile(r'.*'))
        else:
            # Production: Wildcard not allowed with credentials
            raise ValueError(
                "CORS_ORIGINS='*' is not allowed in production. "
                "Please set CORS_ORIGINS to a comma-separated list of allowed origins."
            )
    else:
        # Explicit origins
        cors_origins = [origin.strip() for origin in cors_origins_env.split(',') if origin.strip()]
        CORS(app, supports_credentials=True, origins=cors_origins)


def init_limiter(app: Flask) -> Limiter:
    """Initialize rate limiter with optional Redis backend."""
    global limiter

    redis_url = config.REDIS_URL
    default_limits = config.RATE_LIMIT_DEFAULT

    if redis_url:
        try:
            import redis
            r = redis.from_url(redis_url)
            r.ping()

            limiter = Limiter(
                app=app,
                key_func=get_rate_limit_key,
                default_limits=default_limits,
                storage_uri=redis_url
            )
            logger.info("Rate limiter initialized with Redis")
        except Exception as e:
            logger.warning(f"Redis not available, using in-memory rate limiting: {e}")
            limiter = Limiter(
                app=app,
                key_func=get_rate_limit_key,
                default_limits=default_limits
            )
    else:
        limiter = Limiter(
            app=app,
            key_func=get_rate_limit_key,
            default_limits=default_limits
        )
        logger.info("Rate limiter initialized with in-memory storage")

    return limiter


def init_persistence() -> RepositoryFactory:
    """Initialize repository factory."""
    global repository_factory

    db_path = config.DB_PATH
    repository_factory = RepositoryFactory(db_path, initialize_schema=False)

    return repository_factory


def get_repository_factory() -> RepositoryFactory:
    """Get the repository factory, initializing if needed."""
    global repository_factory
    if repository_factory is None:
        init_persistence()
    return repository_factory


def init_personality_generator() -> PersonalityGenerator:
    """Initialize personality generator and character image service."""
    global personality_generator

    personality_generator = PersonalityGenerator(repository_factory=repository_factory)
    init_character_image_service(personality_generator, repository_factory=repository_factory)

    return personality_generator


def init_oauth(app: Flask) -> OAuth:
    """Initialize OAuth with Google provider."""
    oauth.init_app(app)

    # Only register Google if credentials are configured
    if config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET:
        oauth.register(
            name='google',
            client_id=config.GOOGLE_CLIENT_ID,
            client_secret=config.GOOGLE_CLIENT_SECRET,
            server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
            client_kwargs={
                'scope': 'openid email profile'
            }
        )
        logger.info("Google OAuth registered successfully")
    else:
        logger.warning("Google OAuth credentials not configured - Google sign-in will be disabled")

    return oauth


def init_auth(app: Flask) -> None:
    """Initialize authentication manager."""
    global auth_manager

    from poker.auth import AuthManager
    auth_manager = AuthManager(app, repository_factory, oauth)


def init_extensions(app: Flask) -> None:
    """Initialize all Flask extensions with the app."""
    # Initialize CORS
    init_cors(app)

    # Initialize rate limiter
    init_limiter(app)

    # Initialize SocketIO
    socketio.init_app(app)

    # Initialize persistence
    init_persistence()

    # Seed base pricing from YAML (idempotent - only adds missing SKUs)
    sync_pricing_from_yaml()

    # Initialize OAuth (must be before auth)
    init_oauth(app)

    # Initialize auth
    init_auth(app)

    # Initialize personality generator
    init_personality_generator()
