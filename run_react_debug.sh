#!/bin/bash

# Run React app with debug mode enabled

cd react/react

# Set environment variables
export VITE_API_URL=http://localhost:${FLASK_RUN_PORT:-5001}
export VITE_ENABLE_DEBUG=true

echo "Starting React app with debug mode enabled..."
echo "API URL: $VITE_API_URL"
echo "Debug mode: ENABLED"

npm run dev