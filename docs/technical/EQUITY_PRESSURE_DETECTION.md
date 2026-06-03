---
purpose: Equity-based pressure event detection system for coolers, suckouts, and bad beats
type: spec
created: 2025-06-15
last_updated: 2026-06-03
---

# Equity-Based Pressure Event Detection

## Overview

This document describes the system for detecting equity-based pressure events (coolers, suckouts, bad beats) using retrospective equity calculation at showdown.

## Goals

Enable detection of psychologically significant poker events:
- **Cooler**: Both players had strong hands, unavoidable loss
- **Suckout**: Winner was behind on earlier street, got lucky
- **Got sucked out**: Player was ahead, then lost to luck
- **Enhanced bad_beat**: Use equity thresholds instead of just hand rank

## Architecture

### Key Decision: Retrospective at Showdown

Calculate equity **retrospectively at showdown** rather than during play:
- Hole cards only visible at showdown anyway
- No gameplay slowdown
- Perfect information for accurate calculation
- Fits existing PressureEventDetector pattern

### Data Flow

```
SHOWDOWN REACHED
    │
    ▼
handle_evaluating_hand_phase()
    │
    ├─► determine_winner() → winner_info
    │
    ├─► award_pot_winnings() → game_state updated
    │
    ├─► EquityTracker.calculate_hand_equity_history(hand_in_progress)
    │       (game_handler.py:3273; gated on hand_in_progress.hole_cards)
    │       For each street (PRE_FLOP, FLOP, TURN, RIVER) that was played:
    │         - Get hole cards from HandInProgress
    │         - Get board cards for that street
    │         - EquityCalculator.calculate_equity()
    │         - Build one EquitySnapshot per player (incl. folded, was_active=False)
    │       Return HandEquityHistory
    │
    ├─► HandEquityRepository.save_equity_history()
    │       (game_handler.py:3317; save to hand_equity table for analytics)
    │
    └─► PsychologyPipeline.process_hand()
            │
            ├─► PressureEventDetector.detect_equity_shock_events(equity_history)
            │       Detect: bad_beat, cooler, suckout, got_sucked_out
            │       Uses weighted-delta model (see below)
            │
            └─► PlayerPsychology.resolve_hand_events()
                    Apply to composure/energy axes (no confidence — luck events)
```

---

## Database Schema

### `hand_equity` Table

Added in **schema migration v69** (`schema_manager.py:_migrate_v69_add_hand_equity`,
`SCHEMA_VERSION` currently 140). `player_hole_cards` and `board_cards` are stored
as JSON arrays (e.g. `["As","Kd"]`), not the comma string shown in the older
example rows below.

```sql
CREATE TABLE hand_equity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- FKs with SET NULL - analytics data survives cleanup
    hand_history_id INTEGER REFERENCES hand_history(id) ON DELETE SET NULL,
    game_id TEXT REFERENCES games(game_id) ON DELETE SET NULL,

    -- Denormalized for self-contained analytics
    hand_number INTEGER NOT NULL,
    street TEXT NOT NULL,               -- 'PRE_FLOP', 'FLOP', 'TURN', 'RIVER'
    player_name TEXT NOT NULL,
    player_hole_cards TEXT,             -- JSON array, self-contained
    board_cards TEXT,                   -- JSON array, cards at this street
    equity REAL NOT NULL,
    was_active BOOLEAN DEFAULT 1,       -- False once this player has folded
    sample_count INTEGER,               -- Monte Carlo iterations (NULL = exact river)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(hand_history_id, street, player_name)
);

-- Indexes for common queries
CREATE INDEX idx_hand_equity_game ON hand_equity(game_id);
CREATE INDEX idx_hand_equity_hand ON hand_equity(hand_history_id);
CREATE INDEX idx_hand_equity_player ON hand_equity(player_name);
CREATE INDEX idx_hand_equity_street_equity ON hand_equity(street, equity);
```

### Data Retention

Uses `ON DELETE SET NULL` like other analytics tables (`api_usage`, `prompt_captures`). Equity data persists even if game or hand_history is deleted.

### Example Data

```
| hand_history_id | game_id | hand_number | street   | player_name | player_hole_cards | board_cards     | equity |
|-----------------|---------|-------------|----------|-------------|-------------------|-----------------|--------|
| 42              | abc123  | 5           | PRE_FLOP | Batman      | As,Ah             | NULL            | 0.82   |
| 42              | abc123  | 5           | PRE_FLOP | Joker       | Kd,Kc             | NULL            | 0.18   |
| 42              | abc123  | 5           | FLOP     | Batman      | As,Ah             | Ks,7h,2c        | 0.91   |
| 42              | abc123  | 5           | FLOP     | Joker       | Kd,Kc             | Ks,7h,2c        | 0.09   |
| 42              | abc123  | 5           | TURN     | Batman      | As,Ah             | Ks,7h,2c,Kh     | 0.05   |
| 42              | abc123  | 5           | TURN     | Joker       | Kd,Kc             | Ks,7h,2c,Kh     | 0.95   |
| 42              | abc123  | 5           | RIVER    | Batman      | As,Ah             | Ks,7h,2c,Kh,2d  | 0.00   |
| 42              | abc123  | 5           | RIVER    | Joker       | Kd,Kc             | Ks,7h,2c,Kh,2d  | 1.00   |
```

This shows a cooler: AA vs KK, King on turn gives Joker trips.

---

## Event Detection Thresholds

The current implementation uses a **weighted-delta model** rather than simple per-street threshold checking:

```
weighted_delta = equity_delta × pot_significance × street_weight
```

Street weights: FLOP = 1.0, TURN = 1.2, RIVER = 1.4

| Event | Detection Criteria | Description |
|-------|-------------------|-------------|
| `bad_beat` | Loser had ≥80% equity at worst swing, weighted delta ≥ 0.30 | Strong hand + big favorite, lost |
| `cooler` | Loser had 60-80% equity, weighted delta ≥ 0.30 | Unavoidable loss with strong hand |
| `suckout` | Winner was behind (opponent had ≥80% equity) | Got lucky to win |
| `got_sucked_out` | Loser had ≥80% equity on earlier street, lost | Was ahead, lost to luck |

Additional thresholds: `POT_SIGNIFICANCE_MIN = 0.15` (ignore trivial pots), priority: bad_beat > got_sucked_out > cooler > suckout.

---

## Core Components

> **Where detection lives now.** Equity-event detection is **not** on
> `EquityTracker` (it never carried the COOLER/SUCKOUT constants or the
> `_detect_cooler/_detect_suckout/_detect_got_sucked_out` helpers an earlier
> version of this doc described — those are gone). `EquityTracker` only *builds*
> the equity history; detection is `PressureEventDetector.detect_equity_shock_events`
> in `poker/pressure_detector.py`, using the weighted-delta model above.

### `EquitySnapshot` (dataclass, `poker/equity_snapshot.py`)

One row per player per street (folded players included, with `was_active=False`).

```python
@dataclass(frozen=True)
class EquitySnapshot:
    player_name: str
    street: str                      # 'PRE_FLOP', 'FLOP', 'TURN', 'RIVER'
    equity: float                    # win probability (0-1)
    hole_cards: Tuple[str, ...]
    board_cards: Tuple[str, ...]     # community cards at this street
    was_active: bool                 # False once this player has folded
    sample_count: Optional[int]      # Monte Carlo iterations (None = exact river)
```

### `HandEquityHistory` (dataclass, `poker/equity_snapshot.py`)

```python
@dataclass(frozen=True)
class HandEquityHistory:
    hand_history_id: Optional[int]
    game_id: str
    hand_number: int
    snapshots: Tuple[EquitySnapshot, ...]
```

Verify the exact accessor surface against `poker/equity_snapshot.py` before
relying on helper methods (e.g. `get_player_names`, `get_player_history` are the
methods `pressure_detector` calls) — **unverified** here beyond those two.

### `EquityTracker` (`poker/equity_tracker.py`)

Builds the per-street equity history. **No detection logic, no thresholds.**

```python
class EquityTracker:
    ITERATIONS_BY_STREET = {'PRE_FLOP': 1000, 'FLOP': 2000, 'TURN': 3000, 'RIVER': 5000}

    def calculate_hand_equity_history(
        self, hand: HandInProgress, folded_players: Optional[Set[str]] = None
    ) -> HandEquityHistory       # live path (game_handler.py:3273)

    def calculate_from_recorded_hand(
        self, recorded_hand: RecordedHand, hand_history_id: Optional[int] = None
    ) -> HandEquityHistory       # offline/replay path
```

### `PressureEventDetector.detect_equity_shock_events` (`poker/pressure_detector.py:239`)

The actual detector. Thresholds are class constants (`pressure_detector.py:226-237`):

```python
EQUITY_SHOCK_THRESHOLD = 0.30   # min |weighted delta| to fire
BAD_BEAT_EQUITY_MIN    = 0.80   # loser had 80%+ equity at worst swing
COOLER_EQUITY_MIN      = 0.60   # loser had 60-80% equity at worst swing
POT_SIGNIFICANCE_MIN   = 0.15   # ignore swings in trivial pots
STREET_WEIGHTS = {'FLOP': 1.0, 'TURN': 1.2, 'RIVER': 1.4}

def detect_equity_shock_events(
    self,
    equity_history: HandEquityHistory,
    winner_names: List[str],
    pot_size: int,
    hand_start_stacks: Dict[str, int],
) -> List[Tuple[str, List[str]]]
```

### `HandEquityRepository` (`poker/repositories/hand_equity_repository.py`)

```python
class HandEquityRepository:
    def save_equity_history(equity_history: HandEquityHistory) -> None
    def get_equity_history(hand_history_id: int) -> Optional[HandEquityHistory]
    def get_equity_history_by_game_hand(game_id, hand_number) -> Optional[HandEquityHistory]
    def get_player_equity_stats(player_name, game_id=None, limit=100) -> Dict
    def find_suckouts(game_id: str, threshold: float = 0.40) -> List[Dict]   # :229
    def find_coolers(game_id: str, min_equity: float = 0.30) -> List[Dict]   # :273
```

---

## Event Detection Logic

The detector walks each player's active snapshots, tracks the largest positive
and negative `weighted_delta = delta × pot_significance × street_weight`, and
fires **at most one** event per player by priority
`bad_beat > got_sucked_out > cooler > suckout`
(`pressure_detector.py:306-334`). `pot_significance = pot_size / hand_start_stack`
is **per player** (`:274`); players whose `pot_significance < POT_SIGNIFICANCE_MIN`
or who have fewer than two active snapshots are skipped.

| Event | Fires when (`pressure_detector.py`) |
|-------|-------------------------------------|
| `bad_beat` | lost AND equity before worst swing `≥ 0.80` AND `max_negative_wd ≤ -0.30` (`:313`) |
| `cooler` | lost AND `0.60 ≤` equity before worst swing `< 0.80` AND `max_negative_wd ≤ -0.30` (`:320`) |
| `got_sucked_out` | lost AND `max_negative_wd ≤ -0.30` (no equity floor) (`:327`) |
| `suckout` | won AND `max_positive_wd ≥ 0.30` (`:330`) |

> The earlier per-street `_detect_cooler/_detect_suckout/_detect_got_sucked_out`
> pseudocode (preflop-snapshot scans gated on `loser_hand_ranks` and `is_big_pot`)
> documented an API that no longer exists. The weighted-delta walk above replaced it.

---

## Example Queries

### Find all suckouts in a game

This mirrors `HandEquityRepository.find_suckouts` (turn equity below `threshold`,
default `0.40`, river effectively won at `> 0.99`):

```sql
SELECT turn.hand_number, turn.player_name,
       turn.equity AS turn_equity, river.equity AS river_equity,
       turn.player_hole_cards
FROM hand_equity turn
JOIN hand_equity river ON turn.hand_history_id = river.hand_history_id
                      AND turn.player_name = river.player_name
WHERE turn.game_id = ?
  AND turn.street = 'TURN'  AND turn.equity < 0.40   -- was behind on the turn
  AND river.street = 'RIVER' AND river.equity > 0.99  -- won
ORDER BY turn.hand_number
```

### Find coolers

```sql
SELECT he1.hand_history_id, he1.hand_number,
       he1.player_name as player1, he1.equity as eq1,
       he2.player_name as player2, he2.equity as eq2
FROM hand_equity he1
JOIN hand_equity he2 ON he1.hand_history_id = he2.hand_history_id
                    AND he1.street = he2.street
                    AND he1.player_name < he2.player_name
WHERE he1.game_id = ?
  AND he1.street = 'PRE_FLOP'
  AND he1.equity >= 0.30 AND he2.equity >= 0.30
```

---

## Performance

- **Monte Carlo iterations**: 2000 (fast mode for real-time)
- **Calculations per showdown**: 4 streets × ~20ms = ~80ms total
- **River uses exact calculation**: Instant (5 cards known)
- **Acceptable overhead**: Post-showdown, not blocking gameplay

---

## Files

| File | Purpose |
|------|---------|
| `poker/equity_snapshot.py` | Data structures (EquitySnapshot, HandEquityHistory) |
| `poker/equity_tracker.py` | Equity-history calculation (no detection) |
| `poker/repositories/hand_equity_repository.py` | Database persistence + analytics queries |
| `poker/repositories/schema_manager.py` | Schema migration v69 (`_migrate_v69_add_hand_equity`) |
| `poker/pressure_detector.py` | Event detection (`detect_equity_shock_events`, `:239`) |
| `poker/psychology_pipeline.py` | Orchestration (unified pipeline) |

---

## Related Documentation

- [PSYCHOLOGY_OVERVIEW.md](PSYCHOLOGY_OVERVIEW.md) - Overall psychology architecture
- [PSYCHOLOGY_ZONES_MODEL.md](PSYCHOLOGY_ZONES_MODEL.md) - Zone-based psychology system
- [PRESSURE_STATS_SYSTEM.md](PRESSURE_STATS_SYSTEM.md) - Pressure tracking
