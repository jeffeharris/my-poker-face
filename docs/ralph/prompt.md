# Ralph Wiggum — Persistence Refactor Task Execution Prompt

You are an autonomous code agent working through a pre-approved task list. Every task has been specified through bidirectional planning with the project owner. Your job is to execute, not redesign.

## Instructions

1. Read `docs/ralph/spec.md` thoroughly. It contains the detailed specification for every batch.
2. Read `docs/ralph/implementation_plan.md`. It contains the ordered, checkboxed task list.
3. Find the **first unchecked task** (`- [ ]`).
4. Read the corresponding section in `spec.md` for full context — the task ID (e.g., T3-35-B1) is your key.
5. Execute the batch:

### For each batch:

   a. **Read the source methods** in `poker/persistence.py` at the exact line ranges listed in the spec. Understand the current implementation before copying.

   b. **Read `poker/repositories/base_repository.py`** to understand the BaseRepository contract:
      - `_get_connection()` is a context manager that **auto-commits on clean exit** and **auto-rollbacks on exception**
      - Do NOT add manual `conn.commit()` calls — remove them from copied code
      - Connection has `row_factory = sqlite3.Row` set by default

   c. **Create the new repository file(s)** at the paths specified in the spec:
      - Class extends `BaseRepository`
      - Copy methods from `GamePersistence` — exact logic, no behavior changes
      - Adapt `self._get_connection()` usage: wrap operations in `with self._get_connection() as conn:` block
      - Remove `conn.commit()` calls (BaseRepository handles this)
      - Keep `conn.row_factory = sqlite3.Row` inside methods that need it (BaseRepository sets this on connection creation, but some methods reset it)
      - Keep all imports needed by the methods

   d. **Wire facade delegation** in `poker/persistence.py`:
      - Add a lazy property for the repository:
        ```python
        @property
        def _settings_repo(self):
            if not hasattr(self, '__settings_repo'):
                self.__settings_repo = SettingsRepository(self.db_path)
            return self.__settings_repo
        ```
      - Replace each method body with delegation:
        ```python
        def get_setting(self, key, default=None):
            return self._settings_repo.get_setting(key, default)
        ```
      - Keep the original method signatures and docstrings intact

   e. **Write tests** in `tests/test_repositories/test_{name}_repository.py`:
      - Use temp DB pattern:
        ```python
        import tempfile
        from poker.repositories.schema_manager import SchemaManager
        from poker.repositories.settings_repository import SettingsRepository

        def test_get_set_setting(tmp_path):
            db_path = str(tmp_path / "test.db")
            SchemaManager(db_path).ensure_schema()
            repo = SettingsRepository(db_path)

            repo.set_setting("test_key", "test_value")
            assert repo.get_setting("test_key") == "test_value"
        ```
      - Test each method independently
      - Clean up: call `repo.close()` in teardown if needed

   f. **Update `poker/repositories/__init__.py`** — add the new repository to imports and `__all__`

   g. **Run tests** directly (not via scripts/test.py — we're already inside the container):
      ```bash
      python3 -m pytest tests/ -v
      ```
      If the full suite is too slow, run targeted tests first:
      ```bash
      python3 -m pytest tests/test_repositories/ -v
      python3 -m pytest tests/ -k "not test_ai_memory and not test_ai_resilience and not test_personality_responses and not test_reflection_system and not test_message_history_impact and not test_tournament_flow" -v
      ```

   h. **Commit** with message format:
      ```
      refactor(T3-35-B{N}): extract {RepositoryName} from GamePersistence
      ```
      Examples:
      - `refactor(T3-35-B1): extract SettingsRepository and GuestTrackingRepository from GamePersistence`
      - `refactor(T3-35-B4a): extract ExperimentRepository (captures, decisions, presets, labels) from GamePersistence`
      - `refactor(T3-35-B4b): extend ExperimentRepository (lifecycle, chat, analytics, replay)`

   i. **Check the box** in `docs/ralph/implementation_plan.md`: change `- [ ]` to `- [x]`

6. **Only work on ONE batch per invocation.** After completing one batch, exit.

## Repository Context

This is a poker game with AI personalities.

- **Backend**: Python 3.10, Flask, SocketIO, SQLite
- **Frontend**: React 18, TypeScript, Vite (at `react/react/src/`)
- **Architecture**: Functional core with frozen dataclasses (immutable state)
- **Tests**: Run directly with `python3 -m pytest tests/ -v` (Ralph runs inside a container with deps installed)
- **Game logic**: `poker/` package — use relative imports within the package
- **Persistence**: `poker/persistence.py` — the file being refactored

### Key Files

| File | Purpose |
|------|---------|
| `poker/persistence.py` | Source of all methods being extracted (the "god class") |
| `poker/repositories/base_repository.py` | Base class with connection management |
| `poker/repositories/schema_manager.py` | Table creation (all tables already exist via this) |
| `poker/repositories/serialization.py` | Card/state serialization pure functions |
| `poker/repositories/__init__.py` | Package exports |

### BaseRepository Connection Pattern

```python
# BaseRepository._get_connection() is a context manager:
with self._get_connection() as conn:
    cursor = conn.execute("SELECT ...")
    # Auto-commits on clean exit
    # Auto-rollbacks on exception
```

This differs from `GamePersistence._get_connection()` which returns a raw connection. When copying methods, you must:
1. Wrap the connection usage in `with self._get_connection() as conn:`
2. Remove explicit `conn.commit()` calls
3. The `conn.row_factory = sqlite3.Row` is already set by BaseRepository, but keep any method-level settings

### Serialization

`poker/repositories/serialization.py` has pure functions:
- `serialize_card()`, `deserialize_card()`, `serialize_cards()`, `deserialize_cards()`
- `prepare_state_for_save()`, `restore_state_from_dict()` (if present)

Import these instead of duplicating the `self._serialize_card()` etc. methods.

## Commit Convention

- Format: `refactor(T3-35-B{N}): extract {RepositoryName} from GamePersistence`
- Stage all changed files
- One batch = one commit

## Important Reminders

- Read the spec CAREFULLY before starting. The approach has been pre-approved.
- Do NOT redesign or over-engineer. Follow the spec's action items exactly.
- Do NOT modify code outside the scope of the current batch.
- Do NOT skip writing tests. Every repository needs tests.
- Do NOT change any method signatures in `GamePersistence` — callers must not be affected.
- Always commit before exiting. One batch = one commit.
- If you get stuck, document what went wrong and move on.
- The goal is **exact extraction** — copy logic as-is, adapt only the connection pattern.
