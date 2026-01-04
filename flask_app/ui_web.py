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

if __name__ == '__main__':
    port = int(os.environ.get('FLASK_RUN_PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True, allow_unsafe_werkzeug=True)
