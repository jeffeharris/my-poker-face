# Personality Elasticity System

## Overview

The elasticity system allows AI personalities to change dynamically during gameplay while maintaining their core identity. Each trait has a defined range of flexibility and responds to game events within those boundaries.

## Data Structure

```json
{
  "personality_traits": {
    "bluff_tendency": {
      "value": 0.8,          // Current value (0.0-1.0)
      "elasticity": 0.3,     // How much it can deviate
      "pressure": 0.15,      // Current pressure to change
      "anchor": 0.8,         // Original value (gravitates back)
      "min": 0.5,           // Minimum possible (anchor - elasticity)
      "max": 1.0            // Maximum possible (anchor + elasticity)
    },
    "aggression": {
      "value": 0.9,
      "elasticity": 0.5,     // More elastic than bluff
      "pressure": -0.2,      // Negative pressure
      "anchor": 0.9,
      "min": 0.4,
      "max": 1.0
    },
    "chattiness": {
      "value": 0.6,
      "elasticity": 0.8,     // Very elastic
      "pressure": 0.0,
      "anchor": 0.6,
      "min": 0.0,
      "max": 1.0
    },
    "emoji_usage": {
      "value": 0.3,
      "elasticity": 0.2,     // Not very elastic
      "pressure": 0.0,
      "anchor": 0.3,
      "min": 0.1,
      "max": 0.5
    }
  },
  "mood_elasticity": 0.4,    // Overall mood flexibility
  "recovery_rate": 0.1       // How fast traits return to anchor
}
```

## Pressure System

### Pressure Events
Pressure accumulates from game events and affects trait values:

```python
pressure_events = {
    "big_win": {
        "aggression": +0.2,
        "chattiness": +0.3,
        "bluff_tendency": +0.1
    },
    "big_loss": {
        "aggression": -0.3,    # Might become passive
        "chattiness": -0.2,
        "emoji_usage": -0.1
    },
    "successful_bluff": {
        "bluff_tendency": +0.3,
        "aggression": +0.2
    },
    "bluff_called": {
        "bluff_tendency": -0.4,
        "aggression": -0.1
    },
    "friendly_chat": {
        "chattiness": +0.2,
        "emoji_usage": +0.1
    },
    "rivalry_trigger": {
        "aggression": +0.4,
        "bluff_tendency": +0.2
    }
}
```

### Pressure Application
```python
def apply_pressure(trait, pressure_amount):
    trait["pressure"] += pressure_amount
    
    # If pressure exceeds threshold, update value
    if abs(trait["pressure"]) > 0.25:
        # Calculate new value within elastic range
        change = trait["pressure"] * trait["elasticity"]
        new_value = trait["anchor"] + change
        
        # Clamp to min/max
        trait["value"] = max(trait["min"], min(trait["max"], new_value))
        
        # Reduce pressure after application
        trait["pressure"] *= 0.5
```

### Recovery Mechanism
```python
def recover_traits(personality, recovery_rate=0.1):
    """Traits slowly drift back to their anchor values"""
    for trait_name, trait in personality["personality_traits"].items():
        if trait["value"] != trait["anchor"]:
            # Move toward anchor
            diff = trait["anchor"] - trait["value"]
            trait["value"] += diff * recovery_rate
            
        # Decay pressure over time
        trait["pressure"] *= 0.9
```

## Mood Vocabulary System

Each personality has mood ranges based on their elasticity:

```json
{
  "Eeyore": {
    "confidence_moods": {
      "base": "pessimistic",
      "high_pressure": ["hopeless", "defeated", "miserable"],
      "low_pressure": ["pessimistic", "melancholy", "resigned"],
      "positive_pressure": ["doubtful", "uncertain"]  // Best case for Eeyore
    },
    "attitude_moods": {
      "base": "gloomy",
      "variations": ["depressed", "gloomy", "morose", "dejected"]
    }
  },
  "Donald Trump": {
    "confidence_moods": {
      "base": "supreme",
      "high_pressure": ["supreme", "unstoppable", "dominant"],
      "low_pressure": ["irritated", "frustrated", "angry"],
      "negative_pressure": ["vengeful", "determined", "aggressive"]
    },
    "attitude_moods": {
      "base": "domineering",
      "variations": ["boastful", "commanding", "aggressive", "confrontational"]
    }
  }
}
```

## Elasticity Profiles

Different personality archetypes have different elasticity patterns:

### The Rock (Low Elasticity)
```json
{
  "trait_elasticity": {
    "all_traits": 0.1,
    "mood_elasticity": 0.2
  },
  "description": "Barely changes regardless of events"
}
```

### The Volatile (High Elasticity)
```json
{
  "trait_elasticity": {
    "aggression": 0.9,
    "bluff_tendency": 0.8,
    "mood_elasticity": 0.9
  },
  "description": "Swings wildly based on game flow"
}
```

### The Tilter (Asymmetric Elasticity)
```json
{
  "trait_elasticity": {
    "aggression": {
      "positive": 0.2,  // Doesn't get more aggressive when winning
      "negative": 0.9   // Goes crazy when losing
    }
  }
}
```

## Implementation Notes

1. **Pressure Threshold**: Not every event immediately changes traits
2. **Compound Effects**: Multiple pressures can stack
3. **Personality Breaks**: Extreme pressure might cause dramatic shifts
4. **Elastic Fatigue**: Repeatedly stretched traits might become more rigid
5. **Social Influence**: Other players' moods affect elasticity

## UI Indicators

- **Pressure Gauge**: Visual indicator of building pressure
- **Trait Arrows**: Show which direction traits are moving
- **Mood Aura**: Color/animation reflecting current state
- **Breaking Point**: Warning when personality near dramatic shift

## Future Enhancements

1. **Elastic Relationships**: How much one player affects another's elasticity
2. **Permanent Changes**: Extreme events might shift anchor points
3. **Elastic Memory**: Past elasticity affects future flexibility
4. **Compound Personalities**: Multiple elastic systems interacting