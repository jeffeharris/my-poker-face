"""Route blueprints for the poker application."""

from .admin_dashboard_routes import admin_dashboard_bp
from .capture_label_routes import capture_label_bp
from .cash_routes import cash_bp
from .character_routes import character_bp
from .chip_ledger_routes import chip_ledger_bp
from .coach_routes import coach_bp
from .debug_routes import debug_bp
from .experiment_routes import experiment_bp
from .game_routes import game_bp, register_socket_events
from .image_routes import image_bp
from .personality_routes import personality_bp
from .profile_routes import profile_bp
from .prompt_debug_routes import prompt_debug_bp
from .prompt_preset_routes import prompt_preset_bp
from .range_explorer_routes import range_explorer_bp
from .replay_experiment_routes import replay_experiment_bp
from .stats_routes import stats_bp
from .user_routes import user_bp

__all__ = [
    'game_bp',
    'debug_bp',
    'personality_bp',
    'image_bp',
    'stats_bp',
    'profile_bp',
    'admin_dashboard_bp',
    'prompt_debug_bp',
    'experiment_bp',
    'prompt_preset_bp',
    'range_explorer_bp',
    'capture_label_bp',
    'replay_experiment_bp',
    'user_bp',
    'coach_bp',
    'cash_bp',
    'chip_ledger_bp',
    'character_bp',
    'register_socket_events',
]
