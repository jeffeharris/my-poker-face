"""Flask application factory."""

import logging
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

from .config import SECRET_KEY
from .extensions import init_extensions, socketio
from . import extensions

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_app():
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.secret_key = SECRET_KEY

    # Initialize extensions
    init_extensions(app)

    # Register custom error handlers
    register_error_handlers(app)

    # Register blueprints
    register_blueprints(app)

    # Register socket events
    register_socket_handlers()

    # Register static file serving
    register_static_routes(app)

    return app


def register_error_handlers(app: Flask) -> None:
    """Register custom error handlers."""

    @app.errorhandler(429)
    def ratelimit_handler(e):
        return jsonify({
            'error': 'Rate limit exceeded',
            'message': str(e.description),
            'retry_after': e.retry_after if hasattr(e, 'retry_after') else None
        }), 429


def register_blueprints(app: Flask) -> None:
    """Register all Flask blueprints."""
    from .routes import game_bp, debug_bp, personality_bp, image_bp, stats_bp, admin_bp, prompt_debug_bp, analytics_bp

    app.register_blueprint(game_bp)
    app.register_blueprint(debug_bp)
    app.register_blueprint(personality_bp)
    app.register_blueprint(image_bp)
    app.register_blueprint(stats_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(prompt_debug_bp)
    app.register_blueprint(analytics_bp)


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

        return jsonify({
            'message': 'My Poker Face API',
            'version': '1.0',
            'frontend': 'React app not built',
            'endpoints': {
                'games': '/api/pokergame',
                'new_game': '/api/pokergame/new/<num_players>',
                'game_state': '/api/pokergame/<game_id>',
                'health': '/health'
            }
        })

    @app.route('/health')
    @extensions.limiter.exempt
    def health_check():
        """Health check endpoint for Docker and monitoring."""
        return jsonify({'status': 'healthy', 'service': 'poker-backend'}), 200
