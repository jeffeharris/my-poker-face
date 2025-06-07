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

# Docker version (recommended for web)
docker compose up -d --build
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

# Test persistence layer
python -m pytest tests/test_persistence.py -v

# Test AI resilience and error handling
python -m pytest tests/test_ai_resilience.py -v

# Test prompt system improvements
python -m pytest tests/test_prompt_improvements.py -v
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

5. **Persistence Layer** (`poker/persistence.py`): SQLite-based game storage with automatic saving after each action.

### Key Design Patterns

- **Functional Core**: Game logic uses pure functions that take state and return new state
- **Event-Driven Web Interface**: SocketIO handles real-time game events and player actions
- **AI Integration**: AI players use OpenAI API with personality prompts from `utils.get_celebrities()`
- **Adapter Pattern**: `flask_app/game_adapter.py` bridges differences between Flask expectations and poker module implementation

### Development Notes

- When modifying game logic, maintain immutability - never mutate state objects
- AI player personalities are defined in `poker/personalities.json` (externalized from code)
- The prompt system uses `PromptManager` in `poker/prompt_manager.py`
- The web interface uses room-based sessions for multiplayer games
- Test coverage includes unit tests for core components and functional tests for game scenarios
- Use relative imports (`.module_name`) within the poker package

### Functional Programming Best Practices

1. **No Mutations in Properties**
   - Properties should never mutate state or have side effects
   - Use list/dict comprehensions instead of imperative mutations
   - Example pattern for conditional list building:
   ```python
   # Good - Functional approach
   option_conditions = [
       ('fold', player_cost_to_call > 0),
       ('check', player_cost_to_call == 0),
       ('call', player_has_enough_to_call and player_cost_to_call > 0),
   ]
   return [option for option, condition in option_conditions if condition]
   
   # Bad - Imperative mutations
   options = ['fold', 'check', 'call']
   if condition:
       options.remove('fold')  # Mutation!
   ```

2. **Pure Functions Without Side Effects**
   - Functions should not modify global state (e.g., random number generator)
   - Use local instances for operations that would otherwise have side effects
   - Example from `create_deck`:
   ```python
   # Good - Local Random instance
   rng = random.Random(random_seed)
   shuffled_deck = deck.copy()
   rng.shuffle(shuffled_deck)
   
   # Bad - Modifies global random state
   random.shuffle(deck)
   ```

3. **Immutable Updates**
   - Always create new objects instead of modifying existing ones
   - Use tuple comprehensions for updating player lists
   - Example from `update_player`:
   ```python
   # Good - Functional tuple comprehension
   updated_players = tuple(
       player.update(**kwargs) if i == player_idx else player
       for i, player in enumerate(self.players)
   )
   
   # Bad - List mutation
   players = list(self.players)
   players[player_idx] = updated_player
   ```

4. **Properties Without Parameters**
   - Properties should never take parameters (they're not methods!)
   - If filtering is needed, create separate methods or return all data

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

## Game Launching Guidance

**Key Note**: When launching the game, always assume we're launching the react game.

## [Rest of the file remains unchanged]