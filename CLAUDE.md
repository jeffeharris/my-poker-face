# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Vision & Feature Documentation

Comprehensive documentation about the game's vision and planned features can be found in:
- `/docs/vision/GAME_VISION.md` - Overall game vision and philosophy
- `/docs/vision/PERSONALITY_ELASTICITY.md` - Dynamic personality system design
- `/docs/vision/FEATURE_IDEAS.md` - Detailed feature brainstorming
- `/docs/vision/QUICK_WINS.md` - High-impact, low-effort features
- `/docs/vision/TECH_DEBT_ANALYSIS.md` - Technical debt analysis plan

## Personality Testing Tools

Web-based tools for testing and managing AI personalities:
- `/tests/personality_tester/` - Test personalities with scenarios
- `/tests/personality_tester/personality_manager.py` - Edit personalities
- `/tests/personality_tester/AI_GENERATION_PLAN.md` - AI generation roadmap

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

# Run prompt management tests
python -m pytest tests/test_prompt_management.py tests/test_prompt_golden_path.py -v
```

#### Testing AI Players
- Mock OpenAI API responses when testing AI behavior
- Test personality loading from JSON
- Verify prompt templates render correctly
- Check that personality traits affect decisions appropriately

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
- AI player personalities are defined in `poker/personalities.json` (externalized from code)
- The prompt system uses `PromptManager` in `poker/prompt_manager.py`
- The web interface uses room-based sessions for multiplayer games
- Test coverage includes unit tests for core components and functional tests for game scenarios
- Use relative imports (`.module_name`) within the poker package

### AI and Prompt System

The AI player system uses a centralized prompt management approach:

1. **Personalities** (`poker/personalities.json`): External configuration for AI traits
   - Each personality has: play_style, confidence, attitude, and personality_traits
   - Traits include: bluff_tendency, aggression, chattiness, emoji_usage

2. **Prompt Manager** (`poker/prompt_manager.py`): Handles all prompt templates
   - `PromptTemplate` class for structured, reusable prompts
   - `PromptManager` class for centralized template management
   - Templates support variable substitution

3. **Dynamic Behavior**: AI decisions are influenced by:
   - Personality traits (e.g., high bluff_tendency → more bluffs)
   - Game state (e.g., low chips → conservative play)
   - Personality modifiers applied at runtime

### Known Issues

- `initialize_game_state()` always adds a human player named "Jeff" (needs to be configurable)
- No game setup/configuration page for player selection
- WebSocket disconnections not handled gracefully
- Some imports may fail if not run from project root

### Quick Reference

#### Adding a New AI Personality
1. Edit `poker/personalities.json`
2. Add entry with: play_style, default_confidence, default_attitude, personality_traits
3. Personality automatically available in game

#### Modifying Prompts
1. Edit `poker/prompt_manager.py`
2. Update template sections in `_load_default_templates()`
3. Test with: `python -m pytest tests/test_prompt_management.py`

#### Key Files for AI System
- `poker/personalities.json` - Personality configurations
- `poker/prompt_manager.py` - Prompt templates and management
- `poker/poker_player.py` - AIPokerPlayer class
- `poker/controllers.py` - AIPlayerController for decisions