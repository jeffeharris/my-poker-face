"""Route blueprints for the poker application."""

from .game_routes import game_bp, register_socket_events
from .debug_routes import debug_bp
from .personality_routes import personality_bp
from .image_routes import image_bp
from .stats_routes import stats_bp
from .admin_routes import admin_bp

__all__ = [
    'game_bp',
    'debug_bp',
    'personality_bp',
    'image_bp',
    'stats_bp',
    'admin_bp',
    'register_socket_events',
]
