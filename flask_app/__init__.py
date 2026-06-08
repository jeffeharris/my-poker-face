"""Flask application factory."""

import logging
import math
import os
from pathlib import Path

from flask import Flask, jsonify, send_from_directory
from flask.json.provider import DefaultJSONProvider
from werkzeug.middleware.proxy_fix import ProxyFix

from . import extensions
from .config import SECRET_KEY, is_development
from .extensions import init_extensions, socketio

# Configure logging (PRH-35): structured output + per-request correlation ids,
# with a LogRecordFactory that stamps request_id on every record (so the alert
# webhook carries it too). Quiets the noisy third-party loggers internally.
from .logging_setup import configure_logging

configure_logging()

logger = logging.getLogger(__name__)


def _sanitize_for_json(obj):
    """Replace float('inf'), float('-inf'), and float('nan') with None.

    These values are valid Python floats but not valid JSON, causing
    JSON.parse() failures in browsers.
    """
    if isinstance(obj, float) and (math.isinf(obj) or math.isnan(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_sanitize_for_json(v) for v in obj]
    return obj


class SafeJSONProvider(DefaultJSONProvider):
    """JSON provider that converts inf/nan to null for valid JSON output."""

    def dumps(self, obj, **kwargs):
        return super().dumps(_sanitize_for_json(obj), **kwargs)


def _detect_gunicorn_runtime():
    """Best-effort parse of the gunicorn worker class + worker count from argv.

    gunicorn doesn't rewrite a worker's ``sys.argv`` (it sets the proctitle
    separately), so the master command line — including ``-k <worker-class>``
    and ``-w <n>`` — is still visible from inside ``create_app()``. Returns
    ``(worker_class, workers)``; either may be ``None`` when not launched via
    gunicorn (dev server, tests) or the flag wasn't passed.
    """
    import sys

    argv = sys.argv or []
    worker_class = None
    workers = None
    for i, arg in enumerate(argv):
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        if arg in ("-k", "--worker-class") and nxt:
            worker_class = nxt
        elif arg.startswith("--worker-class="):
            worker_class = arg.split("=", 1)[1]
        elif arg in ("-w", "--workers") and nxt:
            workers = nxt
        elif arg.startswith("--workers="):
            workers = arg.split("=", 1)[1]
    try:
        workers = int(workers) if workers is not None else None
    except (TypeError, ValueError):
        workers = None
    return worker_class, workers


def _log_async_runtime():
    """PRH-24: log (and check) the Socket.IO async model at startup.

    `async_mode='threading'` only yields cooperatively under the prod
    gevent-websocket worker *because* that worker monkey-patches the stdlib —
    a non-standard pairing. Logging whether gevent monkey-patching is actually
    active turns that assumption into an observable fact, and escalates the
    genuinely-broken case (production + threading + NOT patched → blocking I/O
    would stall the single worker) to an alertable ERROR.
    """
    from .config import SOCKETIO_ASYNC_MODE

    try:
        from gevent import monkey

        patched = monkey.is_module_patched("socket")
    except Exception:
        patched = False

    worker_class, workers = _detect_gunicorn_runtime()
    is_gevent_ws_worker = bool(worker_class and "geventwebsocket" in worker_class.lower())

    logger.info(
        "[ASYNC] socketio async_mode=%s; gevent socket monkey-patch active=%s; "
        "worker_class=%s workers=%s",
        SOCKETIO_ASYNC_MODE,
        patched,
        worker_class,
        workers,
    )
    if SOCKETIO_ASYNC_MODE == "threading" and not patched and not is_development:
        logger.error(
            "[ASYNC] production is running async_mode=threading WITHOUT gevent "
            "monkey-patching — blocking I/O will NOT yield cooperatively and can "
            "stall the single worker. Run under the gevent-websocket gunicorn "
            "worker (docker-compose.prod.yml) or set SOCKETIO_ASYNC_MODE=gevent."
        )

    # PRH-24 follow-up (two-hand-flicker): the monkey-patch check above reports
    # active=True under the gevent-websocket worker and stays silent — its blind
    # spot. Even with patching ON, `async_mode='threading'` serves the WS
    # transport via simple_websocket while the worker exposes geventwebsocket: a
    # transport mismatch that can break a WS upgrade at the protocol level (1002).
    #
    # Downgraded ERROR→WARNING after pulling prod evidence (2026-06-08): the
    # "repeated 1002 closes + reconnect storms" framing was overstated — a quiet
    # ~7-min window showed a *single* `simple_websocket 1002` and only ~6 distinct
    # sids (no storm). The real observed effect is that clients run on the heavier
    # long-polling fallback (`transport=polling` only, 0 websocket upgrades) rather
    # than a crash. So this is a degradation worth flagging, not a broken deploy.
    # `gevent` is still the standards-aligned pairing under this worker; flipping
    # to it should be validated (does `transport=websocket` then appear?) rather
    # than assumed — see docs/SCALING.md PRH-40 for the counter-argument.
    if is_gevent_ws_worker and SOCKETIO_ASYNC_MODE == "threading" and not is_development:
        logger.warning(
            "[ASYNC] running under the gevent-websocket gunicorn worker with "
            "async_mode=threading — WS upgrades use simple_websocket and may hit "
            "1002 protocol errors, so clients fall back to long-polling (heavier "
            "on the single worker). Consider validating SOCKETIO_ASYNC_MODE=gevent."
        )

    # Single-process socket-state assumption: the Socket.IO rate limiter
    # (socket_rate_limit), the cash presence registry (services/presence), and
    # the world ticker (services/ticker_service) are all in-process. With >1
    # gunicorn worker, room emits from background tasks won't fan out across
    # workers (no Socket.IO message_queue is configured) and per-caller caps
    # multiply per worker. Refuse to run silently mis-scaled in production.
    if workers is not None and workers > 1 and not is_development:
        logger.error(
            "[ASYNC] gunicorn started with %d workers, but Socket.IO presence, "
            "rate limiting, and the world ticker are single-process. Run with "
            "-w 1, or add a Socket.IO message_queue + shared stores before scaling.",
            workers,
        )


def recover_interrupted_experiments():
    """Mark experiments that were running when server stopped as interrupted.

    Called on startup to detect orphaned 'running' experiments and mark them
    as 'interrupted' so users can manually resume them.
    """
    try:
        from .routes.experiment_routes import detect_orphaned_experiments

        detect_orphaned_experiments()
    except Exception as e:
        logger.error(f"Error recovering interrupted experiments on startup: {e}")


def _init_sentry():
    """Initialize backend error monitoring (no-op unless SENTRY_DSN is set).

    Ties server-side exceptions to the same Sentry project the frontend reports
    to, so a UX session replay can be cross-referenced with the server error
    behind it. The FlaskIntegration is auto-enabled by sentry-sdk when Flask is
    importable. Kept cheap: low trace sampling, send_default_pii off (we attach
    identity explicitly elsewhere if needed).
    """
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return

    def _attach_feature_flags(event, _hint):
        """Stamp the resolved feature-flag state onto each event (best-effort).

        Backend bugs often hinge on which flags were live, so attach the full
        snapshot as a context. Cheap: the registry resolves in-memory (no flag
        is currently db_overridable), and errors are infrequent.
        """
        try:
            from core import feature_flags

            event.setdefault("contexts", {})["feature_flags"] = {
                row["name"]: row["value"] for row in feature_flags.snapshot()
            }
        except Exception:
            pass
        return event

    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=dsn,
            environment=os.environ.get("FLASK_ENV", "development"),
            traces_sample_rate=0.1,
            send_default_pii=False,
            before_send=_attach_feature_flags,
        )
        logger.info("[SENTRY] backend error monitoring enabled")
    except Exception as e:  # never let monitoring setup break app boot
        logger.error("[SENTRY] init failed: %s", e, exc_info=True)


def create_app():
    """Create and configure the Flask application."""
    # Initialize Sentry before the app + extensions so its integrations can
    # instrument them. No-op without SENTRY_DSN.
    _init_sentry()

    app = Flask(__name__)
    app.json_provider_class = SafeJSONProvider
    app.json = SafeJSONProvider(app)
    app.secret_key = SECRET_KEY

    # In production behind a reverse proxy (Caddy), trust X-Forwarded headers
    # This ensures url_for generates https:// URLs for OAuth callbacks
    if os.environ.get('FLASK_ENV') == 'production':
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
        app.config['PREFERRED_URL_SCHEME'] = 'https'

    # Initialize extensions
    init_extensions(app)

    # PRH-36: double-submit CSRF protection (armed in prod; see flask_app.csrf).
    from .csrf import init_csrf

    init_csrf(app)

    # PRH-35: per-request correlation id (X-Request-ID) on every HTTP request.
    from .logging_setup import init_request_logging

    init_request_logging(app)

    # PRH-24: confirm the async model at startup (see _log_async_runtime).
    _log_async_runtime()

    # PRH-34: release per-thread SQLite connections at the end of each request /
    # socket event (flask-socketio pushes an app context per event), so the
    # thread-local connection cache doesn't leak WAL readers + fds over uptime.
    @app.teardown_appcontext
    def _close_thread_db_connections(_exc):
        from poker.repositories.base_repository import close_all_thread_connections

        close_all_thread_connections()

    # PRH-28: attach the webhook alert handler (no-op unless ALERT_WEBHOOK_URL
    # is set) before the budget/pricing startup checks, so a "[LLM BUDGET]
    # DISABLED" or NULL-pricing warning on boot also pages.
    from .services.alerting import init_alerting

    init_alerting()

    # Arm the LLM spend kill-switch (PRH-2) from app config and announce its
    # status. core.llm can't import flask_app, so we push the limits in here.
    from core.llm.budget import configure_spend_limits

    from .config import (
        LLM_GLOBAL_DAILY_BUDGET_USD,
        LLM_PER_OWNER_DAILY_BUDGET_USD,
        log_llm_budget_status,
        warn_missing_pricing_rows,
    )

    configure_spend_limits(LLM_GLOBAL_DAILY_BUDGET_USD, LLM_PER_OWNER_DAILY_BUDGET_USD)
    log_llm_budget_status()
    # Surface any recent api_usage rows missing pricing — those slip the cap.
    warn_missing_pricing_rows()

    # Mark any experiments that were running when server stopped as interrupted
    recover_interrupted_experiments()

    # Register custom error handlers
    register_error_handlers(app)

    # Register blueprints
    register_blueprints(app)

    # Register socket events
    register_socket_handlers()

    # Register static file serving
    register_static_routes(app)

    # Start background cleanup timer (must be after all imports to avoid import lock deadlock)
    from .services.game_state_service import start_cleanup_timer

    start_cleanup_timer()

    # PRH-32: daily retention sweep — purges prompt_captures + api_usage past
    # their configured windows. No-op until LLM_PROMPT_RETENTION_DAYS /
    # API_USAGE_RETENTION_DAYS are set (so inert in dev/tests).
    from .services.retention_service import start_retention_sweep

    start_retention_sweep()

    # Start the realtime cash-mode world ticker (advances unseated tables
    # for active sandboxes; pushes lobby_tick / world_event over socket).
    # Idempotent across create_app() calls; no-op when disabled.
    from .services.ticker_service import start_world_ticker

    start_world_ticker(socketio)

    # Drop every in-flight cash session (memory + DB) and seed the
    # persistent lobby. `kill_all_cash_sessions` subsumes the old
    # `cleanup_orphan_cash_games`: in v1.5 the deploy that lands the
    # lobby has no production users to preserve, and persistent table
    # state lives in `cash_tables` (not in `games`), so wiping every
    # `cash-*` game row is safe.
    try:
        from cash_mode.lobby import kill_all_cash_sessions

        from .services import game_state_service

        kill_all_cash_sessions(
            game_state_service=game_state_service,
            game_repo=extensions.game_repo,
            # T2.2: sweep abandoned cash-* rows (untouched past the TTL)
            # so a session orphaned by a crash/restart doesn't wedge the
            # sit guard forever. Fresh rows are preserved for resume.
            cash_session_repo=extensions.cash_session_repo,
            stake_repo=extensions.stake_repo,
            chip_ledger_repo=extensions.chip_ledger_repo,
        )
    except Exception as e:
        logger.error(f"[CASH] lobby boot hook failed: {e}", exc_info=True)

    return app


def register_error_handlers(app: Flask) -> None:
    """Register custom error handlers.

    Flask-CORS only adds headers via after_request hooks, which are skipped
    for unhandled exceptions. These handlers ensure error responses still
    include CORS headers by returning proper JSON responses.
    """

    @app.errorhandler(429)
    def ratelimit_handler(e):
        return jsonify(
            {
                'error': 'Rate limit exceeded',
                'message': str(e.description),
                'retry_after': e.retry_after if hasattr(e, 'retry_after') else None,
            }
        ), 429

    @app.errorhandler(500)
    def internal_error_handler(e):
        logger.error(f"Internal server error: {e}", exc_info=True)
        return jsonify(
            {'error': 'Internal server error', 'message': 'An unexpected error occurred'}
        ), 500

    @app.errorhandler(Exception)
    def unhandled_exception_handler(e):
        logger.error(f"Unhandled exception: {e}", exc_info=True)
        if is_development:
            return jsonify({'error': type(e).__name__, 'message': str(e)}), 500
        return jsonify(
            {'error': 'Internal server error', 'message': 'An unexpected error occurred'}
        ), 500


def register_blueprints(app: Flask) -> None:
    """Register all Flask blueprints."""
    from .routes import (
        admin_dashboard_bp,
        capture_label_bp,
        cash_bp,
        character_bp,
        chip_ledger_bp,
        coach_bp,
        debug_bp,
        experiment_bp,
        game_bp,
        image_bp,
        personality_bp,
        profile_bp,
        prompt_debug_bp,
        prompt_preset_bp,
        range_explorer_bp,
        replay_experiment_bp,
        sentry_relay_bp,
        stats_bp,
        tournament_bp,
        training_bp,
        user_bp,
    )

    app.register_blueprint(game_bp)
    app.register_blueprint(debug_bp)
    app.register_blueprint(personality_bp)
    app.register_blueprint(image_bp)
    app.register_blueprint(stats_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(admin_dashboard_bp)
    app.register_blueprint(prompt_debug_bp)
    app.register_blueprint(experiment_bp)
    app.register_blueprint(prompt_preset_bp)
    app.register_blueprint(range_explorer_bp)
    app.register_blueprint(capture_label_bp)
    app.register_blueprint(replay_experiment_bp)
    app.register_blueprint(sentry_relay_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(coach_bp)
    app.register_blueprint(cash_bp)
    app.register_blueprint(chip_ledger_bp)
    app.register_blueprint(character_bp)
    app.register_blueprint(tournament_bp)
    app.register_blueprint(training_bp)

    # Test helper endpoints — only available when ENABLE_TEST_ROUTES=true
    if os.environ.get('ENABLE_TEST_ROUTES', 'false').lower() == 'true':
        from .routes.test_routes import test_bp

        app.register_blueprint(test_bp)
        logger.info("Test helper endpoints registered (ENABLE_TEST_ROUTES=true)")


def register_socket_handlers() -> None:
    """Register SocketIO event handlers."""
    from .routes import register_socket_events

    register_socket_events(socketio)


def register_static_routes(app: Flask) -> None:
    """Register static file serving routes."""
    static_path = Path(__file__).parent.parent / 'static'

    @app.route('/', defaults={'path': ''})
    @app.route('/<path:path>')
    @extensions.limiter.exempt
    def serve(path):
        if path != "" and (static_path / path).exists():
            return send_from_directory(str(static_path), path)
        else:
            if (static_path / 'index.html').exists():
                return send_from_directory(str(static_path), 'index.html')

        return jsonify(
            {
                'message': 'My Poker Face API',
                'version': '1.0',
                'frontend': 'React app not built',
                'endpoints': {
                    'games': '/api/pokergame',
                    'new_game': '/api/pokergame/new/<num_players>',
                    'game_state': '/api/pokergame/<game_id>',
                    'health': '/health',
                },
            }
        )

    @app.route('/health')
    @extensions.limiter.exempt
    def health_check():
        """Health check endpoint for Docker and monitoring."""
        return jsonify({'status': 'healthy', 'service': 'poker-backend'}), 200
