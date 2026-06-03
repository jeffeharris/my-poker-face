---
purpose: Navigable index of the SQLite persistence layer — the BaseRepository pattern, every repository in poker/repositories/ and the tables it owns, SchemaManager/SCHEMA_VERSION, and the core games tables
type: reference
created: 2026-06-03
last_updated: 2026-06-03
---

# Persistence repositories

This is the map of game persistence. The monolithic `GamePersistence` god-object
(`poker/persistence.py`) is **gone** — there is no longer a single class that owns
saving. Persistence is split into ~30 focused repositories under
`poker/repositories/`, each owning the read/write surface of a slice of the schema.

> **Supersedes** the old `README_PERSISTENCE.md`, which documented
> `poker.persistence.GamePersistence("poker_games.db")`. That module and class no
> longer exist anywhere in `poker/` or `flask_app/` non-test code. If you find a
> reference to `GamePersistence`, it is dead.

## The repository pattern

All persistence classes extend `BaseRepository`
(`poker/repositories/base_repository.py:92`, a plain `object` — not an ORM base).
It provides three durable guarantees so subclasses only write SQL:

- **Thread-local connection reuse.** `_ensure_connection`
  (`base_repository.py:129`) returns a per-thread `sqlite3` connection, verifies
  liveness with `SELECT 1`, and recreates it on `ProgrammingError`/`OperationalError`.
  New connections set `row_factory = sqlite3.Row` and the WAL PRAGMAs below
  (`base_repository.py:141-146`).
- **WAL + busy-timeout, set once per connection** (`base_repository.py:143-145`):
  `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000` (5000 ms), `PRAGMA
  synchronous=NORMAL`. `SchemaManager._get_connection` mirrors these
  (`schema_manager.py:341-343`) so startup migrations queue behind a WAL writer
  instead of failing with "database is locked".
- **Transaction boundary via context manager.** The canonical subclass idiom is
  `with self._get_connection() as conn:` (`base_repository.py:113`, a
  `@contextmanager`) — it **commits on clean exit and rolls back on exception**
  (`base_repository.py:122-127`).

Two cross-cutting helpers live in the same module:

- **`@retry_on_lock(max_retries=3, base_delay=0.1)`** (`base_repository.py:50`) —
  retries on `sqlite3.OperationalError` whose message contains `'locked'`/`'busy'`,
  with exponential backoff `base_delay * 2**attempt`. Applied to the hot writes,
  e.g. `GameRepository.save_game` (`game_repository.py:54`) and
  `save_tournament_tracker` (`game_repository.py:210`).
- **`close_all_thread_connections() -> int`** (`base_repository.py:29`, tagged
  "PRH-34") — walks a module-level `weakref.WeakSet` (`_repo_registry`, every repo
  registers itself in `__init__` at `base_repository.py:106-111`) and closes the
  current thread's connection on each, best-effort. It is **wired to Flask
  teardown**: `flask_app/__init__.py:127` `@app.teardown_appcontext` calls it
  (`flask_app/__init__.py:129-131`). Without this, gevent greenlet recycling would
  leak per-(thread, repo) WAL reader fds; the connection cache stays live within a
  request and is released at its end.

## Repository index

Everything below lives in `poker/repositories/`. **Architectural invariant: ALL DDL
(`CREATE TABLE`) lives in `schema_manager.py`.** No repository creates its own
tables — repos own the *read/write surface* of their tables, not the schema. The
"tables owned" column is each repo's primary domain (some repos also read other
repos' tables for joins/aggregation).

| File | Class(es) | Tables owned (primary) |
|---|---|---|
| `game_repository.py` | `GameRepository`, `SavedGame` (dataclass) | `games`, `game_messages`, `ai_player_state`, `controller_state`, `emotional_state`, `personality_snapshots`, `opponent_models`, `opponent_observation_lifetime`, `memorable_hands`, `tournament_tracker`, `dossier_informant_unlocks` |
| `bankroll_repository.py` | `BankrollRepository` | `ai_bankroll_state`, `player_bankroll_state`, persona bankroll columns on `personalities` |
| `chip_ledger_repository.py` | `ChipLedgerRepository` | `chip_ledger_entries` (v93 chip-custody ledger) |
| `cash_table_repository.py` | `CashTableRepository` | `cash_tables`, `cash_idle_pool`, `cash_idle_metadata` (reads `entity_presence`) |
| `cash_session_repository.py` | `CashSessionRepository` | `cash_sessions`, `cash_session_events` |
| `cash_scalps_repository.py` | `CashScalpsRepository` | `cash_scalps` (v132 attributed bust counts) |
| `stake_repository.py` | `StakeRepository` | `stakes` (v98 backing/staking) |
| `sandbox_repository.py` | `SandboxRepository`, `SandboxState` (dataclass) | `sandboxes` (v100) |
| `entity_presence_repository.py` | `EntityPresenceRepository` | `entity_presence` (Cut-3 presence machine) |
| `relationship_repository.py` | `RelationshipRepository` | `relationship_states`, `cash_pair_stats` |
| `holdings_snapshots_repository.py` | `HoldingsSnapshotsRepository` | `holdings_snapshots` (v116) |
| `prestige_snapshots_repository.py` | `PrestigeSnapshotsRepository` | `prestige_snapshots` (v121) |
| `renown_field_repository.py` | `RenownFieldRepository` | **read-only aggregator** over `holdings_snapshots`, `relationship_states`, `cash_pair_stats`, `cash_scalps`, `cash_sessions`, `stakes` (Renown-v2 batched field read) |
| `vice_state_repository.py` | `ViceStateRepository`, `ViceState` (dataclass) | `ai_vice_state` |
| `side_hustle_state_repository.py` | `SideHustleStateRepository`, `SideHustleState` (dataclass) | `ai_side_hustle_state` |
| `personality_repository.py` | `PersonalityRepository` | `personalities`, `avatar_images`, `reference_images` |
| `coach_repository.py` | `CoachRepository` | `player_skill_progress`, `player_gate_progress`, `player_coach_profile`, `coach_tips`, `coach_session_evaluations` (reads `player_decision_analysis`) |
| `decision_analysis_repository.py` | `DecisionAnalysisRepository` | `player_decision_analysis` (reads `prompt_captures`) |
| `prompt_capture_repository.py` | `PromptCaptureRepository` | `prompt_captures` (reads `api_usage`, `player_decision_analysis`) |
| `capture_label_repository.py` | `CaptureLabelRepository` | `capture_labels` (reads `prompt_captures`; constructed with a `prompt_capture_repo` handle) |
| `prompt_preset_repository.py` | `PromptPresetRepository` | `prompt_presets` |
| `replay_experiment_repository.py` | `ReplayExperimentRepository` | `replay_experiment_captures`, `replay_results` |
| `experiment_repository.py` | `ExperimentRepository` | `experiments`, `experiment_games`, `experiment_chat_sessions` (constructed with a `game_repo` handle) |
| `tournament_repository.py` | `TournamentRepository` | `tournament_results`, `tournament_standings`, `player_career_stats` |
| `hand_history_repository.py` | `HandHistoryRepository` | `hand_history`, `hand_commentary` |
| `hand_equity_repository.py` | `HandEquityRepository` | `hand_equity` — **not exported in `__init__.py` `__all__` nor wired into `create_repos`** (treat as not-wired; verify before depending on it) |
| `llm_repository.py` | `LLMRepository` | `enabled_models`, `model_pricing`, `api_usage` |
| `sqlite_repositories.py` | `PressureEventRepository` | `pressure_events` |
| `user_repository.py` | `UserRepository` | `users`, `groups`, `user_groups`, `permissions`, `group_permissions` |
| `user_preferences_repository.py` | `UserPreferencesRepository` | `user_preferences` |
| `user_avatar_repository.py` | `UserAvatarRepository` | `user_avatars` |
| `settings_repository.py` | `SettingsRepository` | `app_settings` |
| `guest_tracking_repository.py` | `GuestTrackingRepository` | `guest_usage_tracking` |
| `schema_manager.py` | `SchemaManager` (plain object, **not** a `BaseRepository`) | owns ALL DDL + `schema_version` |

Non-class helper modules in the package:

- **`serialization.py`** — game-state (de)serialization. Card helpers plus
  `restore_state_from_dict(state_dict) -> PokerGameState` (`serialization.py:53`),
  used by `GameRepository.load_game` (`game_repository.py:11,133`).
- **`repository_utils.py`** — `parse_json_fields(row_dict, fields, context="")`
  (line 11) and `build_where_clause(conditions)` (line 23).

## Where the common operations live: `GameRepository`

The save/load/list operations that `GamePersistence` used to own are now on
`GameRepository` (`game_repository.py`):

- **`save_game(game_id, state_machine, owner_id=None, owner_name=None,
  llm_configs=None)`** (`game_repository.py:55`, `@retry_on_lock()`). Serializes
  `state_machine.game_state.to_dict()`, then re-adds the non-`game_state` fields the
  old god-object dropped: `current_phase` (75), `current_hand_seed` (76),
  `stats_hand_count` (81), `blind_config` growth/hands-per-level/max-blind (82-87).
  Writes via `INSERT ... ON CONFLICT(game_id) DO UPDATE` (97-110) — deliberately
  **not** `INSERT OR REPLACE`, to preserve unspecified columns like
  `debug_capture_enabled` (93-95); `llm_configs_json` is COALESCE-preserved (109).
- **`load_game(game_id) -> Optional[PokerStateMachine]`** (`game_repository.py:123`).
  `json.loads(game_state_json)` → `restore_state_from_dict` →
  `PokerStateMachine.from_saved_state(...)` (148-153) with restored phase,
  blind_config, hand_count; restores the deck seed via `with_hand_seed(seed,
  provided=False)` (161-163) to avoid the same shuffle on back-to-back hands.
- **`list_games(owner_id=None, limit=20, offset=0) -> List[SavedGame]`**
  (`game_repository.py:257`). Most-recent-first (`ORDER BY updated_at DESC`),
  optional owner filter; rows map into the `SavedGame` dataclass.
- **`delete_game(game_id)`** (`game_repository.py:300`). Deletes active state (save
  data, snapshots, AI state, messages) but **intentionally preserves historical
  data** — `hand_history`, `tournament_results`, `pressure_events` — so post-session
  analytics survive a cash leave (docstring 301-304).

Also on `GameRepository`: `save_coach_mode`/`load_coach_mode` (40-50),
`load_llm_configs` (167), `get_game_owner_info` (187),
`save_tournament_tracker`/`load_tournament_tracker` (210-255, save is
`@retry_on_lock()`). The `SavedGame` dataclass fields (`game_repository.py:17-28`):
`game_id, created_at, updated_at, phase, num_players, pot_size, game_state_json,
owner_id=None, owner_name=None`.

### Construction & wiring

- **`create_repos(db_path) -> dict`** (`__init__.py:45`) is the factory. It calls
  `SchemaManager(db_path).ensure_schema()` **first** (`__init__.py:53`), then
  constructs every repo and returns a dict keyed by role (`'game_repo'`,
  `'user_repo'`, …, `'db_path'`). Cross-repo wiring: `bankroll_repo.chip_ledger_repo
  = chip_ledger_repo` for D2 derived reads (`__init__.py:57-62`); `capture_label_repo`
  gets `prompt_capture_repo=` (`__init__.py:56`); `experiment_repo` gets `game_repo=`
  (`__init__.py:68`).
- The Flask app wires it at `flask_app/extensions.py:247`
  (`repos = create_repos(db_path)`, `db_path = config.DB_PATH`), then assigns each
  repo into module-level globals (`extensions.py:249-280`).
- **`PressureEventRepository` is constructed twice.** It is in the `create_repos`
  dict as `'pressure_event_repo'` (`__init__.py:79`) **and** re-instantiated
  standalone as `event_repository` at `flask_app/extensions.py:282`.
- Other `ensure_schema()` callers (non-Flask entry points): `poker/character_images.py:129`,
  `poker/personality_generator.py:343`.

## Schema management: `SchemaManager` + `SCHEMA_VERSION`

- **`SCHEMA_VERSION = 140`** (`schema_manager.py:321`).
- **`SchemaManager`** (`schema_manager.py:324`) is the self-described "single source
  of truth for database structure" (327-328). It is a **plain class, not a
  `BaseRepository`**, with its own `_get_connection` (`schema_manager.py:341`,
  `timeout=5.0` + `busy_timeout=5000`).
- **`ensure_schema(self)`** (`schema_manager.py:355`) is idempotent. Order
  (361-367):
  1. `_maybe_seed_from_template()` (361, 403) — test-only fast path: copy a cached
     fully-migrated DB into an empty test DB. Gated by env (`== "1"`); inert in prod.
  2. `_enable_wal_mode()` (363) — WAL / busy_timeout / synchronous=NORMAL (345-353).
  3. `_init_db()` (364) — all `CREATE TABLE IF NOT EXISTS` (no-ops on an existing or
     seeded DB). Creates the `schema_version` tracking table first (476-482).
  4. `_run_migrations()` (365) — early-returns when already at `SCHEMA_VERSION`.
  5. `_maybe_save_as_template()` (367, 421) — snapshots the first clean build as the
     process-wide test template, only when version == `SCHEMA_VERSION` (431).
- **Migrations** — `_run_migrations()` (`schema_manager.py:1631`):
  `_get_current_schema_version()` (1620) reads `SELECT MAX(version) FROM
  schema_version` (0 if the table is missing); if `current >= SCHEMA_VERSION` it
  returns (1635-1636). A `migrations: Dict[int, tuple]` maps `version → (migrate_func,
  description)` (v1 at 1643 … v140 at 2160). The loop `for version in
  range(current_version + 1, SCHEMA_VERSION + 1)` (2195) runs each func, inserts
  `(version, description)` into `schema_version`, commits, and logs; on exception it
  logs and **re-raises** (2196-2208). Newest migrations: `_migrate_v137_create_cash_scalps`
  (7180), `_migrate_v138_add_prestige_v2_columns` (7216),
  `_migrate_v139_add_prestige_entity_kind` (7280), and v140 = a covering index on
  `holdings_snapshots(sandbox_id, entity_id, …)` for the Renown-v2 peak-net-worth
  read (7327; comment at 311-312).
- **Scar to know about:** there is idempotent "training-room renumber collision"
  handling that re-asserts skipped v123/v124 (`schema_manager.py:2184-2193`).

Because `create_repos` calls `ensure_schema()` and Flask calls `create_repos`,
**migrations run transitively at app startup**.

## Core game tables: `games` / `game_messages`

These two tables hold the heart of game persistence. The old doc's "two main tables"
framing is right for the *core* of saving a game — but the DB now has 60+ tables (see
note below), so it is not the whole schema.

- **`games`** — DDL `schema_manager.py:485-500`. Columns: `game_id TEXT PRIMARY
  KEY`, `created_at`/`updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`, `phase TEXT
  NOT NULL`, `num_players INTEGER NOT NULL`, `pot_size REAL NOT NULL`,
  **`game_state_json TEXT NOT NULL`**, `owner_id TEXT`, `owner_name TEXT`,
  `debug_capture_enabled BOOLEAN DEFAULT 0`, `llm_configs_json TEXT`, `coach_mode
  TEXT DEFAULT 'off'`. Indexes: `idx_games_updated ON games(updated_at DESC)` (501),
  `idx_games_owner ON games(owner_id)` (502).
- **`game_messages`** — DDL `schema_manager.py:505-514`. Columns: `id INTEGER
  PRIMARY KEY AUTOINCREMENT`, `game_id TEXT NOT NULL`, `timestamp TIMESTAMP DEFAULT
  CURRENT_TIMESTAMP`, `message_type TEXT NOT NULL`, `message_text TEXT NOT NULL`,
  `FOREIGN KEY (game_id) REFERENCES games(game_id)`. Index: `idx_messages_game_id ON
  game_messages(game_id, timestamp)` (515-517).
- **`game_state_json`** is the serialized frozen `PokerGameState` plus the augmenting
  fields `save_game` adds (`current_phase`, `current_hand_seed`, `stats_hand_count`,
  `blind_config`). It is read back through `restore_state_from_dict` in
  `serialization.py`.

> **Stale-count caveat:** the `_init_db` docstring says "Tables (25 total)"
> (`schema_manager.py:447`). That number is **out of date** — `schema_manager.py`
> now creates 60+ distinct tables (game/chat, AI/personality/psychology, cash mode,
> ledger/staking/sandboxes, presence, relationships, holdings/prestige, coach,
> experiments/replay, tournaments, users/RBAC, settings, LLM usage). Names ending
> `_new`/`_v` (e.g. `api_usage_new`, `opponent_models_new`) are transient migration
> scratch tables, not live surfaces.

## See also

- Functional core / immutable state: [`../../CLAUDE.md`](../../CLAUDE.md)
  (Architecture Overview).
- Doc-debt / staleness tracking for this directory: [`TODO.md`](TODO.md).
