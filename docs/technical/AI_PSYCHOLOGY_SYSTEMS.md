# AI Psychology Systems Architecture

## Overview

The AI player psychology architecture consists of **6 interconnected systems** that work together to create dynamic, emotionally-responsive AI poker players. These systems allow AI personalities to shift under game pressure while maintaining their core identity.

### Related Documentation
- [AI_PLAYER_SYSTEM.md](AI_PLAYER_SYSTEM.md) - Core AI player architecture
- [ELASTICITY_SYSTEM.md](ELASTICITY_SYSTEM.md) - Personality elasticity details
- [PERSONALITY_ELASTICITY.md](PERSONALITY_ELASTICITY.md) - Elasticity configuration

---

## System Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                         PSYCHOLOGY SYSTEMS                             │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │                    PlayerPsychology (Orchestrator)               │  │
│  │                         poker/player_psychology.py               │  │
│  │                                                                  │
│  │  • apply_pressure_event()  → routes to elastic + tilt           │  │
│  │  • on_hand_complete()      → updates tilt, generates emotion    │  │
│  │  • get_prompt_section()    → builds emotional context           │  │
│  │  • apply_tilt_effects()    → modifies prompt under tilt         │  │
│  │  • recover()               → decay all systems toward baseline  │  │
│  └───────┬──────────────────────┬───────────────────────┬──────────┘  │
│          │                      │                       │              │
│          ▼                      ▼                       ▼              │
│  ┌───────────────┐    ┌─────────────────┐    ┌───────────────────┐   │
│  │ Elastic       │    │ EmotionalState  │    │ TiltState         │   │
│  │ Personality   │    │                 │    │                   │   │
│  ├───────────────┤    ├─────────────────┤    ├───────────────────┤   │
│  │ • aggression  │───►│ valence (-1,1)  │    │ • tilt_level 0-1  │   │
│  │ • bluff_tend  │    │ arousal (0,1)   │    │ • source (enum)   │   │
│  │ • chattiness  │    │ control (0,1)   │    │ • nemesis         │   │
│  │ • emoji_usage │    │ focus (0,1)     │    │ • losing_streak   │   │
│  ├───────────────┤    ├─────────────────┤    ├───────────────────┤   │
│  │ anchor+press  │    │ baseline+spike  │    │ prompt modifier   │   │
│  │ → current val │    │ → blended state │    │ → hide info       │   │
│  └───────────────┘    └─────────────────┘    └───────────────────┘   │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│                         DETECTION SYSTEMS                              │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  ┌─────────────────────┐          ┌─────────────────────┐             │
│  │ MomentAnalyzer      │          │ PressureEvent       │             │
│  │ (Pure Utility)      │          │ Detector            │             │
│  ├─────────────────────┤          ├─────────────────────┤             │
│  │ Detects:            │          │ Detects:            │             │
│  │ • all_in            │◄─────────│ • big_win/loss      │             │
│  │ • big_pot           │          │ • bad_beat          │             │
│  │ • huge_raise        │          │ • bluff_called      │             │
│  │ • showdown          │          │ • elimination       │             │
│  │ • heads_up          │          │                     │             │
│  ├─────────────────────┤          └─────────────────────┘             │
│  │ Returns:            │                                               │
│  │ • drama_level       │──────► Prompt intensity guidance              │
│  │ • emotional_tone    │                                               │
│  └─────────────────────┘                                               │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

---

## The Six Systems

### 1. PlayerPsychology (Orchestrator)
**File:** `poker/player_psychology.py`

The unified entry point that combines all psychological subsystems into a single interface.

**Responsibilities:**
- Delegate to subsystems in a coordinated way
- Generate emotional state after hands
- Apply tilt effects to prompts
- Build emotional state sections for prompts

**Key Methods:**
```python
psychology.apply_pressure_event(event_name, opponent)  # Updates elastic + tilt
psychology.on_hand_complete(outcome, amount, ...)      # Hand resolution
psychology.get_prompt_section()                        # Emotional context for prompt
psychology.apply_tilt_effects(prompt)                  # Tilt-based modifications
psychology.recover()                                   # Recovery phase
```

**Properties:** `traits`, `mood`, `tilt_level`, `is_tilted`, `is_severely_tilted`

---

### 2. ElasticPersonality
**File:** `poker/elasticity_manager.py`

Manages dynamic personality traits that shift under game pressure while maintaining core identity.

**Key Components:**
- `ElasticTrait`: Individual trait with anchor (base value), current value, elasticity (range), and pressure
- `ElasticPersonality`: Collection of elastic traits
- `ElasticityManager`: Orchestrator for all players' elastic traits

**Traits Managed:**
| Trait | Description | Typical Elasticity |
|-------|-------------|-------------------|
| `bluff_tendency` | Likelihood to bluff | 0.3 |
| `aggression` | Betting aggression | 0.5 |
| `chattiness` | How much they talk | 0.8 |
| `emoji_usage` | Emoji frequency | 0.2 |

**How Pressure Works:**
```python
# Pressure events modify traits
pressure_events = {
    "big_win": {"aggression": +0.2, "chattiness": +0.3, "bluff_tendency": +0.1},
    "big_loss": {"aggression": -0.3, "chattiness": -0.2, "emoji_usage": -0.1},
    "bad_beat": {"aggression": -0.4, "chattiness": -0.3},
    "successful_bluff": {"bluff_tendency": +0.2, "aggression": +0.1},
}

# When pressure exceeds threshold (0.25), trait changes:
# 1. change = pressure * elasticity
# 2. new_value = anchor + change
# 3. value = clamp(new_value, min, max)
# 4. pressure *= 0.5  (reduce after application)
```

---

### 3. EmotionalState
**File:** `poker/emotional_state.py`

Represents dimensional emotional state using a two-layer architecture.

**Dimensional Model:**
| Dimension | Range | Description |
|-----------|-------|-------------|
| `valence` | -1 to 1 | Negative ← → Positive feeling |
| `arousal` | 0 to 1 | Calm ← → Agitated |
| `control` | 0 to 1 | Losing grip ← → In command |
| `focus` | 0 to 1 | Tunnel vision ← → Clear-headed |

**Two-Layer Architecture:**
1. **Baseline**: Computed deterministically from elastic traits (slow-moving)
2. **Reactive Spike**: Computed from hand outcome (fast, decays back)
3. **Blended State**: Combination of baseline + spike
4. **LLM Narration**: Only the text (narrative, inner_voice) comes from LLM

**Key Methods:**
```python
compute_baseline_mood(traits)              # Baseline from elastic traits
compute_reactive_spike(outcome, amount)    # Spike from hand outcome
blend_emotional_state(baseline, spike)     # Combine layers
decay_toward_baseline(baseline, rate)      # Recovery between hands
get_display_emotion()                      # Maps to avatar (angry, nervous, happy, etc.)
```

---

### 4. TiltState & TiltPromptModifier
**File:** `poker/tilt_modifier.py`

Tracks tilt state and modifies AI behavior/prompts under tilt.

**Tilt Categories:**
| Category | Level Range | Effects |
|----------|-------------|---------|
| `none` | 0.0 - 0.2 | Normal play |
| `mild` | 0.2 - 0.4 | Intrusive thoughts injected |
| `moderate` | 0.4 - 0.7 | Strategy advice degraded, pot odds hidden |
| `severe` | 0.7 - 1.0 | All strategic advice removed |

**Tilt Effects on Prompts:**
- **0.2+**: Inject intrusive thoughts ("You can't believe that river card...")
- **0.3+**: Add tilted strategy advice
- **0.4+**: Degrade strategic guidance
- **0.5+**: Hide pot odds information
- **0.7+**: Remove all strategic advice

**Tilt Sources:** `bad_beat`, `bluff_called`, `losing_streak`, `nemesis`, `pressure`

---

### 5. PressureEventDetector
**File:** `poker/pressure_detector.py`

Detects game outcomes and emits events that trigger psychological reactions.

**Detectable Events:**
| Event | Trigger |
|-------|---------|
| `big_win` / `big_loss` | Significant pot wins or losses |
| `successful_bluff` / `bluff_called` | Bluffing outcomes |
| `eliminated_opponent` | When a player eliminates another |
| `bad_beat` | Strong hand loses to lucky draw |
| `friendly_chat` / `rivalry_trigger` | Chat-based interactions |

**Key Methods:**
```python
detector.detect_showdown_events(game_state, winner_info)
detector.detect_fold_events(game_state, folder)
detector.detect_elimination_events(game_state, eliminated)
detector.detect_chat_events(sender, message, recipients)
detector.apply_detected_events(events)
detector.apply_recovery()
```

---

### 6. MomentAnalyzer
**File:** `poker/moment_analyzer.py`

Determines dramatic significance of game moments for LLM context and psychology.

**Drama Factors:**
| Factor | Detection |
|--------|-----------|
| `all_in` | Player going all-in or desperate |
| `big_pot` | Pot significant vs stacks (>50%) |
| `big_bet` | Facing large bet (>10 BB) |
| `showdown` | All community cards dealt |
| `heads_up` | Only 2 players remain |
| `huge_raise` | Opponent 3x+ pot raise |
| `late_stage` | Tournament late stage |

**Drama Levels:**
| Level | Criteria |
|-------|----------|
| `climactic` | all_in OR (big_pot AND showdown) |
| `high_stakes` | 2+ factors |
| `notable` | 1 factor |
| `routine` | 0 factors |

**Emotional Tones:**
- `triumphant`: Climactic + strong hand (70%+ equity)
- `confident`: Notable+ + good hand (50%+ equity)
- `desperate`: Short stack OR weak hand (<30%) in high-stakes
- `neutral`: Default

---

## Data Flow

### Complete Hand Lifecycle

```
1. DECISION PHASE (before AI acts)
   ──────────────────────────────────

   AIPlayerController.get_action()
           │
           ├──► MomentAnalyzer.analyze()
           │         └─► Returns: {level: "high_stakes", tone: "desperate"}
           │
           ├──► psychology.get_prompt_section()
           │         └─► EmotionalState.to_prompt_section()
           │                └─► "I feel nervous but focused..."
           │
           ├──► psychology.apply_tilt_effects(prompt)
           │         └─► TiltPromptModifier.modify_prompt()
           │                ├─► Inject intrusive thoughts (tilt > 0.2)
           │                ├─► Degrade strategy advice (tilt > 0.4)
           │                └─► Hide pot odds (tilt > 0.5)
           │
           └──► Send prompt to LLM → Get decision


2. HAND COMPLETION PHASE (after showdown)
   ────────────────────────────────────────

   GameHandler.handle_showdown()
           │
           ├──► Detect events
           │         was_bad_beat = ...
           │         was_bluff_called = ...
           │         is_big_win = ...
           │
           ├──► For each affected player:
           │    psychology.apply_pressure_event(event, opponent)
           │         ├──► elastic.apply_pressure_event()
           │         │         └─► Modify trait pressures
           │         └──► tilt.apply_pressure_event()
           │                   └─► Adjust tilt level
           │
           └──► psychology.on_hand_complete(outcome, amount, ...)
                     │
                     ├──► tilt.update_from_hand(outcome, amount, opponent, ...)
                     │         └─► Fine-tune tilt based on specifics
                     │
                     └──► generate_emotional_state()
                               ├─► compute_baseline_mood(traits)  [deterministic]
                               ├─► compute_reactive_spike(outcome) [deterministic]
                               ├─► blend_emotional_state()         [deterministic]
                               └─► LLM narration (narrative text)  [API call]


3. RECOVERY PHASE (between hands)
   ────────────────────────────────

   psychology.recover()
           ├──► elastic.recover_traits(rate=0.1)    → Traits drift to anchor
           ├──► tilt.decay(amount=0.02)             → Tilt decays naturally
           └──► emotional.decay_toward_baseline()   → Spike fades to baseline
```

---

## Dependency Map

```
PlayerPsychology (Orchestrator)
    ├─ imports ElasticPersonality (from elasticity_manager.py)
    ├─ imports EmotionalState (from emotional_state.py)
    ├─ imports TiltState, TiltPromptModifier (from tilt_modifier.py)
    └─ uses compute_baseline_mood(), compute_reactive_spike(), blend_emotional_state()

ElasticityManager
    └─ owns ElasticPersonality instances
       └─ own ElasticTrait instances

EmotionalStateGenerator
    ├─ imports StructuredLLMCategorizer (from core module)
    └─ produces EmotionalState

TiltPromptModifier
    └─ takes TiltState and modifies prompts

PressureEventDetector
    ├─ imports ElasticityManager (owns reference)
    ├─ imports MomentAnalyzer (uses static methods)
    └─ imports HandEvaluator (to detect bad beats)

MomentAnalyzer
    └─ pure utility (static methods, no imports of psychology systems)

AIPlayerController
    ├─ creates PlayerPsychology
    ├─ uses MomentAnalyzer (static methods)
    └─ calls psychology methods for prompt building
```

**No circular dependencies** - clean hierarchy with MomentAnalyzer at the base.

---

## Known Issues & Technical Debt

### Critical Issues

#### 1. Dual Psychology Update Paths
**Problem:** Two separate code paths exist for updating psychology.

**Old Path** (`flask_app/routes/game_routes.py`):
```python
controller.tilt_state.apply_pressure_event(event_name, sender)
# Direct TiltState manipulation, bypasses PlayerPsychology
```

**New Path** (`flask_app/handlers/game_handler.py`):
```python
controller.psychology.apply_pressure_event(event_name, opponent)
# Uses PlayerPsychology orchestrator
```

**Impact:** Chat events use old path, hand events use new path - inconsistent state.

#### 2. PressureEventDetector Partially Unused
**Problem:** `PressureEventDetector` exists but event detection is done inline in `game_handler.py`.

**Impact:** Dead code, duplicated detection logic, no single source of truth.

### Moderate Issues

#### 3. Overlapping Trait Modification
Multiple systems can affect the same traits without clear precedence:
- `ElasticPersonality.apply_pressure_event()`
- `ElasticPersonality.apply_learned_adjustment()`
- `TiltState` effects (implicit via arousal mapping)
- Emotional spike affects trait baseline

#### 4. Redundant Mood Tracking
Both systems track mood:
- `ElasticityManager.get_current_mood()` → strings like "hopeless", "intense"
- `EmotionalState.get_display_emotion()` → "nervous", "happy", etc.

Unclear which is authoritative for frontend display.

#### 5. Hardcoded Configuration
Pressure events and intrusive thoughts are hardcoded in Python, not configurable.

---

## Recommendations

### Immediate (Critical)

1. **Consolidate update paths** - Route all psychology updates through `PlayerPsychology`
   ```python
   # Replace: controller.tilt_state.apply_pressure_event()
   # With:    controller.psychology.apply_pressure_event()
   ```

2. **Activate or deprecate PressureEventDetector** - Either use it in main flow or remove it

3. **Document trait mutation precedence** - Establish clear rules (e.g., tilt > elasticity > learning)

### Short-Term

4. **Consolidate mood tracking** - Use `EmotionalState.get_display_emotion()` exclusively

5. **Move events to JSON config** - Create `poker/pressure_events.json` like `personalities.json`

6. **Document recovery cycle** - When does `recover()` get called? Add assertions to verify.

### Medium-Term

7. **Add psychological state logging** - Track what changed and why for debugging

8. **Optional emotional narration** - Config flag to skip LLM call for narration

9. **Integration tests** - Test full hand → psychology flow end-to-end

---

## Usage Examples

### Accessing Current Psychology State

```python
# From AIPlayerController
controller = AIPlayerController(player, game_state)

# Get current trait values
traits = controller.psychology.traits
# → {'aggression': 0.7, 'bluff_tendency': 0.5, ...}

# Get current mood
mood = controller.psychology.mood
# → "confident"

# Check tilt status
if controller.psychology.is_tilted:
    print(f"Tilt level: {controller.psychology.tilt_level}")
```

### Applying Pressure Events

```python
# After detecting a bad beat
controller.psychology.apply_pressure_event('bad_beat', opponent_name)

# After hand completion
controller.psychology.on_hand_complete(
    outcome='loss',
    amount=500,
    opponent=winner_name,
    was_bad_beat=True,
    was_bluff_called=False
)

# Recovery between hands
controller.psychology.recover()
```

### Building Prompts with Psychology

```python
# Get emotional context for prompt
emotional_section = controller.psychology.get_prompt_section()
# → "Current emotional state: Feeling frustrated after that loss..."

# Apply tilt effects to prompt
modified_prompt = controller.psychology.apply_tilt_effects(base_prompt)
# → Prompt with intrusive thoughts, degraded strategy, etc.
```

---

## Testing

### Run Psychology Tests
```bash
python3 scripts/test.py test_elasticity
python3 scripts/test.py test_tilt
python3 scripts/test.py test_emotional
python3 scripts/test.py test_pressure
```

### Test Coverage Gaps
- No integration tests for full hand → psychology flow
- No tests for trait mutation ordering/precedence
- No tests verifying recovery cycle executes consistently

---

## Architectural Strengths

1. **Clear Separation of Concerns** - Each system has distinct responsibility
2. **No Circular Dependencies** - Clean import hierarchy
3. **Composition Over Inheritance** - PlayerPsychology elegantly combines subsystems
4. **Fallback Handling** - EmotionalState generator has fallback for LLM failures
5. **Serialization Support** - All systems can save/restore state
6. **Deterministic Baseline** - Emotional state baseline doesn't require LLM

---

## Future Enhancements

1. **Elastic Relationships** - How players affect each other's elasticity
2. **Permanent Changes** - Extreme events shifting anchor points
3. **Elastic Memory** - Past elasticity affecting future flexibility
4. **Compound Personalities** - Multiple elastic systems interacting
5. **Visual Indicators** - UI elements showing pressure and mood changes in real-time
