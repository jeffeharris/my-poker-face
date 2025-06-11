# My Poker Face - Quick Start Guide 🃏

Get playing in under 5 minutes! This guide will help you start your first poker game.

## Option 1: Docker Setup (Recommended) 🐳

The easiest way to play is using Docker, which handles all the setup for you.

### Prerequisites
- Install [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- Get an OpenAI API key from [OpenAI](https://platform.openai.com/api-keys) (optional - for AI chat)

### Step 1: Download and Extract
1. Download the My Poker Face zip file
2. Extract it to a folder on your computer

### Step 2: Set Up Environment
```bash
# Copy the example environment file
cp .env.example .env

# Edit .env and add your OpenAI API key (optional)
# OPENAI_API_KEY=your-key-here
```

### Step 3: Start the Game
```bash
# Start all services
docker-compose up -d

# Or use the Makefile
make up
```

### Step 4: Play!
Open your web browser and go to: **http://localhost:5173**

You'll see a modern poker interface where you can play against AI personalities!

## Option 2: Manual Setup (For Developers) 💻

If you prefer to run without Docker or need to modify the code:

### Prerequisites
- Python 3.8+ installed
- Node.js 16+ installed
- OpenAI API key (optional)

### Backend Setup
```bash
# Create virtual environment
python -m venv my_poker_face_venv
source my_poker_face_venv/bin/activate  # On Windows: my_poker_face_venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Create .env file
echo "OPENAI_API_KEY=your-key-here" > .env

# Start the backend
python -m flask_app.ui_web
```

### Frontend Setup (New Terminal)
```bash
# Navigate to React app
cd react/react

# Install dependencies
npm install

# Start the frontend
npm run dev
```

### Play!
Open your browser to: **http://localhost:5173**

## Game Controls 🎮

- **Number Keys (1-5)**: Select menu options
- **Enter**: Confirm selections
- **Ctrl+C**: Quit game (progress is auto-saved)

## Choosing Your Game Mode 🎯

When you start, you'll see:
1. **Quick Game** - Jump right in with 3 AI opponents
2. **Custom Game** - Choose number of opponents and personalities
3. **Load Game** - Resume a previous game

## Tips for New Players 🌟

1. **Start with Quick Game** to learn the controls
2. **Watch the pot odds** displayed on screen - they help with betting decisions
3. **Each AI has a personality** - Gordon Ramsay is aggressive, Bob Ross is chill
4. **Your chip count** is shown at the bottom of the screen
5. **The game auto-saves** after each hand

## Troubleshooting Quick Fixes 🔧

**"Python not found"**
- Make sure Python 3.8+ is installed
- On Windows, try `py` instead of `python`

**"No module named..."**
- Make sure you activated the virtual environment
- Re-run `pip install -r requirements.txt`

**Game seems frozen**
- AI might be "thinking" - wait a few seconds
- Press Ctrl+C to safely exit

**Can't see cards properly**
- Your terminal needs Unicode support
- Try Windows Terminal (not Command Prompt) on Windows
- Terminal app on Mac/Linux should work fine

## Next Steps 📚

- Try different AI personalities in Custom Game mode
- Read [RELEASE_NOTES_v1.0.md](RELEASE_NOTES_v1.0.md) for full feature list
- Check [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for detailed help
- Share feedback about your experience!

## Need Help? 🆘

If you're stuck, the game includes:
- Detailed error messages with suggestions
- Auto-recovery from most errors
- Save files in the `data/` folder if you need to share for debugging

Enjoy your game! May the best hand win! 🏆