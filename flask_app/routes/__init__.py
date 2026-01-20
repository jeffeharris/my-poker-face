"""Route blueprints for the poker application."""

from .game_routes import game_bp, register_socket_events
from .debug_routes import debug_bp
from .personality_routes import personality_bp
from .image_routes import image_bp
from .stats_routes import stats_bp
from .admin_dashboard_routes import admin_dashboard_bp
from .prompt_debug_routes import prompt_debug_bp
from .experiment_routes import experiment_bp
from .prompt_preset_routes import prompt_preset_bp

__all__ = [
    'game_bp',
    'debug_bp',
    'personality_bp',
    'image_bp',
    'stats_bp',
    'admin_dashboard_bp',
    'prompt_debug_bp',
    'experiment_bp',
    'prompt_preset_bp',
    'register_socket_events',
]
