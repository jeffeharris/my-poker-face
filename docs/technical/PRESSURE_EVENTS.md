---
purpose: Catalog of pressure events, their detection rules, axis impacts, and resolution logic
type: reference
created: 2025-06-15
last_updated: 2026-02-07
---

# Pressure Events System

## Overview

Pressure events are game occurrences that affect AI player psychology. When detected, they push the three emotional axes (confidence, composure, energy) through sensitivity filters defined by personality anchors. Events are processed through the unified `PsychologyPipeline` after each hand.

## Architecture

```
GAME EVENTS (showdown, fold, stack change, etc.)
    │
    ▼
PressureEventDetector (poker/pressure_detector.py)
    │  Pure detection — no mutation
    │  Returns: List[(event_name, [affected_players])]
    │
    ▼
PlayerPsychology.resolve_hand_events() (poker/player_psychology.py)
    │  Resolves multiple events into a single psychological update
    │  Applies sensitivity filters (ego → confidence, poise → composure)
    │  Returns: per-event deltas + final axis values
    │
    ▼
PsychologyPipeline.process_hand() (poker/psychology_pipeline.py)
    │  Orchestrates: detect → resolve → persist → callback → composure → recover → save
    │
    ▼
UPDATED AXES (confidence, composure, energy)
    │
    ▼
ZONE DETECTION → PROMPT MODIFICATION → AI DECISION
```

**Key insight:** Detection is pure (no side effects). Resolution applies sensitivity filters and priority rules. The pipeline orchestrates the full cycle.

---

## Event Categories & Impacts

### Outcome Events (ONE chosen per hand)

Only the highest-priority outcome event fires. Priority order (low → high):

`loss` < `win` < `headsup_loss` < `headsup_win` < `big_loss` < `big_win`

| Event | Trigger | Confidence | Composure | Energy |
|-------|---------|------------|-----------|--------|
| `win` | Any pot won | +0.02 | — | +0.02 |
| `loss` | Lost a pot at showdown | -0.02 | — | -0.02 |
| `big_win` | Win > 50% avg stack | +0.12 | +0.02 | +0.08 |
| `big_loss` | Lose > 50% avg stack | -0.15 | -0.05 | -0.08 |
| `headsup_win` | Win in heads-up pot | +0.06 | +0.02 | +0.05 |
| `headsup_loss` | Lose in heads-up pot | -0.06 | -0.02 | -0.05 |

### Ego/Agency Events (at most ONE, scaled 50%)

First detected ego event fires at **half strength**. These are "being wrong" events filtered through **ego** sensitivity.

| Event | Trigger | Confidence | Composure | Energy |
|-------|---------|------------|-----------|--------|
| `successful_bluff` | Win without showdown with weak hand (rank ≥ 9 or self-reported bluff_likelihood ≥ 50) | +0.20 | +0.05 | +0.05 |
| `bluff_called` | Lose at showdown with weak hand (rank ≥ 9 or bluff_likelihood ≥ 50) | -0.25 | -0.10 | -0.05 |
| `nemesis_win` | Beat your nemesis player | +0.18 | +0.05 | +0.05 |
| `nemesis_loss` | Lose to nemesis player | -0.18 | -0.05 | -0.05 |

### Equity Shock Events (at most ONE, full strength)

Require equity history from showdown. Only affect composure and energy (not confidence — luck events aren't about "being wrong"). Priority order (low → high):

`suckout` < `cooler` < `got_sucked_out` < `bad_beat`

| Event | Trigger | Confidence | Composure | Energy |
|-------|---------|------------|-----------|--------|
| `bad_beat` | Loser had ≥80% equity at worst swing, weighted delta ≥ 0.30 | — | -0.35 | -0.10 |
| `cooler` | Loser had 60-80% equity, weighted delta ≥ 0.30 | — | -0.20 | -0.05 |
| `suckout` | Winner was behind (opponent had ≥80% equity) | — | +0.10 | +0.05 |
| `got_sucked_out` | Loser had ≥80% equity on earlier street, lost | — | -0.30 | -0.15 |

### Streak Events (additive)

| Event | Trigger | Confidence | Composure | Energy |
|-------|---------|------------|-----------|--------|
| `winning_streak` | 3+ consecutive wins (fires at 3, 6) | +0.10 | -0.05 | +0.05 |
| `losing_streak` | 3+ consecutive losses (fires at 3, 6) | -0.12 | -0.20 | -0.10 |

### Pressure/Fatigue Events (additive)

These accumulate — all detected events fire.

| Event | Trigger | Confidence | Composure | Energy |
|-------|---------|------------|-----------|--------|
| `big_pot_involved` | Participated in pot > 50% avg stack | — | -0.05 | -0.05 |
| `all_in_moment` | Went all-in or faced all-in | — | -0.08 | -0.08 |
| `card_dead_5` | 5+ hands without playable cards | -0.03 | +0.03 | -0.10 |
| `consecutive_folds_3` | 3+ consecutive folds | — | -0.05 | -0.08 |
| `not_in_hand` | Folded before showdown | — | — | -0.02 |
| `disciplined_fold` | Folded a decent hand to aggression (cooldown: 2 hands) | -0.06 | +0.12 | -0.02 |
| `short_stack_survival` | Stayed short-stacked for 3+ hands without all-in (cooldown: 5 hands) | -0.04 | +0.06 | -0.05 |

### Desperation Events (additive)

| Event | Trigger | Confidence | Composure | Energy |
|-------|---------|------------|-----------|--------|
| `short_stack` | NEW transition below 10 BB | -0.08 | -0.15 | -0.10 |
| `crippled` | Lost 75%+ of stack this hand | -0.20 | -0.25 | -0.15 |
| `fold_under_pressure` | Folded to a large bet | -0.10 | +0.05 | — |

---

## Event Resolution Rules

`resolve_hand_events()` in `player_psychology.py` applies these rules:

1. **ONE outcome event** — highest priority wins, applied at full strength
2. **At most ONE ego/agency event** — first detected, applied at **50% strength**
3. **ALL pressure/fatigue events** — additive, no scaling
4. **ALL desperation + streak events** — additive, no scaling
5. **At most ONE equity shock event** — highest priority wins, full strength, **no confidence delta**
6. **Clamp all axes** to [0.0, 1.0] after all deltas applied

### Sensitivity Filtering

Raw deltas are scaled by personality sensitivity before application:

| Axis | Sensitivity Formula | Effect |
|------|---------------------|--------|
| Confidence | `impact × (severity_floor + (1 - severity_floor) × ego)` | High-ego players lose more confidence when outplayed |
| Composure | `impact × (severity_floor + (1 - severity_floor) × (1 - poise))` | High-poise players shrug off bad outcomes |
| Energy | Applied directly, no sensitivity filter | Everyone reacts equally to energy events |

**Severity floors** (ensure even low-sensitivity players feel major events):

| Severity | Floor | Used For |
|----------|-------|----------|
| MINOR | 0.20 | Routine gameplay (win, loss, not_in_hand) |
| NORMAL | 0.30 | Standard stakes (big_win, bluff events) |
| MAJOR | 0.40 | High-impact moments (bad_beat, crippled) |

The highest severity across all events in a hand determines the floor used.

---

## Detection Infrastructure

### Detector Methods

| Method | Events Detected |
|--------|----------------|
| `detect_showdown_events()` | win, loss, big_win, big_loss, headsup_win, headsup_loss, bluff_called |
| `detect_fold_events()` | successful_bluff (inferred from weak hand winning without showdown) |
| `detect_equity_shock_events()` | bad_beat, cooler, suckout, got_sucked_out |
| `detect_stack_events()` | short_stack, crippled, short_stack_survival |
| `detect_streak_events()` | winning_streak, losing_streak |
| `detect_nemesis_events()` | nemesis_win, nemesis_loss |
| `detect_big_pot_events()` | big_pot_involved, all_in_moment |
| `detect_fatigue_events()` | card_dead_5, consecutive_folds_3, not_in_hand, disciplined_fold |
| `detect_fold_pressure_events()` | fold_under_pressure |

### Equity Shock Detection Model

Uses a **weighted-delta model**, not simple threshold checking:

```
weighted_delta = equity_delta × pot_significance × street_weight
```

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `EQUITY_SHOCK_THRESHOLD` | 0.30 | Minimum weighted delta to qualify |
| `BAD_BEAT_EQUITY_MIN` | 0.80 | Loser must have had ≥80% equity at worst swing |
| `COOLER_EQUITY_MIN` | 0.60 | Loser had 60-80% equity range |
| `POT_SIGNIFICANCE_MIN` | 0.15 | Ignore trivial pots |

**Street weights:** FLOP = 1.0, TURN = 1.2, RIVER = 1.4

**Priority:** bad_beat > got_sucked_out > cooler > suckout

### Bluff Detection

A hand is considered a bluff if:
- Hand rank ≥ 9 (one pair or high card), OR
- Self-reported `bluff_likelihood ≥ 50` (from LLM decision response)

### Cooldowns

| Event | Cooldown | Purpose |
|-------|----------|---------|
| `disciplined_fold` | 2 hands max per player | Prevent spam from tight play |
| `short_stack_survival` | 5 hands max per player | One acknowledgment per short-stack episode |

---

## Pipeline Integration

The `PsychologyPipeline` (poker/psychology_pipeline.py) orchestrates event processing:

```
detect → resolve → persist → callback → update_composure → recover → save
```

| Stage | What Happens |
|-------|-------------|
| **Detect** | All detector methods run, collecting events per player |
| **Resolve** | `resolve_hand_events()` applies priority rules and sensitivity |
| **Persist** | Events saved to `pressure_events` table with per-event deltas |
| **Callback** | `on_events_resolved` fires (UI updates, socket emissions) |
| **Update composure** | Either LLM narration (`on_hand_complete`) or lightweight (`composure_state.update_from_hand`) |
| **Recover** | Baseline drift + zone gravity applied |
| **Save** | Controller + emotional state persisted (if `persist_controller_state=True`) |

### Persistence Format

Each resolved event is saved with:
- `conf_delta`, `comp_delta`, `energy_delta` — post-sensitivity, per-event values
- `conf_after`, `comp_after`, `energy_after` — final axis values after all events
- `opponent` — the opponent involved (both winners and losers get an opponent)
- `resolved_from` — list of all raw events that fed into resolution

Recovery and gravity are persisted as separate pseudo-events (`_recovery`, `_gravity`).

---

## Files

| File | Purpose |
|------|---------|
| `poker/pressure_detector.py` | Pure event detection logic |
| `poker/player_psychology.py` | Axes, sensitivity, resolution, recovery, gravity |
| `poker/psychology_pipeline.py` | Unified pipeline orchestrating detect → save cycle |
| `poker/pressure_stats.py` | Stats tracking for UI display (separate from psychology) |
| `poker/equity_tracker.py` | Equity calculation for shock detection |

## Related Documentation

- [PSYCHOLOGY_OVERVIEW.md](PSYCHOLOGY_OVERVIEW.md) — System architecture (anchors, axes, zones)
- [PSYCHOLOGY_ZONES_MODEL.md](PSYCHOLOGY_ZONES_MODEL.md) — Zone geometry, effects, blending
- [PRESSURE_STATS_SYSTEM.md](PRESSURE_STATS_SYSTEM.md) — Stats tracking and display
- [EQUITY_PRESSURE_DETECTION.md](EQUITY_PRESSURE_DETECTION.md) — Equity-based event detection
