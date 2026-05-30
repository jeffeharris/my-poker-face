"""Application entry point.

This module provides the entry point for running the Flask/SocketIO application.
All application logic has been refactored into modular components:

- config.py: Configuration settings
- extensions.py: Flask extensions initialization
- routes/: HTTP route blueprints
- handlers/: Business logic handlers
- services/: Shared services and state management
"""

import os

from . import create_app
from .extensions import socketio

# Create the Flask application
app = create_app()

# Live only: move showdown equity-at-action telemetry off the hand-completion
# path (best-effort enrichment for opponent models). Sims/tests don't import this
# entry point, so they stay synchronous + deterministic.
from poker.memory.memory_manager import enable_async_equity_telemetry  # noqa: E402

enable_async_equity_telemetry()

if __name__ == '__main__':
    port = int(os.environ.get('FLASK_RUN_PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True, allow_unsafe_werkzeug=True)
