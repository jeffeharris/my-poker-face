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

## Persistence Implementation (Added 2025-06-01)

### Overview
Added SQLite-based persistence to automatically save games and allow resuming. Games are saved after every action.

### Key Changes

1. **Database Schema** (`poker/persistence.py`):
   - `games` table: Stores serialized game state with metadata
   - `game_messages` table: Stores chat/game history
   - Auto-creates database on first use

2. **Serialization Issues Fixed**:
   - All game objects need proper `to_dict()` methods
   - Cards may be dicts or Card objects: `card.to_dict() if hasattr(card, 'to_dict') else card`
   - Fixed imports to use relative imports in poker module

3. **Docker Configuration**:
   - Database stored in `/app/data/poker_games.db` (mounted volume)
   - Local development uses `./poker_games.db`
   - Environment detection in `flask_app/ui_web.py`

### Common Pitfalls & Solutions

#### Import Errors
The poker module requires relative imports:
```python
# ✓ Correct
from .poker_game import PokerGameState
from .hand_evaluator import HandEvaluator

# ✗ Wrong - will cause ModuleNotFoundError
from poker_game import PokerGameState
from hand_evaluator import HandEvaluator
```

#### OpenAI Version Issue
- **Problem**: OpenAI 1.41.0 has `TypeError: Client.__init__() got an unexpected keyword argument 'proxies'`
- **Solution**: Use `openai>=1.82.0` in requirements.txt

#### Environment Variables
Docker requires both `.env` file AND docker-compose.yml configuration:
```yaml
environment:
  - OPENAI_API_KEY=${OPENAI_API_KEY}
env_file:
  - .env
```

#### Database Path Creation
Ensure directory exists before creating database:
```python
os.makedirs(os.path.dirname(db_path), exist_ok=True)
```

### Testing Persistence
```bash
# Unit tests
python -m pytest tests/test_persistence.py -v

# Manual test
python test_persistence.py

# Check database
sqlite3 poker_games.db "SELECT * FROM games;"
```

### API Endpoints
- `GET /games` - JSON list of saved games
- `GET /game/{game_id}` - Load and resume specific game
- Games auto-save after each action

### Future Enhancements
1. ~~**AI State Persistence**: Save conversation history and personality evolution~~ ✓ Implemented via Elasticity System
2. **Game Statistics**: Track win/loss records, biggest pots
3. **Export/Import**: Allow downloading/uploading game saves
4. **Cleanup**: Auto-delete old games after X days

## Poker Engine Deep Dive (Added 2025-06-01, Updated 2025-06-04)

### Core Architecture Philosophy

The poker engine follows a **functional programming paradigm** with **immutable state management**. This design choice ensures:
- Predictable state transitions
- Easy debugging and testing
- Natural support for features like undo/redo and replay
- Thread-safe operations
- No side effects in game logic

**Recent Improvements (2025-06-04):**
- Removed all mutations from property methods
- Made `create_deck` a pure function with no global state modifications
- Converted imperative list operations to functional comprehensions
- Fixed properties that incorrectly accepted parameters

### State Management

#### PokerGameState (`poker/poker_game.py`)
The heart of the engine - a frozen dataclass representing the complete game state:

**Core Properties:**
- `players: Tuple[Player, ...]` - Immutable tuple of players
- `deck: Tuple[Mapping, ...]` - Remaining cards in deck
- `community_cards: Tuple[Mapping, ...]` - Cards on the table
- `pot: Mapping` - Pot amounts including side pots
- `current_player_idx: int` - Index of player to act
- `current_dealer_idx: int` - Dealer button position

**Smart Properties (Computed):**
- `highest_bet` - Maximum bet in current round
- `current_player_options` - Valid actions for current player
- `table_positions` - Dynamic position names (button, SB, BB, UTG, etc.)
- `can_big_blind_take_pre_flop_action` - BB option to check/raise

**Immutability Pattern:**
```python
# All updates create new instances
new_state = game_state.update(pot={'total': 100})
# Or update specific player
new_state = game_state.update_player(player_idx=0, stack=900)
```

#### Player State
Players are also immutable dataclasses with:
- Basic info: `name`, `stack`, `is_human`
- Current round: `bet`, `hand`
- Status flags: `is_all_in`, `is_folded`, `has_acted`
- Computed: `is_active` (can still act this round)

### Game Flow Control

#### PokerStateMachine (`poker/poker_state_machine.py`)
Controls game progression through explicit phases:

**Phase Transitions:**
```
INITIALIZING_GAME
    ↓
INITIALIZING_HAND (deal cards, place blinds)
    ↓
PRE_FLOP (betting round)
    ↓
DEALING_CARDS → FLOP (betting round)
    ↓
DEALING_CARDS → TURN (betting round)
    ↓
DEALING_CARDS → RIVER (betting round)
    ↓
SHOWDOWN/EVALUATING_HAND
    ↓
HAND_OVER → (back to INITIALIZING_HAND)
```

**Key Methods:**
- `run_until_player_action()` - Advances until human/AI input needed
- `advance_state()` - Single state transition
- `update_phase()` - Explicit phase change

### Betting Logic

#### Core Betting Functions
1. **`place_bet()`** - Foundation for all betting actions
   - Handles stack management
   - Sets all-in flags
   - Updates pot
   - Resets other players' `has_acted` if bet raised

2. **Player Actions:**
   - `player_fold()` - Mark folded, move cards to discard
   - `player_check()` - No-op when bet matches highest
   - `player_call()` - Match the highest bet
   - `player_raise()` - Call + additional amount
   - `player_all_in()` - Bet entire stack

3. **`are_pot_contributions_valid()`** - Determines if betting round complete
   - All players have acted
   - All active players have equal bets
   - Special case: BB pre-flop option

### Position Management

Dynamic position assignment based on player count:
- 2 players: Button = SB, other = BB
- 3+ players: Normal positions
- 4-8 players: Adds UTG, Cutoff, MP1-3 as needed

```python
# Example for 6 players:
{
    "button": "Player1",
    "small_blind_player": "Player2", 
    "big_blind_player": "Player3",
    "under_the_gun": "Player4",
    "middle_position_1": "Player5",
    "cutoff": "Player6"
}
```

### Hand Evaluation

#### HandEvaluator (`poker/hand_evaluator.py`)
Evaluates best 5-card hand from 7 cards:

**Hand Rankings (1-10):**
1. Royal Flush
2. Straight Flush
3. Four of a Kind
4. Full House
5. Flush
6. Straight
7. Three of a Kind
8. Two Pair
9. One Pair
10. High Card

**Return Format:**
```python
{
    "hand_rank": 5,  # Flush
    "hand_values": [14, 12, 10, 8, 6],  # Ace-high flush
    "kicker_values": [],  # Not used for flush
    "suit": "Hearts",
    "hand_name": "Flush with Hearts"
}
```

### Side Pot Algorithm

Sophisticated handling of multiple all-in scenarios:

1. **Sort players by contribution** (lowest first)
2. **Create tiers** based on all-in amounts
3. **Award each tier** to best hand among eligible players
4. **Distribute winnings** proportionally

Example with 3 players:
- Player A: All-in $100
- Player B: All-in $300
- Player C: Calls $300

Results in:
- Main pot: $300 (all players eligible)
- Side pot: $400 (only B and C eligible)

### Key Design Patterns

1. **Immutable State Pattern**
   - All state objects are frozen dataclasses
   - Updates return new instances
   - No side effects in game logic

2. **State Machine Pattern**
   - Explicit phase management
   - Clear transitions
   - Prevents invalid states

3. **Strategy Pattern**
   - Controllers abstract player types
   - Same interface for human/AI

4. **Functional Core, Imperative Shell**
   - Pure functions for game logic
   - I/O separated in controllers

### Integration Points

#### For AI Players
- `convert_game_to_hand_state()` - Transforms global state to player perspective
- Controllers handle prompt generation and response parsing
- Game engine remains agnostic to player types

#### For Persistence
- All objects implement `to_dict()` for serialization
- State can be fully reconstructed from dict
- Natural save points after each action

#### For UI
- `prepare_ui_data()` - Extracts display-relevant data
- Event-driven updates via state changes
- Clear separation of concerns

### Best Practices for Modifications

1. **Always preserve immutability** - Use `update()` methods
2. **Add new phases carefully** - Update transition maps
3. **Test side pot logic** thoroughly - Complex edge cases
4. **Maintain relative imports** in poker package
5. **Keep game logic pure** - No I/O in core functions
6. **No mutations in properties** - Use comprehensions and functional patterns
7. **Avoid global state modifications** - Use local instances for operations like shuffling
8. **Properties should be parameterless** - Create methods if parameters are needed

### Common Gotchas

1. **Player indices change** when dealer rotates between hands
2. **Big blind special case** in pre-flop betting
3. **All-in players** still in hand but not active
4. **Flush bug (fixed)** - Must return only best 5 cards
5. **State machine loops** if phase transitions incorrect
6. **Property mutations** - Properties that modify lists/dicts break immutability
7. **Global random state** - Using `random.shuffle()` directly affects global RNG
8. **React UI positioning** - Player positions need to be maintained separately from game logic indices

## Personality Elasticity System (Added 2025-06-04)

### Overview
The Personality Elasticity System creates dynamic AI personalities that respond to game events, making AI players more realistic and engaging.

### Key Components

1. **ElasticityManager** (`poker/elasticity_manager.py`):
   - Manages elastic personalities for all AI players
   - Handles trait changes and recovery
   - Full serialization support

2. **PressureEventDetector** (`poker/pressure_detector.py`):
   - Detects game events (big wins/losses, bluffs, eliminations)
   - Applies appropriate pressure to personality traits
   - Integrates with game flow

3. **Integration with AIPokerPlayer**:
   - Each AI player has an `elastic_personality` attribute
   - Traits change within defined elasticity bounds
   - Moods update based on pressure levels

### Usage

```python
# Initialize elasticity
elasticity_manager = ElasticityManager()
pressure_detector = PressureEventDetector(elasticity_manager)

# Add players
elasticity_manager.add_player(name, personality_config)

# During game events
events = pressure_detector.detect_showdown_events(game_state, winner_info)
pressure_detector.apply_detected_events(events)

# Apply recovery between hands
pressure_detector.apply_recovery()
```

### Testing
```bash
# Run elasticity tests
python -m pytest tests/test_elasticity.py -v

# Run demos
python simple_elasticity_demo.py
python elasticity_demo.py
```

### Documentation
For detailed information, see `/docs/ELASTICITY_SYSTEM.md`
