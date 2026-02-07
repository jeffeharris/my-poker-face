# Plan: Poker-Native Psychology System

## Problem

The current psychology system is hard to understand and maintain:
- 4 elastic traits (aggression, bluff_tendency, chattiness, emoji_usage)
- Separate tilt state (0-1)
- 4 derived emotional dimensions (valence, arousal, control, focus)
- Avatar emotion derived from dimensions
- Multiple interacting systems with unclear ownership

## Proposal

Replace with **5 poker-native traits** that directly map to behavior:

```python
@dataclass
class PokerPsychology:
    # Strategy (how they play)
    tightness: float      # 0 = loose, 1 = tight → Range %
    aggression: float     # 0 = passive, 1 = aggressive → Bet frequency

    # Mental (how they feel)
    confidence: float     # 0 = scared, 1 = fearless → Sizing, commitment
    composure: float      # 0 = tilted, 1 = focused → Decision quality

    # Social (how they communicate)
    table_talk: float     # 0 = silent, 1 = chatty → Chat frequency
```

## Benefits

### 1. Direct Behavioral Mapping

**Current:** Traits → Dimensions → Emotion → Avatar (confusing chain)

**New:** Traits → Behavior (direct)

| Trait | Direct Output |
|-------|---------------|
| tightness | "Play top 22% of hands" |
| aggression | "Bet 75% of the time" |
| confidence | "Use large sizing" |
| composure | "Stick to your ranges" |
| table_talk | "Speak every 3-4 hands" |

### 2. Poker Archetypes Fall Out Naturally

```
tightness × aggression → Style

  TAG  = tight + aggressive
  LAG  = loose + aggressive
  Rock = tight + passive
  Fish = loose + passive
```

### 3. Concrete Range Guidance

```python
def get_preflop_range_pct(self) -> float:
    base = 0.50 - (self.tightness * 0.40)  # 10% to 50%
    return base + (self.confidence - 0.5) * 0.10
```

| Tightness | Range % | Hands |
|-----------|---------|-------|
| 0.2 | 42% | Very loose |
| 0.5 | 30% | Average |
| 0.7 | 22% | Tight |
| 0.9 | 14% | Nit |

### 4. Emotion Derived from 2 Traits

```
                        FOCUSED (high composure)
                              │
         CAUTIOUS ────────────┼──────────── CONFIDENT
         (playing tight,      │            (in control,
          waiting)            │             comfortable)
                              │
LOW ──────────────────────────┼────────────────────── HIGH
CONFIDENCE                    │                   CONFIDENCE
                              │
         DEFEATED ────────────┼──────────── MANIC
         (giving up,          │            (reckless,
          passive tilt)       │             aggressive tilt)
                              │
                        TILTED (low composure)
```

| Composure | Confidence | Emotion | Avatar |
|-----------|------------|---------|--------|
| High | High | confident | 😎 |
| High | Low | cautious | 🤔 |
| Low | High | manic | 🤪 |
| Low | Low | defeated | 😞 |

### 5. Tilt is the Entire Bottom Half

No separate tilt system. `composure` IS the inverse of tilt. **All low-composure states are "tilted"** - they just manifest differently based on confidence:

| Composure | Confidence | Tilt Type | Behavior |
|-----------|------------|-----------|----------|
| Low | High | **Manic tilt** | Overaggressive, reckless, "I can't lose" |
| Low | Mid | **Steaming tilt** | Angry, frustrated, revenge mode |
| Low | Low | **Defeated tilt** | Passive, giving up, "why bother" |

This maps to real poker psychology:
- **Manic tilt**: Player on a heater who thinks they're invincible, overplays marginal hands
- **Steaming tilt**: Classic tilt after bad beat, revenge calls, chasing losses
- **Defeated tilt**: Player who's given up, just clicking buttons, not trying to win

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           POKER TRAITS                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  STRATEGY AXIS                          MENTAL AXIS                     │
│  ──────────────                         ────────────                    │
│                                                                         │
│  tightness ────► Range %                confidence ───► Bet sizing     │
│                  Continue thresholds                   Stack-off        │
│                                                                         │
│  aggression ───► Bet frequency          composure ────► Decision noise │
│                  Bluff frequency                       Discipline       │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  MENTAL AXIS (confidence × composure) ───► EMOTION ───► AVATAR         │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  SOCIAL AXIS                                                            │
│                                                                         │
│  table_talk ───► Chat frequency, message length, emoji usage           │
│                  Modified by emotion (angry → more trash talk)         │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Personality Anchors

Each character has anchor values + elasticity:

```python
@dataclass
class PokerPersonality:
    name: str
    anchors: Dict[str, float]      # Where traits return to
    elasticity: Dict[str, float]   # How far traits can move
    recovery_rate: float           # How fast they return

batman = PokerPersonality(
    name="Batman",
    anchors={
        "tightness": 0.6,
        "aggression": 0.7,
        "confidence": 0.85,
        "composure": 0.9,
        "table_talk": 0.3,
    },
    elasticity={
        "tightness": 0.3,
        "aggression": 0.2,
        "confidence": 0.15,   # Rarely shaken
        "composure": 0.1,     # Almost never tilts
        "table_talk": 0.2,
    },
    recovery_rate=0.15,
)
```

---

## Prompt Guidance

### Preflop

```
## Your Playing Style
- Range: top 22% of hands from Button
- When you play: raise 75%, call 25%
- Bet sizing: large (75-100% pot)

## This Hand
- Your hand: AJs (top 5%)
- ✓ IN your range — play this hand aggressively
```

### Postflop

```
## Post-Flop Situation
- Your hand: strong (top pair, good kicker)
- Board: dry (K♠ 7♦ 2♣)
- Position: Button (in position)

## Your Style Says
- With strong on dry board, you bet 80% of the time
- Recommended: BET
- Sizing: standard (50-66% pot)
```

---

## Pressure Events

Events modify traits the same way, just with clearer meanings:

| Event | tightness | aggression | confidence | composure | table_talk |
|-------|-----------|------------|------------|-----------|------------|
| big_win | -0.05 | +0.10 | +0.20 | +0.05 | +0.15 |
| bad_beat | +0.10 | +0.05 | -0.15 | -0.25 | -0.10 |
| bluff_called | +0.15 | -0.15 | -0.20 | -0.10 | -0.05 |
| successful_bluff | -0.10 | +0.15 | +0.15 | +0.05 | +0.10 |
| suckout | -0.15 | +0.10 | +0.25 | 0 | +0.20 |
| got_sucked_out | +0.10 | 0 | -0.10 | -0.30 | -0.15 |

---

## Migration Path

### Phase 1: New Trait System
1. Create `poker/poker_psychology.py` with new 5-trait model
2. Add `get_preflop_range_pct()`, `get_player_style()`, etc.
3. Add emotion mapping from confidence × composure

### Phase 2: Prompt Integration
1. Create range guidance generator
2. Create postflop guidance generator
3. Integrate into AI prompt building

### Phase 3: Replace Old System
1. Migrate personality anchors to new format
2. Update pressure event effects
3. Remove old elastic traits, emotional dimensions
4. Remove separate tilt system (composure replaces it)

### Phase 4: UI Updates
1. Display player style (TAG/LAG/etc.)
2. Show range % in debug panel
3. Update avatar emotion mapping

---

## Files to Create/Modify

| File | Action |
|------|--------|
| `poker/poker_psychology.py` | Create: new 5-trait system |
| `poker/range_guidance.py` | Create: preflop/postflop advice |
| `poker/player_style.py` | Create: archetype classification |
| `personalities.json` | Modify: new anchor format |
| `poker/elasticity_manager.py` | Replace: use new traits |
| `poker/tilt_modifier.py` | Remove: composure replaces tilt |
| `poker/emotional_state.py` | Simplify: derive from 2 traits |
| `poker/player_psychology.py` | Simplify: single source of truth |

---

## Success Metrics

1. **Simpler mental model**: 5 traits → behavior (no intermediate layers)
2. **Poker-native language**: "top 22% range" not "aggression 0.7"
3. **Direct archetype mapping**: tightness × aggression → TAG/LAG/etc.
4. **Unified tilt**: composure IS tilt, no separate system
5. **Concrete guidance**: "bet 75%" not "you feel aggressive"

---

## Open Questions & Ideas

### Observation: "Thinking" vs "Confident" States

From experiment data:
- Players in a "thinking" state have been doing well
- Players in a "confident" state have been making bad decisions

This suggests **overconfidence hurts overall play**, even though intuition says confidence should help.

### Hypothesis: Confidence Affects Different Decisions Differently

| Decision Type | High Confidence | Low Confidence |
|---------------|-----------------|----------------|
| **Bluffing** | ✅ Helps (commits, good timing) | ❌ Hurts (hesitant, gives up) |
| **Calling** | ❌ Hurts (hero calls, doesn't fold) | ✅ Helps? (more disciplined) |

Confident players might bluff well but leak chips on bad calls.

### Idea: Decision vs Execution Model

Instead of traits affecting **frequency** of decisions, they affect **quality** of execution:

```
STRATEGY TRAITS (WHAT to do)          MENTAL STATE (HOW WELL)
─────────────────────────────         ─────────────────────────

tightness  → which hands to play      confidence → bluff execution
aggression → how often to bet         composure  → call/fold execution
```

Example:
- AI decides to bluff (based on strategy + situation)
- **High confidence** → executes well (right sizing, commits)
- **Low confidence** → executes poorly (hesitant, weird sizing)

Example:
- AI decides to call (based on pot odds + hand strength)
- **High composure** → executes well (disciplined, correct spots)
- **Low composure** → executes poorly (hero calls, revenge calls)

### This Explains the Data

| State | Confidence | Composure | Bluff Execution | Call Execution | Overall |
|-------|------------|-----------|-----------------|----------------|---------|
| **Thinking** | Mid | High | ✅ Good | ✅ Good | ✅ Best |
| **Confident** | High | Mid | ✅ Great | ❌ Poor | ⚠️ Mixed |
| **Cautious** | Low | High | ❌ Poor | ✅ Good | ⚠️ Tight |
| **Manic Tilt** | High | Low | ✅ Great | ❌ Terrible | ❌ Bad |
| **Defeated Tilt** | Low | Low | ❌ Poor | ❌ Poor | ❌ Worst |

"Thinking" wins because **both** bluffs and calls are executed well.

### Possible Simplification: 4 Core Traits

```python
@dataclass
class PokerPsychology:
    # Strategy (WHAT to do)
    tightness: float      # Hand selection, continue thresholds
    aggression: float     # Bet/raise frequency

    # Execution (HOW WELL)
    confidence: float     # Bluff execution quality
    composure: float      # Call/fold execution quality

    # table_talk could be derived from confidence + composure
```

### Questions to Resolve

1. **Should confidence only affect bluffs?** Or does it also help value betting?

2. **Is "thinking" the optimal state?** High composure + moderate confidence = best play?

3. **How to represent in prompts?**
   - Current: "You feel confident"
   - New: "You're executing bluffs well but making loose calls"

4. **Does this change the emotion mapping?**
   - High confidence = good at bluffs, bad at calls → mixed emotion?
   - Or keep simple quadrant model?

5. **Should we test this model?** Run experiment comparing:
   - High confidence + high composure
   - Mid confidence + high composure ("thinking")
   - See which actually performs better

---

## Related Documentation

- [PSYCHOLOGY_DESIGN.md](/docs/technical/PSYCHOLOGY_DESIGN.md) - Current design goals
- [PRESSURE_EVENTS.md](/docs/technical/PRESSURE_EVENTS.md) - Event catalog
- [PSYCHOLOGY_OVERVIEW.md](/docs/technical/PSYCHOLOGY_OVERVIEW.md) - Current architecture
