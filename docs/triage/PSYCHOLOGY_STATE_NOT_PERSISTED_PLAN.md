---
purpose: Implementation blueprint for T1-29 — add psychology_json column and wire save/restore round-trip
type: spec
created: 2026-05-15
last_updated: 2026-05-15
---

# T1-29 Fix Plan — psychology state not persisted

Every game restore wipes psychology state. The bug is entirely in the repository layer; both callers (`psychology_pipeline._save_state()` and `game_handler.py:1857`) already pass `psychology=controller.psychology.to_dict()` correctly. The repository's `save_controller_state()` then immediately discards it by reading `.get('tilt')` and `.get('elastic')` — keys absent in the v2.1 schema — and writes `NULL` to both legacy columns. On restore, `_build_controller_state_dict()` never emits a `psychology` key, so `game_handler.py:361`'s `ctrl_state.get('psychology')` check is always falsy and bots reset.

## Files to modify

| File | Lines | Change |
|---|---|---|
| `poker/repositories/schema_manager.py` | :54 | Bump `SCHEMA_VERSION` 82 → 83 |
| `poker/repositories/schema_manager.py` | :359-371 | Add `psychology_json TEXT` to `_init_db()` `CREATE TABLE controller_state` |
| `poker/repositories/schema_manager.py` | :1092 | Register `_migrate_v83_add_psychology_json` in migrations dict |
| `poker/repositories/schema_manager.py` | append | Implement `_migrate_v83_add_psychology_json()` |
| `poker/repositories/game_repository.py` | :484-499 | Rewrite `save_controller_state()` body |
| `poker/repositories/game_repository.py` | :502-516 | Rewrite `_build_controller_state_dict()` |
| `poker/repositories/game_repository.py` | :526 | Add `psychology_json` to SELECT in `load_controller_state()` |
| `poker/repositories/game_repository.py` | :545 | Add `psychology_json` to SELECT in `load_all_controller_states()` |
| `tests/test_repositories/test_game_repository.py` | :251-284 | Update 4 existing + add 3 new tests |

**No changes needed:**
- `flask_app/handlers/game_handler.py` — already checks `ctrl_state.get('psychology')` and calls `PlayerPsychology.from_dict()`
- `poker/psychology_pipeline.py` — `_save_state()` already passes `psychology=psychology_dict`
- `experiments/run_ai_tournament.py` — no direct save/load calls

## Schema migration v83

```sql
-- _init_db() controller_state CREATE TABLE — add column:
psychology_json TEXT,
```

```python
def _migrate_v83_add_psychology_json(self, conn):
    """v83: add psychology_json to controller_state for v2.1 psychology."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(controller_state)")}
    if 'psychology_json' not in existing:
        conn.execute("ALTER TABLE controller_state ADD COLUMN psychology_json TEXT")
        logger.info("Added psychology_json column to controller_state")

# In migrations dict:
83: (self._migrate_v83_add_psychology_json,
     "Add psychology_json to controller_state for v2.1 psychology persistence"),
```

The old `tilt_state_json` and `elastic_personality_json` columns stay in place (not dropped) — they'll be NULL on new writes but legacy data is preserved.

## Save-side sketch

```python
def save_controller_state(self, game_id, player_name, psychology, prompt_config=None):
    """Save unified psychology state and prompt config for a player.

    Args:
        psychology: Dict from PlayerPsychology.to_dict()
        prompt_config: Dict from PromptConfig.to_dict() (optional)
    """
    with self._get_connection_with_retry() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO controller_state
            (game_id, player_name, psychology_json, prompt_config_json, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            game_id,
            player_name,
            json.dumps(psychology) if psychology else None,
            json.dumps(prompt_config) if prompt_config else None,
        ))
```

## Restore-side sketch

```python
@staticmethod
def _build_controller_state_dict(row, player_name=''):
    psychology = None
    try:
        if row['psychology_json']:
            psychology = json.loads(row['psychology_json'])
    except (KeyError, IndexError):
        if player_name:
            logger.debug(f"psychology_json column not found for {player_name}; fresh-init fallback")

    prompt_config = None
    try:
        if row['prompt_config_json']:
            prompt_config = json.loads(row['prompt_config_json'])
    except (KeyError, IndexError):
        pass

    return {
        'psychology': psychology,     # None → game_handler falls back to fresh-init
        'prompt_config': prompt_config,
        # Legacy keys preserved for any pre-v83 callers:
        'tilt_state': json.loads(row['tilt_state_json']) if row['tilt_state_json'] else None,
        'elastic_personality': (
            json.loads(row['elastic_personality_json']) if row['elastic_personality_json'] else None
        ),
    }
```

Update both SELECTs:
```python
# load_controller_state() ~line 526
SELECT tilt_state_json, elastic_personality_json, prompt_config_json, psychology_json
# load_all_controller_states() ~line 545
SELECT player_name, tilt_state_json, elastic_personality_json, prompt_config_json, psychology_json
```

**NULL fallback:** `psychology_json` is NULL for pre-v83 rows. `_build_controller_state_dict` returns `psychology: None`. `game_handler.py:361` skips the restoration block and the controller keeps its freshly-initialized psychology — same as today's broken behavior, but now intentional.

## Test plan

### Update 4 existing tests (`tests/test_repositories/test_game_repository.py:251-284`)

Replace old `{'tilt': ..., 'elastic': ...}` dicts with v2.1 shape:

```python
SAMPLE_PSYCHOLOGY_V2 = {
    'player_name': 'Batman',
    'anchors': {
        'baseline_aggression': 0.7, 'baseline_looseness': 0.4,
        'ego': 0.8, 'poise': 0.6, 'expressiveness': 0.5,
        'risk_identity': 0.6, 'adaptation_bias': 0.5,
        'baseline_energy': 0.6, 'recovery_rate': 0.15,
    },
    'axes': {'confidence': 0.65, 'composure': 0.45, 'energy': 0.70},
    'composure_state': {
        'pressure_source': 'bad_beat', 'nemesis': 'Joker',
        'recent_losses': [], 'losing_streak': 2,
    },
    'hand_count': 12,
    'consecutive_folds': 1,
    'emotional': None,
    'playstyle_state': None,
}
```

### Three new tests

1. **`test_psychology_full_round_trip`** — Build `PlayerPsychology.from_personality_config()`, apply `bad_beat` event, save, load, reconstruct via `from_dict()`, assert `axes` and `composure_state` match within `1e-4`.
2. **`test_controller_state_null_psychology_is_none`** — Insert a NULL row directly (simulating pre-v83). Load and assert `loaded['psychology']` is falsy. Confirms handler fallback isn't broken.
3. **`test_load_all_controller_states_psychology_populated`** — Save states for two players with v2.1 dicts; `load_all_controller_states()` returns both with non-None `psychology.axes`.

### Integration test (new file `tests/test_persistence.py`)

Wire `PsychologyPipeline` with a real `GameRepository` (tempdb fixture). Run `_save_state()`, then `load_all_controller_states()` and reconstruct `PlayerPsychology`. Assert `axes` values match pre-save objects within float tolerance.

## Risks / open questions

**Serialization completeness** — `to_dict()` excludes private fields (`_poker_face_zone`, `_baseline_confidence`, `_baseline_composure`, `_identity_biases`, `_emotional_generator`). All recomputed deterministically from `anchors` in `__post_init__`. Round-trip safe.

**`emotional` field double-storage** — After this fix, `psychology_json` includes `emotional.to_dict()` inline. The `emotional_state` table still gets a separate row. On restore, `game_handler.py:378` only loads from `emotional_state` as a fallback when `ctrl_state.get('psychology')` is falsy — meaning it won't be reached after this fix. The table becomes redundant but causes no conflict; defer cleanup.

**Blob size** — Full `PlayerPsychology.to_dict()` is ~1,200 bytes JSON. Well within SQLite TEXT limits.

**Migration safety** — `ALTER TABLE ADD COLUMN` is always safe in SQLite (no rewrite). The try/except in `_build_controller_state_dict` handles the brief window between migration registration and application.

**Old DB rows** — Any row written before v83 has NULL `psychology_json`. Restore produces `psychology: None`, triggering fresh-init. Identical to current behavior; correct default.

**Deployment** — Schema migrations run automatically via `ensure_schema()` on server start. Docker restart is all that's needed.
