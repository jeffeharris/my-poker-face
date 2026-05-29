FROM python:3.10-slim

WORKDIR /app

# Install system dependencies. gosu is used by the entrypoint to drop from
# root to a non-root user at runtime (PRH-40), after fixing ownership of the
# bind-mounted data volume.
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# PRH-40: a non-root runtime user. We stay root at ENTRYPOINT so it can chown
# the (root-owned, bind-mounted) data volume, then drop to this user via gosu
# when DROP_PRIVILEGES=1 (set in docker-compose.prod.yml).
RUN useradd --create-home --uid 1000 appuser

# Copy requirements first for better caching
ARG INSTALL_DEV=false
COPY requirements.txt requirements-dev.txt ./
RUN if [ "$INSTALL_DEV" = "true" ]; then \
      pip install --no-cache-dir -r requirements-dev.txt; \
    else \
      pip install --no-cache-dir -r requirements.txt; \
    fi

# Copy the application code
COPY . .

# Create directory for database
RUN mkdir -p /app/data

# Make scripts executable
RUN chmod +x bin/docker-entrypoint.sh bin/seed_personalities.py

# Expose the Flask port
EXPOSE 5000

# Set environment variables
ENV FLASK_APP=flask_app.ui_web
ENV PYTHONPATH=/app

# Use entrypoint to run setup tasks before starting app
ENTRYPOINT ["/bin/bash", "/app/bin/docker-entrypoint.sh"]

# PRH-40: default to the production WSGI server (gunicorn), NOT the Werkzeug
# dev server. Compose files still override `command:` per environment (dev runs
# `python -m flask_app.ui_web`), but a deploy that forgets to set `command:`
# now lands on the safe production server instead of `flask run`.
CMD ["gunicorn", "-k", "geventwebsocket.gunicorn.workers.GeventWebSocketWorker", \
     "-w", "1", "-b", "0.0.0.0:5000", "--timeout", "120", \
     "flask_app.ui_web:create_app()"]