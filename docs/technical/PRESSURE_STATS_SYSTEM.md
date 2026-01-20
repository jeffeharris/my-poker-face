# Pressure Stats System

## Overview

The Pressure Stats System tracks and persists dramatic game events, providing players with fun statistics and leaderboards. Unlike traditional poker statistics (VPIP, PFR), this system focuses on emotional and dramatic moments that create memorable gameplay experiences.

## Architecture

### Components

1. **PressureEventDetector** (`poker/pressure_detector.py`)
   - Detects pressure events during gameplay (wins, bluffs, bad beats)
   - Integrates with the elasticity system to track emotional impact

2. **PressureStatsTracker** (`poker/pressure_stats.py`)
   - Maintains in-memory statistics for active games
   - Optionally persists events to database
   - Provides aggregated stats and leaderboards

3. **PressureEventEntity** and persistence (`poker/repositories/sqlite/emotional_state_repository.py`)
   - Handles database operations for pressure events
   - Supports querying by game or player
   - Provides aggregated statistics

4. **Database Schema** (`poker/persistence.py`)
   - `pressure_events` table stores individual events
   - Indexed for efficient querying
   - JSON storage for flexible event details

### Event Types

The system tracks the following pressure events:

- **win** - Any pot won
- **big_win** - Pot > 75% of average stack
- **big_loss** - Major pot lost
- **successful_bluff** - Won with weak hand
- **bluff_called** - Bluff attempt failed
- **bad_beat** - Lost with strong hand
- **eliminated_opponent** - Knocked out another player
- **fold_under_pressure** - Folded to aggression
- **aggressive_bet** - Made large bet/raise

## Implementation Details

### Database Schema

```sql
CREATE TABLE pressure_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    player_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    details_json TEXT,
    FOREIGN KEY (game_id) REFERENCES games(game_id)
);

-- Indices for performance
CREATE INDEX idx_pressure_events_game ON pressure_events(game_id);
CREATE INDEX idx_pressure_events_player ON pressure_events(player_name);
CREATE INDEX idx_pressure_events_type ON pressure_events(event_type);
```

### Usage Example

```python
# Initialize with persistence
event_repo = PressureEventRepository(db_path)
pressure_stats = PressureStatsTracker(game_id, event_repo)

# Events are automatically saved when recorded
pressure_stats.record_event("big_win", ["Alice"], {
    "pot_size": 1000,
    "hand_rank": 8,
    "hand_name": "Full House"
})

# Stats persist across sessions
new_tracker = PressureStatsTracker(game_id, event_repo)
# Automatically loads existing events from database
```

### API Integration

The Flask backend provides pressure stats via REST endpoint:

```
GET /api/game/<game_id>/pressure-stats

Response:
{
    "session_duration": 45,
    "total_events": 23,
    "biggest_pot": 2500,
    "player_summaries": {
        "Alice": {
            "wins": 5,
            "big_wins": 2,
            "successful_bluffs": 1,
            "tilt_score": 0.3,
            "aggression_score": 0.6,
            "signature_move": "Master Bluffer"
        }
    },
    "leaderboards": {...},
    "fun_facts": [...]
}
```

## Features

### Persistent Storage
- Events survive server restarts
- Historical data accumulates across games
- Can query player stats across all games

### Real-time Updates
- Stats update immediately during gameplay
- Frontend polls for updates every 5 seconds
- WebSocket integration for instant updates

### Backward Compatibility
- Works without database (memory-only mode)
- Graceful degradation if persistence fails
- No breaking changes to existing code

### Player Analytics
- **Tilt Score**: Ratio of negative to positive events
- **Aggression Score**: Frequency of aggressive actions
- **Signature Moves**: Characteristic play style labels
- **Biggest Pots**: Track largest wins/losses

## UI Integration

The React frontend displays stats via the `PressureStats` component:
- Toggle visibility with "Show Stats" button
- Displays leaderboards and player cards
- Shows fun facts generated from stats
- Updates automatically during gameplay

## Future Enhancements

1. **Career Stats**: Track long-term player statistics
2. **Achievement System**: Unlock badges for milestones
3. **Replay System**: Recreate dramatic moments
4. **Advanced Analytics**: Deeper statistical analysis
5. **Export Features**: Download stats as CSV/JSON

## Development Notes

- Stats are designed for entertainment, not serious poker analysis
- Focus on dramatic moments rather than optimal play
- Personality traits affect how events are detected
- System integrates with elasticity for emotional tracking