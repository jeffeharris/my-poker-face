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
# Docker version (recommended)
docker compose up -d --build

# Or using make
make up

# Manual setup
python -m flask_app.ui_web  # Backend API
cd react/react && npm run dev  # Frontend
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

4. **Modern Architecture**: React frontend (`react/`) communicates with Flask API backend (`flask_app/ui_web.py`) via REST and Socket.IO for real-time updates.

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

### LLM Module (`core/llm/`)

The LLM module provides a unified abstraction over LLM providers with built-in usage tracking:

1. **Key Classes**:
   - `LLMClient`: Low-level, stateless client for one-off completions
   - `Assistant`: High-level wrapper with conversation memory (for stateful chats)
   - `CallType`: Enum for categorizing API calls (e.g., `PLAYER_DECISION`, `COMMENTARY`)
   - `UsageTracker`: Records usage to `api_usage` table for cost analysis

2. **Usage Examples**:
   ```python
   # Stateless call (e.g., generating personalities)
   from core.llm import LLMClient, CallType

   client = LLMClient()
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

3. **Provider Support**: Currently supports OpenAI (extensible to Anthropic, Groq)

4. **Tracking Data**: All API calls are logged to `api_usage` table with:
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