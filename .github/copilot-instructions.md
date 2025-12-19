# GitHub Copilot Instructions

This file provides guidance to GitHub Copilot when working with code in this repository.

## Project Overview

My Poker Face is a poker game with AI players powered by OpenAI LLMs. Players can play against famous personalities (Gordon Ramsay, Eeyore, Batman, etc.) and have conversations with them during gameplay.

### Tech Stack
- **Frontend**: React with TypeScript, Vite, Socket.IO client
- **Backend**: Python Flask API with Socket.IO for real-time updates  
- **AI**: OpenAI GPT models for personality-driven gameplay
- **Database**: SQLite for game persistence
- **Deployment**: Docker Compose with Redis for session management

## Quick Setup

### Environment Setup
```bash
# Create virtual environment
python -m venv my_poker_face_venv
source my_poker_face_venv/bin/activate  # On Windows: my_poker_face_venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
echo "OPENAI_API_KEY=your_key_here" > .env
```

### Running the Application
```bash
# Docker (Recommended)
docker compose up -d --build
# Or: make up

# Manual Setup
# Terminal 1 - Backend:
python -m flask_app.ui_web

# Terminal 2 - Frontend:
cd react/react
npm install
npm run dev
```

Access the game at http://localhost:5173

## Testing

### Running Tests
```bash
# All tests
python -m unittest discover -s tests -p "test*.py"

# Specific test module
python -m unittest tests.core.test_card

# With verbose output
python -m unittest discover -s tests -p "test*.py" -v

# Pytest tests (prompt management, persistence, AI)
python -m pytest tests/test_prompt_management.py -v
python -m pytest tests/test_persistence.py -v
python -m pytest tests/test_ai_resilience.py -v
```

### Linting
```bash
# Frontend linting (TypeScript/React)
cd react/react
npm run lint

# Build check
npm run build
```

### Testing Guidelines
- Mock OpenAI API responses when testing AI behavior
- Test personality loading from `poker/personalities.json`
- Verify prompt templates render correctly
- Check that personality traits affect decisions appropriately
- Use pytest for integration tests, unittest for unit tests

## Architecture

### Core Design Principles
This codebase follows a **functional architecture** with **immutable state management**.

#### 1. Immutable Game State
- Game state is represented as frozen dataclasses
- State updates create new instances rather than mutating existing ones
- **Never mutate state objects** - always create new ones

```python
# Good - Functional approach
updated_players = tuple(
    player.update(**kwargs) if i == player_idx else player
    for i, player in enumerate(self.players)
)

# Bad - Mutation
players[player_idx].chips = new_value  # Don't do this!
```

#### 2. Pure Functions
- Functions should not modify global state
- Use local instances for operations that need randomness
- Properties should never have side effects

```python
# Good - Local Random instance
rng = random.Random(random_seed)
shuffled_deck = deck.copy()
rng.shuffle(shuffled_deck)

# Bad - Modifies global random state
random.shuffle(deck)  # Don't do this!
```

#### 3. State Machine
- Game flow is controlled by `poker/poker_state_machine.py`
- Phases: PRE_FLOP → FLOP → TURN → RIVER
- All state transitions are explicit and deterministic

### Key Components

#### Backend (`poker/` and `flask_app/`)
- **poker_game.py**: Immutable game state and core game logic
- **poker_state_machine.py**: Handles game flow and phase transitions
- **controllers.py**: Player interaction abstraction
  - `ConsolePlayerController`: Human input in terminal
  - `AIPlayerController`: OpenAI-powered AI players
- **ui_web.py**: Flask API with Socket.IO for real-time updates
- **game_adapter.py**: Bridges Flask and poker module differences
- **persistence.py**: SQLite-based game storage

#### AI System (`poker/`)
- **personalities.json**: External configuration for AI traits
- **prompt_manager.py**: Centralized prompt template management
- **elasticity_manager.py**: Dynamic personality adaptation during gameplay
- **ai_resilience.py**: Error handling and retry logic for AI calls

#### Frontend (`react/react/src/`)
- React components with TypeScript
- Socket.IO client for real-time game updates
- Component-based architecture

### Project Structure
```
poker/              # Core game logic (Python)
  - poker_game.py       # Game state and rules
  - poker_state_machine.py  # Game flow control
  - controllers.py      # Player controllers
  - personalities.json  # AI personality definitions
  - prompt_manager.py   # Prompt templates

flask_app/          # Web API (Flask + Socket.IO)
  - ui_web.py          # Main Flask application
  - game_adapter.py    # Adapter between Flask and poker module

react/react/        # Frontend (React + TypeScript)
  - src/
    - components/     # React components
    - hooks/         # Custom React hooks
    - types/         # TypeScript types

tests/             # Test suite
  - core/           # Core component tests
  - test_*.py       # Integration tests
```

## Code Style and Best Practices

### Python Code Style
1. **Functional Programming**
   - Prefer pure functions over stateful operations
   - Use comprehensions instead of loops with mutations
   - Always create new objects instead of modifying existing ones

2. **Properties**
   - Properties should never take parameters
   - Properties should never mutate state or have side effects
   - If filtering is needed, create separate methods

3. **Imports**
   - Use relative imports (`.module_name`) within the poker package
   - Use absolute imports for cross-package references

4. **Type Hints**
   - Use type hints for function signatures
   - Use frozen dataclasses for immutable data structures

### TypeScript/React Code Style
- Follow TypeScript best practices
- Use functional components with hooks
- Proper typing for props and state
- See `react/react/CSS_NAMING_CONVENTION.md` for CSS conventions

### Testing Best Practices
- Mock external API calls (especially OpenAI)
- Test state transitions explicitly
- Verify immutability (state objects don't change)
- Test edge cases and error conditions

## AI and Personality System

### How AI Players Work
1. **Personalities** (`poker/personalities.json`): 
   - Each personality has: play_style, confidence, attitude, personality_traits
   - Traits include: bluff_tendency, aggression, chattiness, emoji_usage

2. **Prompt Manager** (`poker/prompt_manager.py`):
   - `PromptTemplate` class for structured, reusable prompts
   - `PromptManager` class for centralized template management
   - Templates support variable substitution

3. **Dynamic Behavior**:
   - AI decisions influenced by personality traits
   - Personality modifiers applied based on game state
   - Elasticity system adapts traits during gameplay

### When Modifying AI Behavior
- Update personality definitions in `poker/personalities.json`
- Modify prompt templates through `PromptManager`
- Test with multiple personalities to ensure consistency
- Mock OpenAI responses in tests

## Important Development Notes

### Immutability Requirements
- **NEVER** mutate state objects directly
- Always use `.update()` methods or create new instances
- State transitions must be explicit and traceable
- This is critical for game state correctness

### Web Interface
- Uses room-based sessions for multiplayer games
- Socket.IO events for real-time updates
- Game state is synchronized between frontend and backend

### Deployment
- Docker Compose setup for production
- Redis for session management and rate limiting
- Environment variables configured in `.env`
- See `RENDER_DEPLOYMENT.md` for Render.com deployment

### Common Tasks

#### Adding a New Personality
1. Add entry to `poker/personalities.json`
2. Test with personality testing tools in `tests/personality_tester/`
3. Verify prompt generation with the new personality

#### Modifying Game Logic
1. Update pure functions in `poker_game.py` or `poker_state_machine.py`
2. Ensure immutability is maintained
3. Add/update tests in `tests/`
4. Run full test suite before committing

#### Adding Frontend Features
1. Create components in `react/react/src/components/`
2. Add TypeScript types in `react/react/src/types/`
3. Connect to backend via Socket.IO events
4. Follow CSS naming conventions

## Documentation

### Vision & Planning
- `/docs/vision/GAME_VISION.md` - Overall game vision and philosophy
- `/docs/vision/PERSONALITY_ELASTICITY.md` - Dynamic personality system design
- `/docs/vision/FEATURE_IDEAS.md` - Detailed feature brainstorming
- `/docs/vision/QUICK_WINS.md` - High-impact, low-effort features

### Technical Documentation
- `QUICK_START.md` - Getting started guide
- `README_DOCKER.md` - Docker setup details
- `README_PERSISTENCE.md` - Persistence layer documentation
- `TROUBLESHOOTING.md` - Common issues and solutions

### Testing Tools
- `/tests/personality_tester/` - Web-based personality testing tools
- `/tests/personality_tester/personality_manager.py` - Personality editor
- `/tests/personality_tester/AI_GENERATION_PLAN.md` - AI generation roadmap

## Game Flow

When implementing features related to game flow:

1. **Game Phases**: PRE_FLOP → FLOP → TURN → RIVER → SHOWDOWN
2. **Player Actions**: fold, check, call, raise (all through state machine)
3. **State Updates**: Always immutable, always traceable
4. **AI Decisions**: Made through `AIPlayerController` using OpenAI API
5. **Web Updates**: Pushed to clients via Socket.IO events

## Key Reminders

- ✅ **Always** maintain immutability in game state
- ✅ **Always** use pure functions without side effects
- ✅ **Always** mock OpenAI API calls in tests
- ✅ **Always** test state transitions explicitly
- ❌ **Never** mutate state objects directly
- ❌ **Never** use properties with side effects
- ❌ **Never** modify global state (like random number generators)

## Getting Help

- Check existing tests for examples of proper patterns
- Review `CLAUDE.md` for additional development context
- See `TROUBLESHOOTING.md` for common issues
- Consult vision documents in `/docs/vision/` for feature context
