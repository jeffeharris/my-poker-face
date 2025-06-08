# My Poker Face v1.0 Release Notes

**Release Date**: January 8, 2025  
**Version**: 1.0 (Friends & Family Release)

## 🎉 Welcome to My Poker Face!

We're excited to share the first release of My Poker Face - a Texas Hold'em poker game where you play against AI personalities powered by large language models. Each AI opponent has unique personality traits that affect their playing style, making every game a unique experience.

## 🌟 Key Features

### Multiple Ways to Play
- **Rich Terminal Interface** (`working_game.py`) - Beautiful console UI with visual cards and table layout
- **Web Interface** - Play in your browser with real-time updates
- **Simple Console** - Basic text-based version for minimal setups

### AI Personalities
- **15+ Unique Characters**: Play against Gordon Ramsay, Bob Ross, Batman, Sherlock Holmes, and more!
- **Dynamic Personalities**: AI players adapt their mood and behavior based on game events
- **Realistic Dialogue**: Each personality has their own speaking style and reactions
- **Smart Decision Making**: AI players make strategic decisions based on their personality traits

### Game Features
- Full Texas Hold'em implementation with all betting rounds
- Support for 2-6 players (mix of human and AI)
- Game persistence - save and resume games later
- Proper handling of side pots and all-ins
- Beautiful card displays and table visualization (in Rich CLI version)

## 🚀 Getting Started

The easiest way to start playing is with the Rich CLI version:

```bash
# Set up your environment
python -m venv my_poker_face_venv
source my_poker_face_venv/bin/activate  # On Windows: my_poker_face_venv\Scripts\activate
pip install -r requirements.txt

# Play with mock AI (no API key needed)
python working_game.py

# Play with real AI personalities
echo "OPENAI_API_KEY=your_key_here" > .env
python working_game.py
```

For detailed setup instructions, see [QUICK_START.md](QUICK_START.md).

## 🎮 Recommended Version

For the best experience, we recommend using the **Rich CLI version** (`working_game.py`):
- Works without an OpenAI API key (uses mock AI)
- Beautiful terminal graphics with Unicode cards
- Stable and well-tested
- Easy to set up and play

## ⚠️ Known Issues

### General
- Player name is hardcoded to "Jeff" in some interfaces (will be customizable in v1.1)
- Some AI chat messages may not display in certain game states
- Game setup UI is minimal - uses default settings

### Web Version
- Security warning: Uses hardcoded secret key (not for production use)
- May require manual page refresh after certain actions
- Mobile layout not optimized

### Limitations
- Requires Python 3.8 or higher
- OpenAI API key needed for full AI personality experience
- No tournament mode yet (single table games only)
- No play money or chip tracking between games

## 🙏 Acknowledgments

This project was built with the help of many amazing open-source tools and libraries:
- **Rich** - For the beautiful terminal UI
- **Flask & SocketIO** - For the web interface
- **OpenAI API** - For powering our AI personalities
- **Click** - For command-line interfaces

Special thanks to Claude (Anthropic) for extensive development assistance and code generation throughout this project.

## 📝 Feedback

We'd love to hear your thoughts! This is a friends & family release, so your feedback is especially valuable. Please share:
- Which personalities are most fun to play against
- Any bugs or issues you encounter
- Features you'd like to see in future versions
- Your best poker hands and memorable games!

## 🔮 Coming Soon in v1.1

- Customizable player names
- More AI personalities
- Tournament mode
- Improved web UI with mobile support
- Play money system with persistent bankroll
- Statistics tracking

Thank you for trying My Poker Face! May the flop be with you! 🃏

---

**Note**: This is a friends & family release. While the game is fully playable, you may encounter minor bugs. Please report any issues you find!