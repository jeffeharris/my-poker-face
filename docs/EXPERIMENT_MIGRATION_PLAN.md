# Experiment Routes Migration Plan

## Overview

Migrate `flask_app/routes/experiment_routes.py` from legacy `GamePersistence` to the new repository pattern. This is the final piece of the repository migration.

## Current State

- `experiment_routes.py` uses 15 persistence methods
- 7 already have equivalents in new repositories (direct swap)
- 6 need new methods added to `experiment_repository.py`
- Legacy `persistence` object imported from extensions for backwards compatibility

## Phase 1: Direct Swaps (Low Risk)

These methods already exist in new repositories - just update the calls:

| Old Call | New Call |
|----------|----------|
| `persistence.load_game(game_id)` | `repo.game.find_by_id(game_id)` |
| `persistence.save_game(game_id, sm, owner)` | `repo.game.save_from_state_machine(game_id, sm, owner)` |
| `persistence.load_ai_player_states(game_id)` | `repo.ai_memory.load_player_states(game_id)` |
| `persistence.create_experiment(config)` | `repo.experiment.create_experiment(config)` |
| `persistence.get_experiment(id)` | `repo.experiment.get_experiment(id)` |
| `persistence.get_experiment_by_name(name)` | `repo.experiment.get_experiment_by_name(name)` |
| `persistence.list_experiments(status, limit, offset)` | `repo.experiment.list_experiments(status, limit)` |
| `persistence.get_experiment_games(id)` | `repo.experiment.get_experiment_games(id)` |

### Steps:
1. Add `from ..extensions import get_repository_factory` to experiment_routes.py
2. Replace each persistence call with repository equivalent
3. Handle any return type differences (entities vs dicts)

## Phase 2: Simple New Methods (Low Risk)

Add these to `poker/repositories/sqlite/experiment_repository.py`:

### 2.1 `update_experiment_status(experiment_id, status, error_message=None)`
```python
def update_experiment_status(self, experiment_id: int, status: str, error_message: str = None) -> None:
    """Update experiment status and optionally error message."""
    with self._db.transaction() as conn:
        if error_message:
            conn.execute(
                "UPDATE experiments SET status = ?, error_message = ?, updated_at = ? WHERE id = ?",
                (status, error_message, datetime.now().isoformat(), experiment_id)
            )
        else:
            conn.execute(
                "UPDATE experiments SET status = ?, updated_at = ? WHERE id = ?",
                (status, datetime.now().isoformat(), experiment_id)
            )
```

### 2.2 `complete_experiment(experiment_id, summary)`
```python
def complete_experiment(self, experiment_id: int, summary: Dict[str, Any]) -> None:
    """Mark experiment as completed with summary."""
    with self._db.transaction() as conn:
        conn.execute(
            "UPDATE experiments SET status = 'completed', summary_json = ?, updated_at = ? WHERE id = ?",
            (to_json(summary), datetime.now().isoformat(), experiment_id)
        )
```

### 2.3 `get_incomplete_tournaments(experiment_id)`
```python
def get_incomplete_tournaments(self, experiment_id: int) -> List[Dict[str, Any]]:
    """Get games/tournaments that haven't completed."""
    rows = self._db.fetch_all(
        """
        SELECT game_id, variant_name, variant_index, tournaments_target, tournaments_completed
        FROM experiment_games
        WHERE experiment_id = ? AND tournaments_completed < tournaments_target
        """,
        (experiment_id,)
    )
    return [dict(row) for row in rows]
```

## Phase 3: Complex Analytics Methods (Medium Risk)

These are 100-170 line methods with complex SQL. Options:

### Option A: Move SQL to experiment_repository.py (Recommended)
Copy the existing SQL logic from `persistence.py` into new repository methods:
- `get_experiment_decision_stats(experiment_id)`
- `get_experiment_live_stats(experiment_id)`
- `get_experiment_game_snapshots(experiment_id)`
- `get_experiment_player_detail(experiment_id, game_id, player_name)`

### Option B: Create ExperimentAnalyticsRepository
If these grow, consider a separate repository for analytics-only queries.

### Implementation Notes:
- These methods return dicts, not entities (they're aggregations)
- Keep return format compatible with existing frontend expectations
- Copy SQL from persistence.py lines: 4725, 5112, 5284, 5432

## Phase 4: Cleanup

1. Remove `persistence` backwards-compat from `flask_app/extensions.py`
2. Remove `from poker.persistence import GamePersistence` imports
3. Update `flask_app/__init__.py` - migrate `recover_interrupted_experiments()` to use repository
4. Remove or deprecate `poker/persistence.py` (5,400 lines) if no longer needed

## Testing Checklist

- [ ] Create new experiment works
- [ ] List experiments with status filter works
- [ ] Get experiment details works
- [ ] Start experiment (background execution) works
- [ ] Pause experiment works
- [ ] Resume experiment works (critical - loads saved state)
- [ ] Live stats endpoint returns data
- [ ] Decision stats endpoint returns data
- [ ] Game snapshots endpoint returns data
- [ ] Player detail endpoint returns data
- [ ] Cost trends endpoint works

## Files to Modify

1. `flask_app/routes/experiment_routes.py` - Main migration
2. `poker/repositories/sqlite/experiment_repository.py` - Add new methods
3. `poker/repositories/protocols.py` - Add method signatures to protocol
4. `flask_app/extensions.py` - Remove persistence backwards-compat (Phase 4)
5. `flask_app/__init__.py` - Update recover_interrupted_experiments (Phase 4)

## Estimated Effort

- Phase 1: 1-2 hours (direct swaps)
- Phase 2: 1 hour (simple new methods)
- Phase 3: 3-4 hours (complex analytics)
- Phase 4: 1 hour (cleanup)
- Testing: 2 hours

Total: ~8-10 hours

## Risk Mitigation

1. **Pause/Resume is critical path** - Test thoroughly before/after
2. **Keep persistence.py until fully migrated** - Don't delete prematurely
3. **Return type compatibility** - New repos return entities, old returned dicts. May need `.to_dict()` or compatibility layer
4. **Background thread context** - Ensure repository factory works in background threads (should be fine - it's thread-local connections)
