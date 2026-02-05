# Psychology System Balance Session Summary

**Date:** 2026-02-05
**Goal:** Balance the psychology system so gameplay feels novel but not chaotic

## Key Discoveries

### 1. The Original Problem

Historical data showed **100% of decisions in "alert" band** (composure 0.6-0.8). No rattled or tilted states. The system was too stable because:
- All personalities had identical poise (0.7) and recovery_rate (0.17)
- Recovery pulled everyone to the same baseline (0.5 conf, 0.7 comp)
- Events were firing but impacts were dampened by sensitivity + fast recovery

### 2. Personality-Specific Baselines

**Composure baseline derived from poise:**
```python
baseline_composure = 0.45 + 0.40 × poise
# Range: 0.45 (low poise) to 0.85 (high poise)
```

**Confidence baseline derived from ego (FIXED - was inverted!):**
```python
baseline_confidence = 0.40 + 0.45 × ego
# Range: 0.40 (low ego) to 0.85 (high ego)
# High ego = starts confident (arrogant), NOT insecure
```

**Base recovery derived from poise:**
```python
base_recovery = 0.12 + 0.25 × (1 - poise)
# Range: 0.12 (high poise, slow) to 0.37 (low poise, fast)
```

### 3. Asymmetric Recovery

Recovery behaves differently above vs below baseline:

```python
if current < baseline:
    # Below baseline - recovering FROM tilt/doubt
    # Current state affects recovery speed (vicious cycle when tilted)
    modifier = 0.6 + 0.4 × current_value
else:
    # Above baseline - riding a hot streak
    # Slow decay, let them enjoy it
    modifier = 0.8

effective_recovery = base_recovery × modifier
```

**Effect:**
- Tilt is sticky - harder to escape when deeply tilted
- Hot streaks last - slow decay from positive states
- Events can still knock you out of any state instantly

### 4. Compounding Events

Multiple events can fire in the same hand (realistic):
- `bad_beat + big_loss + nemesis_loss` = massive composure hit
- `big_win + double_up + nemesis_win` = big confidence boost

This creates dramatic swings that single-event models miss.

### 5. The Zone Model (Final Mental Model)

**Key insight:** Quadrants aren't "good vs bad" - they're different play styles with different strengths.

```
                         CONFIDENCE →
            0.0         0.5         0.8         1.0
           ┌───────────┬───────────┬───────────┬───┐
      1.0  │           │           │  POKER    │   │
           │  GUARDED  │           │   FACE    │   │
   C       │  sweet    │           │  ┌────┐   │ D │
   O  0.8  │   spot    │           │  │    │   │ E │
   M       │    ○      │           │  └────┘   │ T │
   P       ├───────────┼───────────┼───────────┤ A │
   O  0.6  │           │           │COMMANDING │ C │
   S       │           │  NEUTRAL  │  sweet    │ H │
   U       │           │     ○     │   spot    │ E │
   R  0.5  │           │           │    ○      │ D │
   E       ├───────────┼───────────┼───────────┤   │
      0.4  │           │           │ AGGRO     │   │
   ↑       │           │           │ sweet     │ O │
           │  SHAKEN   │           │  spot     │ V │
      0.2  │    ☠      │           │    ○      │ E │
           │           │           │           │ R │
           ├───────────┼───────────┼───────────┤ C │
      0.0  │  TILTED   │           │ OVERHEATED│ O │
           │    ☠      │           │    ☠      │ N │
           └───────────┴───────────┴───────────┴───┘
```

**Zone Types:**
- **Sweet spots (○):** Benefits - access to style-specific information/bonuses
- **Penalty zones (☠):** Penalties - tilted, overconfident, shaken, etc.
- **Neutral:** Center - no special bonuses or penalties

**Key Principles:**
1. Anchors live somewhere reasonable, NOT at extremes
2. You move in/out of sweet spots through play
3. Events push you around; recovery pulls toward anchor
4. Extremes are visited, not lived in

### 6. Zone Benefits (Design Intent)

| Zone | Information/Bonus |
|------|-------------------|
| **Poker Face** | GTO info - pot odds, equity, balanced ranges |
| **Commanding** | Pressure/value - "extract max value", opponent weakness |
| **Guarded** | Patience/traps - "wait for better spot", trap-setting cues |
| **Aggro** (sweet) | Exploitation - opponent fold rates, tilt levels, attack cues |
| **Overheated** ☠ | Penalty - reckless, ignores warnings |
| **Shaken** ☠ | Penalty - desperate, poor decisions |
| **Overconfident** ☠ | Penalty - hero calls, ignores contradicting info |

### 7. Archetype Examples

| Archetype | Poise | Ego | Home Zone | Visits Often |
|-----------|-------|-----|-----------|--------------|
| **Batman** | 0.77 | 0.32 | Poker Face / Guarded | Rarely moves |
| **Napoleon** | 0.72 | 0.80 | Commanding | Shaken (when crushed) |
| **Gordon Ramsay** | 0.35 | 0.80 | Aggro sweet spot | Overheated ☠ (when triggered) |
| **Bob Ross** | 0.72 | 0.40 | Guarded | Commanding (when winning) |

## Simulation Results

### With Full System (personality baselines + asymmetric recovery + compounding)

| Archetype | Poise | Base Comp | Base Rec | Alert | Rattled | Tilted |
|-----------|-------|-----------|----------|-------|---------|--------|
| poker_face | 0.77 | 0.76 | 0.18 | 85.2% | 12.0% | 0.6% |
| commanding | 0.72 | 0.74 | 0.19 | 81.8% | 17.0% | 1.2% |
| overheated | 0.35 | 0.59 | 0.28 | 14.0% | 72.6% | 13.4% |
| guarded | 0.72 | 0.74 | 0.19 | 81.8% | 17.0% | 1.2% |

### Tilt Recovery (from composure 0.20)

| Archetype | Hands to Recover | Base Recovery |
|-----------|------------------|---------------|
| Overheated | 8 hands | 0.28 |
| Commanding | 13 hands | 0.19 |
| Poker Face | 14 hands | 0.18 |

### Variance Across Seeds

- Poker Face: 0.0% - 1.8% tilted (avg 0.7%) - consistent
- Overheated: 5.2% - 18.0% tilted (avg 10.1%) - high variance (intentional!)

## Open Questions for Next Session

### 1. Exact Zone Boundaries
- What are the precise coordinates for each sweet spot?
- How big is each zone (radius/shape)?
- Where exactly do penalty zones begin?

### 2. Zone Overlap / Transitions
- Can you be in multiple zones at once?
- How do transitions work (gradual blend or hard switch)?

### 3. Adaptation Bias
- Not fully fleshed out
- Intended: High = reads table, adjusts to threats; Low = plays own game
- How does this interact with zones?

### 4. Implementation Details
- How to modify prompts based on zone
- When to show/hide information
- How to communicate zone state to player/UI

### 5. Confidence Axis Tuning
- The fixed formula (0.40 + 0.45 × ego) improved quadrant distribution
- But may need further tuning based on playtesting

## Files Created This Session

1. `experiments/psychology_balance_simulator.py` - Simulation tool with all mechanics
2. `docs/technical/PSYCHOLOGY_BALANCE_GUIDE.md` - Parameter tuning guide
3. `docs/technical/PSYCHOLOGY_REBALANCE_PROPOSAL.md` - Original rebalance proposal
4. `docs/technical/emotional_quadrants.svg` - Original quadrant diagram
5. `docs/technical/PSYCHOLOGY_ZONES_MODEL.md` - Zone model documentation
6. `docs/technical/emotional_zones_v2.svg` - Updated zone diagram
7. `docs/technical/PSYCHOLOGY_SESSION_SUMMARY.md` - This file

## Key Takeaways

1. **Poise drives composure dynamics** - baseline, sensitivity, and recovery
2. **Ego drives confidence dynamics** - high ego = arrogant start, brittle when wrong
3. **Asymmetric recovery creates meaningful tilt** - sticky when low, slow decay when high
4. **Quadrants are play styles, not quality levels** - each has strengths
5. **Extremes are penalties, sweet spots are benefits** - the zone model
6. **Different archetypes have different optimal strategies** - not just different stability
