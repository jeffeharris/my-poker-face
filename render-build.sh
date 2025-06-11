#!/bin/bash
# Build script for Render deployment

set -e

echo "🎲 Starting My Poker Face build..."

# Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Install production dependencies
pip install gunicorn[eventlet]

# Build React frontend
echo "🔨 Building React frontend..."
cd react/react

# Install Node.js if not present (Render should have it)
if ! command -v node &> /dev/null; then
    echo "Node.js not found, installing..."
    curl -fsSL https://deb.nodesource.com/setup_lts.x | bash -
    apt-get install -y nodejs
fi

# Install npm dependencies
npm install

# Build React app with production API URL
# Use the Render service URL or relative paths
VITE_API_URL="" VITE_SOCKET_URL="" npm run build

# Copy built files to static directory
cd ../..
mkdir -p static
cp -r react/react/dist/* static/

echo "✅ React build complete!"

# Set up data directory
mkdir -p data

# Run any database migrations if needed
# python manage.py migrate (if you add migrations later)

echo "✅ Build complete!"