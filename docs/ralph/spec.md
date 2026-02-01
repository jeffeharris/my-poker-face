# Persistence Refactor — Batch Specifications (T3-35)

> Every batch below has been reviewed through bidirectional planning.
> Design decisions are final. Ralph should execute, not redesign.
> Full refactoring plan: see original plan in project history.

---

## T3-35-B1: Extract SettingsRepository + GuestTrackingRepository

**Type**: Refactor — extract repository
**Source**: `poker/persistence.py`
**Targets**:
- `poker/repositories/settings_repository.py`
- `poker/repositories/guest_tracking_repository.py`

### Problem

`GamePersistence` is a 6,138-line god class. AppSettings and GuestTracking are small, self-contained domains that can be trivially extracted.

### Methods to Extract

#### SettingsRepository (~100 lines)

| Method | Source Lines | Table |
|--------|-------------|-------|
| `get_setting(key, default)` | 4843–4863 | `app_settings` |
| `set_setting(key, value, description)` | 4865–4891 | `app_settings` |
| `get_all_settings()` | 4893–4916 | `app_settings` |
| `delete_setting(key)` | 4918–4937 | `app_settings` |

#### GuestTrackingRepository (~35 lines)

| Method | Source Lines | Table |
|--------|-------------|-------|
| `increment_hands_played(tracking_id)` | 2144–2162 | `guest_usage_tracking` |
| `get_hands_played(tracking_id)` | 2164–2172 | `guest_usage_tracking` |

### Action

1. Create `poker/repositories/settings_repository.py`:
   - Class `SettingsRepository(BaseRepository)`
   - Copy all 4 methods from `GamePersistence`, adapting `self._get_connection()` to use BaseRepository's context manager (which auto-commits/rollbacks — remove manual `conn.commit()` calls)
   - Keep `conn.row_factory = sqlite3.Row` inside methods that need it

2. Create `poker/repositories/guest_tracking_repository.py`:
   - Class `GuestTrackingRepository(BaseRepository)`
   - Copy both methods, adapting connection usage

3. Wire facade in `poker/persistence.py`:
   - Add lazy properties:
     ```python
     @property
     def _settings_repo(self):
         if not hasattr(self, '__settings_repo'):
             self.__settings_repo = SettingsRepository(self.db_path)
         return self.__settings_repo

     @property
     def _guest_tracking_repo(self):
         if not hasattr(self, '__guest_tracking_repo'):
             self.__guest_tracking_repo = GuestTrackingRepository(self.db_path)
         return self.__guest_tracking_repo
     ```
   - Replace method bodies with delegation:
     ```python
     def get_setting(self, key, default=None):
         return self._settings_repo.get_setting(key, default)
     ```

4. Write tests in `tests/test_repositories/test_settings_repository.py` and `tests/test_repositories/test_guest_tracking_repository.py`:
   - Use temp DB pattern: `SchemaManager(tmp_path / "test.db").ensure_schema()` then create repo
   - Test each method independently

5. Update `poker/repositories/__init__.py` exports

6. Run full test suite

### Acceptance Criteria

- [ ] `SettingsRepository` exists with all 4 methods, extends `BaseRepository`
- [ ] `GuestTrackingRepository` exists with both methods, extends `BaseRepository`
- [ ] `GamePersistence` delegates to the new repos
- [ ] New unit tests pass for both repositories
- [ ] Full test suite passes (no regressions)
- [ ] `poker/repositories/__init__.py` updated

---

## T3-35-B2: Extract PersonalityRepository (Personality + Avatar)

**Type**: Refactor — extract repository
**Source**: `poker/persistence.py`
**Target**: `poker/repositories/personality_repository.py`

### Problem

Personality and avatar methods form a cohesive domain (~500 lines). They share the `personalities` and `avatar_images` tables.

### Methods to Extract

#### Personality CRUD

| Method | Source Lines | Table |
|--------|-------------|-------|
| `save_personality(name, config, source)` | 1045–1071 | `personalities` |
| `load_personality(name)` | 1072–1110 | `personalities` |
| `increment_personality_usage(name)` | 1111–1119 | `personalities` |
| `list_personalities(limit)` | 1120–1143 | `personalities` |
| `delete_personality(name)` | 1144–1157 | `personalities` |
| `seed_personalities_from_json(json_path, overwrite)` | 2405–2452 | `personalities` |

#### Avatar CRUD

| Method | Source Lines | Table |
|--------|-------------|-------|
| `save_avatar_image(personality_name, emotion, image_data, ...)` | 2175–2213 | `avatar_images` |
| `load_avatar_image(personality_name, emotion)` | 2214–2232 | `avatar_images` |
| `load_avatar_image_with_metadata(personality_name, emotion)` | 2233–2258 | `avatar_images` |
| `load_full_avatar_image(personality_name, emotion)` | 2259–2277 | `avatar_images` |
| `load_full_avatar_image_with_metadata(personality_name, emotion)` | 2278–2303 | `avatar_images` |
| `has_full_avatar_image(personality_name, emotion)` | 2304–2312 | `avatar_images` |
| `has_avatar_image(personality_name, emotion)` | 2313–2321 | `avatar_images` |
| `get_available_avatar_emotions(personality_name)` | 2322–2331 | `avatar_images` |
| `has_all_avatar_emotions(personality_name)` | 2332–2337 | `avatar_images` |
| `delete_avatar_images(personality_name)` | 2338–2349 | `avatar_images` |
| `list_personalities_with_avatars()` | 2350–2368 | `avatar_images` + `personalities` |
| `get_avatar_stats()` | 2369–2404 | `avatar_images` + `personalities` |

### Action

1. Create `poker/repositories/personality_repository.py`:
   - Class `PersonalityRepository(BaseRepository)`
   - Copy all 18 methods, adapting connection usage (BaseRepository's `_get_connection()` is a context manager with auto-commit/rollback)
   - Remove manual `conn.commit()` calls
   - Keep `conn.row_factory = sqlite3.Row` inside methods that need it

2. Wire facade in `poker/persistence.py`:
   - Add lazy property `_personality_repo`
   - Replace all 18 method bodies with delegation

3. Write tests in `tests/test_repositories/test_personality_repository.py`:
   - Test personality CRUD cycle (save, load, list, delete)
   - Test avatar save/load with actual bytes
   - Test `seed_personalities_from_json` with a temp JSON file
   - Test `get_avatar_stats` returns correct counts

4. Update `poker/repositories/__init__.py`

5. Run full test suite

### Acceptance Criteria

- [ ] `PersonalityRepository` exists with all 18 methods, extends `BaseRepository`
- [ ] `GamePersistence` delegates to `PersonalityRepository`
- [ ] Unit tests cover personality CRUD, avatar CRUD, and seed
- [ ] Full test suite passes
- [ ] `poker/repositories/__init__.py` updated

---

## T3-35-B3: Extract UserRepository (User + RBAC)

**Type**: Refactor — extract repository
**Source**: `poker/persistence.py`
**Target**: `poker/repositories/user_repository.py`

### Problem

User management and RBAC form a distinct domain (~580 lines). Methods manage users, groups, permissions, and admin initialization.

### Methods to Extract

| Method | Source Lines | Table(s) |
|--------|-------------|----------|
| `count_user_games(owner_id)` | 296–303 | `games` |
| `get_last_game_creation_time(owner_id)` | 304–313 | (session/rate-limit — reads from users or games) |
| `update_last_game_creation_time(owner_id, timestamp)` | 314–323 | (session/rate-limit) |
| `create_google_user(google_id, email, name, ...)` | 324–371 | `users` |
| `get_user_by_id(user_id)` | 372–391 | `users` |
| `get_user_by_email(email)` | 392–411 | `users` |
| `get_user_by_linked_guest(guest_id)` | 412–431 | `users` |
| `update_user_last_login(user_id)` | 432–443 | `users` |
| `transfer_game_ownership(from_owner_id, to_owner_id, to_owner_name)` | 444–459 | `games` |
| `transfer_guest_to_user(from_id, to_id, to_name)` | 460–545 | `games`, `users`, multiple tables |
| `get_all_users()` | 546–568 | `users` |
| `get_user_groups(user_id)` | 569–587 | `user_groups`, `groups` |
| `get_user_permissions(user_id)` | 588–607 | `user_groups`, `group_permissions`, `permissions` |
| `assign_user_to_group(user_id, group_name, assigned_by)` | 608–654 | `groups`, `user_groups` |
| `remove_user_from_group(user_id, group_name)` | 655–671 | `user_groups` |
| `count_users_in_group(group_name)` | 672–689 | `user_groups`, `groups` |
| `get_all_groups()` | 690–704 | `groups` |
| `get_user_stats(user_id)` | 705–754 | `users`, `games`, `tournament_standings` |
| `initialize_admin_from_env()` | 755–791 | `users`, `groups`, `user_groups` |

### Action

1. Create `poker/repositories/user_repository.py`:
   - Class `UserRepository(BaseRepository)`
   - Copy all 19 methods, adapting connection usage
   - `transfer_guest_to_user` is a complex multi-table method — copy exactly as-is, adapting only the connection pattern
   - `initialize_admin_from_env()` reads `os.environ` — keep that behavior

2. Wire facade in `poker/persistence.py`:
   - Add lazy property `_user_repo`
   - Replace all 19 method bodies with delegation

3. Write tests in `tests/test_repositories/test_user_repository.py`:
   - Test user CRUD (create, get by id/email/guest)
   - Test group assignment/removal
   - Test permission lookup
   - Test `transfer_guest_to_user` with mock data
   - Test `initialize_admin_from_env` with env var set/unset

4. Update `poker/repositories/__init__.py`

5. Run full test suite

### Acceptance Criteria

- [ ] `UserRepository` exists with all 19 methods, extends `BaseRepository`
- [ ] `GamePersistence` delegates to `UserRepository`
- [ ] Unit tests cover user CRUD, RBAC, transfer, and admin init
- [ ] Full test suite passes
- [ ] `poker/repositories/__init__.py` updated

---

## T3-35-B4a: Extract ExperimentRepository — Part 1 (Captures, Decisions, Presets, Labels)

**Type**: Refactor — extract repository
**Source**: `poker/persistence.py`
**Target**: `poker/repositories/experiment_repository.py`

### Problem

The experiment domain is the largest slice (~1,800 lines). This batch extracts the first half: prompt captures, decision analysis, prompt presets, and capture labels. These are the data-collection and annotation sub-domains.

### Methods to Extract

#### Prompt Captures (lines 2453–2962)

| Method | Source Lines | Table |
|--------|-------------|-------|
| `save_prompt_capture(capture)` | 2453–2545 | `prompt_captures` |
| `get_prompt_capture(capture_id)` | 2546–2590 | `prompt_captures` |
| `list_prompt_captures(...)` | 2591–2703 | `prompt_captures` |
| `get_prompt_capture_stats(...)` | 2704–2758 | `prompt_captures` |
| `update_prompt_capture_tags(...)` | 2759–2779 | `prompt_captures` |
| `delete_prompt_captures(...)` | 2780–2808 | `prompt_captures` |
| `list_playground_captures(...)` | 2809–2899 | `prompt_captures` |
| `get_playground_capture_stats()` | 2900–2934 | `prompt_captures` |
| `cleanup_old_captures(retention_days)` | 2935–2962 | `prompt_captures` |

#### Decision Analysis (lines 2963–3220)

| Method | Source Lines | Table |
|--------|-------------|-------|
| `save_decision_analysis(analysis)` | 2963–3036 | `player_decision_analysis` |
| `get_decision_analysis(analysis_id)` | 3037–3049 | `player_decision_analysis` |
| `get_decision_analysis_by_request(request_id)` | 3050–3062 | `player_decision_analysis` |
| `get_decision_analysis_by_capture(capture_id)` | 3063–3097 | `player_decision_analysis` |
| `list_decision_analyses(...)` | 3098–3157 | `player_decision_analysis` |
| `get_decision_analysis_stats(...)` | 3158–3220 | `player_decision_analysis` |

#### Prompt Presets (lines 4943–5186)

| Method | Source Lines | Table |
|--------|-------------|-------|
| `create_prompt_preset(...)` | 4943–4984 | `prompt_presets` |
| `get_prompt_preset(preset_id)` | 4985–5016 | `prompt_presets` |
| `get_prompt_preset_by_name(name)` | 5017–5048 | `prompt_presets` |
| `list_prompt_presets(...)` | 5049–5098 | `prompt_presets` |
| `update_prompt_preset(...)` | 5099–5157 | `prompt_presets` |
| `delete_prompt_preset(preset_id)` | 5158–5186 | `prompt_presets` |

#### Capture Labels (lines 5187–5688)

| Method | Source Lines | Table(s) |
|--------|-------------|----------|
| `add_capture_labels(...)` | 5187–5222 | `capture_labels` |
| `compute_and_store_auto_labels(capture_id, capture_data)` | 5223–5285 | `capture_labels` |
| `remove_capture_labels(...)` | 5286–5315 | `capture_labels` |
| `get_capture_labels(capture_id)` | 5316–5334 | `capture_labels` |
| `list_all_labels(label_type)` | 5335–5362 | `capture_labels` |
| `get_label_stats(...)` | 5363–5405 | `capture_labels` |
| `search_captures_with_labels(...)` | 5406–5602 | `capture_labels`, `prompt_captures` |
| `bulk_add_capture_labels(...)` | 5603–5646 | `capture_labels` |
| `bulk_remove_capture_labels(...)` | 5647–5688 | `capture_labels` |

### Action

1. Create `poker/repositories/experiment_repository.py`:
   - Class `ExperimentRepository(BaseRepository)`
   - Copy all methods listed above (~900 lines), adapting connection usage
   - Remove manual `conn.commit()` calls (BaseRepository auto-commits)
   - Keep `conn.row_factory = sqlite3.Row` inside methods that need it

2. Wire facade in `poker/persistence.py`:
   - Add lazy property `_experiment_repo`
   - Replace method bodies with delegation for the methods in this batch only

3. Write tests in `tests/test_repositories/test_experiment_repository.py`:
   - Test prompt capture save/load/list/delete cycle
   - Test decision analysis save/load
   - Test prompt preset CRUD
   - Test capture label add/remove/search

4. Update `poker/repositories/__init__.py`

5. Run full test suite

### Acceptance Criteria

- [ ] `ExperimentRepository` exists with all Part 1 methods, extends `BaseRepository`
- [ ] `GamePersistence` delegates to `ExperimentRepository` for these methods
- [ ] Unit tests cover prompt captures, decision analysis, presets, and labels
- [ ] Full test suite passes
- [ ] `poker/repositories/__init__.py` updated

---

## T3-35-B4b: Extend ExperimentRepository — Part 2 (Lifecycle, Chat, Analytics, Replay)

**Type**: Refactor — extend existing repository
**Source**: `poker/persistence.py`
**Target**: `poker/repositories/experiment_repository.py` (already created in B4a)

### Problem

This batch adds the remaining experiment methods to the `ExperimentRepository` created in B4a: experiment lifecycle, chat sessions, live stats/analytics, and replay experiments.

### Methods to Extract

#### Experiment Lifecycle (lines 3221–3845)

| Method | Source Lines | Table(s) |
|--------|-------------|----------|
| `create_experiment(config, parent_experiment_id)` | 3221–3262 | `experiments` |
| `link_game_to_experiment(...)` | 3263–3296 | `experiment_games` |
| `complete_experiment(experiment_id, summary)` | 3297–3314 | `experiments` |
| `get_experiment(experiment_id)` | 3315–3348 | `experiments` |
| `get_experiment_by_name(name)` | 3349–3364 | `experiments` |
| `get_experiment_games(experiment_id)` | 3365–3394 | `experiment_games` |
| `update_experiment_game_heartbeat(...)` | 3395–3428 | `experiment_games` |
| `get_stalled_variants(...)` | 3429–3488 | `experiment_games` |
| `acquire_resume_lock(experiment_game_id)` | 3489–3510 | `experiment_games` |
| `release_resume_lock(game_id)` | 3511–3523 | `experiment_games` |
| `release_resume_lock_by_id(experiment_game_id)` | 3524–3536 | `experiment_games` |
| `check_resume_lock_superseded(game_id)` | 3537–3567 | `experiment_games` |
| `get_experiment_decision_stats(...)` | 3568–3645 | `prompt_captures`, `player_decision_analysis` |
| `list_experiments(...)` | 3646–3725 | `experiments` |
| `update_experiment_status(...)` | 3726–3765 | `experiments` |
| `update_experiment_tags(experiment_id, tags)` | 3766–3780 | `experiments` |
| `mark_running_experiments_interrupted()` | 3781–3802 | `experiments` |
| `get_incomplete_tournaments(experiment_id)` | 3803–3845 | `experiment_games` |

#### Chat Sessions (lines 3846–4054)

| Method | Source Lines | Table(s) |
|--------|-------------|----------|
| `save_chat_session(...)` | 3846–3881 | `experiment_chat_sessions` |
| `get_chat_session(session_id)` | 3882–3917 | `experiment_chat_sessions` |
| `get_latest_chat_session(owner_id)` | 3918–3955 | `experiment_chat_sessions` |
| `archive_chat_session(session_id)` | 3956–3969 | `experiment_chat_sessions` |
| `delete_chat_session(session_id)` | 3970–3982 | `experiment_chat_sessions` |
| `save_experiment_design_chat(experiment_id, chat_history)` | 3983–3999 | `experiments` |
| `get_experiment_design_chat(experiment_id)` | 4000–4018 | `experiments` |
| `save_experiment_assistant_chat(experiment_id, chat_history)` | 4019–4035 | `experiments` |
| `get_experiment_assistant_chat(experiment_id)` | 4036–4054 | `experiments` |

#### Live Stats & Analytics (lines 4055–4842)

| Method | Source Lines | Table(s) |
|--------|-------------|----------|
| `get_experiment_live_stats(experiment_id)` | 4055–4494 | Multiple |
| `get_experiment_game_snapshots(experiment_id)` | 4495–4645 | `experiment_games`, `games` |
| `get_experiment_player_detail(...)` | 4646–4842 | Multiple |

#### Replay Experiments (lines 5689–6138)

| Method | Source Lines | Table(s) |
|--------|-------------|----------|
| `create_replay_experiment(...)` | 5689–5774 | `experiments`, `replay_experiment_captures` |
| `add_replay_result(...)` | 5775–5861 | `replay_results` |
| `get_replay_experiment(experiment_id)` | 5862–5912 | `experiments`, `replay_experiment_captures`, `replay_results` |
| `get_replay_results(...)` | 5913–5975 | `replay_results` |
| `get_replay_results_summary(experiment_id)` | 5976–6047 | `replay_results` |
| `get_replay_experiment_captures(experiment_id)` | 6048–6070 | `replay_experiment_captures` |
| `list_replay_experiments(...)` | 6071–6138 | `experiments`, `replay_experiment_captures`, `replay_results` |

### Action

1. **Extend** `poker/repositories/experiment_repository.py` (already exists from B4a):
   - Add all methods listed above to the existing `ExperimentRepository` class
   - Same patterns: adapt connection usage, remove manual `conn.commit()`, keep `conn.row_factory`
   - Keep `import numpy as np` if any analytics methods use it (check `get_experiment_live_stats`)

2. Wire additional facade delegations in `poker/persistence.py`:
   - The `_experiment_repo` lazy property already exists from B4a
   - Replace method bodies with delegation for the methods in this batch

3. Extend tests in `tests/test_repositories/test_experiment_repository.py`:
   - Test experiment create/get/complete lifecycle
   - Test experiment-game linking
   - Test chat session save/load/archive
   - Test replay experiment create/add_result/get
   - Note: Don't test the large analytics methods (live_stats, snapshots, player_detail) in unit tests — they're read-only aggregations that will be covered by integration tests

4. Run full test suite

### Acceptance Criteria

- [ ] `ExperimentRepository` now contains ALL experiment methods (Part 1 + Part 2)
- [ ] `GamePersistence` delegates to `ExperimentRepository` for all experiment methods
- [ ] Unit tests cover experiment lifecycle, chat sessions, and replay
- [ ] Full test suite passes

---

## T3-35-B5: Extract GameRepository (Game State)

**Type**: Refactor — extract repository
**Source**: `poker/persistence.py`
**Target**: `poker/repositories/game_repository.py`

### Problem

The game state domain is the core of persistence (~800 lines). It handles game CRUD, messages, AI player state, emotional/controller state, opponent models, and tournament tracker. It depends on `serialization.py` for card/state serialization.

### Methods to Extract

#### Game CRUD

| Method | Source Lines | Table |
|--------|-------------|-------|
| `save_coach_mode(game_id, mode)` | 96–103 | `games` |
| `load_coach_mode(game_id)` | 105–113 | `games` |
| `save_game(game_id, state_machine, ...)` | 115–162 | `games` |
| `load_game(game_id)` | 164–192 | `games` |
| `load_llm_configs(game_id)` | 194–215 | `games` |
| `save_tournament_tracker(game_id, tracker)` | 216–239 | `tournament_tracker` |
| `load_tournament_tracker(game_id)` | 240–260 | `tournament_tracker` |
| `list_games(owner_id, limit)` | 261–295 | `games` |
| `delete_game(game_id)` | 876–884 | `games` + cascading deletes |

#### Messages

| Method | Source Lines | Table |
|--------|-------------|-------|
| `save_message(game_id, message_type, message_text)` | 885–892 | `game_messages` |
| `load_messages(game_id, limit)` | 893–922 | `game_messages` |

#### State Serialization (use `serialization.py`)

| Method | Source Lines | Notes |
|--------|-------------|-------|
| `_prepare_state_for_save(game_state)` | 923–930 | Use `serialization.prepare_state_for_save()` |
| `_restore_state_from_dict(state_dict)` | 931–994 | Use `serialization.restore_state_from_dict()` |

**Important**: These methods already exist as pure functions in `poker/repositories/serialization.py`. The repository should import and use those directly instead of copying the `self._` versions.

#### AI Player State

| Method | Source Lines | Table |
|--------|-------------|-------|
| `save_ai_player_state(game_id, player_name, ...)` | 995–1008 | `ai_player_state` |
| `load_ai_player_states(game_id)` | 1009–1027 | `ai_player_state` |
| `save_personality_snapshot(game_id, player_name, ...)` | 1028–1044 | `personality_snapshots` |

#### Emotional & Controller State

| Method | Source Lines | Table |
|--------|-------------|-------|
| `save_emotional_state(game_id, player_name, ...)` | 1158–1196 | `emotional_state` |
| `load_emotional_state(game_id, player_name)` | 1197–1228 | `emotional_state` |
| `load_all_emotional_states(game_id)` | 1229–1260 | `emotional_state` |
| `save_controller_state(game_id, player_name, ...)` | 1261–1288 | `controller_state` |
| `load_controller_state(game_id, player_name)` | 1289–1321 | `controller_state` |
| `load_all_controller_states(game_id)` | 1322–1353 | `controller_state` |
| `delete_emotional_state_for_game(game_id)` | 1354–1358 | `emotional_state` |
| `delete_controller_state_for_game(game_id)` | 1359–1363 | `controller_state` |

#### Opponent Models

| Method | Source Lines | Table |
|--------|-------------|-------|
| `save_opponent_models(game_id, opponent_model_manager)` | 1365–1432 | `opponent_models` |
| `load_opponent_models(game_id)` | 1433–1509 | `opponent_models` |
| `delete_opponent_models_for_game(game_id)` | 1510–1516 | `opponent_models` |

### Action

1. Create `poker/repositories/game_repository.py`:
   - Class `GameRepository(BaseRepository)`
   - Copy all methods listed above, adapting connection usage
   - For `save_game` and `load_game`: import and use the serialization functions from `poker.repositories.serialization` instead of the `self._` versions:
     ```python
     from poker.repositories.serialization import (
         serialize_card, deserialize_card, serialize_cards, deserialize_cards,
         prepare_state_for_save, restore_state_from_dict
     )
     ```
   - Note: `prepare_state_for_save` and `restore_state_from_dict` may need to be added to `serialization.py` if they don't already exist there as standalone functions. Check first — if they're only on `GamePersistence` as `_prepare_state_for_save` / `_restore_state_from_dict`, extract them to `serialization.py` as pure functions first.
   - Import `PokerGameState`, `Player`, `PokerStateMachine`, `PokerPhase`, `Card` as needed

2. Wire facade in `poker/persistence.py`:
   - Add lazy property `_game_repo`
   - Replace all method bodies with delegation

3. Write tests in `tests/test_repositories/test_game_repository.py`:
   - Test game save/load round-trip (create a minimal `PokerStateMachine`, save, load, verify state)
   - Test list_games, delete_game
   - Test message save/load
   - Test AI player state save/load
   - Test emotional/controller state save/load
   - Test opponent models save/load

4. Update `poker/repositories/__init__.py`

5. Run full test suite

### Acceptance Criteria

- [ ] `GameRepository` exists with all methods, extends `BaseRepository`
- [ ] Uses `serialization.py` functions (not duplicated `self._` methods)
- [ ] `GamePersistence` delegates to `GameRepository`
- [ ] Unit tests cover game CRUD, messages, AI state, emotional state, opponent models
- [ ] Full test suite passes
- [ ] `poker/repositories/__init__.py` updated

---

## T3-35-B6: Extract HandHistoryRepository + TournamentRepository + LLMRepository

**Type**: Refactor — extract repository
**Source**: `poker/persistence.py`
**Targets**:
- `poker/repositories/hand_history_repository.py`
- `poker/repositories/tournament_repository.py`
- `poker/repositories/llm_repository.py`

### Problem

Three remaining domains: hand history (with session stats), tournament results/career stats, and LLM model management. Each is self-contained.

### Methods to Extract

#### HandHistoryRepository (~520 lines)

| Method | Source Lines | Table(s) |
|--------|-------------|----------|
| `save_hand_history(recorded_hand)` | 1517–1553 | `hand_history` |
| `save_hand_commentary(game_id, hand_number, player_name, commentary)` | 1556–1587 | `hand_commentary` |
| `get_recent_reflections(game_id, player_name, limit)` | 1589–1612 | `hand_commentary` |
| `get_hand_count(game_id)` | 1614–1629 | `hand_history` |
| `load_hand_history(game_id, limit)` | 1631–1678 | `hand_history` |
| `delete_hand_history_for_game(game_id)` | 1680–1683 | `hand_history` |
| `get_session_stats(game_id, player_name)` | 1685–1808 | `hand_history` |
| `get_session_context_for_prompt(game_id, player_name, ...)` | 1809–1847 | `hand_history`, `hand_commentary` |

#### TournamentRepository (~400 lines)

| Method | Source Lines | Table(s) |
|--------|-------------|----------|
| `save_tournament_result(game_id, result)` | 1848–1905 | `tournament_results`, `tournament_standings` |
| `get_tournament_result(game_id)` | 1906–1948 | `tournament_results`, `tournament_standings` |
| `update_career_stats(owner_id, player_name, tournament_result)` | 1950–2052 | `player_career_stats` |
| `get_career_stats(owner_id)` | 2054–2076 | `player_career_stats` |
| `get_tournament_history(owner_id, limit)` | 2078–2104 | `tournament_results`, `tournament_standings` |
| `get_eliminated_personalities(owner_id)` | 2106–2141 | `tournament_standings`, `tournament_results` |

#### LLMRepository (~85 lines)

| Method | Source Lines | Table |
|--------|-------------|-------|
| `get_available_providers()` | 793–804 | `enabled_models` |
| `get_enabled_models()` | 806–827 | `enabled_models` |
| `get_all_enabled_models()` | 829–844 | `enabled_models` |
| `update_model_enabled(model_id, enabled)` | 846–858 | `enabled_models` |
| `update_model_details(model_id, display_name, notes)` | 860–874 | `enabled_models` |

### Action

1. Create `poker/repositories/hand_history_repository.py`:
   - Class `HandHistoryRepository(BaseRepository)`
   - Copy all 8 methods, adapting connection usage
   - `get_session_stats` is complex (~120 lines) — copy exactly as-is

2. Create `poker/repositories/tournament_repository.py`:
   - Class `TournamentRepository(BaseRepository)`
   - Copy all 6 methods, adapting connection usage
   - `update_career_stats` has complex upsert logic — copy exactly as-is

3. Create `poker/repositories/llm_repository.py`:
   - Class `LLMRepository(BaseRepository)`
   - Copy all 5 methods, adapting connection usage

4. Wire facade in `poker/persistence.py`:
   - Add lazy properties `_hand_history_repo`, `_tournament_repo`, `_llm_repo`
   - Replace all method bodies with delegation

5. Write tests:
   - `tests/test_repositories/test_hand_history_repository.py`:
     - Test save/load hand history round-trip
     - Test hand commentary save and recent reflections
     - Test session stats with multi-hand data
   - `tests/test_repositories/test_tournament_repository.py`:
     - Test save/get tournament result with standings
     - Test career stats update (new player + existing player)
     - Test tournament history query
   - `tests/test_repositories/test_llm_repository.py`:
     - Test get_enabled_models returns correct grouping
     - Test update_model_enabled toggle

6. Update `poker/repositories/__init__.py`

7. Run full test suite

### Acceptance Criteria

- [ ] `HandHistoryRepository` exists with all 8 methods, extends `BaseRepository`
- [ ] `TournamentRepository` exists with all 6 methods, extends `BaseRepository`
- [ ] `LLMRepository` exists with all 5 methods, extends `BaseRepository`
- [ ] `GamePersistence` delegates to all three repos
- [ ] Unit tests cover each repository's core operations
- [ ] Full test suite passes
- [ ] `poker/repositories/__init__.py` updated
