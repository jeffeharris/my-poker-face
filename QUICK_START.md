# My Poker Face - Quick Start Guide 🃏

Get playing in under 5 minutes! This guide will help you start your first poker game.

## Option 1: Quick Play (No AI Chat) - Recommended! ⭐

This is the fastest way to start playing. You'll face AI opponents that make smart decisions but don't chat.

### Step 1: Download and Extract
1. Download the My Poker Face zip file
2. Extract it to a folder on your computer

### Step 2: Install Python (if needed)
- **Windows**: Download from [python.org](https://python.org) (version 3.8 or newer)
- **Mac**: Python usually pre-installed, or use `brew install python3`
- **Linux**: Use your package manager, e.g., `sudo apt install python3`

### Step 3: Open Terminal/Command Prompt
- **Windows**: Right-click in the game folder → "Open in Terminal" or "Open PowerShell window here"
- **Mac**: Open Terminal, type `cd ` (with space), drag the game folder in, press Enter
- **Linux**: Open terminal and navigate to the game folder

### Step 4: Set Up (One Time Only)
```bash
# Windows
python -m venv my_poker_face_venv
my_poker_face_venv\Scripts\activate
pip install -r requirements.txt

# Mac/Linux
python3 -m venv my_poker_face_venv
source my_poker_face_venv/bin/activate
pip install -r requirements.txt
```

### Step 5: Play!
```bash
python working_game.py
```

You'll see a beautiful poker table in your terminal. Use number keys to make your choices!

## Option 2: Full AI Experience (With Personality Chat) 🤖

Want the full experience with AI opponents that chat and taunt? You'll need an OpenAI API key.

### Additional Step: Set Up OpenAI
1. Get an API key from [OpenAI](https://platform.openai.com/api-keys)
2. Create a `.env` file in the game folder:
   ```bash
   echo "OPENAI_API_KEY=your-key-here" > .env
   ```
   Replace `your-key-here` with your actual API key

Then run the same command:
```bash
python working_game.py
```

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