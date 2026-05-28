---
purpose: Plan to reduce developer wait time from tests by fixing fixture setup cost and compartmentalizing the suite safely
type: plan
created: 2026-05-14
last_updated: 2026-05-28
---

# Test Wait Time Reduction

## TL;DR — there are two bottlenecks, not one

Measured on 2026-05-28 (see [Baseline](#measured-baseline-2026-05-28)):

1. **Fixture setup tax (the dominant cost).** `create_repos()` takes **~5.2s per call**
   because it runs the full schema-migration chain plus ~28 repository constructors.
   Every test that uses the `repos` / `persistence` / `flask_client` fixtures pays
   that 5.2s. The 30 slowest tests in the full run are *all* ~8.3–8.6s and *most are
   `setup`, not `call`* — the time is in building databases, not in test logic.
2. **A monolithic suite shape.** CI runs the entire backend as one job; the local
   `--quick` runner skips a hardcoded list of 11 files; pytest markers are applied to
   only a fraction of tests. There is no reliable "run just the part I touched" path.

**Compartmentalizing (what this plan is mostly about) attacks #2.** But #1 is why even
a small compartment feels slow: a bucket of 100 DB-touching tests is still ~9 minutes
of *pure setup*. So the highest-leverage single change is fixing the fixture tax, and
it makes every tier and every compartment faster at once. Do it first or alongside the
split — don't ship the split and assume the wait is solved.

> **Status (2026-05-28): the fixture tax fix is implemented and validated.**
> The full suite dropped from ~23 min to **3:01 (181s)** with no new failures.
> See [Results](#results-2026-05-28). Phase 0 (markers + runner + Make targets) is
> also implemented. Remaining: CI job split (Phase 2) and pollution cleanup (Phase 3).

## Goal

Local development should run the smallest *trustworthy* test set for the change at
hand. Full-suite, integration, and poker-quality validation still exist, but they are
not part of every edit/test loop.

## Measured baseline (2026-05-28)

All runs inside the `backend` container (8 CPUs visible).

| Measurement | Result | Notes |
|---|---|---|
| Full suite, `pytest tests/ -n auto` | **~23 min wall** (6157 passed, 16 failed) | Inflated by CPU contention during measurement; a clean run is estimated ~12–15 min |
| `create_repos()` fresh DB | **5237 ms / call** (avg of 5) | The fixture tax. Schema migrations + ~28 repo constructors |
| 30 slowest tests | **all ~8.3–8.6s, mostly `setup`** | Flat distribution → cost is fixture setup, not a few pathological tests |
| Collection only (`--collect-only`) | **~6s** | Pure import overhead before any test runs |
| `tests/test_strategy/` serial (61 files) | **2m06s** | Largest bucket by file count |
| `tests/test_core/` serial (10 files) | **3m16s** | "Pure unit" by name, but LLM-heavy and slow — directory name ≠ speed |
| Total test count | **6157 tests across 311 files** | |

### Inventory

| Bucket | Files | Character |
|---|---:|---|
| `tests/test_strategy/` | 61 | Bot strategy, classification, exploitation; mostly CPU-bound, some sims |
| `tests/test_cash_mode/` | 40 | Cash economy; many DB + integration |
| `tests/test_memory/` | 30 | Psychology/relationship/memory; DB-backed |
| `tests/test_repositories/` | 22 | Repository + schema migration tests; DB-heavy by definition |
| `tests/test_core/` | 10 | LLM client/assistant; slow (`llm` tier) |
| `tests/` (root) | 148 | Mixed: ~29 Flask/route, ~40 run real game sims/state machine, rest unit-ish |

### Existing marker coverage (incomplete — this is part of the problem)

| Marker | Uses | Gap |
|---|---:|---|
| `integration` | 46 | Many integration tests unmarked |
| `flask` | 19 | ~29 root files import `create_app`/`test_client`; only 19 marked |
| `slow` | 5 | Slowest cost is unmarked fixture setup, not these 5 |
| `llm` | 3 | `test_core/llm` (3m16s) largely unmarked |
| `simulation` | **0** | Defined in the plan but never applied |

The local `--quick` path does **not** use markers — `scripts/test.py` carries a
hardcoded `SLOW_TESTS` list of 11 files it `--ignore`s. That list drifts out of date
and silently excludes whatever someone remembered to add.

## Results (2026-05-28)

After implementing Lever 1 (schema-template fast path) and Phase 0 (markers, runner,
Make targets):

| Metric | Before | After |
|---|---:|---:|
| Full suite (`-n auto`) | ~23 min (measured, contended) | **3:01 (181s)** |
| `create_repos()` fresh DB | 5337 ms | 11 ms (seeded copy) |
| Per-test DB `setup` (sample) | 2.7–3.5 s | **0.04 s** |
| `tests/test_repositories/` bucket | 6m19s | folded into a 39s 7-bucket run |
| Slowest 20 tests | all `setup`, ~8.3s | all genuine `call` (sims/route); **no `setup`** |
| Full-suite failures | 16 | 1 (pre-existing, see below) |

The fixture `setup` tax is gone from the top of the profile. The remaining slow tests
are genuine work — simulations (`test_sng_runner`, cash conservation/side-hustle),
AI-resilience retries, and Flask route tests building `create_app()` per test. Those
are what the `simulation` / `integration` / `flask` markers now gate out of the quick
loop.

Review follow-ups applied (Codex second-opinion, 2026-05-28):
- `_db_is_empty()` now counts ANY user schema object (not just tables), so a migration
  test preparing only a view/trigger/index can't be silently seeded over.
- `tests/test_schema_template_fastpath.py` pins two invariants: a seeded DB is
  schema-identical to a real migration build, and a non-empty DB is never seeded.
- `make test-repos` now also runs the root `tests/test_schema_migration_v*.py` tests
  (the bucket map always claimed them; the target had omitted them).
- Caveat: a test that asserts on the migration *process* (not just end-state) should set
  `POKER_TEST_SCHEMA_TEMPLATE=0` so it exercises a real first build. End-state schema
  tests need no change (seeded == built).

What shipped:
- `poker/repositories/schema_manager.py` — env-gated schema-template fast path.
- `tests/conftest.py` — sets `POKER_TEST_SCHEMA_TEMPLATE=1`; `pytest_collection_modifyitems`
  backfills `slow`/`simulation` markers on legacy unittest modules by filename.
- `pytest.ini` — declares the `simulation` marker.
- `scripts/test.py` — `--quick` now deselects by marker (`QUICK_DESELECT`) instead of a
  hardcoded `--ignore` list.
- `Makefile` — `test-quick`, `test-strategy`, `test-repos`, `test-cash`, `test-memory`,
  `test-flask`, `test-llm`, `test-last` bucket targets.

## Known risk: the suite is not cleanly partitionable yet

The pre-fix run produced **16 failures**, all in `tests/test_experiment_routes.py` (15)
and `tests/test_fast_forward.py` (1). They pass in isolation but fail under `-n auto`
distribution — **cross-test state pollution** (a fixture/mock/global leaking between
tests that land on the same xdist worker, consistent with the prior
`test_websocket_auth` → `create_app` leak noted in project memory).

Post-fix, **only 1 of these still fails** (`test_fast_forward::test_404_when_game_missing`),
confirmed to **pass in isolation**. The faster setup reshuffled xdist scheduling so the
15 experiment-routes failures happen not to collide now — which underscores the point:
the pass/fail of these tests depends on *ordering*, not correctness. The pollution
source is still there; Phase 3 must fix or quarantine it before narrow buckets can be
trusted as a merge signal.

Related observation (also Phase 3): the marker-based quick loop surfaced a non-failing
background-thread warning `sqlite3.OperationalError: no such table: avatar_images`
(`PytestUnhandledThreadExceptionWarning`). No test fails, and `avatar_images` *is* in
the schema/template — this is the same daemon-vs-teardown race the conftest already
fights (a lingering thread reconnecting to a deleted tmp DB → a fresh empty file). It is
independent of the schema-build fast path (which only changes how a DB is *built*, not
teardown). **A/B confirms it is pre-existing:** the same quick selection with the fast
path OFF produced the warning **6 times** vs **1** with it on — faster teardown actually
shrinks the race window. Candidate for the same daemon-lifecycle cleanup in Phase 3.

### Root cause of the xdist pollution (investigated 2026-05-28)

Bisected with serial pairwise runs: `pytest tests/test_websocket_auth.py
tests/test_fast_forward.py::...::test_404_when_game_missing` reproduces it deterministically.

The mechanism is **import-time copies of `extensions` globals**, not a leaked fixture:

- Every route module does `from ..extensions import (limiter, game_repo, auth_manager, …)`
  at module scope — *copies* the references at import time.
- `flask_app/routes/__init__.py` imports **all 20 route modules eagerly**, so importing
  any one (e.g. `game_routes`) imports the whole package.
- Route modules decorate views at import with `@limiter.limit(...)` (28×) and
  `@limiter.exempt` (8×).
- `test_websocket_auth`'s autouse fixture sets `ext.limiter = MagicMock()` and (being a
  pure unit test) can be the **first** thing on an xdist worker to import the routes
  package — while `ext.limiter`/`ext.game_repo`/… are still uninitialised. That freezes
  bad copies **process-wide on that worker**:
  - `limiter` → a bare `MagicMock`, so `@limiter.limit/exempt` *replace* each view with a
    `MagicMock`; the next `create_app()` then dies in `register_blueprint` reading
    `view_func.__name__` → **every** Flask test on that worker errors.
  - `game_repo` → `None`, so route handlers calling `game_repo.get_game_owner_info(...)`
    raise → **500 instead of the expected 404** for tests that survive blueprint registration.

So it is **a class of failures with one root**, scheduling-dependent under `-n auto`
(hence 16 → 1 as setup timing shifted), and *not* a product bug. Per-symbol mock patches
are whack-a-mole — fixing `limiter` just surfaces the `game_repo` layer next.

**Recommended fix (the real Phase 3 work, needs care across ~20 files):**
1. Make `extensions.limiter` a real app-less `Limiter(key_func=…)` at module scope and
   call `limiter.init_app(app)` in `init_extensions` — so `@limiter.*` always works at
   import regardless of test state (removes the blueprint-poisoning vector entirely).
2. Have route handlers reference mutable globals **live** (`extensions.game_repo`) instead
   of import-time copies (`from ..extensions import game_repo`) — so per-test rebinding
   via `init_persistence` is honoured no matter when the module was first imported.

A time-boxed attempt confirmed a partial mock fix only relocates the failure; the proper
fix is the refactor above, scoped as its own task. Until then the failure is rare and
order-dependent (passes in isolation); it does not block the speedup work that shipped.

This matters directly for compartmentalization: **a bucket can only be trusted in
isolation if it does not depend on (or get corrupted by) global state from other
tests.** Splitting a polluted suite can make a real regression pass locally and only
surface in CI — the opposite of "safe." So pollution cleanup is a prerequisite for
trusting narrow buckets, not an optional extra.

## Strategy: two levers

### Lever 1 — Kill the fixture tax (highest leverage) — IMPLEMENTED

Build the migrated schema **once per process**, then seed each fresh test DB from it.

A fixture-only approach was rejected because the cost has *many* entry points: a
package `db_path` fixture, several per-file `db_path` fixtures, and ~15 `unittest`
`setUp()` methods that call `create_repos()` directly. The single chokepoint they all
funnel through is **`SchemaManager.ensure_schema()`**, so the fast path lives there,
gated by an env var only tests set (`POKER_TEST_SCHEMA_TEMPLATE=1`, set in
`tests/conftest.py`). Production never sets it → behavior is unchanged in prod.

How it works (`poker/repositories/schema_manager.py`):
- The first time an **empty** DB is built in a process, the result is snapshotted to a
  temp template (only if it reached `SCHEMA_VERSION` — a clean full build).
- Every subsequent **empty**-DB `ensure_schema()` is seeded from that template via the
  **sqlite backup API** (WAL-safe, per project memory's "no plain `cp` of a live DB"),
  then the normal `_init_db()` / `_run_migrations()` run as cheap no-ops.
- Guarded to **only seed empty databases**, so schema-migration tests (which build an
  OLD schema then assert forward migration) are never seeded and keep their coverage.

Why it's safe: the seeded schema is byte-for-byte what a real build produces (same
`schema_version` rows, tables, indexes, constraints), and each test still gets a fresh,
isolated DB file.

There is also a ~6s collection / import cost (e.g. `eval7`/`pyparsing`). Lower priority;
revisit only if it dominates now that the fixture tax is gone.

### Lever 2 — Compartmentalize safely

Run the bucket that covers the code you touched, fall back to the full suite as the
merge gate. "Safe" rests on three rules:

1. **The full suite stays the merge gate.** Narrow buckets are a *fast local signal*,
   never the thing that authorizes a merge. CI runs everything (Lever 2 only changes
   how CI is *split*, not what it covers).
2. **Buckets must be pollution-free to be trusted alone.** Fix or `@pytest.mark.quarantine`
   the 16 order-dependent failures first; otherwise a green bucket can hide a real break.
3. **Map source → tests conservatively.** When in doubt, a touched module pulls in the
   integration tests that exercise it, not just its unit tests.

#### Source → test bucket map

| If you change… | Run this bucket | Marker shorthand |
|---|---|---|
| `poker/strategy/`, `poker/bounded_options.py`, `poker/*_controller.py`, charts | `tests/test_strategy/` | `make test-strategy` |
| `poker/repositories/`, `poker/persistence.py`, schema/migrations | `tests/test_repositories/` + `tests/test_schema*` | `make test-repos` |
| `poker/cash_mode/`, cash economy, lobby/whereabouts | `tests/test_cash_mode/` + `tests/test_cash*` | `make test-cash` |
| `poker/` psychology / relationships / memory | `tests/test_memory/` | `make test-memory` |
| `flask_app/` routes, auth, Socket.IO | `-m flask` + `tests/test_*route*` | `make test-flask` |
| `core/llm/` | `tests/test_core/` | `make test-llm` (slow, opt-in) |
| Game engine (`poker_game.py`, `poker_state_machine.py`) | `-m "not slow and not llm"` quick unit + sim smoke | `make test-unit` |
| Docs / frontend only | none (backend) | — |

## Test tiers (revised targets, post–Lever 1)

| Tier | Purpose | Target | When |
|---|---|---:|---|
| Tier 0 | Import/collect smoke (`--collect-only`) | `<10s` | While editing |
| Tier 1 | Touched module's unit tests | `<30s` | Every loop |
| Tier 2 | The relevant bucket from the map above | `<2 min` | Before handoff |
| Tier 3 | `-m "not slow and not integration and not llm and not simulation"` + TypeScript | `2–4 min` | Before PR |
| Tier 4 | Full suite + coverage | CI | Pre-merge gate |
| Tier 5 | Poker simulation / bb100 validation | manual | Strategy-quality changes only |

(Tier 3 target assumes Lever 1 lands; without it the quick loop is still dominated by
the 5.2s fixture tax.)

## Phase plan

Ordered by leverage and safety.

- **Phase 0 — Marker hygiene + reliable quick runner ✅ DONE.**
  - `simulation` marker declared in `pytest.ini`; `slow`/`simulation` backfilled onto
    legacy unittest modules via `pytest_collection_modifyitems` in `tests/conftest.py`.
  - `scripts/test.py --quick` now uses `-m "not slow and not integration and not llm
    and not simulation"` instead of the hardcoded `SLOW_TESTS` list.
  - Make targets added.
  - *Remaining backfill:* full `flask`/`integration` completeness (e.g.
    `test_game_route_auth`, `test_bot_type_dispatch` are slow + unmarked) — drive off
    the appendix greps. Not blocking; the quick loop already excludes the big buckets.
- **Phase 1 — Fixture tax fix (highest leverage) ✅ DONE.** Env-gated schema-template
  fast path in `SchemaManager.ensure_schema()`. Full suite 23 min → 3:01; see
  [Results](#results-2026-05-28).
- **Phase 2 — Split CI into parallel jobs** (see [CI structure](#ci-structure)) so PR
  feedback arrives in tiers. *Not started.* (Less urgent now the suite is ~3 min, but
  still worth it for categorized signal.)
- **Phase 3 — Isolation hardening.** Fix/quarantine the order-dependent failure(s);
  audit fixtures that mutate shared files/DBs so `-n auto` is deterministic; only then
  lean on narrow buckets as a *trusted* merge signal. *Not started.*

## Proposed Make targets

Add focused targets so the bucket map is one command. (All run in the backend
container — mirror `scripts/test.py`'s `docker compose exec` form.)

```makefile
test-quick:        ## Fast loop: skip slow/integration/llm/simulation
	docker compose exec backend python -m pytest -q \
		-m "not slow and not integration and not llm and not simulation"

test-strategy:     ## Bot strategy + classification + exploitation
	docker compose exec backend python -m pytest -q tests/test_strategy/

test-repos:        ## Repositories + schema/migration
	docker compose exec backend python -m pytest -q tests/test_repositories/ -k "repo or schema"

test-cash:         ## Cash mode economy + lobby
	docker compose exec backend python -m pytest -q tests/test_cash_mode/ tests/ -k "cash"

test-memory:       ## Psychology / relationships / memory
	docker compose exec backend python -m pytest -q tests/test_memory/

test-flask:        ## Routes / auth / Socket.IO
	docker compose exec backend python -m pytest -q -m flask

test-llm:          ## LLM client/assistant (slow, opt-in)
	docker compose exec backend python -m pytest -q tests/test_core/

test-last:         ## Re-run last failures
	docker compose exec backend python -m pytest -q --lf

validate-bots:     ## Strategy-quality sims (Tier 5, manual)
	docker compose exec backend python experiments/phase_8_diagnostics.py
```

Refine `-k` filters once markers are backfilled (Phase 0) — prefer `-m` over path/`-k`.

## CI structure

Split the single `test-backend` job into parallel jobs so signal arrives in tiers.
Keep total coverage identical — the full suite still runs; it is just sharded.

1. `python-quick` — `-m "not slow and not integration and not llm and not simulation"`.
   Fast PR signal, blocks merge.
2. `python-integration` — `-m "integration or flask"`. Routes/repos/state-machine.
3. `python-llm-sim` — `-m "llm or simulation"`. Slower; can run in parallel.
4. `python-coverage` — full suite + `--cov-fail-under=40` (current gate). The
   authoritative merge gate.
5. `typescript` — unchanged (lint + typecheck + vitest + build).
6. `bot-validation` — Tier 5 sims, scheduled/manual (already effectively manual).

Jobs 1–3 give fast, categorized feedback; job 4 preserves today's guarantee. With
`paths-filter` (already used for E2E), skip backend jobs entirely on docs/frontend-only
PRs.

## Simulation validation policy

Poker simulations are quality validation, not unit tests. Keep them as explicit
commands, run only when changing strategy tables, exploitation offsets, hand-strength
classification, value override, bluff-catch, math floor, or opponent modeling:

```bash
docker compose exec backend python experiments/phase_8_diagnostics.py
docker compose exec backend python experiments/simulate_bb100.py
```

Do not require simulation validation for Flask, repository, frontend, or doc changes.

## Definition of done

- [x] `create_repos`-backed tests no longer pay 5.2s each (schema-template fast path
  landed; full suite 23 min → 3:01, re-measured above).
- [x] `scripts/test.py --quick` selects by marker, not a hardcoded file list.
- [x] Documented Make targets exist for each bucket in the source→test map.
- [~] `slow` / `integration` / `llm` / `simulation` / `flask` markers applied
  consistently enough that `-m` selection is trustworthy. *Slow/simulation legacy
  backfill done; `flask`/`integration` completeness is remaining Phase 0 work.*
- [ ] CI runs tiered jobs for fast feedback while a full-coverage job remains the gate.
  *(Phase 2.)*
- [ ] The order-dependent failure(s) are fixed or quarantined, so narrow buckets can be
  trusted in isolation. *(Phase 3.)*

## Appendix — measurement commands

```bash
# Full suite + slowest tests + total time
docker compose exec -T backend python -m pytest tests/ -n auto --durations=30

# Fixture tax
docker compose exec -T backend python -c "import time,tempfile,os;from poker.repositories import create_repos;\
t=time.perf_counter();[create_repos(tempfile.mktemp(suffix='.db')) for _ in range(5)];\
print((time.perf_counter()-t)/5*1000,'ms/call')"

# Collection-only (import overhead)
docker compose exec -T backend python -m pytest tests/ --collect-only -q

# Per-bucket serial time
docker compose exec -T backend python -m pytest tests/test_strategy/ -q

# Inventory greps used for the marker backfill
grep -rln "create_app\|test_client\|socketio" tests/*.py     # flask candidates
grep -rln "run_hand\|StateMachine\|tournament\|simulate"  tests/*.py   # simulation candidates
```
