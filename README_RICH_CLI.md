# My Poker Face - Rich CLI Edition

A beautiful terminal-based Texas Hold'em poker game built with Python's Rich library, featuring celebrity AI opponents and an enhanced visual interface.

## Features

- 🎰 **Visual Poker Table** - Players positioned around a virtual table with live status indicators
- 🃏 **Full Card Display** - Unicode card rendering with suits (♠♥♦♣) in appropriate colors
- 🎭 **Celebrity AI Opponents** - Play against Gordon Ramsay and Bob Ross with unique personalities
- ⚡ **Action History** - Track recent moves with intuitive icons (📂 fold, 📞 call, 💰 raise, ✓ check)
- 📊 **Pot Odds Calculator** - Real-time odds calculation with color-coded evaluation
- 💬 **Dual Panel System** - Separate chat and action history for better organization
- ⌨️ **Quick Keys** - Keyboard shortcuts for faster gameplay ([C]all, [F]old, [R]aise)
- 🎯 **Visual Indicators** - Clear turn indicators and player status at a glance

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the game
python3 working_game.py

# Note: Currently uses mock AI - no OpenAI API key required!
```

## How to Play

1. **Launch the Game**
   ```bash
   python3 working_game.py
   ```

2. **Use Quick Keys**
   - **[C]** or **1** - Call/Check
   - **[F]** or **2** - Fold
   - **[R]** or **3** - Raise
   - Or just press the number shown next to each action

3. **Read the Table**
   - 🎯 Shows whose turn it is
   - 💤 Shows folded players
   - ⭕ Shows active players
   - 💰 Shows stack sizes and current bets

4. **Make Informed Decisions**
   - Check the pot odds calculator when facing a bet
   - Review recent actions in the action history
   - Watch the chat for AI personality messages

## Game Interface

```
┌─────────────────────────────────────────────────┐
│      🎰 MY POKER FACE | 💰 Pot: $300           │
├─────────────────────────────────────────────────┤
│             Bob Ross ($9,500)                   │  ⚡ Recent Actions
│               💤 Folded                         │  ├───────────────
│                                                 │  │ 📞 You: call
│  Gordon 🍳          🃏 Community        📊 Odds │  │ 💰 Gordon: $100
│  ($8,750)        ╭─────╮╭─────╮╭─────╮         │  │ 📂 Bob: fold
│  Bet: $100       │ K♥  ││ Q♠  ││ 7♦  │  $300   │  │ ✓ You: check
│  ⭕ Active       ╰─────╯╰─────╯╰─────╯  $100   │  │ 📞 Gordon: call
│                                         33.3%   │  
│                    You 🎯                Good   │  💬 Chat
│                  ($10,250)                      │  ├───────────────
│                  Bet: $100                      │  │ Welcome!
├─────────────────────────────────────────────────┤  │ Gordon: This hand
│         Your Cards: [A♠] [K♦]                   │  │ is BLOODY RAW!
│         Current: Pair of Kings                  │  │ Bob: Happy little
└─────────────────────────────────────────────────┘  │ cards...
```

## Key Features Explained

### Visual Poker Table
- Players are positioned around a virtual table
- Each player box shows:
  - Name and emoji indicator
  - Current stack size
  - Current bet (if any)
  - Status (🎯 current turn, 💤 folded, ⭕ active)

### Card Rendering
- Full Unicode support for card suits
- Proper coloring (red for hearts/diamonds, white for spades/clubs)
- Cards are displayed in boxes with proper spacing
- Community cards show placeholders for undealt cards

### Action History with Icons
- 📂 **Fold** - Player gave up their hand
- 📞 **Call** - Player matched the bet
- 💰 **Raise** - Player increased the bet
- ✓ **Check** - Player passed with no bet
- 🎯 **All-in** - Player bet everything

### Pot Odds Calculator
- Shows current pot size
- Displays amount needed to call
- Calculates pot odds percentage
- Color-coded evaluation:
  - Green: Excellent odds (< 20%)
  - Yellow: Good odds (20-33%)
  - Red: Poor odds (> 33%)

### Keyboard Shortcuts
- Single key presses for quick decisions
- Number keys (1, 2, 3) also work
- Default to check when no bet to call
- Raise prompts for amount with smart defaults

## Project Structure

```
working_game.py          # Main game file with all UI logic
fresh_ui/
├── display/
│   ├── cards.py        # Unicode card rendering
│   ├── pot_odds.py     # Pot odds calculator
│   └── hand_strength.py # Hand evaluation (ready for integration)
├── utils/
│   └── mock_ai.py      # Mock AI for testing without OpenAI
└── (other components ready for future features)

poker/                   # Core poker engine (unchanged)
├── poker_game.py       # Game logic
├── poker_state_machine.py # State management
└── ...
```

## Technical Implementation

### Architecture
- **Immutable State**: Uses the functional poker engine without modification
- **Mock AI**: Currently uses random decisions with personality flavor
- **Rich Layouts**: Sophisticated multi-panel layout system
- **Error Handling**: Graceful handling of invalid inputs and edge cases

### Key Improvements Made
1. Fixed card rendering to show complete cards with suits
2. Fixed action parsing bug that was causing crashes
3. Added comprehensive error handling
4. Improved layout to prevent UI elements from being cut off
5. Added terminal compatibility checks

## Current Limitations

- **Players**: Fixed at 3 players (you + 2 AI opponents)
- **AI**: Uses mock AI (no OpenAI integration in current version)
- **Persistence**: No save/load functionality yet
- **Statistics**: No long-term stats tracking
- **Hand Evaluation**: Hand strength display prepared but not integrated

## Upcoming Features

See `UI_IMPROVEMENTS.md` for the detailed roadmap:

### High Priority
- Hand strength indicator showing current hand ranking
- Card dealing animations for more engaging gameplay
- Terminal bell notifications for your turn
- Enhanced AI personalities with betting patterns

### Medium Priority
- Session statistics tracking
- Visual betting slider
- Advanced pot odds with implied odds
- Tournament mode with blind increases

### Future Enhancements
- Network multiplayer support
- Custom themes and card backs
- Hand history and replay
- Educational mode with tips

## Development

To contribute or modify:

1. The main game logic is in `working_game.py`
2. UI components are modular in `fresh_ui/display/`
3. The poker engine in `poker/` should not be modified
4. Test with: `python3 working_game.py`

## Troubleshooting

### Common Issues

1. **Unicode symbols not showing**: Ensure your terminal supports UTF-8
2. **Colors not displaying**: Check terminal color support
3. **Layout issues**: Try maximizing terminal window
4. **Import errors**: Run from project root directory

### Terminal Requirements
- UTF-8 support for card symbols
- 256 color support recommended
- Minimum 80x30 terminal size
- Interactive terminal (not piped)

## Credits

Built with:
- [Rich](https://github.com/Textualize/rich) - Python terminal UI library
- Original poker engine by the My Poker Face team
- Unicode card symbols for beautiful display
- Mock AI system for offline play