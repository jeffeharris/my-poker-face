---
purpose: Deep dive on why retry_on_lock was never applied, with per-caller side-effect audit and final recommendation
type: reference
created: 2026-05-15
last_updated: 2026-05-15
---

# `retry_on_lock` deep dive (T1-32 follow-up)

## TL;DR — Apply the decorator. Codex's concern doesn't apply here.

The decorator was written alongside `_get_connection_with_retry` in a single authoring pass and **never applied in any commit** — not backed out, just dead code from day one. Codex's pushback (LLM calls, socket emissions, in-memory mutations in caller bodies) does **not** apply to any of the 7 caller methods. The repository layer is a pure persistence boundary. Apply `@retry_on_lock` as specified in `BASE_REPO_RETRY_REWRITE_PLAN.md`, with `INSERT OR IGNORE` as the mandatory prerequisite for `save_personality_snapshot`.

## Git history

Across `development`, `tieredbot-messages`, and `hybrid-ai` branches: no commit ever applied `@retry_on_lock` to any method, and no commit ever removed it from a previously decorated method. The decorator was written as a forward-planned utility alongside `_get_connection_with_retry` and left unused.

Conclusion: not "applied and backed out". Just dead from day one.

## Per-caller side-effect audit

All 7 callers are in `poker/repositories/game_repository.py`. Audited each method body for: LLM calls, in-memory state mutations, socket emissions, file writes, user-facing logging, cache invalidation, external service calls.

| Method | Line | SQL Pattern | Non-DB work in method body | Retry-safety |
|---|---|---|---|---|
| `save_game` | 58 | `INSERT ... ON CONFLICT DO UPDATE` | `game_state.to_dict()`, `json.dumps()` — all BEFORE `with` | **SAFE** |
| `save_tournament_tracker` | 188 | `INSERT ... ON CONFLICT DO UPDATE` | `tracker.to_dict()`, `json.dumps()` — all BEFORE `with` | **SAFE** |
| `save_ai_player_state` | 319 | `INSERT OR REPLACE` | `json.dumps(messages)`, `json.dumps(personality_state)` INSIDE `with` — pure serialization, idempotent | **SAFE** |
| `save_personality_snapshot` | 352 | Plain `INSERT` (no conflict clause) | None | **UNSAFE — requires prerequisite** |
| `save_emotional_state` | 371 | `INSERT OR REPLACE` | `emotional_state.to_dict()` BEFORE `with`; `json.dumps()` in SQL args | **SAFE** |
| `save_controller_state` | 473 | `INSERT OR REPLACE` | `psychology.get('tilt')`, `json.dumps()` BEFORE `with` | **SAFE** |
| `save_opponent_models` | 557 | `DELETE` + multi-`INSERT OR REPLACE` | `to_dict()` BEFORE `with`; `logger.debug()` and `datetime.now()` INSIDE `with` — benign duplication | **SAFE** |

### Direct response to codex's pushback

Codex raised four concerns about applying the decorator:

1. **LLM calls** — *none* of the 7 methods make LLM calls.
2. **In-memory game state mutations** — none. The repo takes pre-computed data as arguments.
3. **Socket emissions** — none. The repo is the persistence boundary.
4. **User-facing logging** — none at warning/error level. Only `logger.debug` (`save_opponent_models`).

The repository layer is a **pure persistence boundary**: it takes pre-computed data as arguments, serializes it, and writes it. No controllers, no services, no emissions.

## Risk comparison

| Scenario | Implement (`@retry_on_lock`) | Don't implement (leave broken CM) |
|---|---|---|
| Normal operation | Identical; zero overhead unless `OperationalError` fires | Identical |
| WAL execute/commit contention | Decorator catches, sleeps 0.1s → 0.2s → 0.4s, retries up to 3x. `_get_connection` rolls back before each retry. Write eventually succeeds. | `_get_connection_with_retry` catches via `generator.throw()`, attempts second `yield`, raises `RuntimeError: generator didn't stop after throw()`. Original `OperationalError` is lost. **Caller sees a 500. Silent write loss.** |
| `save_personality_snapshot` retry before prerequisite | Duplicate snapshot row inserted; elasticity timeline corrupted | Duplicate never inserted — but write fails entirely on contention |
| High contention (all retries exhausted) | `OperationalError` propagates after 3 attempts + 0.7s backoff. Caller gets clean exception. | First failure surfaces immediately. |
| `busy_timeout=5000` alone | Decorator still valuable for Python-level `OperationalError` that slips through driver-level spin | `busy_timeout` does not prevent `OperationalError` from propagating on commit |

**Not implementing** means every WAL contention event that reaches Python becomes a `RuntimeError` 500 with silent write loss.

**Implementing without prerequisite** risks the personality elasticity timeline only on `save_personality_snapshot` retries — fully addressed by `INSERT OR IGNORE`.

## Recommendation — Option (a): Apply as planned

The earlier plan in `BASE_REPO_RETRY_REWRITE_PLAN.md` stands unchanged. Codex's concern was valid for the general case but does not apply to these specific callers.

### Why not Option (d) — `busy_timeout` alone?

`busy_timeout` is a driver-level spin-wait. It does not prevent `OperationalError` from propagating in all cases (especially on commit). The combination of `busy_timeout=5000` + `@retry_on_lock(3 retries)` gives a ~21.5s contention window, much wider than either alone.

### Why not Option (c) — retry only commits?

Adding a separate commit-only retry layer adds complexity with no benefit. `_get_connection`'s rollback-on-failure already gives each retry a clean connection state.

## Migration plan

`BASE_REPO_RETRY_REWRITE_PLAN.md` is confirmed correct. Execute in order:

1. **Prerequisite** (`game_repository.py:357`): `INSERT INTO personality_snapshots` → `INSERT OR IGNORE INTO personality_snapshots`
2. **Import** (`game_repository.py:9`): `from poker.repositories.base_repository import BaseRepository, retry_on_lock`
3. **Apply `@retry_on_lock()`** to all 7 methods (lines 58, 188, 319, 352, 371, 473, 557). Change `_get_connection_with_retry()` → `_get_connection()` in each body.
4. **Delete** `base_repository.py:111-143` (`_get_connection_with_retry`).
5. **Verify**: `grep -r '_get_connection_with_retry' poker/ flask_app/ tests/` → zero results.

## Test plan additions

Earlier plan's test table stands. Two additions based on this audit:

| Test | What it verifies |
|---|---|
| `test_save_opponent_models_debug_log_on_retry` | `logger.debug` fires once (on eventual success), not twice |
| `test_save_ai_player_state_json_serialized_inside_cm` | `json.dumps` inside CM produces identical output on retry; single row written |

The `test_cm_retry_does_not_retry_caller_body` bug reproducer from the earlier plan should be written first (proves the broken CM) and deleted in Step 4.

## Key files

- `poker/repositories/base_repository.py:19-55` — `retry_on_lock` (correct, never applied)
- `poker/repositories/base_repository.py:111-143` — broken `_get_connection_with_retry`
- `poker/repositories/game_repository.py` — 7 callsites at lines 79, 202, 323, 356, 385, 488, 572
- `poker/repositories/schema_manager.py:181-191` — confirms `personality_snapshots` has no UNIQUE constraint
- `docs/triage/BASE_REPO_RETRY_REWRITE_PLAN.md` — prior plan, confirmed valid
- `docs/triage/CODEX_REVIEW_OF_PLANS.md` — Codex pushback (now addressed)
