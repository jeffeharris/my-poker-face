"""Flask extensions initialization.

Extensions are created here without being initialized to an app.
They get initialized in the app factory via init_app().
"""

import logging
import re

from authlib.integrations.flask_client import OAuth
from flask import Flask, request
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_socketio import SocketIO

from poker.authorization import init_authorization
from poker.character_images import init_character_image_service
from poker.game_modes_loader import sync_game_modes_from_yaml
from poker.personality_generator import PersonalityGenerator
from poker.pricing_loader import sync_enabled_models, sync_pricing_from_yaml
from poker.repositories import (
    PressureEventRepository,
    create_repos,
)

from . import config

logger = logging.getLogger(__name__)

# Capacitor/Ionic native WebView origins (iOS/Android). The native shell serves
# the app from capacitor://localhost (iOS) etc. and calls the API cross-origin
# with credentials; the Socket.IO handshake also carries this origin. Both the
# REST CORS and the Socket.IO allow-list must include it or the response is
# dropped / the handshake rejected. Appended to the explicit production allow-list
# too, so the native app can point at the deployed backend — these are fixed
# scheme+host origins (not wildcards), so credentialed CORS still requires an
# exact match.
_NATIVE_WEBVIEW_ORIGINS = [
    "capacitor://localhost",
    "ionic://localhost",
    "http://localhost",
    "https://localhost",
]


def _get_socketio_cors_origins():
    """Resolve Socket.IO allowed origins from app configuration."""
    if config.CORS_ORIGINS_ENV == '*':
        if config.is_development:
            return [
                "http://localhost:5173",
                "http://localhost:5174",
                "http://127.0.0.1:5173",
                "http://127.0.0.1:5174",
                *_NATIVE_WEBVIEW_ORIGINS,
            ]
        raise ValueError(
            "CORS_ORIGINS='*' is not allowed in production. "
            "Please set CORS_ORIGINS to a comma-separated list of allowed origins."
        )

    explicit = [origin.strip() for origin in config.CORS_ORIGINS_ENV.split(',') if origin.strip()]
    return explicit + _NATIVE_WEBVIEW_ORIGINS


# SocketIO instance - initialized without app. async_mode is env-configurable
# (PRH-24); defaults to 'threading' (unchanged). See flask_app.config.
socketio = SocketIO(
    cors_allowed_origins=_get_socketio_cors_origins(),
    async_mode=config.SOCKETIO_ASYNC_MODE,
)

# Limiter instance is created below, AFTER get_rate_limit_key/_skip_options_requests
# are defined. It is a real, app-less Limiter (not None) so that route modules'
# `@limiter.limit(...)` / `@limiter.exempt` decorators work at import time
# regardless of init ordering. It gets bound to the app + storage in init_limiter()
# via limiter.init_app(app). See docs/plans/TEST_WAIT_TIME_REDUCTION.md (Phase 3).

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
tournament_session_repo = None  # durable backing for the MTT registry (v123)
tournament_invite_repo = None  # circuit Main Event invites (v135)
coach_repo = None
relationship_repo = None
bankroll_repo = None
cash_table_repo = None
chip_ledger_repo = None
stake_repo = None
cash_session_repo = None
sandbox_repo = None
vice_state_repo = None
side_hustle_state_repo = None
user_prefs_repo = None
user_avatar_repo = None
holdings_snapshots_repo = None
prestige_snapshots_repo = None
career_progress_repo = None
cash_scalps_repo = None  # durable attributed "who busted whom" counter (v132)
renown_field_repo = None  # batched Renown-v2 field-input read (v133)
entity_presence_repo = None  # dormant Presence machine store (Cut 3 / cutover)
persistence_db_path = None  # for callers that need the raw path

# Human-player avatar service (acquire/generate/process/persist user avatars)
user_avatar_service = None

# Pressure event repository (separate, not part of create_repos)
event_repository = None

# Auth manager - will be set after app creation
auth_manager = None

# OAuth instance - will be initialized with app
oauth = OAuth()

# Personality generator
personality_generator = None


def get_rate_limit_key():
    """Rate-limit key: a real (OAuth) account's stable id, else the client IP (PRH-41).

    The per-route caps (coach, personality/theme/image generation, game actions,
    chat suggestions) were all keyed on IP — an authenticated abuser could
    multiply every quota by rotating IPs. Keying logged-in accounts on their
    stable user id binds those caps **per-user**, closing that bypass for the
    whole expensive surface at once.

    Guests stay IP-keyed: their id is cookie-resettable, so a per-id key would be
    weaker than IP; fresh-guest minting is separately throttled per IP (PRH-26).
    Reads auth live via the module-global `auth_manager` (set post-app); any
    failure falls back to IP so the limiter can never error a request.
    """
    try:
        if auth_manager is not None:
            user = auth_manager.get_current_user()
            if user and user.get("id") and not user.get("is_guest"):
                return f"user:{user['id']}"
    except Exception:
        pass
    return get_remote_address() or "127.0.0.1"


def _skip_options_requests() -> bool:
    """Exempt CORS preflight (OPTIONS) requests from rate limiting."""
    return request.method == "OPTIONS"


# Real, app-less limiter (see the note above the former `limiter = None`).
# Created once at import; bound to an app + storage in init_limiter(). Keeping a
# single stable object means the view decorators registered at route-import time
# stay attached across every create_app() (the old per-app reassignment orphaned
# them) and import order can never leave `limiter` as None/a mock.
limiter = Limiter(
    key_func=get_rate_limit_key,
    default_limits=config.RATE_LIMIT_DEFAULT,
    default_limits_exempt_when=_skip_options_requests,
)


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
                *_NATIVE_WEBVIEW_ORIGINS,
            ]
            CORS(app, supports_credentials=True, origins=dev_origins)
        else:
            # Production: Wildcard not allowed with credentials
            raise ValueError(
                "CORS_ORIGINS='*' is not allowed in production. "
                "Please set CORS_ORIGINS to a comma-separated list of allowed origins."
            )
    else:
        # Explicit origins (prod) + the native WebView origins, so the installed
        # iOS/Android app can call the deployed backend with credentials.
        explicit = [origin.strip() for origin in cors_origins_env.split(',') if origin.strip()]
        CORS(app, supports_credentials=True, origins=explicit + _NATIVE_WEBVIEW_ORIGINS)


def init_limiter(app: Flask) -> Limiter:
    """Bind the module-level limiter to ``app`` (optionally Redis-backed).

    Only chooses storage and calls ``limiter.init_app(app)`` on the SAME object
    created at import — never reassigns the global, so the view decorators
    already registered against it stay bound.
    """
    storage_uri = "memory://"
    storage_label = "in-memory"
    if config.REDIS_URL:
        try:
            import redis

            redis.from_url(config.REDIS_URL).ping()
            storage_uri = config.REDIS_URL
            storage_label = "Redis"
        except Exception as e:
            # PRH-10: in production a configured-but-unreachable Redis must NOT
            # silently degrade to per-worker in-memory limits — every per-IP cap
            # becomes N× under `-w N`, and presence/world-ticker assume a shared
            # store. Fail startup loudly so the deploy is fixed, not masked.
            # (Dev/test still fall back to in-memory for convenience.)
            if not config.is_development:
                raise RuntimeError(
                    f"REDIS_URL is set but unreachable in production; refusing to "
                    f"start with per-worker in-memory rate limiting. Fix Redis, or "
                    f"unset REDIS_URL to opt into in-memory explicitly. Error: {e}"
                ) from e
            logger.warning(f"Redis not available, using in-memory rate limiting: {e}")

    app.config["RATELIMIT_STORAGE_URI"] = storage_uri
    limiter.init_app(app)
    logger.info(f"Rate limiter initialized with {storage_label} storage")
    return limiter


def init_persistence() -> None:
    """Initialize persistence layer with individual repositories."""
    global game_repo, user_repo, settings_repo, personality_repo
    global experiment_repo, llm_repo, guest_tracking_repo
    global \
        hand_history_repo, \
        tournament_repo, \
        tournament_session_repo, \
        tournament_invite_repo, \
        coach_repo, \
        relationship_repo, \
        bankroll_repo, \
        cash_table_repo, \
        chip_ledger_repo, \
        stake_repo, \
        cash_session_repo, \
        sandbox_repo, \
        vice_state_repo, \
        side_hustle_state_repo, \
        user_prefs_repo, \
        user_avatar_repo, \
        holdings_snapshots_repo, \
        prestige_snapshots_repo, \
        career_progress_repo, \
        cash_scalps_repo, \
        renown_field_repo, \
        entity_presence_repo, \
        persistence_db_path
    global prompt_capture_repo, decision_analysis_repo, prompt_preset_repo
    global capture_label_repo, replay_experiment_repo
    global event_repository, user_avatar_service

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
    relationship_repo = repos['relationship_repo']
    bankroll_repo = repos['bankroll_repo']
    cash_table_repo = repos['cash_table_repo']
    chip_ledger_repo = repos['chip_ledger_repo']
    stake_repo = repos['stake_repo']
    cash_session_repo = repos['cash_session_repo']
    sandbox_repo = repos['sandbox_repo']
    vice_state_repo = repos['vice_state_repo']
    side_hustle_state_repo = repos['side_hustle_state_repo']
    user_prefs_repo = repos['user_prefs_repo']
    user_avatar_repo = repos['user_avatar_repo']
    holdings_snapshots_repo = repos['holdings_snapshots_repo']
    prestige_snapshots_repo = repos['prestige_snapshots_repo']
    career_progress_repo = repos['career_progress_repo']
    cash_scalps_repo = repos['cash_scalps_repo']
    renown_field_repo = repos['renown_field_repo']
    entity_presence_repo = repos['entity_presence_repo']
    persistence_db_path = repos['db_path']

    event_repository = PressureEventRepository(db_path)

    # Durable backing for the multi-table tournament registry (MTT meta-state,
    # schema v123). Not part of create_repos; constructed directly like
    # event_repository.
    from poker.repositories.tournament_session_repository import (
        TournamentSessionRepository,
    )

    tournament_session_repo = TournamentSessionRepository(db_path)

    from poker.repositories.tournament_invite_repository import (
        TournamentInviteRepository,
    )

    tournament_invite_repo = TournamentInviteRepository(db_path)

    from poker.user_avatar_service import UserAvatarService

    user_avatar_service = UserAvatarService(user_avatar_repo)


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
                logger.debug(
                    f"Admin email {admin_id} not found in users table yet, skipping personality assignment"
                )
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
            client_kwargs={'scope': 'openid email profile'},
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
