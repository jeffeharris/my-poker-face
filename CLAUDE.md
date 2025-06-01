# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Setup
```bash
python -m venv my_poker_face_venv
source my_poker_face_venv/bin/activate
pip install -r requirements.txt
echo "OPENAI_API_KEY=your_key_here" > .env
```

### Run Applications
```bash
# Console version
python -m console_app.ui_console

# Web version
python -m flask_app.ui_web
```

### Testing
```bash
# Run all tests
python -m unittest discover -s tests -p "test*.py"

# Run specific test module
python -m unittest tests.core.test_card

# Run with verbose output
python -m unittest discover -s tests -p "test*.py" -v
```

## Architecture Overview

This is a poker game application with AI players powered by OpenAI. The codebase follows a functional architecture with immutable state management.

### Core Components

1. **State Machine** (`poker/poker_state_machine.py`): Controls game flow through phases (PRE_FLOP → FLOP → TURN → RIVER). All state transitions are explicit and deterministic.

2. **Immutable Game State** (`poker/poker_game.py`): Game state is represented as frozen dataclasses. State updates create new instances rather than mutating existing ones.

3. **Controller Pattern**: Player interaction is abstracted through controllers:
   - `ConsolePlayerController`: Human input in terminal
   - `AIPlayerController`: OpenAI-powered AI players with celebrity personalities

4. **Multiple UIs**: The same game engine supports both console (`console_app/`) and web (`flask_app/`) interfaces. The web app uses Flask-SocketIO for real-time multiplayer.

### Key Design Patterns

- **Functional Core**: Game logic uses pure functions that take state and return new state
- **Event-Driven Web Interface**: SocketIO handles real-time game events and player actions
- **AI Integration**: AI players use OpenAI API with personality prompts from `utils.get_celebrities()`

### Development Notes

- When modifying game logic, maintain immutability - never mutate state objects
- AI player personalities are defined in `poker/utils.py`
- The web interface uses room-based sessions for multiplayer games
- Test coverage includes unit tests for core components and functional tests for game scenarios