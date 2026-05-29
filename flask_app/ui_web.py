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
    from .config import is_development

    port = int(os.environ.get('FLASK_RUN_PORT', 5000))
    # PRH-40: this entry point runs the Werkzeug dev server with debug +
    # allow_unsafe_werkzeug — fine for local dev, unsafe for production. Prod
    # serves via gunicorn (see docker-compose.prod.yml); refuse to start the
    # dev server there rather than silently exposing it on a misconfigured run.
    if not is_development:
        raise RuntimeError(
            "Refusing to start the Werkzeug dev server in production "
            "(FLASK_ENV != development). Use gunicorn — see docker-compose.prod.yml."
        )
    socketio.run(app, host='0.0.0.0', port=port, debug=True, allow_unsafe_werkzeug=True)
