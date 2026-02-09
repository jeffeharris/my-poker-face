"""Flask extensions initialization.

Extensions are created here without being initialized to an app.
They get initialized in the app factory via init_app().
"""

import logging
import re

from flask import Flask, request
from flask_socketio import SocketIO
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from authlib.integrations.flask_client import OAuth

from poker.repositories import (
    create_repos,
    PressureEventRepository,
)
from poker.personality_generator import PersonalityGenerator
from poker.character_images import init_character_image_service
from poker.pricing_loader import sync_pricing_from_yaml, sync_enabled_models
from poker.game_modes_loader import sync_game_modes_from_yaml
from poker.authorization import init_authorization

from . import config

logger = logging.getLogger(__name__)

def _get_socketio_cors_origins():
    """Resolve Socket.IO allowed origins from app configuration."""
    if config.CORS_ORIGINS_ENV == '*':
        if config.is_development:
            return [
                "http://localhost:5173",
                "http://localhost:5174",
                "http://127.0.0.1:5173",
                "http://127.0.0.1:5174",
            ]
        raise ValueError(
            "CORS_ORIGINS='*' is not allowed in production. "
            "Please set CORS_ORIGINS to a comma-separated list of allowed origins."
        )

    return [origin.strip() for origin in config.CORS_ORIGINS_ENV.split(',') if origin.strip()]


# SocketIO instance - initialized without app
socketio = SocketIO(cors_allowed_origins=_get_socketio_cors_origins(), async_mode='threading')

# Limiter instance - will be initialized with app
limiter = None

# Individual repository globals (replace former `persistence` facade)
game_repo = None
user_repo = None
settings_repo = None
personality_repo = None
experiment_repo = None
prompt_capture_repo = None
decision_analysis_repo = None
prompt_preset_repo = None
capture_label_repo = None
replay_experiment_repo = None
llm_repo = None
guest_tracking_repo = None
hand_history_repo = None
tournament_repo = None
coach_repo = None
persistence_db_path = None  # for callers that need the raw path

# Pressure event repository (separate, not part of create_repos)
event_repository = None

# Auth manager - will be set after app creation
auth_manager = None

# OAuth instance - will be initialized with app
oauth = OAuth()

# Personality generator
personality_generator = None


def get_rate_limit_key():
    """Get IP address for rate limiting."""
    return get_remote_address() or "127.0.0.1"


def _skip_options_requests() -> bool:
    """Exempt CORS preflight (OPTIONS) requests from rate limiting."""
    return request.method == "OPTIONS"


def init_cors(app: Flask) -> None:
    """Initialize CORS configuration."""
    cors_origins_env = config.CORS_ORIGINS_ENV

    if cors_origins_env == '*':
        if config.is_development:
            # Development: Allow common local dev origins with credentials
            dev_origins = [
                "http://localhost:5173",
                "http://localhost:5174",
                "http://127.0.0.1:5173",
                "http://127.0.0.1:5174",
                re.compile(r'^http://homehub:\d+$'),
            ]
            CORS(app, supports_credentials=True, origins=dev_origins)
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
                storage_uri=redis_url,
                default_limits_exempt_when=_skip_options_requests
            )
            logger.info("Rate limiter initialized with Redis")
        except Exception as e:
            logger.warning(f"Redis not available, using in-memory rate limiting: {e}")
            limiter = Limiter(
                app=app,
                key_func=get_rate_limit_key,
                default_limits=default_limits,
                default_limits_exempt_when=_skip_options_requests
            )
    else:
        limiter = Limiter(
            app=app,
            key_func=get_rate_limit_key,
            default_limits=default_limits,
            default_limits_exempt_when=_skip_options_requests
        )
        logger.info("Rate limiter initialized with in-memory storage")

    return limiter


def init_persistence() -> None:
    """Initialize persistence layer with individual repositories."""
    global game_repo, user_repo, settings_repo, personality_repo
    global experiment_repo, llm_repo, guest_tracking_repo
    global hand_history_repo, tournament_repo, coach_repo, persistence_db_path
    global prompt_capture_repo, decision_analysis_repo, prompt_preset_repo
    global capture_label_repo, replay_experiment_repo
    global event_repository

    db_path = config.DB_PATH
    repos = create_repos(db_path)

    game_repo = repos['game_repo']
    user_repo = repos['user_repo']
    settings_repo = repos['settings_repo']
    personality_repo = repos['personality_repo']
    experiment_repo = repos['experiment_repo']
    prompt_capture_repo = repos['prompt_capture_repo']
    decision_analysis_repo = repos['decision_analysis_repo']
    prompt_preset_repo = repos['prompt_preset_repo']
    capture_label_repo = repos['capture_label_repo']
    replay_experiment_repo = repos['replay_experiment_repo']
    llm_repo = repos['llm_repo']
    guest_tracking_repo = repos['guest_tracking_repo']
    hand_history_repo = repos['hand_history_repo']
    tournament_repo = repos['tournament_repo']
    coach_repo = repos['coach_repo']
    persistence_db_path = repos['db_path']

    event_repository = PressureEventRepository(db_path)


def init_personality_generator() -> PersonalityGenerator:
    """Initialize personality generator and character image service."""
    global personality_generator

    personality_generator = PersonalityGenerator(personality_repo=personality_repo)
    init_character_image_service(personality_generator, personality_repo=personality_repo)

    # Assign unowned disabled personalities to admin (idempotent)
    _assign_disabled_personalities_to_admin()

    return personality_generator


def _assign_disabled_personalities_to_admin() -> None:
    """Assign disabled personalities with no owner to the admin user.

    Uses INITIAL_ADMIN_EMAIL to resolve the admin user ID.
    Idempotent: no-op if all disabled personalities already have owners.
    """
    import os
    admin_id = os.environ.get('INITIAL_ADMIN_EMAIL')
    if not admin_id:
        return

    # For guest IDs, use directly; for emails, resolve to user ID
    if not admin_id.startswith('guest_'):
        try:
            user = user_repo.get_user_by_email(admin_id)
            if not user:
                logger.debug(f"Admin email {admin_id} not found in users table yet, skipping personality assignment")
                return
            admin_id = user['id']
        except Exception:
            return

    try:
        assigned = personality_repo.assign_unowned_disabled_to_owner(admin_id)
        if assigned:
            logger.info(f"Assigned {assigned} disabled personalities to admin {admin_id}")
    except Exception as e:
        logger.warning(f"Failed to assign disabled personalities to admin: {e}")


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
    auth_manager = AuthManager(app, user_repo, oauth)

    # Initialize authorization service
    init_authorization(user_repo, auth_manager)


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

    # Sync enabled_models with PROVIDER_MODELS (idempotent - only adds missing models)
    sync_enabled_models()

    # Sync game mode presets from YAML (overwrites system presets each startup)
    sync_game_modes_from_yaml()

    # Initialize OAuth (must be before auth)
    init_oauth(app)

    # Initialize auth
    init_auth(app)

    # Initialize admin from environment variable
    if user_repo:
        admin_user_id = user_repo.initialize_admin_from_env()
        if admin_user_id:
            logger.info(f"Initial admin configured: {admin_user_id}")

    # Initialize personality generator
    init_personality_generator()
