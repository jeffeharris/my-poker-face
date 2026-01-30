# AI Player System Documentation

## Overview

The AI Player System in My Poker Face creates dynamic, personality-driven poker opponents using OpenAI's language models. Each AI player has a unique personality that influences their playing style, communication, and decision-making process.

## Architecture

```
┌─────────────────────┐
│   Web/Console UI    │
└──────────┬──────────┘
           │
┌──────────▼──────────┐
│  AIPlayerController │ ← Orchestrates decisions
└──────────┬──────────┘
           │
┌──────────▼──────────┐     ┌────────────────────┐
│    AIPokerPlayer    │────►│ PersonalityGenerator│
└──────────┬──────────┘     └────────────────────┘
           │                           │
┌──────────▼──────────┐     ┌─────────▼──────────┐
│   PromptManager     │     │   Database/JSON    │
└──────────┬──────────┘     └────────────────────┘
           │
┌──────────▼──────────┐
│ OpenAILLMAssistant  │
└─────────────────────┘
```

## Core Components

### 1. AIPokerPlayer (`poker/poker_player.py`)

The main AI player class that extends the base PokerPlayer with AI capabilities.

**Key Features:**
- Maintains personality state (confidence, attitude)
- Integrates with OpenAI through an assistant
- Tracks conversation history for consistent behavior

**Example Initialization:**
```python
ai_player = AIPokerPlayer(name="Gordon Ramsay", starting_money=10000)
# Automatically loads/generates personality
# Sets up OpenAI assistant with character prompt
```

### 2. PersonalityGenerator (`poker/personality_generator.py`)

Manages personality creation and storage with a smart lookup hierarchy:

1. **Session Cache** - In-memory for current game
2. **Database** - Persistent storage across games
3. **JSON File** - Pre-defined personalities
4. **AI Generation** - Dynamic creation if not found

**Personality Structure:**
```json
{
  "play_style": "aggressive and confrontational",
  "default_confidence": "supreme",
  "default_attitude": "intense",
  "personality_traits": {
    "bluff_tendency": 0.7,     // 0-1 scale
    "aggression": 0.9,          // 0-1 scale
    "chattiness": 0.8,          // 0-1 scale
    "emoji_usage": 0.2          // 0-1 scale
  },
  "verbal_tics": [
    "This hand is RAW!",
    "You donkey!",
    "SHUT IT DOWN!"
  ],
  "physical_tics": [
    "*slams fist on table*",
    "*points aggressively*"
  ]
}
```

### 3. PromptManager (`poker/prompt_manager.py`)

Centralizes all AI prompts for consistency and maintainability.

**Templates:**
- `poker_player` - Initial character definition
- `decision` - Per-turn decision making

**Response Format:**
The AI responds with structured JSON containing both game actions and roleplay elements:

```json
{
  // Thinking
  "inner_monologue": "These idiots don't know what hit them...",
  "hand_strategy": "analysis of cards and situation",
  "player_observations": {"john": "seems nervous"},
  "hand_strength": "strong",
  "bluff_likelihood": 75,

  // Decision
  "action": "raise",
  "raise_to": 200,

  // Reaction
  "dramatic_sequence": ["*slams chips on table*", "This pot is MINE! I raise $200!"]
}
```

### 4. AIPlayerController (`poker/controllers.py`)

Orchestrates the decision-making process:

1. **Game State Translation** - Converts global state to player perspective
2. **Context Building** - Includes recent actions, chat messages
3. **Prompt Rendering** - Uses templates with current data
4. **Response Validation** - Ensures all required fields present

## Decision Flow

### 1. Initialization Phase
```
Player Named "Gordon Ramsay" joins
    ↓
PersonalityGenerator checks:
    - Database? → Not found
    - personalities.json? → Not found
    - Generate via AI → Creates chef personality
    ↓
AIPokerPlayer initialized with:
    - Personality config
    - OpenAI assistant
    - Starting game state
```

### 2. Turn Decision Phase
```
Game requests AI decision
    ↓
AIPlayerController:
    - Builds game context
    - Summarizes recent actions
    - Renders decision prompt
    ↓
OpenAI processes with:
    - System prompt (character)
    - User prompt (game state)
    ↓
Returns structured JSON
    ↓
Controller extracts:
    - Game action (fold/call/raise)
    - Chat message
    - Physical actions
```

## Key Features

### Personality Persistence
- Personalities stored in SQLite database
- Reused across multiple games
- Usage statistics tracked

### Dynamic Behavior
AI adjusts based on:
- **Personality Traits**: High aggression → more raises
- **Game State**: Low chips → conservative play
- **Emotional State**: Confidence affects betting

### Rich Communication
- **Public Chat**: What everyone sees ("This hand is MINE!")
- **Inner Monologue**: Private thoughts ("Should I bluff here?")
- **Physical Actions**: Gestures and emotes ("*drums fingers*")

### Contextual Memory
- Maintains conversation history
- Remembers previous actions
- Consistent character portrayal

## Example Game Interaction

```
=== Pre-Flop ===
Gordon Ramsay (AI): 
- Cards: [K♠, K♥]
- Inner thought: "Finally, a bloody decent hand!"
- Says: "Let's turn up the heat! I raise $200!"
- Action: *slams chips decisively*

Eeyore (AI):
- Cards: [7♣, 2♦]
- Inner thought: "Oh bother, another terrible hand..."
- Says: "I suppose I'll fold. Not that it matters."
- Action: *sighs heavily*

=== Flop: [K♦, 8♣, 3♠] ===
Gordon Ramsay (AI):
- Inner thought: "Three kings! These donkeys are finished!"
- Says: "You call that a bet? PATHETIC! I raise $500!"
- Action: *pounds table triumphantly*
```

## Extending the System

### Adding New Personalities

1. **Via personalities.json:**
```json
{
  "personalities": {
    "Sherlock Holmes": {
      "play_style": "analytical and deductive",
      "default_confidence": "certain",
      "default_attitude": "aloof",
      "personality_traits": {
        "bluff_tendency": 0.4,
        "aggression": 0.5,
        "chattiness": 0.6,
        "emoji_usage": 0.0
      }
    }
  }
}
```

2. **Via AI Generation:**
Simply use a new name - the system will generate an appropriate personality automatically.

3. **Via Database:**
Personalities can be imported/exported or manually added to the SQLite database.

### Customizing Behavior

Modify these files to adjust AI behavior:
- `prompt_manager.py` - Change decision criteria or response format
- `personality_generator.py` - Adjust generation prompts
- `controllers.py` - Modify game state interpretation

## Performance Considerations

- **Caching**: Personalities cached in memory during games
- **API Calls**: One call per decision (typically 1-2 seconds)
- **Token Usage**: ~500-1000 tokens per decision
- **Persistence**: Full state can be saved/loaded

## Personality Elasticity Integration (NEW)

The AI Player System now includes dynamic personality traits that change during gameplay through the Elasticity System.

### How Elasticity Works with AI Players

1. **Trait Modification**: Each personality trait (bluff_tendency, aggression, etc.) can elastically change within defined bounds based on game events.

2. **Mood Updates**: The AI's confidence and attitude update based on accumulated pressure:
   ```python
   # In AIPokerPlayer
   ai_player.apply_pressure_event('big_loss')  # Reduces aggression
   ai_player.update_mood_from_elasticity()     # Updates confidence/attitude
   ```

3. **Dynamic Decisions**: The AIPlayerController uses current elastic trait values:
   ```python
   # Gets current trait values, not just base values
   traits = controller.get_current_personality_traits()
   ```

4. **Persistence**: Elastic personality state is fully serialized with the player.

### Example Elasticity Flow

```
Gordon Ramsay starts with aggression = 0.95
    ↓
Loses big pot → "big_loss" event
    ↓
Pressure applied: aggression -0.3
    ↓
Trait changes to 0.80 (within elasticity bounds)
    ↓
Mood updates from "intense" to "frustrated"
    ↓
AI decisions reflect lower aggression
    ↓
Over time, trait recovers toward 0.95
```

For full details, see [ELASTICITY_SYSTEM.md](ELASTICITY_SYSTEM.md).

## Future Enhancements

1. **Learning System**: Track win rates per personality
2. **Adaptive Difficulty**: Adjust skill based on player level
3. **Team Play**: Personalities that work together
4. ~~**Emotional Arcs**: Mood changes based on wins/losses~~ ✓ Implemented via Elasticity System
5. **Custom Personalities**: UI for players to design their own

## Debugging

Enable debug logging to see:
- Personality lookups
- Prompt generation
- AI responses
- Decision parsing

Check logs for `[PersonalityGenerator]` tags to trace personality loading.