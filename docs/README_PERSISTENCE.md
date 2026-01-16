# Poker Game Persistence

This poker game now supports persistent storage using SQLite. Games are automatically saved after each action and can be resumed later.

## Features

- **Automatic Saving**: Games are saved after every player action
- **Resume Games**: Navigate directly to `/game/{game_id}` to resume a saved game
- **Game History**: Messages and chat are preserved
- **Game Listing**: View saved games on the home page

## Database Schema

The persistence layer uses SQLite with two main tables:

1. **games**: Stores game state
   - game_id (primary key)
   - created_at, updated_at timestamps
   - phase (current game phase)
   - num_players, pot_size
   - game_state_json (full serialized state)

2. **game_messages**: Stores game chat/events
   - game_id (foreign key)
   - timestamp
   - message_type (user, ai, table)
   - message_text

## API Endpoints

- `GET /` - Home page with saved games list
- `GET /games` - JSON API to list saved games
- `GET /game/{game_id}` - Load and resume a specific game

## Implementation Details

The persistence is handled by:
- `poker/persistence.py` - Core persistence module
- `flask_app/game_adapter.py` - Adapters for Flask compatibility
- Auto-save hooks in `flask_app/ui_web.py`

## Usage Example

```python
from poker.persistence import GamePersistence
from poker.poker_game import initialize_game_state
from poker.poker_state_machine import PokerStateMachine

# Initialize persistence
persistence = GamePersistence("poker_games.db")

# Save a game
game_state = initialize_game_state(player_names=["Jeff", "AI1", "AI2"])
state_machine = PokerStateMachine(game_state)
persistence.save_game("game_123", state_machine)

# Load a game
loaded_state_machine = persistence.load_game("game_123")

# List saved games
games = persistence.list_games()
for game in games:
    print(f"Game {game.game_id}: {game.num_players} players, ${game.pot_size} pot")
```

## Notes

- The database file `poker_games.db` is created in the project root
- AI player state (OpenAI assistants) is recreated on load, not persisted
- Games remain in memory while active for performance