# tests/ — CLAUDE.md

Conventions for writing tests in this repo. Most tests here are authored by Claude;
follow these so new tests stay fast, isolated, and trustworthy. Full rationale, the
source→bucket map, and how to run things: `docs/guides/TESTING.md`.

## Where tests go

- Put tests under `tests/`. Use the bucket subdirs when one fits:
  `test_strategy/`, `test_repositories/`, `test_cash_mode/`, `test_memory/`,
  `test_core/llm/`. Otherwise a top-level `tests/test_*.py` is fine.
- Files `test_*.py`, classes `Test*`, functions `test_*`.

## Mark expensive tests (keeps `--quick` fast)

Add a module-level marker (or a list) — markers are declared in `pytest.ini`:

```python
import pytest
pytestmark = pytest.mark.integration          # one
pytestmark = [pytest.mark.flask, pytest.mark.integration]  # several
```

| Marker | Use for |
|---|---|
| `slow` | materially slows the quick loop |
| `integration` | crosses modules / needs app + DB |
| `flask` | route / auth / Socket.IO / app wiring |
| `llm` | exercises LLM provider code |
| `simulation` | runs hands / tournaments / replays / benchmark loops |

`python3 scripts/test.py --quick` deselects `slow`/`integration`/`llm`/`simulation`, so an
unmarked slow test silently drags the fast loop down for everyone.

## Isolation

- Use the shared fixtures in `conftest.py`: **`db_path`** (fresh temp DB path) and
  **`repos`** (all repositories on a temp DB). Don't hand-roll DB creation.
- Use pytest's **`tmp_path`** for any files. **Never** write to `data/` or the real DB
  (`/app/data/poker_games.db`).
- No test-order or cross-test dependencies — a test must pass run alone and in `-n auto`.
- The schema-template fast path (build-once, seed-copies) is automatic; just build DBs
  via `create_repos`/`ensure_schema`. It only seeds **empty** DBs, so schema-migration
  tests that craft an old schema are unaffected. If a test asserts on the migration
  *process* (not the end state), set `POKER_TEST_SCHEMA_TEMPLATE=0`.

## Mock all LLM / network calls

Use `make_openai_response` / `mock_openai_response` from `conftest.py`. Tests must never
hit a real provider. (LLM/narrative side-paths are disabled suite-wide in `conftest.py`.)

## Don't import-copy mutable globals

In code under test, prefer reading `extensions.X` live over
`from ..extensions import game_repo`. Import-time copies are the root of the known xdist
import-ordering pollution (see `docs/guides/TESTING.md` § Known gotchas). If a Flask test
passes alone but fails in the full run, suspect this — not your change.

## Before you call it done

- Run the relevant bucket: `make test-<bucket>` (or `python3 scripts/test.py <pattern>`).
- Before a PR: `python3 scripts/test.py --quick` and `--ts`.
- Don't run bare `pytest` on the host — tests run inside the backend container.

## Coverage policy

CI enforces `--cov-fail-under=40` on `poker`/`flask_app`/`core` — a **floor, not a
target**. New logic needs *behavioural* tests (branches, edge cases, error paths), not
line-coverage padding. A test that can't fail teaches nothing. Strategy / EV / bot-quality
changes are validated by sims (`experiments/…`), not unit coverage.
