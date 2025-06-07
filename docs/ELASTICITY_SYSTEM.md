# Personality Elasticity System

## Recent Updates (2025-01-06)

### Personality-Specific Elasticity
The elasticity system now supports personality-specific elasticity configurations:

1. **Database Integration**: 
   - Added `elasticity_config` column to personalities table
   - Each personality can have custom elasticity values for traits

2. **UI Support**:
   - Personality Manager shows elasticity ranges for each trait
   - Visual indicators display min/max bounds based on elasticity
   - Sliders to adjust elasticity per trait

3. **Automatic Generation**:
   - AI personality generator includes elasticity in output
   - Script to generate elasticity for existing personalities
   - Smart defaults based on trait extremeness

4. **ElasticityManager Updates**:
   - Now reads elasticity from personality config
   - No more hardcoded values for all personalities
   - Proper integration with persistence layer

## Overview

The Personality Elasticity System allows AI personalities in My Poker Face to dynamically change during gameplay while maintaining their core identity. Each personality trait has a defined range of flexibility and responds to game events within those boundaries, creating more realistic and engaging AI opponents.

## Architecture

### Core Components

#### 1. **ElasticTrait** (`poker/elasticity_manager.py`)
Represents a single personality trait that can change within defined bounds.

```python
@dataclass
class ElasticTrait:
    value: float        # Current value (0.0-1.0)
    anchor: float       # Original/baseline value
    elasticity: float   # How much it can deviate
    pressure: float     # Current pressure to change
```

Key properties:
- `min`: Minimum possible value (anchor - elasticity, clamped to 0.0)
- `max`: Maximum possible value (anchor + elasticity, clamped to 1.0)

#### 2. **ElasticPersonality** (`poker/elasticity_manager.py`)
Manages all elastic traits for a single AI personality.

Features:
- Tracks multiple traits (bluff_tendency, aggression, chattiness, emoji_usage)
- Applies pressure events to modify traits
- Manages mood vocabulary based on pressure levels
- Supports trait recovery toward anchor values

#### 3. **ElasticityManager** (`poker/elasticity_manager.py`)
Coordinates elasticity for all AI players in a game.

```python
manager = ElasticityManager()
manager.add_player(name, personality_config)
manager.apply_game_event("big_win", ["PlayerName"])
manager.recover_all()
```

#### 4. **PressureEventDetector** (`poker/pressure_detector.py`)
Detects game events and triggers appropriate pressure changes.

Detectable events:
- `big_win` / `big_loss`: Significant pot wins or losses
- `successful_bluff` / `bluff_called`: Bluffing outcomes
- `eliminated_opponent`: When a player eliminates another
- `bad_beat`: Strong hand loses to lucky draw
- `friendly_chat` / `rivalry_trigger`: Chat-based interactions

## How It Works

### 1. Pressure System

Pressure accumulates from game events and affects trait values:

```python
pressure_events = {
    "big_win": {
        "aggression": +0.2,
        "chattiness": +0.3,
        "bluff_tendency": +0.1
    },
    "big_loss": {
        "aggression": -0.3,
        "chattiness": -0.2,
        "emoji_usage": -0.1
    }
}
```

When pressure exceeds a threshold (default 0.25), the trait value changes:
1. Calculate change: `change = pressure * elasticity`
2. Update value: `new_value = anchor + change`
3. Clamp to bounds: `value = clamp(new_value, min, max)`
4. Reduce pressure: `pressure *= 0.5`

### 2. Recovery Mechanism

Traits gradually return to their anchor values:
- Each recovery cycle moves the trait 10% closer to its anchor
- Pressure decays by 10% each cycle
- Recovery typically applied between hands

### 3. Mood Vocabulary

Each personality has mood ranges based on their elasticity:

```json
{
  "Eeyore": {
    "confidence_moods": {
      "base": "pessimistic",
      "high_pressure": ["hopeless", "defeated", "miserable"],
      "low_pressure": ["pessimistic", "melancholy", "resigned"],
      "positive_pressure": ["doubtful", "uncertain"]
    }
  },
  "Gordon Ramsay": {
    "confidence_moods": {
      "base": "intense",
      "high_pressure": ["furious", "explosive", "volcanic"],
      "low_pressure": ["intense", "focused", "critical"],
      "positive_pressure": ["passionate", "energized", "fierce"]
    }
  }
}
```

## Integration with Game Flow

### 1. During Hand Evaluation
```python
# In evaluating_hand_transition
winner_info = determine_winner(game_state)
events = pressure_detector.detect_showdown_events(game_state, winner_info)
pressure_detector.apply_detected_events(events)
```

### 2. During Player Actions
```python
# In AIPlayerController
controller.ai_player.update_mood_from_elasticity()
current_traits = controller.get_current_personality_traits()
```

### 3. Between Hands
```python
# Apply recovery
elasticity_manager.recover_all()
```

## Usage Examples

### Basic Setup
```python
from poker.elasticity_manager import ElasticityManager
from poker.pressure_detector import PressureEventDetector

# Initialize
elasticity_manager = ElasticityManager()
pressure_detector = PressureEventDetector(elasticity_manager)

# Add players
for player in game_state.players:
    elasticity_manager.add_player(
        player.name,
        personality_config
    )
```

### Detecting Events
```python
# After showdown
events = pressure_detector.detect_showdown_events(game_state, winner_info)
pressure_detector.apply_detected_events(events)

# After chat
events = pressure_detector.detect_chat_events(sender, message, recipients)
pressure_detector.apply_detected_events(events)
```

### Accessing Current State
```python
# Get current trait values
traits = elasticity_manager.get_player_traits("Gordon Ramsay")
mood = elasticity_manager.get_player_mood("Gordon Ramsay")

# Get pressure summary
summary = pressure_detector.get_pressure_summary()
```

## Persistence

The elasticity system fully supports serialization:

```python
# Save state
elasticity_data = elasticity_manager.to_dict()

# Restore state
elasticity_manager = ElasticityManager.from_dict(elasticity_data)
```

AI players automatically save/restore their elastic personality:
```python
# In AIPokerPlayer.to_dict()
"elastic_personality": self.elastic_personality.to_dict()

# In AIPokerPlayer.from_dict()
instance.elastic_personality = ElasticPersonality.from_dict(data['elastic_personality'])
```

## Configuration

### Default Elasticity Values
```python
default_elasticities = {
    'bluff_tendency': 0.3,
    'aggression': 0.5,
    'chattiness': 0.8,
    'emoji_usage': 0.2
}
```

### Customizing Elasticity
You can provide custom elasticity configurations:

```python
elasticity_config = {
    'trait_elasticity': {
        'aggression': 0.9,  # Very elastic
        'bluff_tendency': 0.1  # Not very elastic
    },
    'mood_elasticity': 0.6,
    'recovery_rate': 0.15
}
```

## Testing

Run the elasticity tests:
```bash
python -m pytest tests/test_elasticity.py -v
```

Run the demos:
```bash
python simple_elasticity_demo.py  # Basic trait demonstration
python elasticity_demo.py          # Full game integration
python test_elasticity_integration.py  # Integration tests
```

## Future Enhancements

1. **Elastic Relationships**: How players affect each other's elasticity
2. **Permanent Changes**: Extreme events shifting anchor points
3. **Elastic Memory**: Past elasticity affecting future flexibility
4. **Compound Personalities**: Multiple elastic systems interacting
5. **Visual Indicators**: UI elements showing pressure and mood changes