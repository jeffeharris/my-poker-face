---
purpose: Architecture of the coaching system — engine, routes, skills, and coaching modes
type: architecture
created: 2026-02-04
last_updated: 2026-06-03
---

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

Stats require **≥15 hands observed** before being used for style labeling. Cross-session historical data is loaded when current session data is insufficient.

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
is_tight = vpip < 0.3
is_aggressive = aggression_factor > 1.5

if is_tight and is_aggressive:     return 'tight-aggressive'
elif not is_tight and is_aggressive: return 'loose-aggressive'
elif is_tight and not is_aggressive: return 'tight-passive'
else:                                return 'loose-passive'
```

### Cross-Session Persistence

When a game starts, historical opponent data is loaded from the database:
- Aggregated stats across all previous sessions
- Used when current session has < 10 hands
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
        'coaching_mode': 'learn',   # CoachingMode enum: 'learn' | 'compete' | 'silent'
        'primary_skill': 'bet_when_strong',
        'relevant_skills': ['bet_when_strong', 'checking_is_allowed'],
        'skill_states': {...}
    }
}
```

> The `coaching_mode` value is the `CoachingMode` enum (`poker/coach_models.py:34-39`):
> **`learn`** (teach concepts), **`compete`** (brief reminders), **`silent`** (no
> coaching, skill is automatic). There is no `teaching` mode. (`learn`/`compete`/`silent`
> are the *skill-state-driven* modes; the per-game config in §6 — `proactive`/`reactive`/
> `off` — is a separate, user-facing setting.)

### LLM tier (important)

The conversational coach (`CoachAssistant` in `coach_assistant.py`) runs on the
**Assistant LLM tier**, not the in-game Default tier. It builds its `Assistant` with
`get_assistant_provider()` / `get_assistant_model()` and tags calls
`CallType.COACHING` (`coach_assistant.py:226-228`). This is a deliberate fix: the Default
tier (an 8B-class model) hallucinated hand facts — e.g. narrating a "set of fives" on a
hand that ended preflop — so coaching was moved to the stronger Assistant tier. The
system prompt also hard-forbids inventing board-made hands the cards don't support. See
the root `CLAUDE.md` CallType→tier table.

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

In `MobilePokerTable.tsx`, the recommended action is derived from the coach's explicit recommendation (set when `/ask` returns):

```tsx
const recommendedAction = useMemo(() => {
  if (coach.mode === 'off') return null;
  return coach.coachAction;
}, [coach.mode, coach.coachAction]);

<MobileActionButtons
  recommendedAction={recommendedAction}
  raiseToAmount={raiseToAmount}
/>
```

The highlight source can be configured via `COACH_HIGHLIGHT_SOURCE` environment variable:
- `coach` (default): Uses LLM coach's recommendation from `/ask`
- `gto`: Uses GTO-based recommendation from stats

Each action button type has its own highlight color (fold/check/call/raise) with a pulsing glow animation.

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
- `introduced` — Initial state, coach introduces concept
- `practicing` — Player is learning, coach reinforces
- `reliable` — Player demonstrates competence
- `automatic` — Skill is mastered, minimal coaching needed

### Evidence Rules

```python
EvidenceRules(
    min_opportunities=12,        # Min chances before advancement
    advancement_threshold=0.75,  # Success rate to advance
    regression_threshold=0.60,   # Fall below this to regress
    window_size=20,              # Rolling window for accuracy (default; actual skills use 30)
)
```

### Gate Progression

To unlock the next gate, player must have **2+ skills** in `reliable` state within current gate.

---

## 8. API Endpoints

All player-facing routes are gated by `@require_permission('can_access_coach')`; the
`/metrics/*` routes are admin-only (`can_access_admin_tools`). Source: `coach_routes.py`.

**Per-game (in-hand) coaching:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/coach/<game_id>/stats` | GET | Fetch all coaching data (+progression) |
| `/api/coach/<game_id>/ask` | POST | Ask a question or get proactive tip (serves a prefetched tip when ready) |
| `/api/coach/<game_id>/config` | GET/POST | Get/set coach mode (`proactive`/`reactive`/`off`) |
| `/api/coach/<game_id>/hand-review` | POST | Get post-hand analysis (with skill evaluations) |
| `/api/coach/<game_id>/progression` | GET | Get full progression state |
| `/api/coach/<game_id>/onboarding` | POST | Skip ahead to experience level |

**User-scoped review surfaces (across the caller's real games, not one game):**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/coach/preflop-leaks` | GET | Your preflop range vs a reference; specific below-range hands you keep playing |
| `/api/coach/preflop-leaks/feedback` | POST | LLM (Assistant tier) interprets your recomputed leak profile |
| `/api/coach/drill` | GET | Build a preflop drill from your top confirmed leak |
| `/api/coach/drill/answer` | POST | Grade one drill answer against the solver chart |
| `/api/coach/opponent-tells` | GET | How readable an opponent's bet *sizing* is, with a stability trend |
| `/api/coach/sizing-readability` | GET | Self-coaching twin: how readable *your own* sizing is |
| `/api/coach/tip-effectiveness` | GET | Did you follow the solver line after a leak nudge, vs baseline |

**Admin metrics:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/coach/metrics/overview` | GET | Aggregate progression usage |
| `/api/coach/metrics/skills` | GET | Per-skill distribution |
| `/api/coach/metrics/advancement` | GET | Skill advancement timing/difficulty |
| `/api/coach/metrics/tip-effectiveness` | GET | Global (or `?owner=`) leak-nudge follow-through |

### 8.1 Sub-feature surface (services behind these routes)

Beyond the in-hand engine, the coach has several analysis surfaces, each backed by its own
service module:

| Surface | Service module(s) | Routes |
|---------|-------------------|--------|
| **Preflop leak detection** | `coach_leaks.py` (VPIP-by-position), `coach_chart_leaks.py` (chart-graded leaks, trends, depth/recent slices), `coach_chart_data.py` (loaders) | `/preflop-leaks`, `/preflop-leaks/feedback` |
| **Drills** | `coach_drill.py` (`pick_drill_leak`, `sample_drill_spots`, `grade_drill_answer`) | `/drill`, `/drill/answer` |
| **Sizing tells** | `coach_sizing_tells.py` (`compute_opponent_sizing_tell`, size→strength polarization + stability trend) | `/opponent-tells`, `/sizing-readability` |
| **Tip prefetch** | `coach_prefetch.py` (`prefetch_proactive_tip` fired at human turn-start, `take_cached_tip`) — hides LLM round-trip latency, guarantees one coach call per decision | (consumed by `/ask`) |
| **Tip instrumentation** | `coach_repository.py` (`record_tip`, `get_tip_effectiveness`) joined to `player_decision_analysis` | `/tip-effectiveness`, `/metrics/tip-effectiveness` |

> In the Circuit (cash mode), the over-time opponent sizing tell on `/opponent-tells` is
> gated behind the same `sizing_polarization` read the dossier sells (`_sizing_tell_locked`
> in `coach_routes.py`) — it fails open outside the Circuit.

---

## 9. Key Files

| File | Purpose |
|------|---------|
| `flask_app/services/coach_engine.py` | Main coaching data computation |
| `flask_app/services/coach_assistant.py` | LLM coach (Assistant tier, `CallType.COACHING`) |
| `flask_app/services/coach_progression.py` | Skill progression service |
| `flask_app/services/skill_definitions.py` | Skill and gate definitions |
| `poker/coach_models.py` | Shared enums/dataclasses (`SkillState`, `CoachingMode`, `EvidenceRules`, ...) |
| `flask_app/services/context_builder.py` | `build_poker_context()` helper |
| `flask_app/services/coach_leaks.py` / `coach_chart_leaks.py` / `coach_chart_data.py` | Preflop leak detection |
| `flask_app/services/coach_drill.py` | Preflop drills |
| `flask_app/services/coach_sizing_tells.py` | Bet-sizing readability tells |
| `flask_app/services/coach_prefetch.py` | Proactive tip prefetch |
| `poker/repositories/coach_repository.py` | Coach state + tip instrumentation persistence |
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
