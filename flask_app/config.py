"""Configuration for the Flask application."""

import logging
import os

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv(override=True)

# Environment detection
flask_env = os.environ.get('FLASK_ENV', 'production')
flask_debug = os.environ.get('FLASK_DEBUG', '0')
is_development = flask_env == 'development' or flask_debug == '1'

# AI Debug mode - enables LLM stats on player cards
enable_ai_debug = os.environ.get('ENABLE_AI_DEBUG', 'false').lower() == 'true'

# Animation speed multiplier — 1.0 is normal, 0 disables all pacing delays
ANIMATION_SPEED = float(os.environ.get('ANIMATION_SPEED', '1.0'))

# All-in run-out: how long (seconds, before ANIMATION_SPEED scaling) to hold on
# the hole-card reveal so the player can register the matchup before the board
# runs out. Was 4s, which read as a dead beat (the first board card is already
# showing by the time this fires); 1.5s matches the per-street reaction cadence.
RUNOUT_REVEAL_HOLD = float(os.environ.get('RUNOUT_REVEAL_HOLD', '1.5'))

# AI decision mode — 'llm' for real LLM calls, 'fallback_random' for instant random actions
AI_DECISION_MODE = os.environ.get('AI_DECISION_MODE', 'llm')

# Optional expensive background features (avatar generation, post-hand commentary)
ENABLE_AVATAR_GENERATION = os.environ.get('ENABLE_AVATAR_GENERATION', 'true').lower() == 'true'
ENABLE_AI_COMMENTARY = os.environ.get('ENABLE_AI_COMMENTARY', 'true').lower() == 'true'

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

# Native (mobile) Google sign-in client IDs. Each platform registers its own
# OAuth client in the Google Cloud Console, so the ID token's `aud` claim differs
# per platform. The native sign-in endpoint accepts a token whose audience
# matches ANY of these, which is what lets one backend serve web + iOS + Android.
# Extra/future platforms can be added via comma-separated GOOGLE_NATIVE_CLIENT_IDS.
GOOGLE_IOS_CLIENT_ID = os.environ.get('GOOGLE_IOS_CLIENT_ID')
GOOGLE_ANDROID_CLIENT_ID = os.environ.get('GOOGLE_ANDROID_CLIENT_ID')
_EXTRA_NATIVE_CLIENT_IDS = os.environ.get('GOOGLE_NATIVE_CLIENT_IDS', '')
GOOGLE_ALLOWED_AUDIENCES = [
    cid.strip()
    for cid in (
        GOOGLE_CLIENT_ID,
        GOOGLE_IOS_CLIENT_ID,
        GOOGLE_ANDROID_CLIENT_ID,
        *_EXTRA_NATIVE_CLIENT_IDS.split(','),
    )
    if cid and cid.strip()
]

FRONTEND_URL = os.environ.get('FRONTEND_URL', 'http://localhost:5173')

# CORS configuration
CORS_ORIGINS_ENV = os.environ.get('CORS_ORIGINS', '*')

# PRH-24: Socket.IO async model. Prod runs under a gevent-websocket gunicorn
# worker that monkey-patches I/O, so `gevent` is the standards-aligned pairing.
# Default stays `threading` (the long-standing value — it works because the
# worker patches threading→greenlets; dev's Werkzeug server also uses threading),
# so behavior is unchanged until an operator opts into `gevent` via this env
# after validating the WebSocket flow. The startup runtime check logs whether
# gevent monkey-patching is actually active (see flask_app.create_app).
SOCKETIO_ASYNC_MODE = os.environ.get('SOCKETIO_ASYNC_MODE', 'threading')

# CSRF protection (PRH-36) — double-submit cookie. Default ARMED in production
# (the SPA and API are same-origin there, so the frontend JS can read the
# non-HttpOnly csrf_token cookie and echo it in the X-CSRF-Token header). Default
# OFF in development, where the SPA (:5173) and API (:5000) are cross-origin and
# the cookie isn't JS-readable, and off under the test suite (FLASK_ENV is
# development there). Override explicitly with CSRF_PROTECTION_ENABLED=true/false.
CSRF_PROTECTION_ENABLED = os.environ.get(
    'CSRF_PROTECTION_ENABLED',
    'false' if is_development else 'true',
).strip().lower() in ('1', 'true', 'yes', 'on')
CSRF_COOKIE_NAME = 'csrf_token'
CSRF_HEADER_NAME = 'X-CSRF-Token'

# Rate limiting configuration (all env-var overridable)
_rate_limit_default_env = os.environ.get('RATE_LIMIT_DEFAULT')
if _rate_limit_default_env is not None:
    RATE_LIMIT_DEFAULT = [s.strip() for s in _rate_limit_default_env.split(';') if s.strip()]
else:
    RATE_LIMIT_DEFAULT = ['10000 per day', '1000 per hour', '100 per minute']
RATE_LIMIT_NEW_GAME = os.environ.get('RATE_LIMIT_NEW_GAME', '10 per hour')
RATE_LIMIT_GAME_ACTION = os.environ.get('RATE_LIMIT_GAME_ACTION', '60 per minute')
# PRH-26: per-IP cap on FRESH guest minting (only applies when there's no valid
# existing guest cookie — returning guests and password logins are exempt, so
# legit users behind shared/CGNAT IPs aren't penalized). Bounds scripted guest
# creation; the global LLM budget (PRH-25) is the financial backstop.
RATE_LIMIT_GUEST_LOGIN = os.environ.get('RATE_LIMIT_GUEST_LOGIN', '60 per hour')
# High-frequency read-only state polling (cash/game state, lobby). A single
# generous per-minute window — these are cheap GETs driven by client polling,
# and a day/hour cap would punish long play sessions. The minute cap still
# blocks runaway loops. Overrides the default limits for the decorated routes.
RATE_LIMIT_POLLING = os.environ.get('RATE_LIMIT_POLLING', '600 per minute')
RATE_LIMIT_CHAT_SUGGESTIONS = os.environ.get('RATE_LIMIT_CHAT_SUGGESTIONS', '100 per hour')
RATE_LIMIT_GENERATE_PERSONALITY = os.environ.get('RATE_LIMIT_GENERATE_PERSONALITY', '15 per hour')
RATE_LIMIT_GENERATE_THEME = os.environ.get('RATE_LIMIT_GENERATE_THEME', '10 per hour')
RATE_LIMIT_REGENERATE_AVATAR = os.environ.get('RATE_LIMIT_REGENERATE_AVATAR', '10 per hour')
RATE_LIMIT_GENERATE_IMAGES = os.environ.get('RATE_LIMIT_GENERATE_IMAGES', '5 per hour')

# ---------------------------------------------------------------------------
# LLM spend kill-switch (PRH-2)
# ---------------------------------------------------------------------------
# Rolling 24h spend ceilings in USD, enforced centrally in LLMClient (the gate
# itself lands in a follow-up step). Two layers:
#   - LLM_GLOBAL_DAILY_BUDGET_USD: total spend across every owner.
#   - LLM_PER_OWNER_DAILY_BUDGET_USD: spend attributable to a single owner_id.
#
# Disabled sentinel: 0 (or unset, or any non-positive value) => that layer is
# OFF and enforces no ceiling. Both default to disabled so this change is inert
# until an operator opts in via env. Startup logs loudly which layers are live
# (see log_llm_budget_status, called from create_app).


def _read_budget_usd(env_name: str) -> float:
    """Parse a USD budget env var; treat blank/garbage/non-positive as disabled (0.0)."""
    raw = os.environ.get(env_name)
    if not raw:
        return 0.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Ignoring non-numeric %s=%r; treating spend layer as disabled", env_name, raw
        )
        return 0.0
    return value if value > 0 else 0.0


LLM_GLOBAL_DAILY_BUDGET_USD = _read_budget_usd('LLM_GLOBAL_DAILY_BUDGET_USD')
LLM_PER_OWNER_DAILY_BUDGET_USD = _read_budget_usd('LLM_PER_OWNER_DAILY_BUDGET_USD')


def log_llm_budget_status() -> None:
    """Log loudly, at startup, whether the LLM spend kill-switch is armed.

    A disabled budget means an overrun runs until the provider's own billing
    limit — operators should see this in the boot log, not discover it later.
    """
    if LLM_GLOBAL_DAILY_BUDGET_USD <= 0 and LLM_PER_OWNER_DAILY_BUDGET_USD <= 0:
        logger.warning(
            "[LLM BUDGET] spend kill-switch DISABLED — no global or per-owner daily "
            "ceiling is enforced. Set LLM_GLOBAL_DAILY_BUDGET_USD (and optionally "
            "LLM_PER_OWNER_DAILY_BUDGET_USD) to arm it."
        )
        return

    parts = []
    if LLM_GLOBAL_DAILY_BUDGET_USD > 0:
        parts.append(f"global=${LLM_GLOBAL_DAILY_BUDGET_USD:.2f}/24h")
    else:
        parts.append("global=disabled")
    if LLM_PER_OWNER_DAILY_BUDGET_USD > 0:
        parts.append(f"per_owner=${LLM_PER_OWNER_DAILY_BUDGET_USD:.2f}/24h")
    else:
        parts.append("per_owner=disabled")
    logger.info("[LLM BUDGET] spend kill-switch ARMED — %s", ", ".join(parts))


def warn_missing_pricing_rows() -> None:
    """At startup, scan recent api_usage for rows with NULL ``estimated_cost``.

    Such rows almost always mean the matching ``model_pricing`` row is missing
    — and because the spend gate sums via ``COALESCE(SUM, 0)``, they count as
    $0 and silently slip the cap. Surfacing the offending ``(provider, model)``
    combos in the boot log lets the operator add pricing rows before drift
    accumulates. Idempotent; fails open (no log on DB error).
    """
    from core.llm.tracking import UsageTracker

    try:
        tracker = UsageTracker.get_default()
        combos = tracker.find_recent_null_cost_combos()
    except Exception as e:
        logger.debug("[LLM BUDGET] missing-pricing scan skipped: %s", e)
        return

    if not combos:
        return

    for provider, model, count in combos:
        logger.warning(
            "[LLM BUDGET] %s/%s has %d recent api_usage row(s) with NULL "
            "estimated_cost — those silently slip the cap; add a model_pricing "
            "row for this SKU to make them billable.",
            provider,
            model,
            count,
        )


# Pagination
GAME_LIST_MAX_LIMIT = int(os.environ.get('GAME_LIST_MAX_LIMIT', '100'))

# Redis configuration
REDIS_URL = os.environ.get('REDIS_URL')

# AI model configuration - import from centralized config

# Per-call LLM timeout constants — canonical source is core.llm.config.
# Re-exported here so flask_app.routes can reference config.FAST_LLM_TIMEOUT_SECONDS.
from core.llm.config import (  # noqa: F401
    COMMENTARY_LLM_TIMEOUT_SECONDS,
    FAST_LLM_TIMEOUT_SECONDS,
    INGAME_LLM_TIMEOUT_SECONDS,
    TICKER_LLM_TIMEOUT_SECONDS,
)

# DB-backed LLM settings — canonical source is core.llm.settings.
# Re-exported here for backwards compatibility with flask_app.routes etc.
from core.llm.settings import (  # noqa: F401
    _get_config_persistence,
    get_assistant_model,
    get_assistant_provider,
    get_default_model,
    get_default_provider,
    get_fast_model,
    get_fast_provider,
    get_image_model,
    get_image_provider,
    get_nano_model,
    get_nano_provider,
)

# Database path
from poker.db_utils import (
    get_default_db_path as get_db_path,  # noqa: F401 — re-exported for route imports
)

DB_PATH = get_db_path()
