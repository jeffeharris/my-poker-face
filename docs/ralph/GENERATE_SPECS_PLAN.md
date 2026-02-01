# Plan: Generate Ralph Wiggum Specs for Persistence Refactor Batches 1-6

## Context

Batch 0 of the persistence refactor (T3-35) is complete. It established:
- `poker/repositories/base_repository.py` — BaseRepository with thread-local connection pooling
- `poker/repositories/schema_manager.py` — SchemaManager (extracted from GamePersistence)
- `poker/repositories/serialization.py` — Card/state serialization as pure functions
- `persistence.py` reduced from 8,979 → 6,136 lines

**Full refactoring plan**: `.claude/plans/functional-napping-forest.md`

## Task

Generate `docs/ralph/spec.md` and `docs/ralph/implementation_plan.md` for Ralph Wiggum to execute Batches 1-6. Each batch is one Ralph task.

## Ralph Format

See `/home/jeffh/projects/ralph-wiggum/` for the existing format:
- `spec.md` — Per-task specs with Problem, Action, Acceptance Criteria
- `implementation_plan.md` — Ordered checkboxed task list
- `prompt.md` — Ralph's execution instructions
- One task per Ralph invocation, commit after each

Commit format: `refactor(T3-35-B{N}): extract {RepositoryName} from GamePersistence`

## Per-Batch Spec Template

Each batch spec needs:

### 1. Task ID and description
### 2. Exact methods to extract (with source line ranges from current persistence.py)
### 3. Target repository file path
### 4. Tables the repository manages
### 5. How to wire the facade delegation in GamePersistence
### 6. Test expectations
### 7. Acceptance criteria (tests pass, no regressions)

## Batches to Generate Specs For

### Batch 1: AppSettings + GuestTracking (T3-35-B1)
- `poker/repositories/settings_repository.py` (~100 lines)
  - `get_setting()`, `set_setting()`, `get_all_settings()`, `delete_setting()`
- `poker/repositories/guest_tracking_repository.py` (~60 lines)
  - `increment_hands_played()`, `get_hands_played()`

### Batch 2: Personality + Avatar (T3-35-B2)
- `poker/repositories/personality_repository.py` (~500 lines)
  - Personality CRUD: `save_personality()`, `load_personality()`, `list_personalities()`, `delete_personality()`, `increment_personality_usage()`, `seed_personalities_from_json()`
  - Avatar CRUD: `save_avatar_image()`, `load_avatar_image()`, `load_avatar_image_with_metadata()`, `load_full_avatar_image()`, `has_avatar_image()`, `has_full_avatar_image()`, `has_all_avatar_emotions()`, `get_available_avatar_emotions()`, `delete_avatar_images()`, `list_personalities_with_avatars()`, `get_avatar_stats()`
  - Tables: `personalities`, `avatar_images`

### Batch 3: User + RBAC (T3-35-B3)
- `poker/repositories/user_repository.py` (~580 lines)
  - User management + ownership + RBAC methods
  - Tables: `users`, `groups`, `user_groups`, `permissions`, `group_permissions`

### Batch 4: Experiments (T3-35-B4) — LARGEST
- `poker/repositories/experiment_repository.py` (~1800 lines)
  - Experiment lifecycle, games, chat sessions, analytics
  - Prompt captures, decision analysis, labels, presets, replay
  - Tables: `experiments`, `experiment_games`, `experiment_chat_sessions`, `prompt_captures`, `player_decision_analysis`, `capture_labels`, `prompt_presets`, `replay_experiment_captures`, `replay_results`, `reference_images`

### Batch 5: Game State (T3-35-B5) — CORE
- `poker/repositories/game_repository.py` (~800 lines)
  - Game CRUD, messages, AI state, emotional/controller state, tournament tracker
  - Uses `serialization.py` for card/state serialization
  - Tables: `games`, `game_messages`, `ai_player_state`, `personality_snapshots`, `emotional_state`, `controller_state`, `tournament_tracker`

### Batch 6: Hand History + Tournament + LLM (T3-35-B6)
- `poker/repositories/hand_history_repository.py` (~520 lines)
- `poker/repositories/tournament_repository.py` (~400 lines)
- `poker/repositories/llm_repository.py` (~250 lines)

## Per-Batch Process (for Ralph to follow)

1. **Create repository** extending `BaseRepository` from `poker/repositories/base_repository.py`
2. **Copy methods** from `GamePersistence` in `poker/persistence.py` — exact logic, no behavior changes
3. **Use `self._get_connection()`** from BaseRepository (context manager with commit/rollback)
4. **Write tests** in `tests/test_repositories/test_{name}_repository.py`
   - Use temp DB pattern: create `SchemaManager(tempfile)` + `ensure_schema()` to init tables, then test repo
5. **Wire facade delegation** in `poker/persistence.py`:
   - Add lazy property: `@property` + `_repo = None` pattern
   - Replace method bodies with `return self.{repo}.method(*args, **kwargs)`
6. **Update `poker/repositories/__init__.py`** exports
7. **Run tests**: `python3 -m pytest tests/ -k "not test_ai_memory and not test_ai_resilience and not test_personality_responses and not test_reflection_system and not test_message_history_impact and not test_tournament_flow" -v`
8. **Commit**: `refactor(T3-35-B{N}): extract {RepositoryName} from GamePersistence`

## Important Notes for Spec Generation

1. **Read `poker/persistence.py` FIRST** to get exact line numbers for each method (they shifted after Batch 0)
2. **BaseRepository._get_connection()** is a context manager that auto-commits/rollbacks — differs from GamePersistence's `_get_connection()` which returns a raw connection used as context manager
3. **serialization.py** has pure functions (not methods) — repos should `from poker.repositories.serialization import serialize_card, deserialize_card` etc.
4. **SchemaManager** handles all table creation — repos assume tables exist
5. For Batch 4 (experiments), consider whether to split into 2 sub-tasks if the spec is too large for one Ralph invocation
6. For Batch 5 (game state), the `_prepare_state_for_save` and `_restore_state_from_dict` methods already exist in `serialization.py` — the repo should use those instead of `self._` versions

## How to Execute This Plan

```
# In a fresh Claude Code session:
# 1. Read this plan
# 2. Read the full refactoring plan: .claude/plans/functional-napping-forest.md
# 3. Read current persistence.py to get exact line numbers
# 4. Generate docs/ralph/spec.md with all 6 batch specs
# 5. Generate docs/ralph/implementation_plan.md with checkboxed task list
# 6. Generate docs/ralph/prompt.md adapted for this refactoring work
# 7. Commit the specs
```
