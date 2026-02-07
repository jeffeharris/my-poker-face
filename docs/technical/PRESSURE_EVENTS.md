# Pressure Events System

## Overview

Pressure events are game occurrences that affect AI player psychology. When detected, they modify elastic personality traits and tilt state, creating dynamic AI behavior that responds realistically to poker situations.

## Architecture: The Deterministic Chain

```
PERSONALITY (personalities.json)
    │
    │  Defines trait anchors + elasticity bounds
    │
    ▼
ELASTIC TRAITS (runtime state)
    │
    │  Events push values within bounds
    │  • aggression: anchor ± elasticity
    │  • bluff_tendency: anchor ± elasticity
    │  • chattiness: anchor ± elasticity
    │  • emoji_usage: anchor ± elasticity
    │
    ▼
EMOTIONAL DIMENSIONS (computed, stateless)
    │
    │  Deterministic functions of trait values:
    │  • valence = f(avg_trait_drift)
    │  • arousal = f(aggression, drift)
    │  • control = f(drift)
    │  • focus = f(chattiness, emoji, drift)
    │
    ▼
AVATAR EMOTION (deterministic mapping)
    │
    │  • angry, sad, happy, nervous, confident, etc.
    │
    ▼
IMAGE SHOWN TO USER
```

**Key insight:** The only mutable state is the elastic trait values. Everything downstream is derived deterministically.

**Tilt is separate:** It has its own state and directly modifies AI prompts (hiding information, adding intrusive thoughts).

---

## Event Categories

### Outcome Events (Win/Loss)

| Event | Trigger | Status |
|-------|---------|--------|
| `win` | Any pot won | Detected, no effects |
| `big_win` | Win > 50% avg stack | ✅ Active |
| `big_loss` | Lose > 50% avg stack | ✅ Active |
| `double_up` | End hand with 2x starting stack | Not detected |
| `crippled` | Lose 75%+ stack in one hand | Not detected |

### Bluff Events

| Event | Trigger | Status |
|-------|---------|--------|
| `successful_bluff` | Win without showdown, weak hand inferred | ✅ Active |
| `bluff_called` | Lose at showdown with weak hand (rank ≥ 8) | ⚠️ Bug: applied to wrong players |

### Bad Beat / Luck Events

| Event | Trigger | Status |
|-------|---------|--------|
| `bad_beat` | Lose with strong hand (rank ≤ 4) | ✅ Active |
| `cooler` | Both players strong hands, unavoidable loss | Not detected (needs equity) |
| `suckout` | Win after being <30% equity | Not detected (needs equity) |
| `got_sucked_out` | Lose after being >70% equity | Not detected (needs equity) |

### Position / Situation Events

| Event | Trigger | Status |
|-------|---------|--------|
| `headsup_win` | Win in heads-up situation | Detected, no effects |
| `headsup_loss` | Lose in heads-up situation | Detected, no effects |
| `short_stack` | Stack < 3 BB | Not detected |
| `eliminated_opponent` | Knock someone out | ✅ Active |

### Streak Events

| Event | Trigger | Status |
|-------|---------|--------|
| `winning_streak` | Win 3+ hands in a row | Data exists, not detected |
| `losing_streak` | Lose 3+ hands in a row | Data exists, not detected |

### Social Events

| Event | Trigger | Status |
|-------|---------|--------|
| `friendly_chat` | Message contains positive words | ✅ Active |
| `rivalry_trigger` | Message contains aggressive words | ✅ Active |
| `nemesis_win` | Beat your nemesis player | Not detected |
| `nemesis_loss` | Lose to nemesis player | Not detected |

### Action Events

| Event | Trigger | Status |
|-------|---------|--------|
| `fold_under_pressure` | Fold to big bet | Defined, not detected |
| `aggressive_bet` | Make large bet (3x+ pot) | Defined, not detected |

---

## Current Pressure Effects

### Elasticity Manager (`poker/elasticity_manager.py`)

These events modify elastic traits:

```python
PRESSURE_EVENTS = {
    "big_win": {
        "aggression": +0.2,
        "chattiness": +0.3,
        "bluff_tendency": +0.1
    },
    "big_loss": {
        "aggression": -0.3,
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
    },
    "eliminated_opponent": {
        "aggression": +0.3,
        "chattiness": +0.2,
        "bluff_tendency": +0.15
    },
    "bad_beat": {
        "aggression": -0.2,
        "bluff_tendency": -0.3,
        "chattiness": -0.1
    },
    "fold_under_pressure": {
        "aggression": -0.15,
        "bluff_tendency": -0.1,
        "chattiness": -0.05
    },
    "aggressive_bet": {
        "aggression": +0.25,
        "bluff_tendency": +0.15,
        "chattiness": +0.1
    }
}
```

### Tilt Modifier (`poker/tilt_modifier.py`)

These events modify tilt level directly:

| Event | Tilt Change | Notes |
|-------|-------------|-------|
| bad_beat | +0.25 | Major tilt trigger |
| bluff_called | +0.20 | Embarrassment/frustration |
| big_loss | +0.15 | Financial pain |
| rivalry_trigger | +0.10 | Social aggravation |
| fold_under_pressure | +0.05 | Mild frustration |
| regular_loss | +0.05 | Normal variance |
| win | -0.15 | Relief |
| big_win (extra) | -0.10 | Additional relief |
| successful_bluff | -0.15 | Satisfaction |
| eliminated_opponent | -0.15 | Dominance |
| Natural decay | -0.02/hand | Recovery |

---

## Known Issues

### 1. `bluff_called` Applied to Wrong Players

**Bug location:** `poker/pressure_detector.py:75`

**Current behavior:** When someone wins without showdown (successful bluff), `bluff_called` is applied to the **folders** instead of nobody.

**Correct behavior:** `bluff_called` should only trigger when a player **loses at showdown** with a weak hand (they tried to bluff and got caught).

### 2. Tilt Increments Too Aggressive

From experiment data: Eeyore hit full tilt 39% of decisions. Four bad beats = full tilt.

**Proposed fix:** Reduce increments, increase decay (see Tilt Tuning Plan).

### 3. Absolute vs Relative Thresholds

| System | Threshold | Type |
|--------|-----------|------|
| `moment_analyzer.py` | 50% of stack | Relative ✅ |
| `pressure_detector.py` | Uses MomentAnalyzer | Relative ✅ |
| `tilt_modifier.py` | $500 absolute | Inconsistent ❌ |

**Proposed fix:** Use BB-relative thresholds (e.g., 15 BB) in tilt_modifier.

---

## Proposed New Events

### Events Ready to Implement

| Event | Trait Effects | Tilt Effect | Detection |
|-------|---------------|-------------|-----------|
| `win` | agg +0.03, chat +0.05 | -0.03 | Already detected |
| `headsup_win` | agg +0.08, bluff +0.05 | -0.05 | Already detected |
| `headsup_loss` | agg -0.05, chat -0.08 | +0.08 | Already detected |
| `winning_streak` | agg +0.10, chat +0.15 | -0.10 | SessionMemory has data |
| `losing_streak` | agg -0.10, chat -0.15 | +0.15 | SessionMemory has data |
| `double_up` | agg +0.15, bluff +0.10 | -0.12 | Stack comparison |
| `crippled` | agg -0.20, bluff -0.15 | +0.20 | Stack < 10% of start |
| `nemesis_win` | agg +0.12, chat +0.10 | -0.08 | TiltState.nemesis |
| `nemesis_loss` | agg -0.08, chat -0.12 | +0.12 | TiltState.nemesis |

### Events Requiring Equity Tracking

| Event | Trait Effects | Tilt Effect | Detection |
|-------|---------------|-------------|-----------|
| `cooler` | agg -0.05, bluff -0.08 | +0.08 | Both ≥30% preflop equity, loser strong hand |
| `suckout` | agg +0.15, bluff +0.10, chat +0.12 | -0.10 | Winner <30% on earlier street |
| `got_sucked_out` | agg -0.15, bluff -0.12 | +0.25 | Loser >70% on earlier street |

---

## Balancing Principles

### Magnitude Hierarchy

```
Smaller ──────────────────────────────────────────────► Larger

win < headsup_win < big_win < double_up < suckout
 │
 │  Regular wins should have minimal effect
 │  Big/dramatic events have larger effects
 │  Streaks compound over time
```

### Trait Effect Guidelines

| Trait | What Increases It | What Decreases It |
|-------|-------------------|-------------------|
| aggression | Wins, dominance, rivalry | Losses, being outplayed |
| bluff_tendency | Successful bluffs, confidence | Failed bluffs, getting caught |
| chattiness | Wins, social engagement | Losses, embarrassment |
| emoji_usage | Positive emotions, wins | Negative emotions, tilt |

### Tilt Guidelines

- **Tilt increase:** Losses, bad luck, being outplayed, social attacks
- **Tilt decrease:** Wins, successful plays, knocking out opponents
- **Target distribution:** ~75% low, ~15% medium, ~5% high, ~1% full (realistic - players should be calm most of the time)

---

## Detection Infrastructure

### Data Available for Detection

| Data Source | What It Provides |
|-------------|------------------|
| `PokerGameState` | Stacks, pot, community cards, fold status |
| `winner_info` | Winner names, hand rank, pot breakdown |
| `HandEvaluator` | Hand classification (1-10 rank) |
| `SessionMemory` | Streak tracking, hand outcomes |
| `TiltState` | Nemesis tracking, losing streak count |
| `MomentAnalyzer` | Big pot detection, drama factors |
| `EquityCalculator` | Pre-flop and street equity (requires hole cards) |

### Detection Entry Points

| Location | When Called | Events Detected |
|----------|-------------|-----------------|
| `PressureEventDetector.detect_showdown_events()` | After showdown | big_win, big_loss, bad_beat, bluff events |
| `PressureEventDetector.detect_fold_events()` | After fold | successful_bluff (inferred) |
| `PressureEventDetector.detect_chat_events()` | After chat | friendly_chat, rivalry_trigger |
| `PressureEventDetector.detect_elimination_events()` | After elimination | eliminated_opponent |

---

## Related Documentation

- [PSYCHOLOGY_OVERVIEW.md](PSYCHOLOGY_OVERVIEW.md) - Overall psychology architecture
- [PSYCHOLOGY_ZONES_MODEL.md](PSYCHOLOGY_ZONES_MODEL.md) - Zone mechanics
- [PRESSURE_STATS_SYSTEM.md](PRESSURE_STATS_SYSTEM.md) - Stats tracking and display
- [EQUITY_PRESSURE_DETECTION.md](EQUITY_PRESSURE_DETECTION.md) - Equity-based event detection

## Files

| File | Purpose |
|------|---------|
| `poker/pressure_detector.py` | Event detection logic |
| `poker/elasticity_manager.py` | Trait effect definitions |
| `poker/tilt_modifier.py` | Tilt increment/decrement logic |
| `poker/player_psychology.py` | Orchestrates trait + tilt updates |
| `poker/pressure_stats.py` | Stats tracking (separate from psychology) |
