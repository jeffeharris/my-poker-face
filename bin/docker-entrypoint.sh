#!/bin/bash
set -e

echo "=== Starting Poker Backend ==="

# Seed personalities if database is empty
echo "Checking personalities database..."
python bin/seed_personalities.py --check 2>/dev/null || {
    echo "Seeding personalities from JSON..."
    python bin/seed_personalities.py
}

# Start the application
echo "Starting Flask application..."
exec "$@"
