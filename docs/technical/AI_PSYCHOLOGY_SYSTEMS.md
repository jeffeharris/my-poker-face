# AI Psychology Systems Architecture

## Overview

The AI player psychology architecture consists of **5 interconnected systems** that work together to create dynamic, emotionally-responsive AI poker players. These systems allow AI personalities to shift under game pressure while maintaining their core identity.

**Recent Changes (2026-02-04):** The system now uses a **5-trait poker-native model** where composure is a trait rather than a separate tilt system. This simplifies the architecture while maintaining expressive capabilities.

### Related Documentation
- [AI_PLAYER_SYSTEM.md](AI_PLAYER_SYSTEM.md) - Core AI player architecture
- [ELASTICITY_SYSTEM.md](ELASTICITY_SYSTEM.md) - Personality elasticity details

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
│  │  • apply_pressure_event()  → routes to elastic (incl. composure)│  │
│  │  • on_hand_complete()      → updates composure, generates emotion│  │
│  │  • get_prompt_section()    → builds emotional context           │  │
│  │  • apply_composure_effects()→ modifies prompt when rattled      │  │
│  │  • recover()               → decay all systems toward baseline  │  │
│  └───────┬──────────────────────┬───────────────────────┬──────────┘  │
│          │                      │                       │              │
│          ▼                      ▼                       ▼              │
│  ┌───────────────┐    ┌─────────────────┐    ┌───────────────────┐   │
│  │ Elastic       │    │ EmotionalState  │    │ ComposureState    │   │
│  │ Personality   │    │                 │    │                   │   │
│  ├───────────────┤    ├─────────────────┤    ├───────────────────┤   │
│  │ • tightness   │───►│ valence (-1,1)  │    │ • pressure_source │   │
│  │ • aggression  │    │ arousal (0,1)   │    │ • nemesis         │   │
│  │ • confidence  │    │ control (0,1)   │    │ • losing_streak   │   │
│  │ • composure   │    │ focus (0,1)     │    │ • recent_losses   │   │
│  │ • table_talk  │    ├─────────────────┤    ├───────────────────┤   │
│  ├───────────────┤    │ baseline+spike  │    │ Tracks context    │   │
│  │ anchor+press  │    │ → blended state │    │ for intrusive     │   │
│  │ → current val │    │                 │    │ thoughts          │   │
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

## The Five Systems

### 1. PlayerPsychology (Orchestrator)
**File:** `poker/player_psychology.py`

The unified entry point that combines all psychological subsystems into a single interface.

**Responsibilities:**
- Delegate to subsystems in a coordinated way
- Generate emotional state after hands
- Apply composure-based effects to prompts
- Build emotional state sections for prompts

**Key Methods:**
```python
psychology.apply_pressure_event(event_name, opponent)  # Updates elastic traits (incl. composure)
psychology.on_hand_complete(outcome, amount, ...)      # Hand resolution
psychology.get_prompt_section()                        # Emotional context for prompt
psychology.apply_composure_effects(prompt)             # Composure-based modifications
psychology.recover()                                   # Recovery phase
```

**Properties:** `traits`, `mood`, `composure`, `tilt_level` (backward compat), `is_tilted`, `is_severely_tilted`

---

### 2. ElasticPersonality
**File:** `poker/elasticity_manager.py`

Manages dynamic personality traits that shift under game pressure while maintaining core identity.

**Key Components:**
- `ElasticTrait`: Individual trait with anchor (base value), current value, elasticity (range), and pressure
- `ElasticPersonality`: Collection of elastic traits (owned by `PlayerPsychology`)

**5-Trait Poker-Native Model:**
| Trait | Range | Description | Typical Elasticity |
|-------|-------|-------------|-------------------|
| `tightness` | 0 (loose) to 1 (tight) | Range selectivity | 0.3 |
| `aggression` | 0 (passive) to 1 (aggressive) | Bet frequency | 0.5 |
| `confidence` | 0 (scared) to 1 (fearless) | Sizing/commitment | 0.4 |
| `composure` | 0 (tilted) to 1 (focused) | Decision quality | 0.4 |
| `table_talk` | 0 (silent) to 1 (chatty) | Chat frequency | 0.6 |

**How Pressure Works:**
```python
# Pressure events modify traits (5-trait poker-native model)
pressure_events = {
    "big_win": {"confidence": +0.20, "composure": +0.15, "aggression": +0.10, "tightness": -0.05},
    "big_loss": {"confidence": -0.15, "composure": -0.25, "tightness": +0.10},
    "bad_beat": {"composure": -0.30, "confidence": -0.10, "tightness": +0.10},
    "successful_bluff": {"confidence": +0.20, "aggression": +0.15, "tightness": -0.10},
    "bluff_called": {"confidence": -0.20, "composure": -0.15, "aggression": -0.15},
}

# When pressure exceeds threshold (0.1):
# 1. immediate_change = pressure * elasticity * 0.3
# 2. accumulated_change = pressure * elasticity * 0.5
# 3. new_value = anchor + change
# 4. value = clamp(new_value, min, max)
# 5. pressure *= 0.7  (reduce after application)
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

### 4. ComposureState (Replaces TiltState)
**File:** `poker/player_psychology.py`

Tracks context for composure-based prompt modifications. Note: Composure **level** is now a trait in `ElasticPersonality`. `ComposureState` only tracks the **source** and **context** for intrusive thoughts.

**Tracked State:**
| Field | Description |
|-------|-------------|
| `pressure_source` | What caused the pressure (bad_beat, bluff_called, etc.) |
| `nemesis` | Player who caused the most recent pressure |
| `losing_streak` | Consecutive losses counter |
| `recent_losses` | Last 5 loss details for context |

**Composure Categories (from composure trait value):**
| Category | Composure Range | Tilt Equivalent | Effects |
|----------|-----------------|-----------------|---------|
| `focused` | 0.8 - 1.0 | `none` | Normal play |
| `alert` | 0.6 - 0.8 | `mild` | Intrusive thoughts injected |
| `rattled` | 0.4 - 0.6 | `moderate` | Strategy advice degraded |
| `tilted` | 0.0 - 0.4 | `severe` | Heavy strategy degradation |

**Composure Effects on Prompts:**
- **< 0.8 (alert)**: Inject intrusive thoughts ("You can't believe that river card...")
- **< 0.6 (rattled)**: Add tilted strategy advice
- **< 0.4 (tilted)**: Degrade strategic guidance, hide pot odds
- **< 0.4 + high aggression**: Add angry flair

**Pressure Sources:** `bad_beat`, `bluff_called`, `big_loss`, `got_sucked_out`, `losing_streak`, `nemesis`

---

### 5. PressureEventDetector
**File:** `poker/pressure_detector.py`

Detects game outcomes and returns events that trigger psychological reactions. This class is
**detection-only** - it does not apply events. Callers route events through `PlayerPsychology`.

**Detectable Events:**
| Event | Trigger | Composure Impact |
|-------|---------|------------------|
| `big_win` / `big_loss` | Significant pot wins or losses | +0.15 / -0.25 |
| `successful_bluff` / `bluff_called` | Bluffing outcomes | +0.10 / -0.15 |
| `eliminated_opponent` | When a player eliminates another | +0.10 |
| `bad_beat` | Strong hand loses to lucky draw | -0.30 |
| `got_sucked_out` | Was ahead but lost to luck | -0.35 |
| `friendly_chat` / `rivalry_trigger` | Chat-based interactions | +0.05 / -0.10 |

**Key Methods:**
```python
detector = PressureEventDetector()  # Stateless, no dependencies
events = detector.detect_showdown_events(game_state, winner_info)
events = detector.detect_fold_events(game_state, folder)
events = detector.detect_elimination_events(game_state, eliminated)
events = detector.detect_chat_events(sender, message, recipients)

# Caller applies events through PlayerPsychology:
for event_name, affected_players in events:
    for player_name in affected_players:
        controller.psychology.apply_pressure_event(event_name, opponent)
```

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
           ├──► psychology.apply_composure_effects(prompt)
           │         ├─► Inject intrusive thoughts (composure < 0.8)
           │         ├─► Degrade strategy advice (composure < 0.6)
           │         └─► Heavy degradation (composure < 0.4)
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
           │         └──► elastic.apply_pressure_event()
           │                   └─► Modify all trait pressures (incl. composure)
           │
           └──► psychology.on_hand_complete(outcome, amount, ...)
                     │
                     ├──► composure_state.update_from_hand(outcome, amount, opponent, ...)
                     │         └─► Track pressure source, nemesis, losing streak
                     │
                     └──► generate_emotional_state()
                               ├─► compute_baseline_mood(traits)  [deterministic]
                               ├─► compute_reactive_spike(outcome) [deterministic]
                               ├─► blend_emotional_state()         [deterministic]
                               └─► LLM narration (narrative text)  [API call]


3. RECOVERY PHASE (between hands)
   ────────────────────────────────

   psychology.recover()
           ├──► elastic.recover_traits(rate=0.1)    → All traits drift to anchor
           │         └─► Including composure trait    (composure recovers toward focused)
           └──► emotional.decay_toward_baseline()   → Spike fades to baseline
```

---

## Dependency Map

```
PlayerPsychology (Orchestrator)
    ├─ imports ElasticPersonality (from elasticity_manager.py)
    ├─ imports EmotionalState (from emotional_state.py)
    ├─ defines ComposureState (replaces TiltState)
    ├─ owns ElasticPersonality instance directly
    ├─ owns ComposureState instance
    └─ uses compute_baseline_mood(), compute_reactive_spike(), blend_emotional_state()

ElasticPersonality
    ├─ owns ElasticTrait instances (5 traits: tightness, aggression, confidence, composure, table_talk)
    └─ imports trait_converter for format detection/conversion

EmotionalStateGenerator
    ├─ imports StructuredLLMCategorizer (from core module)
    └─ produces EmotionalState

PressureEventDetector
    ├─ imports MomentAnalyzer (uses static methods)
    ├─ imports HandEvaluator (to detect bad beats)
    └─ stateless - returns events, does not apply them

MomentAnalyzer
    └─ pure utility (static methods, no imports of psychology systems)

AIPlayerController
    ├─ creates PlayerPsychology (which owns ElasticPersonality + ComposureState)
    ├─ uses MomentAnalyzer (static methods)
    └─ calls psychology methods for prompt building

trait_converter (poker/trait_converter.py)
    └─ pure utility - converts old 4-trait format to new 5-trait format
```

**No circular dependencies** - clean hierarchy with MomentAnalyzer and trait_converter at the base.

---

## Known Issues & Technical Debt

### Resolved Issues

#### ~~1. Dual Psychology Update Paths~~ (Fixed 2026-02-03)
**Problem:** Code referenced non-existent `controller.tilt_state` instead of `controller.psychology`.

**Resolution:** Updated all references to use `controller.psychology.apply_pressure_event()` and `controller.psychology.tilt` consistently. Chat events and hand events now both flow through `PlayerPsychology`.

#### ~~2. PressureEventDetector Partially Unused~~ (Fixed 2026-02-03)
**Problem:** `PressureEventDetector` existed with unused `apply_detected_events()` and `apply_recovery()` methods that updated a parallel `ElasticityManager` not used for AI decisions.

**Resolution:** Removed `ElasticityManager` class entirely. Made `PressureEventDetector` detection-only (returns events, doesn't apply them). All pressure events now route through `controller.psychology.apply_pressure_event()` for unified handling of both elastic traits and tilt state.

#### ~~3. Separate TiltState System~~ (Fixed 2026-02-04)
**Problem:** Tilt was tracked as a separate system (`TiltState` in `tilt_modifier.py`) alongside elastic traits, requiring dual updates and causing complexity.

**Resolution:** Composure is now a trait in the 5-trait elastic model. `ComposureState` only tracks context (pressure source, nemesis) for intrusive thoughts. `tilt_modifier.py` removed. All tilt-like effects flow through `PlayerPsychology.apply_composure_effects()`.

#### ~~4. Old 4-Trait Model~~ (Fixed 2026-02-04)
**Problem:** Old model used non-poker-native traits (bluff_tendency, chattiness, emoji_usage).

**Resolution:** Migrated to 5-trait poker-native model. `trait_converter.py` handles automatic conversion of old-format personalities.

### Remaining Issues

#### 1. Overlapping Trait Modification
Multiple mechanisms can affect the same traits:
- `ElasticPersonality.apply_pressure_event()` - game events
- `ElasticPersonality.apply_learned_adjustment()` - opponent adaptation

Clear precedence isn't documented - both apply pressure to the same traits.

#### 2. Redundant Mood Tracking
Both systems track mood:
- `ElasticPersonality.get_current_mood()` → strings like "hopeless", "intense"
- `EmotionalState.get_display_emotion()` → "nervous", "happy", etc.

`PlayerPsychology.mood` delegates to elastic personality. Frontend should use `controller.psychology.mood` or `controller.psychology.get_display_emotion()`.

#### 3. Hardcoded Configuration
Pressure events and intrusive thoughts are hardcoded in Python, not configurable via JSON.

---

## Recommendations

### Completed

1. ~~**Consolidate update paths**~~ - Done (2026-02-03)

2. ~~**Activate or deprecate PressureEventDetector**~~ - Done (2026-02-03). Made detection-only, removed ElasticityManager.

3. ~~**Unify tilt into trait system**~~ - Done (2026-02-04). Composure is now a trait.

4. ~~**Migrate to poker-native traits**~~ - Done (2026-02-04). 5-trait model with auto-conversion.

### Short-Term

5. **Document trait mutation precedence** - Establish clear rules for pressure events vs learned adjustments

6. **Consolidate mood tracking** - Use `EmotionalState.get_display_emotion()` exclusively

7. **Move events to JSON config** - Create `poker/pressure_events.json` like `personalities.json`

### Medium-Term

8. **Add psychological state logging** - Track what changed and why for debugging

9. **Optional emotional narration** - Config flag to skip LLM call for narration

10. **Integration tests** - Test full hand → psychology flow end-to-end

---

## Usage Examples

### Accessing Current Psychology State

```python
# From AIPlayerController
controller = AIPlayerController(player, game_state)

# Get current trait values (5-trait model)
traits = controller.psychology.traits
# → {'tightness': 0.5, 'aggression': 0.7, 'confidence': 0.6, 'composure': 0.8, 'table_talk': 0.5}

# Get individual traits
composure = controller.psychology.composure  # 0.8
tightness = controller.psychology.tightness  # 0.5
aggression = controller.psychology.aggression  # 0.7

# Get derived values
archetype = controller.psychology.archetype  # "TAG", "LAG", "Rock", or "Fish"
bluff_propensity = controller.psychology.bluff_propensity  # Derived from tightness + aggression

# Get current mood
mood = controller.psychology.mood
# → "confident"

# Check composure/tilt status
if controller.psychology.is_tilted:  # composure < 0.6
    print(f"Composure: {controller.psychology.composure}")  # 0.0-1.0
    print(f"Tilt level: {controller.psychology.tilt_level}")  # Backward compat: 1.0 - composure
    print(f"Category: {controller.psychology.composure_category}")  # focused/alert/rattled/tilted
```

### Applying Pressure Events

```python
# After detecting a bad beat
controller.psychology.apply_pressure_event('bad_beat', opponent_name)
# This updates all elastic traits, including composure

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
# All traits (including composure) drift back toward anchor values
```

### Building Prompts with Psychology

```python
# Get emotional context for prompt
emotional_section = controller.psychology.get_prompt_section()
# → "Current emotional state: Feeling frustrated after that loss..."

# Apply composure effects to prompt
modified_prompt = controller.psychology.apply_composure_effects(base_prompt)
# → Prompt with intrusive thoughts (if composure < 0.8), degraded strategy (if < 0.6), etc.

# Backward compatibility alias
modified_prompt = controller.psychology.apply_tilt_effects(base_prompt)  # Same as apply_composure_effects
```

---

## Testing

### Run Psychology Tests
```bash
python3 scripts/test.py test_elasticity      # Elastic personality tests
python3 scripts/test.py test_emotional       # Emotional state tests
python3 scripts/test.py test_pressure        # Pressure detector tests
python3 scripts/test.py test_psychology      # PlayerPsychology orchestrator tests
python3 scripts/test.py test_trait_converter # Trait format conversion tests
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
7. **Backward Compatibility** - Auto-converts old 4-trait personalities to 5-trait model
8. **Unified Composure** - Tilt is now a trait, simplifying the architecture

---

## Future Enhancements

1. **Elastic Relationships** - How players affect each other's elasticity
2. **Permanent Changes** - Extreme events shifting anchor points
3. **Elastic Memory** - Past elasticity affecting future flexibility
4. **Compound Personalities** - Multiple elastic systems interacting
5. **Visual Indicators** - UI elements showing pressure and mood changes in real-time
