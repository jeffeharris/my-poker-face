---
purpose: Pre-main blocker — generator-based retry context manager doesn't actually retry; all write paths silently fail under contention
type: reference
created: 2026-05-15
last_updated: 2026-05-15
---

# `_get_connection_with_retry` retry mechanism is structurally broken

**Severity:** T1 (reliability — write paths under WAL contention fail without retry)
**Confidence:** 92%
**Discovered:** pre-main review, 2026-05-15
**File:** `poker/repositories/base_repository.py:111-143`

## The defect

`_get_connection_with_retry` is decorated `@contextmanager`. Inside the retry loop:

```python
for attempt in range(max_retries + 1):
    try:
        with self._get_connection() as conn:
            yield conn         # <-- transfer to caller's `with` body
            return
    except sqlite3.OperationalError as e:
        if 'locked' in str(e).lower() and attempt < max_retries:
            time.sleep(...)
            continue
        raise
```

The intent: caller's `with self._get_connection_with_retry() as conn:` body raises `OperationalError('locked')` → outer `except` catches → retries.

The reality: after `yield`, control transfers to the caller's body. When that body raises, the exception propagates *back into the generator at the yield point*, which is *inside the inner `with self._get_connection()` context manager*. The inner CM rolls back and re-raises. The outer `except` catches — but **the generator has already yielded once**, and Python generators cannot yield again in the same coroutine instance. Attempting to re-enter the loop and yield again raises:
- `StopIteration` (Python <3.7) silently bypassing the retry, or
- `RuntimeError: generator already executing` (Python 3.7+).

**Net effect:** Lock errors raised in the caller's body are never retried. The only path that actually retries is `OperationalError` raised *before* `yield` (i.e., from `_get_connection()` itself, when SQLite can't even open a connection). In WAL mode that almost never happens — contention surfaces during execute/commit, not connect.

## Who's affected

`_get_connection_with_retry` is the standard write path. Used by:
- `game_repository.save_game()`
- `game_repository.save_ai_player_state()`
- `game_repository.save_opponent_models()`
- `game_repository.save_tournament_tracker()`
- `hand_history_repository.save_hand_history()`
- ... and many others.

Production write contention silently fails to retry — first OperationalError surfaces as an unhandled 500.

## Fix

Acquire the connection once outside the retry loop; retry only the execute/commit cycle:

```python
@contextmanager
def _get_connection_with_retry(self, max_retries=3, base_delay=0.1):
    conn = self._ensure_connection()
    for attempt in range(max_retries + 1):
        try:
            yield conn
            conn.commit()
            return
        except sqlite3.OperationalError as e:
            conn.rollback()
            msg = str(e).lower()
            if ('locked' in msg or 'busy' in msg) and attempt < max_retries:
                time.sleep(base_delay * (2 ** attempt))
                continue
            raise
```

Caveat: this changes semantics slightly. Each yield now uses the same connection across retries, so partial writes from a failed attempt may persist. Verify all callers do all-or-nothing writes within the `with` block, or wrap with explicit `BEGIN/ROLLBACK`.

## Test plan

Add to `tests/test_repositories/`:
```python
def test_get_connection_with_retry_retries_on_locked():
    repo = SomeRepo()
    attempts = []
    with patch.object(repo, '_ensure_connection') as ensure:
        conn = sqlite3.connect(':memory:')
        ensure.return_value = conn

        # First call raises locked, second succeeds
        original_execute = conn.execute
        call_count = [0]
        def execute(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise sqlite3.OperationalError('database is locked')
            return original_execute(*args, **kwargs)
        conn.execute = execute

        with repo._get_connection_with_retry() as c:
            c.execute("SELECT 1")
        assert call_count[0] == 2  # retried once
```
