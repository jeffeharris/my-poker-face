# Personality Elasticity System

> **Note (Feb 2026)**: This document describes the elasticity system. For the most up-to-date technical details, see [ELASTICITY_SYSTEM.md](ELASTICITY_SYSTEM.md) and [AI_PSYCHOLOGY_SYSTEMS.md](AI_PSYCHOLOGY_SYSTEMS.md).

## Overview

The elasticity system allows AI personalities to change dynamically during gameplay while maintaining their core identity. Each trait has a defined range of flexibility and responds to game events within those boundaries.

**Status**: ✅ Fully Implemented with 5-Trait Poker-Native Model (Feb 2026)

### 5-Trait Model (Current)
| Trait | Range | Description |
|-------|-------|-------------|
| **tightness** | 0 (loose) to 1 (tight) | Range selectivity |
| **aggression** | 0 (passive) to 1 (aggressive) | Bet frequency |
| **confidence** | 0 (scared) to 1 (fearless) | Sizing/commitment |
| **composure** | 0 (tilted) to 1 (focused) | Decision quality - replaces separate tilt system |
| **table_talk** | 0 (silent) to 1 (chatty) | Chat frequency |

Old 4-trait personalities (bluff_tendency, aggression, chattiness, emoji_usage) are auto-converted.

## How It Works

The personality elasticity system consists of four main components:

1. **Elastic Traits** - Individual personality traits that can vary within bounds
2. **Pressure Events** - Game situations that trigger trait changes
3. **Recovery Mechanism** - Gradual return to baseline personality
4. **Mood System** - Dynamic mood descriptions based on trait states

### Quick Example

When Gordon Ramsay loses a big pot:
- His `aggression` drops from 0.9 → 0.6 (becomes less aggressive)
- His `chattiness` drops from 0.7 → 0.5 (talks less)
- His mood changes from "intense" → "furious"
- Over the next few hands, traits gradually recover back to baseline
- If he wins the next big pot, traits might swing positive beyond baseline

## Data Structure (5-Trait Model)

```json
{
  "personality_traits": {
    "tightness": {
      "value": 0.5,          // Current value (0.0-1.0)
      "elasticity": 0.3,     // How much it can deviate
      "pressure": 0.0,       // Current pressure to change
      "anchor": 0.5,         // Original value (gravitates back)
      "min": 0.2,            // Minimum possible (anchor - elasticity)
      "max": 0.8             // Maximum possible (anchor + elasticity)
    },
    "aggression": {
      "value": 0.9,
      "elasticity": 0.5,     // High elasticity - swings with wins/losses
      "pressure": -0.2,      // Negative pressure
      "anchor": 0.9,
      "min": 0.4,
      "max": 1.0
    },
    "confidence": {
      "value": 0.7,
      "elasticity": 0.4,     // Moderate elasticity
      "pressure": 0.0,
      "anchor": 0.7,
      "min": 0.3,
      "max": 1.0
    },
    "composure": {
      "value": 0.8,
      "elasticity": 0.4,     // Affected by bad beats
      "pressure": -0.1,      // Pressure from losses
      "anchor": 0.8,
      "min": 0.4,
      "max": 1.0
    },
    "table_talk": {
      "value": 0.6,
      "elasticity": 0.6,     // High elasticity - varies with mood
      "pressure": 0.0,
      "anchor": 0.6,
      "min": 0.0,
      "max": 1.0
    }
  },
  "mood_elasticity": 0.4,    // Overall mood flexibility
  "recovery_rate": 0.1       // How fast traits return to anchor
}
```

> **Note**: Composure replaces the old separate tilt system. Low composure (< 0.6) triggers intrusive thoughts and prompt degradation.

## Pressure System

### Pressure Events (5-Trait Model)
Pressure accumulates from game events and affects all 5 traits:

```python
pressure_events = {
    # Win events boost confidence, composure, aggression; reduce tightness
    "big_win": {
        "confidence": +0.20,
        "composure": +0.15,
        "aggression": +0.10,
        "tightness": -0.05,    # Play looser after wins
        "table_talk": +0.15,
    },
    # Loss events reduce confidence/composure, increase tightness
    "big_loss": {
        "confidence": -0.15,
        "composure": -0.25,
        "tightness": +0.10,    # Tighten up after losses
        "table_talk": -0.10,
    },
    # Bluff outcomes affect confidence and aggression
    "successful_bluff": {
        "confidence": +0.20,
        "composure": +0.10,
        "aggression": +0.15,
        "tightness": -0.10,
        "table_talk": +0.10,
    },
    "bluff_called": {
        "confidence": -0.20,
        "composure": -0.15,
        "aggression": -0.15,
        "tightness": +0.15,
        "table_talk": -0.05,
    },
    # Bad beats heavily affect composure (tilting)
    "bad_beat": {
        "composure": -0.30,
        "confidence": -0.10,
        "tightness": +0.10,
        "table_talk": -0.15,
    },
    "got_sucked_out": {
        "composure": -0.35,    # Very tilting
        "confidence": -0.15,
        "table_talk": -0.15,
    },
    # Social events
    "friendly_chat": {
        "table_talk": +0.10,
        "composure": +0.05,
    },
    "rivalry_trigger": {
        "aggression": +0.15,
        "composure": -0.10,
        "table_talk": +0.10,
    },
    # Elimination events
    "eliminated_opponent": {
        "confidence": +0.15,
        "composure": +0.10,
        "aggression": +0.10,
        "table_talk": +0.15,
    },
}
```

### Pressure Application
```python
def apply_pressure(trait, pressure_amount):
    trait["pressure"] += pressure_amount
    
    # Always apply some immediate effect for dramatic moments
    immediate_change = pressure_amount * trait["elasticity"] * 0.3
    trait["value"] = max(trait["min"], min(trait["max"], trait["value"] + immediate_change))
    
    # If pressure exceeds threshold, apply additional effects
    if abs(trait["pressure"]) > 0.1:  # Lower threshold for more responsive changes
        # Calculate additional change from accumulated pressure
        change = trait["pressure"] * trait["elasticity"] * 0.5
        new_value = trait["anchor"] + change
        
        # Clamp to min/max
        trait["value"] = max(trait["min"], min(trait["max"], new_value))
        
        # Reduce pressure after application
        trait["pressure"] *= 0.7
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

## Implementation Details

### Architecture

The elasticity system is implemented across several modules:

1. **`poker/elasticity_manager.py`**
   - `ElasticTrait`: Individual trait with value, anchor, elasticity, and pressure
   - `ElasticPersonality`: Collection of 5 elastic traits, owned by `PlayerPsychology`
   - Auto-converts old 4-trait format to new 5-trait model

2. **`poker/player_psychology.py`**
   - `PlayerPsychology`: Orchestrator that owns `ElasticPersonality`
   - `ComposureState`: Tracks pressure source, nemesis for intrusive thoughts
   - `apply_composure_effects()`: Modifies prompts based on composure trait

3. **`poker/trait_converter.py`**
   - `detect_trait_format()`: Identifies old vs new trait format
   - `convert_old_to_new_traits()`: Converts 4-trait to 5-trait model
   - `convert_tilt_to_composure()`: Maps tilt level to composure

4. **`poker/pressure_detector.py`**
   - `PressureEventDetector`: Detects game events (detection-only, stateless)
   - Events are routed through `controller.psychology.apply_pressure_event()`

5. **`poker/pressure_stats.py`**
   - `PressureStatsTracker`: Tracks statistics and creates leaderboards
   - Generates fun facts and player signature moves

### Event Detection

The system detects these key moments:

1. **Showdown Events**
   - Big wins/losses (pot > 1.5x average stack)
   - Successful bluffs (weak hand wins with others folding)
   - Bad beats (strong hand loses to stronger hand)

2. **Action Events**
   - Fold under pressure (folding when pot > 100 chips)
   - Aggressive betting (bet > 75% of pot)

3. **Game Flow Events**
   - Player eliminations
   - Chat interactions (friendly or aggressive)

### Integration Points

1. **AI Decision Making**
   - Elastic trait values affect AI behavior
   - Higher aggression → more likely to bet/raise
   - Lower bluff_tendency → more honest play

2. **Chat Responses**
   - Mood affects tone and word choice
   - Chattiness affects message frequency
   - Emoji usage varies with trait value

3. **Real-time Updates**
   - WebSocket events push elasticity changes
   - Debug panel shows live trait values
   - Stats panel tracks dramatic moments

### UI Components

1. **Elasticity Debug Panel** (`ElasticityDebugPanel.tsx`)
   - Shows current trait values with color coding
   - Displays elasticity ranges and anchor points
   - Updates in real-time via WebSocket

2. **Pressure Stats Panel** (`PressureStats.tsx`)
   - Leaderboards for various achievements
   - Player cards with signature moves
   - Fun facts about dramatic moments

## Future Enhancements

1. **Elastic Relationships**: How much one player affects another's elasticity
2. **Permanent Changes**: Extreme events might shift anchor points
3. **Elastic Memory**: Past elasticity affects future flexibility
4. **Compound Personalities**: Multiple elastic systems interacting
5. **Tournament Mode**: Elasticity persists across multiple games
6. **Rivalry System**: Special elasticity between specific players

## Integration with Enhanced Prompt System

The elasticity system forms the foundation for advanced AI behavior improvements:

### Trait-Influenced Language Generation
Elastic trait values directly influence how AI players express themselves:
- **High aggression (0.8-1.0)**: "I'm crushing this table" / "That's a pathetic bet"
- **Low aggression (0.0-0.3)**: "I'll just call" / "Maybe I should fold"
- **Dynamic bluff tendency**: Affects certainty in statements
- **Mood-based vocabulary**: Current emotional state colors word choices

### Contextual Memory Integration
Elasticity changes create memorable moments that persist:
- Dramatic trait swings become long-term memories
- Recovery patterns influence future elasticity
- Personality "learns" from extreme pressure events
- Memory of past elasticity affects current flexibility

### Emotional State Evolution (Implemented)
Elastic traits now directly drive the avatar emotional state through a two-layer model:

**Layer 1 — Baseline Mood**: `compute_baseline_mood()` deterministically maps current elastic
trait values to emotional dimensions (valence, arousal, control, focus). This is the
slow-moving "session mood" that reflects accumulated pressure and recovery. When traits
drift above anchor (winning streak), valence rises; when they drift below (losing streak),
the player's baseline mood darkens.

**Layer 2 — Reactive Spike**: `compute_reactive_spike()` produces a fast emotional shift from
each hand outcome, amplified by tilt level. Big wins spike positive valence; bad beats
spike negative valence + high arousal. The spike decays toward the elastic baseline between
hands via `decay_toward_baseline()`.

**Result**: Avatar emotions track the session arc (via elastic traits) with reactive bursts
from dramatic hands. The LLM is called only to narrate the computed dimensions — it produces
personality-authentic text, not the dimensional values themselves.

Future ideas:
- Emotional contagion between players at the table
- Permanent anchor shifts from extreme pressure events

### Meta-Strategy Awareness
Elastic traits adapt to game context:
- Tournament pressure increases trait volatility
- Stack size affects elasticity thresholds
- Table dynamics influence recovery rates
- Past sessions affect starting elasticity values

This creates a unified system where personality elasticity drives natural language variation, memory formation, emotional evolution, and strategic adaptation.