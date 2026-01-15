# My Poker Face - Troubleshooting Guide 🔧

This guide covers common issues and their solutions. If your issue isn't listed here, please report it!

## Table of Contents
- [Installation Issues](#installation-issues)
- [Game Won't Start](#game-wont-start)
- [Display Problems](#display-problems)
- [AI/API Issues](#aiapi-issues)
- [Game Errors](#game-errors)
- [Performance Issues](#performance-issues)

## Installation Issues

### "Python not found" or "python3 not found"

**Windows:**
```bash
# Try using 'py' instead:
py -m venv my_poker_face_venv
py working_game.py

# Or specify version:
py -3 -m venv my_poker_face_venv
```

**Mac/Linux:**
```bash
# Check if Python is installed:
which python3
python3 --version

# If not installed:
# Mac: brew install python3
# Ubuntu/Debian: sudo apt install python3 python3-pip python3-venv
# Fedora: sudo dnf install python3 python3-pip
```

### "No module named 'venv'"

Some Linux distributions need the venv package separately:
```bash
# Ubuntu/Debian:
sudo apt install python3-venv

# Fedora:
sudo dnf install python3-venv
```

### "pip: command not found"

```bash
# Try using pip3:
pip3 install -r requirements.txt

# Or use Python directly:
python -m pip install -r requirements.txt
```

### Requirements installation fails

**"error: Microsoft Visual C++ 14.0 is required" (Windows)**
- Install Visual Studio Build Tools from [Microsoft](https://visualstudio.microsoft.com/downloads/)
- Or try: `pip install --only-binary :all: -r requirements.txt`

**SSL Certificate errors:**
```bash
# Temporary fix (not recommended for production):
pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt
```

## Game Won't Start

### "No module named 'rich'" or similar

You forgot to activate the virtual environment:
```bash
# Windows:
my_poker_face_venv\Scripts\activate

# Mac/Linux:
source my_poker_face_venv/bin/activate

# Then reinstall:
pip install -r requirements.txt
```

### "Permission denied" errors

**Mac/Linux:**
```bash
# Make the script executable:
chmod +x working_game.py

# Or run with python directly:
python working_game.py
```

### Game crashes immediately

Check Python version:
```bash
python --version  # Should be 3.8 or higher
```

Check for error logs:
```bash
# Run with more verbose output:
python -u working_game.py

# Check if all modules import correctly:
python -c "import rich; import openai; print('Imports OK')"
```

## Display Problems

### Cards show as question marks or boxes

Your terminal doesn't support Unicode. Solutions:

**Windows:**
- Use Windows Terminal (recommended) instead of Command Prompt
- In Command Prompt, try: `chcp 65001` before running the game
- Use PowerShell instead of Command Prompt

**Mac:**
- Terminal.app should work by default
- Try iTerm2 if having issues

**Linux:**
- Most modern terminals work (gnome-terminal, konsole, xterm)
- Check locale: `locale` (should show UTF-8)
- Fix locale: `export LC_ALL=en_US.UTF-8`

### Colors don't display properly

```bash
# Force color output:
export FORCE_COLOR=1
python working_game.py

# Or disable colors if they're garbled:
export NO_COLOR=1
python working_game.py
```

### Screen is jumbled or doesn't update

- Resize your terminal window (make it bigger)
- Minimum recommended: 80 columns x 24 rows
- Clear screen: Press Ctrl+L (Linux/Mac) or `cls` (Windows)

## AI/API Issues

### "OpenAI API key not found"

```bash
# Create .env file:
echo "OPENAI_API_KEY=sk-yourkeyhere" > .env

# Make sure .env is in the game directory
ls -la .env  # Should show the file
```

### "Invalid API key" or "Authentication failed"

- Check your API key at [OpenAI Platform](https://platform.openai.com/api-keys)
- Make sure the key starts with `sk-`
- Check for extra spaces or quotes in .env file
- Ensure you have credits/valid payment method on OpenAI account

### AI responses are slow or timeout

- OpenAI API might be experiencing high load
- Check your internet connection
- The game will fall back to mock AI if API fails
- Try reducing the number of AI players

### "Rate limit exceeded"

- You've hit OpenAI's rate limits
- Wait a few minutes and try again
- Consider upgrading your OpenAI plan
- Game will use mock AI as fallback

## Game Errors

### "AttributeError" or "TypeError" during game

This usually means a game state issue:
1. Save your current game state (it auto-saves)
2. Exit with Ctrl+C
3. Start fresh: `python working_game.py`
4. Try loading your saved game

If the error persists:
```bash
# Clear saved games and start fresh:
rm -rf data/*.json  # Linux/Mac
del data\*.json     # Windows
```

### Betting doesn't work correctly

- Make sure you're entering valid numbers
- Check your chip count (can't bet more than you have)
- Side pots might be in effect if someone is all-in

### Game freezes during AI turn

- AI might be processing (wait 10-15 seconds)
- If using real AI, check internet connection
- Press Ctrl+C to safely exit and restart

## Performance Issues

### Game runs slowly

**Reduce AI thinking time:**
- Play with mock AI (no API calls)
- Reduce number of AI players
- Close other applications

**Terminal performance:**
- Use a native terminal (not through VS Code/IDE)
- Disable terminal logging/history for better performance

### High CPU usage

The Rich library can be CPU-intensive:
```bash
# Run with simpler display:
export TERM=dumb
python working_game.py
```

## Advanced Debugging

### Enable debug logging

```bash
# Set debug environment variable:
export DEBUG=1
python working_game.py 2> debug.log

# View the log:
cat debug.log
```

### Check Python package versions

```bash
pip list | grep -E "(rich|openai|click)"
# Should show recent versions
```

### Reset everything

```bash
# Full cleanup and reinstall:
deactivate  # Exit virtual environment
rm -rf my_poker_face_venv data .env
python -m venv my_poker_face_venv
source my_poker_face_venv/bin/activate  # or Windows equivalent
pip install -r requirements.txt
echo "OPENAI_API_KEY=your-key" > .env
python working_game.py
```

## Still Having Issues?

If none of these solutions work:

1. **Collect information:**
   ```bash
   python --version > debug_info.txt
   pip list >> debug_info.txt
   echo "---ERROR OUTPUT---" >> debug_info.txt
   python working_game.py 2>&1 | tee -a debug_info.txt
   ```

2. **Report the issue** with:
   - Your operating system and version
   - Python version
   - The debug_info.txt file
   - Screenshot of the error (if possible)
   - Steps to reproduce the issue

Remember: The game is designed to be resilient. Most errors will show a helpful message and recover automatically!