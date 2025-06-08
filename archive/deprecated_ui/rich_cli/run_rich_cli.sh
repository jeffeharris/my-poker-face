#!/bin/bash
# Run the Rich CLI poker game

echo "Starting My Poker Face - Rich CLI Edition..."
echo ""

# Check if virtual environment exists
if [ ! -d "my_poker_face_venv" ]; then
    echo "Virtual environment not found. Creating..."
    python -m venv my_poker_face_venv
fi

# Activate virtual environment
source my_poker_face_venv/bin/activate

# Install/update requirements
pip install -q -r requirements.txt

# Run the game
python -m fresh_ui

# Deactivate virtual environment
deactivate