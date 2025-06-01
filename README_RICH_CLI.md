# My Poker Face - Rich CLI Edition

A beautiful terminal-based poker game with celebrity AI opponents, built using Python Rich library.

## Features

- 🎨 **Beautiful Terminal UI** - Colorful cards, tables, and animations using Rich
- 🎭 **Celebrity AI Opponents** - Play against personalities like Gordon Ramsay, Bob Ross, Trump, and more
- ⚡ **Quick Start** - Playing within 30 seconds
- 🎯 **Personality Showcases** - Each AI has unique playing styles and catchphrases
- 🃏 **Full Texas Hold'em** - Complete poker rules and hand evaluation

## Quick Start

```bash
# First, ensure you have the required dependencies
pip install -r requirements.txt

# Make sure you have your OpenAI API key set
export OPENAI_API_KEY=your_key_here
# Or create a .env file with: OPENAI_API_KEY=your_key_here

# Run with the provided script
./run_rich_cli.sh

# Or run directly
python -m fresh_ui
```

**Note:** The game works with or without an OpenAI API key:
- With API key: AI players use OpenAI for realistic personality-driven decisions
- Without API key: AI players use a mock system with random but sensible decisions

## How to Play

1. **Main Menu Options:**
   - Quick Game - Jump right in with 2 random opponents
   - Choose Opponents - Pick your AI opponents
   - View Personalities - See all available AI personalities
   
2. **Game Controls:**
   - [F]old - Give up your hand
   - [C]all - Match the current bet
   - [R]aise - Increase the bet
   - [A]ll-in - Bet all your chips
   - [Ch]eck - Pass when no bet is required

3. **AI Personalities:**
   - Each AI has unique traits affecting their play style
   - Watch for their signature phrases and reactions
   - Some are aggressive bluffers, others play it safe

## Project Structure

```
fresh_ui/
├── game_runner.py      # Main game loop
├── display/           
│   ├── table.py       # Table visualization
│   ├── cards.py       # Card rendering
│   └── animations.py  # UI animations
├── menus/            
│   ├── main_menu.py   # Start screen
│   └── personality_selector.py  # AI selection
└── utils/            
    ├── input_handler.py  # User input
    └── game_adapter.py   # Poker engine interface
```

## Adding New Features

Check `CLI_TODO.md` for enhancement ideas like:
- Sound effects
- Tournament mode
- Achievement system
- Custom personalities
- Multiplayer support

## Technical Details

- Uses the existing poker engine from `poker/` module
- Rich library for terminal UI
- Asynchronous animations for smooth gameplay
- Clean separation between UI and game logic

## Troubleshooting

If you encounter import errors:
1. Make sure you're running from the project root
2. The virtual environment is activated
3. All requirements are installed: `pip install -r requirements.txt`

## Credits

Built with:
- [Rich](https://github.com/Textualize/rich) - Terminal UI library
- OpenAI API - AI decision making
- Original poker engine by the My Poker Face team