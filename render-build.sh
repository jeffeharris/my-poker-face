#!/bin/bash
# Build script for Render deployment

set -e

echo "🎲 Starting My Poker Face build..."

# Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Install production dependencies
pip install gunicorn[eventlet]

# Set up data directory
mkdir -p data

# Run any database migrations if needed
# python manage.py migrate (if you add migrations later)

echo "✅ Build complete!"