# Coach System Architecture

This document describes the poker coaching system that provides real-time guidance to human players. The coach calculates equity, analyzes opponents, recommends actions, and tracks skill progression.

---

## Overview

The coach system consists of several interconnected components:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Frontend (React)                               │
│  useCoach.ts → MobilePokerTable → MobileActionButtons (highlighting)    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                         REST API calls
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     Flask Backend Routes                                 │
│  /api/coach/<game_id>/stats    → compute_coaching_data()                │
│  /api/coach/<game_id>/ask      → CoachAssistant (LLM)                   │
│  /api/coach/<game_id>/config   → mode persistence                       │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        Coach Engine                                      │
│  coach_engine.py: compute_coaching_data()                               │
│  ├── Equity calculation (vs ranges + vs random)                         │
│  ├── Pot odds & EV calculation                                          │
│  ├── Hand strength evaluation                                           │
│  ├── Optimal action recommendation                                       │
│  ├── Opponent stats aggregation                                         │
│  └── Board texture analysis                                             │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
         ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
         │  Decision    │  │   Opponent   │  │    Hand      │
         │  Analyzer    │  │   Modeling   │  │   Ranges     │
         └──────────────┘  └──────────────┘  └──────────────┘
```

---

## 1. Equity Calculation

### Location
`poker/decision_analyzer.py`

### Two Methods

| Method | Description | When Used |
|--------|-------------|-----------|
| `calculate_equity_vs_ranges()` | Monte Carlo vs modeled opponent ranges | Primary (when opponent data available) |
| `calculate_equity_vs_random()` | Monte Carlo vs random hands | Fallback |

### Algorithm (Monte Carlo Simulation)

```python
for _ in range(2000):  # iterations
    # 1. Sample opponent hands from estimated ranges
    opponent_hands = sample_hands_for_opponent_infos(opponent_infos, ...)

    # 2. Deal remaining board cards
    sim_board = board + remaining_deck[:cards_needed]

    # 3. Evaluate all hands using eval7 library
    hero_score = eval7.evaluate(hero_hand + sim_board)

    # 4. Check if hero beats ALL opponents
    for opp_hand in opponent_hands:
        if eval7.evaluate(opp_hand + sim_board) > hero_score:
            hero_wins = False

return wins / iterations  # Equity as 0.0-1.0
```

### Output

Both values returned to frontend:
- `equity` — Used for coaching guidance and action recommendation
- `equity_vs_random` — Baseline reference for comparison

---

## 2. Opponent Range Estimation

### Location
`poker/hand_ranges.py`

### Priority Hierarchy

The system estimates opponent hand ranges using this fallback chain:

1. **Action-based narrowing** (most specific)
   - `open_raise` → PFR range
   - `3bet` → ~30% of PFR range
   - `4bet+` → Ultra-premium only (AA-JJ, AK)
   - `call` → VPIP minus PFR range
   - `limp` → VPIP range

2. **PFR-based estimation** (when stats available)
   ```python
   if pfr <= 0.08:  return ULTRA_PREMIUM_RANGE   # ~5%
   if pfr <= 0.12:  return EARLY_POSITION_RANGE  # ~15%
   if pfr <= 0.18:  return MIDDLE_POSITION_RANGE # ~22%
   if pfr <= 0.25:  return BLIND_DEFENSE_RANGE   # ~28%
   else:            return LATE_POSITION_RANGE   # ~32%
   ```

3. **VPIP-based estimation** (fallback)
   - Similar mapping from VPIP percentage to range tiers

4. **Position-based static ranges** (final fallback)
   - Early position: ~15% of hands
   - Middle position: ~22% of hands
   - Late position: ~32% of hands
   - Blinds: ~28% of hands

5. **Aggression adjustment** (for postflop)
   - Passive player (AF < 0.8) betting → Narrow to top 70% (stronger range)
   - Aggressive player (AF > 2.5) betting → Keep full range

### Data Requirements

Stats require **≥5 hands observed** before being used. Cross-session historical data is loaded when current session data is insufficient.

---

## 3. Opponent Modeling

### Location
`poker/memory/opponent_model.py`

### Stats Tracked

| Stat | Formula | Meaning |
|------|---------|---------|
| `vpip` | vpip_count / hands | % of hands player voluntarily enters |
| `pfr` | pfr_count / hands | % of hands player raises pre-flop |
| `aggression_factor` | (bets + raises) / calls | Aggression vs passivity |
| `fold_to_cbet` | fold_count / cbets_faced | Folds to continuation bets |
| `showdown_win_rate` | wins / showdowns | Win rate at showdown |

### Play Style Classification

```python
is_tight = vpip < 0.25
is_aggressive = aggression_factor > 1.5

if is_tight and is_aggressive:     return 'tight-aggressive'
elif not is_tight and is_aggressive: return 'loose-aggressive'
elif is_tight and not is_aggressive: return 'tight-passive'
else:                                return 'loose-passive'
```

### Cross-Session Persistence

When a game starts, historical opponent data is loaded from the database:
- Aggregated stats across all previous sessions
- Used when current session has < 5 hands
- AI personalities have deterministic behavior, so historical data is reliable

---

## 4. Optimal Action Recommendation

### Location
`poker/decision_analyzer.py` → `determine_optimal_action()`

### Inputs

| Parameter | Description |
|-----------|-------------|
| `equity` | Win probability (0-1) |
| `ev_call` | Expected value of calling |
| `required_equity` | Minimum equity to call profitably |
| `num_opponents` | Number of opponents in hand |
| `phase` | PRE_FLOP, FLOP, TURN, RIVER |
| `pot_total` | Current pot size |
| `cost_to_call` | Amount needed to call |
| `player_stack` | Player's remaining chips |
| `player_position` | Table position (button, UTG, etc.) |

### Decision Logic

```
CAN CHECK (no bet to face):
├── equity >= raise_threshold → "raise" (bet for value)
├── post-flop, >50% equity, SPR < 3 → "raise" (deny equity)
└── else → "check"

FACING A BET:
├── EV < 0 → "fold"
├── equity >= raise_threshold → "raise" (value raise)
├── equity >= required_equity → "call" (or raise in specific spots)
├── deep SPR (>10), equity close to required → "call" (implied odds)
└── else → "fold"
```

### Position Adjustment

Position significantly affects thresholds:

| Position | Adjustment | Effect |
|----------|------------|--------|
| Early (UTG) | +8% | Need more equity (tighter) |
| Middle | +3% | Slightly tighter |
| Late (BTN/CO) | -5% | Can play looser |
| Blinds | -3% | Already invested, slightly looser |

### Raise Threshold Calculation

```python
base_raise_threshold = 0.55 + position_adjustment
opponent_adjustment = (num_opponents - 1) * 0.05  # +5% per extra opponent
raise_threshold = min(0.75, base_raise_threshold + opponent_adjustment)
```

---

## 5. Coach Engine

### Location
`flask_app/services/coach_engine.py`

### Main Function

`compute_coaching_data(game_id, player_name, ...)` returns:

```python
{
    # Game state
    'phase': 'FLOP',
    'position': 'Button',
    'pot_total': 150,
    'cost_to_call': 50,
    'big_blind': 10,
    'stack': 500,

    # Equity calculations
    'equity': 0.65,           # vs opponent ranges
    'equity_vs_random': 0.58, # baseline
    'pot_odds': 3.0,          # pot / cost_to_call
    'required_equity': 0.25,  # cost / (pot + cost)
    'is_positive_ev': True,
    'ev_call': 47.5,

    # Hand evaluation
    'hand_strength': 'Two Pair',
    'hand_rank': 3,
    'outs': 8,
    'outs_cards': ['Ah', 'Ad', ...],

    # Recommendation
    'recommendation': 'raise',  # fold/check/call/raise
    'available_actions': ['fold', 'call', 'raise'],

    # Opponent data
    'opponent_stats': [
        {
            'name': 'Batman',
            'stack': 800,
            'vpip': 0.35,
            'pfr': 0.28,
            'aggression': 2.1,
            'style': 'loose-aggressive',
            'hands_observed': 15,
            'historical': {...}  # cross-session data
        }
    ],
    'opponent_ranges': {
        'Batman': {
            'range_size': 45,
            'range_pct': 26.6,
            'sample_hands': ['AA', 'KK', 'AKs', ...]
        }
    },

    # Board analysis
    'board_texture': {
        'wetness': 'wet',
        'connectedness': 'high',
        'flush_possible': True,
        ...
    },

    # Player's hand analysis
    'player_range_analysis': {
        'canonical_hand': 'AKs',
        'position_group': 'late',
        'in_range': True,
        'hand_tier': 'premium'
    },

    # Position context
    'position_context': 'In position - act last, big advantage',

    # Progression (when enabled)
    'progression': {
        'coaching_mode': 'teaching',
        'primary_skill': 'bet_when_strong',
        'relevant_skills': ['bet_when_strong', 'checking_is_allowed'],
        'skill_states': {...}
    }
}
```

---

## 6. Frontend Integration

### Location
`react/react/src/hooks/useCoach.ts`

### Coach Modes

| Mode | Stats Fetch | Proactive Tips | Button Highlighting |
|------|-------------|----------------|---------------------|
| `proactive` | On turn | Yes | Yes |
| `reactive` | On turn | No | Yes |
| `off` | No | No | No |

### Action Button Highlighting

In `MobilePokerTable.tsx`:

```tsx
<MobileActionButtons
  recommendedAction={coach.mode !== 'off' ? coach.stats?.recommendation : null}
/>
```

The `coach-recommended` CSS class adds a green pulsing glow to the recommended button.

### Features

- **Auto-refresh**: Stats fetched when player's turn starts (300ms debounce)
- **Proactive tips**: LLM-generated coaching tips in proactive mode
- **Q&A**: Player can ask questions via `/ask` endpoint
- **Hand review**: Post-hand analysis via `/hand-review` endpoint
- **Skill progression**: Tracks and displays skill advancement

---

## 7. Skill Progression System

### Location
- `flask_app/services/skill_definitions.py` — Skill and gate definitions
- `flask_app/services/coach_progression.py` — Progression service

### Gate Structure

| Gate | Name | Skills |
|------|------|--------|
| 1 | Preflop Fundamentals | Fold Trash Hands, Position Awareness, Raise or Fold |
| 2 | Post-Flop Basics | Flop Connection, Bet When Strong, Checking Is Allowed |
| 3 | Pressure Recognition | Draws Need Price, Respect Big Bets, Have a Plan |
| 4 | Multi-Street Thinking | Don't Pay Double Barrels, Size Bets With Purpose |

### Skill States

Each skill progresses through states:
- `learning` — Initial state, coach actively teaches
- `reliable` — Player demonstrates competence
- `regressed` — Performance dropped below threshold

### Evidence Rules

```python
EvidenceRules(
    min_opportunities=12,        # Min chances before advancement
    advancement_threshold=0.75,  # Success rate to advance
    regression_threshold=0.60,   # Fall below this to regress
    window_size=50,              # Rolling window for accuracy
)
```

### Gate Progression

To unlock the next gate, player must have **2+ skills** in `reliable` state within current gate.

---

## 8. API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/coach/<game_id>/stats` | GET | Fetch all coaching data |
| `/api/coach/<game_id>/ask` | POST | Ask a question or get proactive tip |
| `/api/coach/<game_id>/config` | GET/POST | Get/set coach mode |
| `/api/coach/<game_id>/hand-review` | POST | Get post-hand analysis |
| `/api/coach/<game_id>/progression` | GET | Get full progression state |
| `/api/coach/<game_id>/onboarding` | POST | Skip ahead to experience level |

---

## 9. Key Files

| File | Purpose |
|------|---------|
| `flask_app/services/coach_engine.py` | Main coaching data computation |
| `flask_app/services/coach_progression.py` | Skill progression service |
| `flask_app/services/skill_definitions.py` | Skill and gate definitions |
| `flask_app/routes/coach_routes.py` | API endpoints |
| `poker/decision_analyzer.py` | Equity calculation, action recommendation |
| `poker/hand_ranges.py` | Opponent range estimation |
| `poker/memory/opponent_model.py` | Opponent stats tracking |
| `poker/board_analyzer.py` | Board texture analysis |
| `react/react/src/hooks/useCoach.ts` | Frontend coach hook |
| `react/react/src/components/mobile/MobileActionButtons.tsx` | Button highlighting |

---

## 10. Related Documentation

- [Coach Progression Architecture](./COACH_PROGRESSION_ARCHITECTURE.md) — Detailed progression system design
- [Coach Progression Requirements](./COACH_PROGRESSION_REQUIREMENTS.md) — Requirements and specifications
