#!/bin/bash
# Launch script for React + Flask poker game

# Get port from argument or use default
BACKEND_PORT=${1:-5001}

echo "🎰 My Poker Face - React Version Launcher"
echo "========================================="
echo "Usage: ./run_react.sh [backend_port]"
echo "Backend port: $BACKEND_PORT"
echo ""

# Check if .env file exists
if [ ! -f .env ]; then
    echo "❌ Error: .env file not found!"
    echo "Please create a .env file with your OPENAI_API_KEY"
    echo "Example: echo 'OPENAI_API_KEY=your_key_here' > .env"
    exit 1
fi

# Function to cleanup on exit
cleanup() {
    echo ""
    echo "🛑 Shutting down services..."
    kill $BACKEND_PID 2>/dev/null
    kill $FRONTEND_PID 2>/dev/null
    exit
}

# Set trap for cleanup
trap cleanup EXIT INT TERM

# Start backend
echo "🚀 Starting Flask backend..."
source my_poker_face_venv/bin/activate 2>/dev/null || {
    echo "❌ Virtual environment not found!"
    echo "Run: python -m venv my_poker_face_venv"
    echo "Then: pip install -r requirements.txt"
    exit 1
}

FLASK_RUN_PORT=$BACKEND_PORT python -m flask_app.ui_web &
BACKEND_PID=$!

# Wait for backend to start
echo "⏳ Waiting for backend to start..."
sleep 3

# Check if backend is running
if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo "❌ Backend failed to start!"
    exit 1
fi

echo "✅ Backend running on http://localhost:$BACKEND_PORT"

# Start frontend
echo ""
echo "🚀 Starting React frontend..."
cd react/react

# Check if node_modules exists
if [ ! -d "node_modules" ]; then
    echo "📦 Installing npm packages (first time setup)..."
    npm install || {
        echo "❌ npm install failed!"
        exit 1
    }
fi

VITE_API_URL=http://localhost:$BACKEND_PORT npm run dev &
FRONTEND_PID=$!

# Wait a moment for frontend to start
sleep 3

echo ""
echo "✅ React app should be running on http://localhost:3000"
echo ""
echo "🎮 Game is ready! Open your browser to http://localhost:3000"
echo ""
echo "Press Ctrl+C to stop both servers"
echo ""

# Wait for user to stop
wait