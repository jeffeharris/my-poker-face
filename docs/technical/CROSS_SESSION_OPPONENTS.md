# Cross-Session Opponent Modeling

## Document Purpose

Requirements and architecture for persisting and aggregating opponent models across game sessions. This enables the coach to reference historical observations about returning opponents (e.g., "You've played 3 sessions against Batman — he tends to bluff on the river").

**Status**: Implemented
**Scope**: Data model, aggregation logic, coach integration, persistence fixes
**Related**: `COACH_PROGRESSION_REQUIREMENTS.md` §18.4

---

## 1. Problem Statement

Currently, opponent models are **per-game only**. When a player starts a new game, all learned tendencies about opponents are lost — even if the same AI personalities appear again.

This creates a missed opportunity for the coach:
- Cannot reference historical patterns ("Batman has been aggressive in your last 3 sessions")
- Cannot surface session counts ("This is your 5th game against Taylor Swift")
- Cannot track cross-session narrative observations

---

## 2. Solution Overview

The implementation provides **nested historical data** alongside current game stats. Rather than seeding/blending historical data into the current game's model (which could confuse the coach about current vs historical behavior), we keep them separate:

- **Current game stats**: Track the opponent's behavior in this specific session
- **Historical stats**: Aggregate data from all previous sessions, nested under a `historical` key

This allows the coach LLM to compare current behavior to historical patterns (e.g., "Batman is playing tighter than usual today — his current VPIP is 0.35 vs his historical 0.58").

---

## 3. Data Structure

### 3.1 Opponent Stats Format

Each opponent in the coaching data now has:

```python
{
    'name': 'Batman',
    # Current game (existing)
    'vpip': 0.42,
    'pfr': 0.35,
    'aggression': 1.8,
    'style': 'tight-aggressive',
    'hands_observed': 12,
    # Historical (new) - only present if player has history
    'historical': {
        'session_count': 3,
        'total_hands': 47,
        'vpip': 0.58,
        'pfr': 0.42,
        'aggression': 2.1,
        'style': 'loose-aggressive',
        'notes': ['Overvalues top pair', 'Bluffs missed draws'],
    }
}
```

### 3.2 Edge Cases

| Case | Behavior |
|------|----------|
| New player (no history) | `historical` key absent from opponent stats |
| Guest user | No `owner_id`, so no historical aggregation — current game only |
| First game against opponent | `historical` absent (no previous sessions) |
| Opponent name collision | Same name = same opponent (acceptable for AI personalities) |

---

## 4. Implementation Details

### 4.1 Notes Persistence Bug Fix

**Problem**: `OpponentModel.to_dict()` uses key `'narrative_observations'` but `save_opponent_models()` read `model_data.get('notes')` — always None.

**Fix in `game_repository.py`**:

```python
# save_opponent_models() - reads narrative_observations, serializes to JSON
narrative_obs = model_data.get('narrative_observations', [])
notes = json.dumps(narrative_obs) if narrative_obs else None

# load_opponent_models() - deserializes from JSON
notes_json = row['notes'] if 'notes' in row.keys() else None
narrative_observations = json.loads(notes_json) if notes_json else []
```

### 4.2 Cross-Session Aggregation Query

**New method**: `GameRepository.load_cross_session_opponent_models()`

```python
def load_cross_session_opponent_models(
    self,
    observer_name: str,
    user_id: str
) -> Dict[str, dict]:
    """Aggregate opponent stats across all games for this user.

    Returns dict mapping opponent_name -> {
        'session_count': int,
        'total_hands': int,
        'vpip': float,
        'pfr': float,
        'aggression_factor': float,
        'notes': List[str],
    }
    """
```

**SQL query**:
```sql
SELECT
    opponent_name,
    COUNT(DISTINCT om.game_id) as session_count,
    SUM(hands_observed) as total_hands,
    SUM(vpip * hands_observed) / SUM(hands_observed) as vpip,
    SUM(pfr * hands_observed) / SUM(hands_observed) as pfr,
    SUM(aggression_factor * hands_observed) / SUM(hands_observed) as aggression,
    GROUP_CONCAT(notes, '|||') as all_notes
FROM opponent_models om
JOIN games g ON om.game_id = g.game_id
WHERE om.observer_name = ?
  AND g.owner_id = ?
  AND om.hands_observed > 0
GROUP BY opponent_name
```

### 4.3 Coach Engine Integration

**Updated function**: `_get_opponent_stats()` in `coach_engine.py`

```python
def _get_opponent_stats(game_data: dict, human_name: str, user_id: str = None) -> List[Dict]:
    """Extract opponent stats with historical context."""

    # Load historical data if user_id provided
    historical_data = {}
    if user_id:
        historical_data = game_repo.load_cross_session_opponent_models(human_name, user_id)

    stats = []
    for player in game_state.players:
        # Build current game stats
        stat_entry = {
            'name': player.name,
            'vpip': ...,  # current game
            'pfr': ...,
            'aggression': ...,
            'style': ...,
            'hands_observed': ...,
        }

        # Add historical if available
        if player.name in historical_data:
            hist = historical_data[player.name]
            stat_entry['historical'] = {
                'session_count': hist['session_count'],
                'total_hands': hist['total_hands'],
                'vpip': hist['vpip'],
                'pfr': hist['pfr'],
                'aggression': hist['aggression_factor'],
                'style': _get_style_label(hist['vpip'], hist['aggression_factor']),
                'notes': hist['notes'][:5],  # Most recent 5
            }

        stats.append(stat_entry)

    return stats
```

---

## 5. Files Modified

| File | Changes |
|------|---------|
| `poker/repositories/game_repository.py` | Added `load_cross_session_opponent_models()` method, fixed notes key mismatch in `save_opponent_models()` and `load_opponent_models()` |
| `flask_app/services/coach_engine.py` | Added `_get_style_label()` helper, updated `_get_opponent_stats()` to accept `user_id` and add nested `historical` block, updated `compute_coaching_data()` to accept and pass through `user_id` |

**Not modified** (no seeding approach):
- `poker/memory/opponent_model.py` — no changes needed
- `flask_app/routes/game_routes.py` — no seeding call needed

---

## 6. Testing

### 6.1 Unit Tests (in `tests/test_repositories/test_game_repository.py`)

| Test | Description |
|------|-------------|
| `test_opponent_models_narrative_observations_roundtrip` | Verifies narrative observations survive save/load cycle |
| `test_cross_session_opponent_models_aggregation` | Verifies weighted averages across games |
| `test_cross_session_distinct_session_count` | session_count = distinct game_ids |
| `test_cross_session_no_data_for_guest` | Guest users get no historical data |
| `test_cross_session_filters_by_owner_id` | Only aggregates user's own games |
| `test_cross_session_excludes_zero_hand_observations` | Zero-hand records excluded |

### 6.2 Manual Validation

1. Play 2+ games against same AI opponent
2. Open coach panel in new game
3. Check opponent stats include `historical` with session_count > 1
4. Verify notes from previous games appear

---

## 7. Coach Usage

The coach LLM receives historical data in the opponent stats and can generate insights like:

> "You've played 3 sessions against Batman (47 total hands). He's historically loose-aggressive (VPIP 58%, aggression 2.1), but today he's playing tighter (VPIP 35%). Your previous notes: 'Overvalues top pair', 'Bluffs missed draws'."

The nested structure lets the LLM decide what's worth highlighting — it might surface:
- Session count for context
- Deviations from historical patterns
- Relevant historical notes
- Or nothing if the data isn't actionable

---

## 8. Migration Notes

No schema migration required — the `opponent_models` and `memorable_hands` tables already support the necessary columns. The `notes` column exists but was previously always NULL due to the key mismatch bug.

Existing data in `notes` column (if any) is treated as JSON string of observation list. Empty strings are treated as empty list.

---

**End of Document**
