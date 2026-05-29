#!/bin/bash
set -e

echo "=== Starting Poker Backend ==="

# PRH-40: when started as root AND opted in (DROP_PRIVILEGES=1, set in
# docker-compose.prod.yml), fix ownership of the bind-mounted data volume and
# re-exec this script as the non-root appuser. Everything after — seeding and
# the app itself — then runs unprivileged. Dev (no DROP_PRIVILEGES) stays root,
# so the bind-mounted source tree and the local workflow are unaffected.
if [ "$(id -u)" = "0" ] && [ "${DROP_PRIVILEGES:-0}" = "1" ]; then
    mkdir -p /app/data
    chown -R appuser:appuser /app/data
    echo "Dropping privileges to appuser..."
    exec gosu appuser "$0" "$@"
fi

# Seed personalities if database is empty
echo "Checking personalities database..."
python bin/seed_personalities.py --check 2>/dev/null || {
    echo "Seeding personalities from JSON..."
    python bin/seed_personalities.py
}

# Start the application
echo "Starting Flask application..."
exec "$@"
