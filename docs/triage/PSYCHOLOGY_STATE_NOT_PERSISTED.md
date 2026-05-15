---
purpose: Pre-main blocker — psychology refactor broke persistence; every game restore wipes psychology state
type: reference
created: 2026-05-15
last_updated: 2026-05-15
---

# Psychology state is never actually persisted after the refactor

**Severity:** T1 (must-fix before main)
**Confidence:** 100%
**Discovered:** pre-main review, 2026-05-15
**Files:**
- `poker/repositories/game_repository.py:484-499` — `save_controller_state()`
- `flask_app/handlers/game_handler.py:361-378` — restore path
- `poker/psychology_pipeline.py` — `_save_state()`

## What's broken

The psychology refactor replaced the legacy `tilt` / `elastic` shape with a richer one (`anchors`, `axes`, `composure_state`, `playstyle_state`, `hand_count`, `consecutive_folds`, etc.), but the persistence layer was never updated. The result is that every game restore wipes psychology state and resets all players to defaults.

### The two ends of the bug

**Save side** — `game_repository.save_controller_state()`:
```python
tilt_state = psychology.get('tilt')           # → None (key removed in new to_dict())
elastic_personality = psychology.get('elastic') # → None
# Both columns written as NULL
```

`PlayerPsychology.to_dict()` no longer emits `tilt` or `elastic` keys. Both `tilt_state_json` and `elastic_personality_json` columns are always written as `NULL`.

**Restore side** — `_build_controller_state_dict()` returns:
```python
{'tilt_state': None, 'elastic_personality': None, 'prompt_config': ...}
```

`game_handler.py:361` checks `ctrl_state.get('psychology')` — that key is not in the returned dict, so the condition is always falsy. The fallback path reads `ctrl_state.get('tilt_state')` which is also `None`. Result: every game restore resets all players to default psychology, discarding the entire session's axes, composure history, nemesis, playstyle, and consecutive-fold count.

### Why earlier "restore" fixes didn't catch this

Recent commit `546babf6 fix(restore): apply psychology/emotional state to all controller paths` wired the path to apply psychology to all controller types — but the data flowing through that path is always empty (`None`). Same for `261373d8` (HybridAI default) and `2e2d7d7a` (pressure_stats). These fixed wiring; this issue is the data itself.

## Why it matters

Psychology drives bot decision-making (composure influences fold/call thresholds, anchors set baseline behavior, axes accumulate session pressure). Wiping it on restart means:
- Tilt/composure drift accumulated in a game evaporates on restore.
- Bots reset to neutral mood after any server restart, even mid-tournament.
- Long sessions show inconsistent behavior across reconnects.

## Fix

1. Add `psychology_json TEXT` column to `controller_state` table (schema migration v83+).
2. `save_controller_state()` stores the full `psychology.to_dict()` output in the new column.
3. `_build_controller_state_dict()` returns it under the key `psychology`.
4. `restore_ai_controllers` calls `PlayerPsychology.from_dict(...)` on the loaded blob.

Old DB rows: `psychology_json` is NULL → fall back to default initialization (current behavior).

## Cross-references

- `PsychologyPipeline._save_state()` (psychology_pipeline.py) has the same defect — it calls into `save_controller_state` which strips the dict.
- `_apply_recovery()` writes `_gravity` events to a state that won't survive restore.
- All `psychology_json` writes from the pipeline are no-ops without the new column.

## Test plan

1. Start a game, play 20 hands, build up some tilt/composure changes.
2. Stop the server, restart, load the game.
3. Verify `controller.psychology.axes` matches pre-restart values.
4. Add a regression test in `tests/test_persistence.py` that round-trips a non-default `PlayerPsychology` through DB.
