# Psychology System Design

> **⚠️ DEPRECATED**: This document describes the legacy 5-trait model.
> The system has been updated to v2.1 with a 9-anchor + 3-axis architecture.
> See **[PSYCHOLOGY_PRD_v2.md](./PSYCHOLOGY_PRD_v2.md)** for the current design.
>
> Key changes in v2.1:
> - 9 static anchors (identity layer) instead of 5 elastic traits
> - 3 dynamic axes (confidence, composure, energy) instead of 4D emotional model
> - Quadrant-based emotions (Commanding, Overheated, Guarded, Shaken)
> - Derived aggression/looseness from anchors + emotional modifiers

---

## Purpose (Legacy)

The psychology system exists to create **novelty and variety** in AI poker play, not to simulate human psychology accurately. Every hand should feel a bit different - the AI isn't a solved GTO bot playing optimal strategy.

### Core Principles

1. **Novelty over Realism** - We don't expect AI to feel "truly human" (that would be boring). We want texture and variety in decisions.

2. **Competitive Foundation** - Psychology adds spice, not chaos. Skilled play should still win consistently.

3. **Tight Coupling** - What you see (avatar emotion, chat tone) matches what the AI "feels" internally. No performative emotions that don't affect behavior.

4. **Tunable Difficulty** - Psychology's influence on decisions scales with difficulty settings.

---

## The Deterministic Chain

```
PERSONALITY (personalities.json)
    │
    │  Defines trait anchors + elasticity bounds
    │  (e.g., aggression: 0.7 ± 0.3)
    │
    ▼
ELASTIC TRAITS (runtime state - 5-trait poker-native model)
    │
    │  Pressure events push values within bounds
    │  Modified by: difficulty_multiplier
    │
    ├── tightness (0-1)    Range selectivity: 0=loose, 1=tight
    ├── aggression (0-1)   Bet frequency: 0=passive, 1=aggressive
    ├── confidence (0-1)   Sizing/commitment: 0=scared, 1=fearless
    ├── composure (0-1)    Decision quality: 0=tilted, 1=focused
    └── table_talk (0-1)   Chat frequency: 0=silent, 1=chatty
    │
    ▼
EMOTIONAL DIMENSIONS (computed, stateless)
    │
    │  Deterministic functions of trait values:
    │  valence = f(avg_trait_drift)
    │  arousal = f(aggression, drift)
    │  control = f(composure, drift)
    │  focus = f(table_talk, drift)
    │
    ▼
AVATAR EMOTION (deterministic mapping)
    │
    │  angry, sad, happy, nervous, confident, etc.
    │
    ▼
IMAGE SHOWN TO USER
```

**Key insight:** The only mutable state is elastic trait values. Everything downstream is derived deterministically.

**Composure replaces tilt:** Composure is now a trait (0=tilted, 1=focused). Low composure triggers intrusive thoughts and prompt degradation.

---

## Difficulty Scaling (Planned)

> **Note:** This feature is not yet implemented. The design below describes planned functionality.

Psychology's influence on AI behavior is controlled by a `difficulty_multiplier`:

```python
difficulty_multiplier: float  # 0.5 (hard) to 1.5 (easy)
```

### Effects

| Aspect | Easy (1.5x) | Normal (1.0x) | Hard (0.5x) |
|--------|-------------|---------------|-------------|
| Tilt increment | +0.23 (bad beat) | +0.15 | +0.08 |
| Trait shift | aggression +0.30 | +0.20 | +0.10 |
| Info hidden | At effective 0.6 tilt | At 0.4 | Rarely |
| Recovery | Slower | Normal | Faster |
| Exploitability | High | Medium | Low |

### Implementation

```python
# In TiltState.apply_pressure_event()
effective_increase = base_increase * difficulty_multiplier

# In ElasticTrait.apply_pressure()
effective_pressure = amount * difficulty_multiplier
```

### Player Experience

| Difficulty | Experience |
|------------|------------|
| **Easy** | AI is volatile, emotional, makes exploitable mistakes when tilted. Good for casual play, learning to read opponents. |
| **Normal** | Balanced. Psychology adds variety without overwhelming strategy. |
| **Hard** | AI "fights through" emotional states. Stays closer to optimal play. Harder to exploit psychological weaknesses. |

---

## Goals & Success Metrics

### 1. Novelty (Variety in Play)

**Goal:** Each AI shows meaningful decision variety, not robotic consistency.

**Metric:** Action entropy per player per phase

```sql
SELECT
    player_name,
    phase,
    COUNT(DISTINCT action_taken) as action_variety,
    COUNT(*) as total_decisions
FROM player_decision_analysis
GROUP BY player_name, phase
```

**Target:** Neither 95% fold (too tight) nor 33/33/33 (random). Meaningful variety that reflects personality and situation.

### 2. Competitive Foundation (Skill Matters)

**Goal:** Good decisions should lead to better outcomes. Psychology adds noise, not chaos.

**Metric:** Decision quality correlation with outcomes

```sql
SELECT
    decision_quality,
    AVG(CASE WHEN outcome = 'won' THEN 1 ELSE 0 END) as win_rate
FROM player_decision_analysis
GROUP BY decision_quality
```

**Target:** Clear positive correlation between decision quality and win rate.

### 3. Tight Coupling (Emotion = Behavior)

**Goal:** Visible emotional state predicts actual behavior changes.

**Metric:** Tilt level correlation with mistake rate

```sql
SELECT
    CASE
        WHEN tilt_level >= 0.7 THEN 'severe'
        WHEN tilt_level >= 0.4 THEN 'moderate'
        WHEN tilt_level >= 0.2 THEN 'mild'
        ELSE 'none'
    END as tilt_band,
    AVG(CASE WHEN decision_quality = 'mistake' THEN 1 ELSE 0 END) as mistake_rate,
    COUNT(*) as decisions
FROM player_decision_analysis
GROUP BY tilt_band
```

**Target:**
- Severe tilt: 2-3x baseline mistake rate
- Clear gradient from none → severe

### 4. Balanced Distribution (Not Always Extreme)

**Goal:** Extreme emotional states are rare and dramatic, not constant.

**Metric:** Tilt level distribution

```sql
SELECT
    CASE
        WHEN tilt_level >= 0.9 THEN 'full'
        WHEN tilt_level >= 0.6 THEN 'high'
        WHEN tilt_level >= 0.3 THEN 'medium'
        ELSE 'low'
    END as tilt_band,
    COUNT(*) as cnt,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) as pct
FROM player_decision_analysis
GROUP BY tilt_band
```

**Target Distribution:**
| Band | Target % |
|------|----------|
| Low (<0.3) | 40-50% |
| Medium (0.3-0.6) | 25-35% |
| High (0.6-0.9) | 15-20% |
| Full (0.9+) | 5-10% |

### 5. Personality Distinctiveness

**Goal:** Different AI characters behave differently.

**Metric:** Trait distribution variance across personalities

```sql
SELECT
    player_name,
    AVG(elastic_aggression) as avg_aggression,
    STDDEV(elastic_aggression) as aggression_variance
FROM player_decision_analysis
GROUP BY player_name
```

**Target:** Distinct clusters per personality. Eeyore ≠ Batman ≠ Snoop Dogg.

---

## Pressure Events

Events that modify psychological state (all 5 traits including composure).

### Event Categories

| Category | Examples | Typical Effect |
|----------|----------|----------------|
| Outcome | win, big_win, big_loss | Moderate trait changes, composure ±0.15-0.25 |
| Bluff | successful_bluff, bluff_called | Large confidence/aggression changes |
| Luck | suckout, got_sucked_out, cooler | Large composure changes (-0.30 to -0.35) |
| Position | headsup_win, headsup_loss | Small-medium changes |
| Streak | winning_streak, losing_streak | Cumulative effects, composure ±0.10-0.20 |
| Stack | double_up, crippled, short_stack | Situational changes, confidence affected |
| Social | friendly_chat, rivalry_trigger | Small trait changes, composure ±0.05-0.10 |
| Rivalry | nemesis_win, nemesis_loss | Targeted composure effects (±0.10-0.15) |

### Effect Magnitudes

Effects scale with difficulty:

```
Actual Effect = Base Effect × difficulty_multiplier
```

Base effects are tuned so that at Normal (1.0x):
- Single events don't cause extreme state changes
- 4-5 consecutive bad events might approach high tilt
- Recovery happens naturally over ~5-10 hands

---

## Composure System (Replaces Tilt)

Composure is now a trait in the 5-trait model, not a separate system. Low composure degrades AI decision-making.

### Composure Levels

| Composure | Category | Effects |
|-----------|----------|---------|
| 0.8-1.0 | Focused | Normal play |
| 0.6-0.8 | Alert | Intrusive thoughts in prompt |
| 0.4-0.6 | Rattled | Strategy advice degraded, some info hidden |
| 0.0-0.4 | Tilted | Most strategic advice removed |

### Pressure Sources

Tracked in `ComposureState` for intrusive thought selection:
- `bad_beat` - Lost with strong hand
- `bluff_called` - Bluff failed
- `big_loss` - Significant chip loss
- `got_sucked_out` - Was ahead, lost to luck
- `losing_streak` - 3+ consecutive losses
- `nemesis` - Specific opponent causing problems

---

## Validation Checklist

Before shipping psychology changes:

1. **Run composure distribution query** - Verify target distribution (40-50% focused, 25-35% alert, 15-20% rattled, 5-10% tilted)
2. **Check composure→mistake correlation** - Should be 2-3x at low composure
3. **Verify personality distinctiveness** - Different characters, different behaviors
4. **Test difficulty scaling** - Easy should feel exploitable, hard should feel stable
5. **Playtest for "feel"** - Does it add novelty without feeling random?

---

## Related Documentation

- [AI_PSYCHOLOGY_SYSTEMS.md](AI_PSYCHOLOGY_SYSTEMS.md) - System architecture
- [ELASTICITY_SYSTEM.md](ELASTICITY_SYSTEM.md) - Trait mechanics
