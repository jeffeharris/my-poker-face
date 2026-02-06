# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Vision & Feature Documentation

Comprehensive documentation about the game's vision and planned features can be found in:
- `/docs/vision/GAME_VISION.md` - Overall game vision and philosophy
- `/docs/vision/FEATURE_IDEAS.md` - Detailed feature brainstorming
- `/docs/vision/QUICK_WINS.md` - High-impact, low-effort features
- `/docs/technical/PERSONALITY_ELASTICITY.md` - Dynamic personality system (implemented)

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
cp .env.example .env  # then fill in API keys
```

### Run Applications
```bash
# Docker version (recommended)
docker compose up -d --build

# Or using make
make up

# Manual setup
python -m flask_app.ui_web  # Backend API
cd react/react && npm run dev  # Frontend
```

### Testing

**Important**: All tests run inside Docker. Use `scripts/test.py` for easy test execution.

```bash
# Using the test runner script (recommended)
python3 scripts/test.py                  # Run all Python tests
python3 scripts/test.py --quick          # Run fast tests only (skip slow/integration)
python3 scripts/test.py test_card        # Run tests matching 'test_card'
python3 scripts/test.py -k "flush"       # Run tests matching pytest pattern
python3 scripts/test.py --ts             # TypeScript type checking
python3 scripts/test.py --all            # Python + TypeScript
python3 scripts/test.py --list           # List all test files
python3 scripts/test.py --status         # Check if containers are running

# From Python/Claude:
from scripts.test import run, ts, quick, status
run()                    # Run all Python tests
run("test_card")         # Run tests matching pattern
quick()                  # Fast tests only
ts()                     # TypeScript type checking
status()                 # Check containers

# Direct Docker commands (if needed)
docker compose exec backend python -m pytest tests/ -k "test_card" -v
docker compose exec frontend npx tsc --noEmit
```

#### Testing AI Players
- Mock OpenAI API responses when testing AI behavior
- Test personality loading from JSON
- Verify prompt templates render correctly
- Check that personality traits affect decisions appropriately

### Database Location

**IMPORTANT**: The database path differs between environments:
- **Docker (Flask app)**: `/app/data/poker_games.db` (mounted from `./data/`)
- **Local development**: `poker_games.db` in project root

When running commands in Docker, always use the correct path:
```bash
# Correct - uses the Flask app's database
docker compose exec backend python -c "
import sqlite3
conn = sqlite3.connect('/app/data/poker_games.db')
# ... queries
"

# WRONG - creates/uses a separate database file
docker compose exec backend python -c "
from poker.persistence import GamePersistence
p = GamePersistence()  # Uses poker_games.db, not /app/data/poker_games.db
"
```

### Database Query Utility

Use `scripts/dbq.py` for quick database exploration:

```bash
# List all tables
python3 scripts/dbq.py tables

# Show table schema
python3 scripts/dbq.py schema prompt_captures

# Count rows
python3 scripts/dbq.py count prompt_captures

# Run queries (auto-limits to 20 rows)
python3 scripts/dbq.py "SELECT id, phase, action_taken FROM prompt_captures"

# With format parameters
python3 scripts/dbq.py "SELECT * FROM prompt_captures WHERE phase = '{phase}'" --phase PRE_FLOP
```

From Python:
```python
from scripts.dbq import q, tables, schema, pprint
results = q("SELECT * FROM prompt_captures WHERE phase = ?", ("PRE_FLOP",))
pprint(results)
```

## Architecture Overview

This is a poker game with AI personalities AND an experiment platform for testing LLM capabilities at scale. The codebase follows a functional architecture with immutable state management.

### Core Components

1. **State Machine** (`poker/poker_state_machine.py`): Controls game flow through phases (PRE_FLOP → FLOP → TURN → RIVER). All state transitions are explicit and deterministic.

2. **Immutable Game State** (`poker/poker_game.py`): Game state is represented as frozen dataclasses. State updates create new instances rather than mutating existing ones.

3. **Controller Pattern**: Player interaction is abstracted through controllers:
   - `ConsolePlayerController`: Human input in terminal
   - `AIPlayerController`: OpenAI-powered AI players with celebrity personalities

4. **Modern Architecture**: React frontend (`react/`) communicates with Flask API backend (`flask_app/ui_web.py`) via REST and Socket.IO for real-time updates.

5. **Persistence Layer** (`poker/persistence.py`): SQLite-based game storage with automatic saving after each action.

6. **Experiment Manager** (`experiments/run_ai_tournament.py`): Run AI-only tournaments to A/B test models, prompts, and configurations at scale.

### Key Design Patterns

- **Functional Core**: Game logic uses pure functions that take state and return new state
- **Event-Driven Web Interface**: SocketIO handles real-time game events and player actions
- **AI Integration**: AI players use LLM providers (OpenAI, Anthropic, etc.) with personality prompts
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

### LLM Module (`core/llm/`)

The LLM module provides a unified abstraction over LLM providers with built-in usage tracking:

1. **Key Classes**:
   - `LLMClient`: Low-level, stateless client for one-off completions
   - `Assistant`: High-level wrapper with conversation memory (for stateful chats)
   - `CallType`: Enum for categorizing API calls (e.g., `PLAYER_DECISION`, `COMMENTARY`)
   - `UsageTracker`: Records usage to `api_usage` table for cost analysis

2. **Usage Examples**:
   ```python
   # Stateless call with specific provider/model
   from core.llm import LLMClient, CallType

   client = LLMClient(provider="openai", model="gpt-5-nano")
   response = client.complete(
       messages=[{"role": "user", "content": "Hello"}],
       json_format=True,
       call_type=CallType.CHAT_SUGGESTION,
       game_id="game_123"
   )

   # Stateful conversation (e.g., AI player)
   from core.llm import Assistant, CallType

   assistant = Assistant(
       system_prompt="You are a poker player...",
       call_type=CallType.PLAYER_DECISION,
       player_name="Batman"
   )
   response = assistant.chat("What's your move?", json_format=True)
   ```

3. **Provider Support**: OpenAI, Anthropic, Groq, DeepSeek, Mistral, Google, xAI

4. **Model Tiers**: Four configuration tiers control which model handles each type of work:

   | Tier | Config Constants | Description |
   |---|---|---|
   | Default | `DEFAULT_PROVIDER`, `DEFAULT_MODEL` | Personality generation, commentary, game support |
   | Fast | `FAST_PROVIDER`, `FAST_MODEL` | Chat suggestions, categorization, quick tasks |
   | Image | `IMAGE_PROVIDER`, `IMAGE_MODEL` | AI player avatar generation |
   | Assistant | `ASSISTANT_PROVIDER`, `ASSISTANT_MODEL` | Experiment design, analysis, theme generation |

   **CallType → Tier mapping**:

   | CallType | Tier | Notes |
   |---|---|---|
   | `PLAYER_DECISION` | Per-game | Set by user in game UI |
   | `COMMENTARY` | Default | |
   | `CHAT_SUGGESTION` | Fast | |
   | `CATEGORIZATION` | Fast | |
   | `PERSONALITY_GENERATION` | Default | |
   | `PERSONALITY_PREVIEW` | Default | |
   | `THEME_GENERATION` | Default | |
   | `IMAGE_GENERATION` | Image | |
   | `IMAGE_DESCRIPTION` | Default | |
   | `EXPERIMENT_DESIGN` | Assistant | |
   | `EXPERIMENT_ANALYSIS` | Assistant | |
   | `DEBUG_REPLAY` | User-specified | |
   | `DEBUG_INTERROGATE` | User-specified | |
   | `COACHING` | Default | |

5. **Tracking Data**: All API calls are logged to `api_usage` table with:
   - Token counts (input, output, cached, reasoning)
   - Latency, model, provider
   - Game context (game_id, owner_id, player_name, hand_number)
   - Call type for cost breakdown by category

## Game Launching Guidance

**Key Note**: When launching the game, always assume we're launching the react game.

## DevOps & Production

Production deployment documentation: `/docs/DEVOPS.md`

### Quick Commands
```bash
# Deploy changes to production
./deploy.sh

# SSH to server
ssh root@178.156.202.136

# View logs
ssh root@178.156.202.136 "docker logs -f poker-backend-1"

# Restart services
ssh root@178.156.202.136 "cd /opt/poker && docker compose -f docker-compose.prod.yml restart"
```

### Production URLs
- **Site**: https://mypokerfacegame.com
- **Health**: https://mypokerfacegame.com/health

### Key Files
- `docker-compose.prod.yml` - Production container orchestration
- `Caddyfile` - Reverse proxy with auto-SSL
- `deploy.sh` - Deployment script

## GitHub CLI Usage

When working with GitHub issues and PRs, use these patterns:

### Viewing Issues
```bash
# Get repo name from git remote (don't guess!)
git remote -v

# View issue with --json to avoid GraphQL deprecation errors
gh issue view 38 --repo jeffeharris/my-poker-face --json title,body,state,labels

# Without --json fails due to deprecated Projects (classic) API
# DON'T USE: gh issue view 38 --repo jeffeharris/my-poker-face
```

### Viewing PR Status
```bash
# Check CI status
gh pr checks 41 --repo jeffeharris/my-poker-face

# View failed logs
gh run view <run_id> --repo jeffeharris/my-poker-face --log-failed
```

### Creating PRs
```bash
# Use HEREDOC for body to preserve formatting
gh pr create --title "fix: description" --body "$(cat <<'EOF'
## Summary
- Change 1
- Change 2

## Test plan
- [x] Tests pass

EOF
)"
```