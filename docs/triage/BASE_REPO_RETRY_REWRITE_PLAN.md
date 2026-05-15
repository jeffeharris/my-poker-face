---
purpose: Rewrite plan for _get_connection_with_retry — caller audit, recommended fix, migration steps, and test plan
type: spec
created: 2026-05-15
last_updated: 2026-05-15
---

# `_get_connection_with_retry` rewrite plan

Companion to `BASE_REPO_RETRY_BROKEN.md`. Full caller audit, bug reproducer, recommended approach, migration steps, and test plan for T1-32.

## Caller audit

`_get_connection_with_retry` has exactly **7 callsites**, all in `poker/repositories/game_repository.py`. No other repository file uses it.

| Callsite line | Method | SQL pattern | Idempotent on retry? |
|---|---|---|---|
| 79 | `save_game` | `INSERT ... ON CONFLICT DO UPDATE` (1 stmt) | YES |
| 202 | `save_tournament_tracker` | `INSERT ... ON CONFLICT DO UPDATE` (1 stmt) | YES |
| 323 | `save_ai_player_state` | `INSERT OR REPLACE` (1 stmt) | YES |
| **356** | **`save_personality_snapshot`** | **plain `INSERT`, no UNIQUE constraint, autoincrement PK** | **NO — retry creates duplicate row** |
| 385 | `save_emotional_state` | `INSERT OR REPLACE` (1 stmt) | YES |
| 488 | `save_controller_state` | `INSERT OR REPLACE` (1 stmt) | YES |
| 572 | `save_opponent_models` | `DELETE` + multi-`INSERT OR REPLACE` in one block | YES — DELETE re-clears on retry |

**`save_personality_snapshot` is the only non-idempotent caller.** The `personality_snapshots` table (`schema_manager.py:181-191`) has no UNIQUE constraint on `(game_id, player_name, hand_number)`. A retry after a failed commit would insert a duplicate snapshot row, corrupting the elasticity tracking timeline. This must be fixed before applying `@retry_on_lock` to this method.

**`save_opponent_models` atomicity:** The DELETE-then-multi-INSERT block runs inside a single transaction via `_get_connection`. On retry, `_get_connection` rolls back the failed attempt before the next call. Correct.

## Bug reproducer

Add to `tests/test_repositories/test_base_repository.py` (new file):

```python
import sqlite3, pytest
from poker.repositories.base_repository import BaseRepository

class _Repo(BaseRepository):
    pass

def test_cm_retry_does_not_retry_caller_body(tmp_path):
    """_get_connection_with_retry does NOT retry lock errors from caller body.

    After yield, contextmanager calls generator.throw() when the caller raises.
    The generator catches OperationalError, sleeps, and loops to a second yield.
    contextmanager sees the second yield and raises
    RuntimeError('generator didn't stop after throw()').
    The original OperationalError is lost and NO retry occurs.
    """
    repo = _Repo(str(tmp_path / "t.db"))
    conn = repo._ensure_connection()
    conn.execute("CREATE TABLE t (x INTEGER)")

    orig = conn.execute
    insert_calls = [0]

    def fail_on_first_insert(sql, *a, **kw):
        if "INSERT" in sql.upper():
            insert_calls[0] += 1
            if insert_calls[0] == 1:
                raise sqlite3.OperationalError("database is locked")
        return orig(sql, *a, **kw)

    conn.execute = fail_on_first_insert

    with pytest.raises(RuntimeError, match="generator didn't stop after throw"):
        with repo._get_connection_with_retry() as c:
            c.execute("INSERT INTO t VALUES (1)")

    assert insert_calls[0] == 1, "Lock error was not retried — only 1 INSERT attempt"
    repo.close()
```

Delete this test in Phase 4 when `_get_connection_with_retry` is removed.

## Recommended approach — Option A: `@retry_on_lock` decorator

**`retry_on_lock` is already implemented correctly at `base_repository.py:19-55` but never applied to any method.** The entire migration is: import it in `game_repository.py`, add `@retry_on_lock()` to the 7 methods, change `_get_connection_with_retry()` to `_get_connection()` in each body, and delete the broken CM.

### Why Option A wins

- **Zero new code.** The decorator is complete and correct.
- **Visibility.** `@retry_on_lock()` at the `def` line is explicit; the broken CM hid retry logic.
- **Clean retry semantics.** `_get_connection`'s `except Exception: conn.rollback(); raise` leaves the thread-local connection clean for the next attempt.
- **Option B** (manual retry loop in each caller) duplicates 7 identical loops. No advantage.
- **Option C** (CM for connection-acquisition only + separate function retry) adds complexity for no gain. Acquisition does not fail in WAL mode; contention is at execute/commit.

### Retry flow with `_get_connection`

1. Decorator calls the method (attempt N).
2. `with self._get_connection() as conn:` yields thread-local connection.
3. Caller body raises `OperationalError('locked')`.
4. `_get_connection.__exit__`: `conn.rollback()`, re-raise.
5. Exception exits the method body; decorator catches, checks 'locked'/'busy', sleeps (0.1s × 2^attempt), loops.
6. Attempt N+1: same thread-local connection, clean state after rollback. On success, `_get_connection` calls `conn.commit()`.

`busy_timeout=5000` (driver-level, 5s per attempt) + `@retry_on_lock` (3 retries, 0.1+0.2+0.4+0.8 = 1.5s backoff) = up to ~21.5s total contention window per write. Consider reducing `busy_timeout` to 1000ms as a follow-up.

## Migration steps

### Step 1 — Fix `save_personality_snapshot` idempotency (prerequisite)

`game_repository.py:357` — change:
```python
INSERT INTO personality_snapshots
```
to:
```python
INSERT OR IGNORE INTO personality_snapshots
```

`personality_snapshots` rows are append-only. Suppressing a duplicate on retry is correct: the first attempt either committed (ignore the dup) or was rolled back (second INSERT is the real one). No schema migration needed.

### Step 2 — Update import in `game_repository.py`

```python
# Line 9 — before:
from poker.repositories.base_repository import BaseRepository

# After:
from poker.repositories.base_repository import BaseRepository, retry_on_lock
```

### Step 3 — Apply `@retry_on_lock()` to all 7 methods

For each method at lines 58, 188, 319, 352, 371, 473, 557:

```python
# Before:
def save_game(self, ...):
    ...
    with self._get_connection_with_retry() as conn:
        conn.execute(...)

# After:
@retry_on_lock()
def save_game(self, ...):
    ...
    with self._get_connection() as conn:
        conn.execute(...)
```

No other changes to method bodies.

### Step 4 — Delete `_get_connection_with_retry`

Remove `base_repository.py:111-143`. Verify:

```bash
grep -r '_get_connection_with_retry' poker/ flask_app/ tests/
```

Zero results before merging. Delete the bug-reproducer test from Step 1 of the test plan.

## Test plan

New file `tests/test_repositories/test_base_repository.py`:

| Test | What it verifies |
|---|---|
| `test_cm_retry_does_not_retry_caller_body` | Bug reproducer — `RuntimeError` propagates, `insert_calls == 1`. Delete after Phase 4. |
| `test_retry_on_lock_retries_on_locked` | Patches `conn.execute` to raise 'locked' once; decorated method retries and succeeds. |
| `test_retry_on_lock_exhausts_then_raises` | All attempts raise 'locked'; `OperationalError` propagates after `max_retries`. |
| `test_retry_on_lock_non_lock_error_not_retried` | `OperationalError('no such table')` propagates immediately, call count = 1. |

Addition to `tests/test_repositories/test_game_repository.py`:

| Test | What it verifies |
|---|---|
| `test_save_personality_snapshot_idempotent` | Two calls with same `(game_id, player_name, hand_number)` → exactly 1 row. |

## Risks

**Same connection across retries.** All retries share the thread-local `sqlite3.Connection`. Safe — `_get_connection` calls `conn.rollback()` before re-raising. `_ensure_connection`'s `SELECT 1` health check recreates the connection if broken.

**`save_personality_snapshot` duplicate rows.** Applying `@retry_on_lock` before completing Step 1 risks duplicates if `conn.commit()` raises 'locked' after a successful INSERT. Complete Step 1 before Step 3 for this method.

**Pre-computation on every retry.** JSON serialization (`json.dumps(state_dict)` in `save_game`) reruns on each attempt. Negligible — sub-millisecond.
