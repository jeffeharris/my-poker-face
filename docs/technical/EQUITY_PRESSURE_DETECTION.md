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
    ├─► EquityTracker.calculate_showdown_equity_history()
    │       For each street (PRE_FLOP, FLOP, TURN, RIVER):
    │         - Get hole cards from recorded_hand
    │         - Get board cards for that street
    │         - EquityCalculator.calculate_equity()
    │         - Build EquitySnapshot
    │       Return HandEquityHistory
    │
    ├─► HandEquityRepository.save_hand_equity_history()
    │       Save to hand_equity table for analytics
    │
    └─► handle_pressure_events()
            │
            └─► PressureEventDetector.detect_showdown_events(equity_history)
                    Detect: cooler, suckout, got_sucked_out
                    │
                    └─► ElasticityManager.apply_game_event()
```

---

## Database Schema

### `hand_equity` Table

```sql
CREATE TABLE hand_equity (
    id INTEGER PRIMARY KEY,

    -- FKs with SET NULL - analytics data survives cleanup
    hand_history_id INTEGER REFERENCES hand_history(id) ON DELETE SET NULL,
    game_id TEXT REFERENCES games(game_id) ON DELETE SET NULL,

    -- Denormalized for self-contained analytics
    hand_number INTEGER NOT NULL,
    street TEXT NOT NULL,               -- 'PRE_FLOP', 'FLOP', 'TURN', 'RIVER'
    player_name TEXT NOT NULL,
    player_hole_cards TEXT,             -- "As,Kd" - self-contained
    board_cards TEXT,                   -- Cards at this street
    equity REAL NOT NULL,
    sample_count INTEGER,               -- Monte Carlo iterations (NULL = exact)
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

| Event | Threshold | Description |
|-------|-----------|-------------|
| `cooler` | Both players ≥30% pre-flop equity, loser hand_rank ≤4 | Unavoidable loss with strong hand |
| `suckout` | Winner <30% equity on any earlier street | Got lucky to win |
| `got_sucked_out` | Loser >70% equity on any earlier street | Was ahead, lost to luck |
| `bad_beat` (enhanced) | Loser >70% equity + hand_rank ≤4 | Strong hand + big favorite, lost |

---

## Core Components

### `EquitySnapshot` (dataclass)

```python
@dataclass(frozen=True)
class EquitySnapshot:
    street: str                      # 'PRE_FLOP', 'FLOP', 'TURN', 'RIVER'
    equities: Dict[str, float]       # player_name -> win probability (0-1)
    board_cards: Tuple[str, ...]     # Community cards at this street
    sample_count: int                # Monte Carlo iterations used
```

### `HandEquityHistory` (dataclass)

```python
@dataclass(frozen=True)
class HandEquityHistory:
    snapshots: Tuple[EquitySnapshot, ...]

    def get_preflop_equity(self, player: str) -> float
    def get_equity_at_street(self, player: str, street: str) -> float
    def was_behind_then_won(self, player: str, threshold: float = 0.30) -> bool
    def was_ahead_then_lost(self, player: str, threshold: float = 0.70) -> bool
```

### `EquityTracker`

```python
class EquityTracker:
    COOLER_MIN_EQUITY = 0.30
    COOLER_MIN_HAND_RANK = 4
    SUCKOUT_THRESHOLD = 0.30
    BAD_BEAT_THRESHOLD = 0.70

    def calculate_showdown_equity_history(
        self,
        hole_cards: Dict[str, List[str]],
        community_cards: List[str],
        phase_community: Dict[str, List[str]]
    ) -> HandEquityHistory

    def detect_equity_events(
        self,
        equity_history: HandEquityHistory,
        winner_names: List[str],
        loser_hand_ranks: Dict[str, int],
        is_big_pot: bool
    ) -> List[Tuple[str, List[str]]]
```

### `HandEquityRepository`

```python
class HandEquityRepository:
    def save_hand_equity_history(...)
    def get_equity_history_for_hand(hand_history_id: int) -> HandEquityHistory
    def find_suckouts(game_id: str, threshold: float = 0.30) -> List[Dict]
    def find_coolers(game_id: str, min_equity: float = 0.30) -> List[Dict]
```

---

## Event Detection Logic

### Cooler Detection

```python
def _detect_cooler(self, equity_history, loser_hand_ranks, winner_names):
    preflop = equity_history.get_snapshot_for_street('PRE_FLOP')
    if not preflop:
        return []

    # Both players had reasonable pre-flop equity AND loser had strong hand
    strong_players = [
        p for p, eq in preflop.equities.items()
        if eq >= 0.30 and (
            p in winner_names or
            loser_hand_ranks.get(p, 10) <= 4  # Straight or better
        )
    ]

    if len(strong_players) >= 2:
        losers = [p for p in strong_players if p not in winner_names]
        return [("cooler", losers)] if losers else []
    return []
```

### Suckout Detection

```python
def _detect_suckout(self, equity_history, winner_names, is_big_pot):
    if not is_big_pot:
        return []

    suckout_players = []
    for winner in winner_names:
        for street in ['PRE_FLOP', 'FLOP', 'TURN']:
            equity = equity_history.get_equity_at_street(winner, street)
            if equity and equity < 0.30:
                suckout_players.append(winner)
                break

    return [("suckout", suckout_players)] if suckout_players else []
```

### Got Sucked Out Detection

```python
def _detect_got_sucked_out(self, equity_history, winner_names, losers, is_big_pot):
    if not is_big_pot:
        return []

    victims = []
    for loser in losers:
        for street in ['PRE_FLOP', 'FLOP', 'TURN']:
            equity = equity_history.get_equity_at_street(loser, street)
            if equity and equity > 0.70:
                victims.append(loser)
                break

    return [("got_sucked_out", victims)] if victims else []
```

---

## Example Queries

### Find all suckouts in a game

```sql
SELECT DISTINCT he1.hand_history_id, he1.hand_number, he1.player_name,
       he1.player_hole_cards, he1.equity as was_behind
FROM hand_equity he1
JOIN hand_equity he2 ON he1.hand_history_id = he2.hand_history_id
                    AND he1.player_name = he2.player_name
WHERE he1.game_id = ?
  AND he1.street IN ('PRE_FLOP', 'FLOP', 'TURN')
  AND he1.equity < 0.30           -- Was behind
  AND he2.street = 'RIVER'
  AND he2.equity = 1.0            -- Won
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
| `poker/equity_tracker.py` | Calculation + event detection |
| `poker/repositories/hand_equity_repository.py` | Database persistence |
| `poker/repositories/schema_manager.py` | Schema migration v68 |
| `poker/pressure_detector.py` | Integration point |
| `flask_app/handlers/game_handler.py` | Orchestration |

---

## Related Documentation

- [AI_PSYCHOLOGY_SYSTEMS.md](AI_PSYCHOLOGY_SYSTEMS.md) - Overall psychology architecture
- [ELASTICITY_SYSTEM.md](ELASTICITY_SYSTEM.md) - Trait elasticity system
- [PRESSURE_STATS_SYSTEM.md](PRESSURE_STATS_SYSTEM.md) - Pressure tracking
