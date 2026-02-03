# Personality Elasticity System

## Recent Updates

### 2026-02-03: Removed ElasticityManager
- **Removed** `ElasticityManager` class - was a redundant wrapper
- `ElasticPersonality` is now owned directly by `PlayerPsychology`
- `PressureEventDetector` is now detection-only (returns events, doesn't apply them)
- All pressure events route through `controller.psychology.apply_pressure_event()`

### 2025-01-06: Personality-Specific Elasticity
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

4. **PlayerPsychology Integration**:
   - Psychology reads elasticity from personality config
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
Manages all elastic traits for a single AI personality. Owned by `PlayerPsychology`.

Features:
- Tracks multiple traits (bluff_tendency, aggression, chattiness, emoji_usage)
- Applies pressure events to modify traits
- Manages mood vocabulary based on pressure levels
- Supports trait recovery toward anchor values

```python
# Create from personality config
personality = ElasticPersonality.from_base_personality(name, personality_config)

# Apply pressure events
personality.apply_pressure_event("big_win")

# Recovery between hands
personality.recover_traits(rate=0.1)
```

#### 3. **PressureEventDetector** (`poker/pressure_detector.py`)
Detects game events and returns pressure events. Detection-only - does not apply events.

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
# In game_handler.handle_pressure_events()
events = pressure_detector.detect_showdown_events(game_state, winner_info)

# Route events through PlayerPsychology
for event_name, affected_players in events:
    for player_name in affected_players:
        if player_name in ai_controllers:
            controller = ai_controllers[player_name]
            opponent = winner if player_name != winner else None
            controller.psychology.apply_pressure_event(event_name, opponent)
```

### 2. During Player Actions
```python
# In AIPlayerController
current_traits = controller.psychology.traits  # From elastic personality
mood = controller.psychology.mood
```

### 3. Between Hands
```python
# Apply recovery through PlayerPsychology
controller.psychology.recover()  # Recovers elastic traits + tilt + emotional state
```

## Usage Examples

### Basic Setup
```python
from poker.pressure_detector import PressureEventDetector
from poker.controllers import AIPlayerController

# Initialize detector (stateless, no dependencies)
pressure_detector = PressureEventDetector()

# Players get elastic personality through AIPlayerController
# which creates PlayerPsychology (which owns ElasticPersonality)
controller = AIPlayerController(player_name, state_machine, ...)
# controller.psychology.elastic is the ElasticPersonality instance
```

### Detecting and Applying Events
```python
# After showdown - detect events
events = pressure_detector.detect_showdown_events(game_state, winner_info)

# Apply events through PlayerPsychology
for event_name, affected_players in events:
    for player_name in affected_players:
        if player_name in ai_controllers:
            controller = ai_controllers[player_name]
            controller.psychology.apply_pressure_event(event_name, opponent=None)

# After chat
events = pressure_detector.detect_chat_events(sender, message, recipients)
for event_name, affected_players in events:
    for player_name in affected_players:
        if player_name in ai_controllers:
            controller.psychology.apply_pressure_event(event_name, opponent=None)
```

### Accessing Current State
```python
# Get current trait values from PlayerPsychology
traits = controller.psychology.traits  # {'aggression': 0.7, 'bluff_tendency': 0.5, ...}
mood = controller.psychology.mood      # "intense", "hopeless", etc.

# Direct access to elastic personality
elastic = controller.psychology.elastic
trait_value = elastic.get_trait_value('aggression')
```

## Persistence

The elasticity system fully supports serialization through PlayerPsychology:

```python
# Save state (through controller)
elastic_data = controller.psychology.elastic.to_dict()

# Restore state
from poker.elasticity_manager import ElasticPersonality
controller.psychology.elastic = ElasticPersonality.from_dict(elastic_data)
```

AI controllers automatically save/restore their elastic personality through PlayerPsychology.

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