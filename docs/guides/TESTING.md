---
purpose: How to run, structure, and write tests in this repo (for humans and Claude agents)
type: guide
created: 2026-05-28
last_updated: 2026-05-28
---

# Testing Guide

Light, practical reference for running and writing tests. If you're an agent: the
must-follow rules are mirrored in `CLAUDE.md` (§ Testing); this guide is the "why".

## Running tests

All tests run **inside the backend Docker container**. Use `scripts/test.py` or the
`make test-*` targets — don't run bare `pytest` on the host.

```bash
python3 scripts/test.py              # full Python suite
python3 scripts/test.py --quick      # fast loop (skip slow/integration/llm/simulation)
python3 scripts/test.py test_card    # tests matching a pattern
python3 scripts/test.py --ts         # TypeScript type-check
make test-strategy                   # one bucket (see the map below)
```

The full suite is ~3 min (`-n auto`). You almost never need it during a tight loop —
run the **bucket** for the code you touched, then `--quick` before a PR, then rely on
CI for the full run. See `docs/plans/TEST_WAIT_TIME_REDUCTION.md` for the rationale and
measured baselines.

## Tiers — run the smallest useful set

| Tier | What | When |
|---|---|---|
| 0 | `pytest --collect-only` (import smoke) | while editing |
| 1 | the touched module's tests | every loop |
| 2 | the relevant **bucket** (below) | before handoff |
| 3 | `--quick` + `--ts` | before PR |
| 4 | full suite + coverage | CI / pre-merge |
| 5 | sims (`experiments/…`) | only for strategy-quality changes |

## Source → test bucket map

Change this code → run this bucket:

| You changed… | Run | Command |
|---|---|---|
| `poker/strategy/`, `bounded_options.py`, `*_controller.py` | strategy | `make test-strategy` |
| `poker/repositories/`, `persistence.py`, schema/migrations | repos + root schema tests | `make test-repos` |
| `poker/cash_mode/`, cash economy/lobby | cash | `make test-cash` |
| psychology / relationships / memory | memory | `make test-memory` |
| `flask_app/` routes, auth, Socket.IO | flask | `make test-flask` |
| `core/llm/` | llm (slow) | `make test-llm` |
| game engine / pure logic | quick unit | `make test-quick` |
| docs / frontend only | (no backend tests) | — |

The full suite stays the **merge gate** — buckets are a fast local signal, not a
substitute for it.

## Markers

Declared in `pytest.ini`; `--quick` deselects `slow`/`integration`/`llm`/`simulation`.

| Marker | Use for |
|---|---|
| `slow` | materially slows the quick loop |
| `integration` | crosses modules / needs app+DB |
| `flask` | route / auth / Socket.IO / app wiring |
| `llm` | exercises LLM provider code |
| `simulation` | runs hands / tournaments / replays / benchmark loops |

Apply markers with a module-level `pytestmark = pytest.mark.X` (or a list). A few legacy
`unittest` modules are tagged by filename in `tests/conftest.py::pytest_collection_modifyitems` —
prefer in-file markers for new modules.

## Fixtures & isolation

Shared fixtures live in `tests/conftest.py` (plus per-package conftests):

- **`db_path`** — a fresh temp DB path. **`repos`** — all repositories on a temp DB.
  Use these instead of hand-rolling DB setup.
- **Schema template fast path** — the first fully-migrated DB built per process is
  snapshotted; later fresh builds are seeded from it (~10ms vs ~5.2s). It's automatic
  (env-gated `POKER_TEST_SCHEMA_TEMPLATE=1`, set in conftest). You don't call it; just
  build DBs through `create_repos`/`ensure_schema` as usual. Only **empty** DBs are
  seeded, so schema-migration tests that build an old schema are untouched. If a test
  asserts on the migration *process* (not end-state), set `POKER_TEST_SCHEMA_TEMPLATE=0`.
- **`make_openai_response` / `mock_openai_response`** — mock LLM responses. Never hit a
  real provider in tests; LLM/narrative paths are disabled suite-wide in conftest.
- Use pytest's `tmp_path` for any files. Don't write to `data/` or the real DB.

## Known gotchas

- **xdist import-copy pollution (open).** Route modules do
  `from ..extensions import (game_repo, auth_manager, …)` — *copies* taken at import.
  A unit test that imports a route module before extensions are initialised can freeze
  stale globals process-wide, making a later Flask test fail order-dependently under
  `-n auto`. The limiter half is fixed (`extensions.limiter` is a real app-less Limiter);
  the repo half needs a live-`extensions.X` refactor (tracked in the plan doc). If you
  see a Flask test that passes alone but fails in the full run, this is why — not your
  change.
- **WAL + DB copies.** Never `cp` a live SQLite DB; use the sqlite backup API
  (`source.backup(dest)`). See the schema template code.
- **Don't run bare `pytest` on the host** — wrong deps/paths. Use the container.

## Coverage policy

- CI gate: `--cov-fail-under=40` on `poker`/`flask_app`/`core` (a floor, not a target).
- New logic should have **behavioural** tests (branches, edge cases, error paths), not
  line-coverage padding. A test that can't fail teaches nothing.
- Strategy/EV/bot-quality changes are validated by **sims** (Tier 5), not unit coverage.
```
