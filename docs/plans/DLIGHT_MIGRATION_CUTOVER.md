---
purpose: Sequencing + strategy to move schema management off the hand-rolled SchemaManager onto dlight, without entangling it with the circuit feature ship
type: guide
created: 2026-06-07
last_updated: 2026-06-07
---

# dlight schema-management cutover

> **Confidence note.** This plan is concrete on the parts we control — the
> *sequencing*, the *squash baseline*, the *diff-validation*, and the
> *cutover/rollback*. It is deliberately **tool-agnostic about dlight itself**:
> the author did not have reliable knowledge of dlight's exact API/mechanics, so
> every dlight-specific step is marked **[CONFIRM]** and must be filled in against
> the real tool before executing. Don't take any dlight API detail here as fact.

## Why

Schema is managed today by a hand-rolled `SchemaManager`
(`poker/repositories/schema_manager.py`): a module-level `SCHEMA_VERSION`
(currently **156**) and a dict of numbered `_migrate_vN_*` methods, applied in
order against whatever version a DB reports (tracked in a `schema_version` table,
`SELECT MAX(version)`). Fresh DBs are built straight at `SCHEMA_VERSION` and skip
the migration chain; existing DBs run forward from their recorded version.

This works, but the **migration numbers are a shared global namespace across
branches**, so every long-lived branch that adds a migration collides on merge
and has to be **manually renumbered**. Exhibit A, fresh: the main→circuit sync
(2026-06-07) hit `v155` claimed by *both* sides (main's regard rebaseline #202 vs
circuit's `career_progress`); resolving it meant hand-renumbering career to
`v156`, bumping `SCHEMA_VERSION`, and deleting a duplicate `v153/v154` method pair
the merge produced. That manual surgery is error-prone and recurs on every
feature branch. **dlight's value is eliminating that** — and the highest-value
piece is the **squash** (collapse the historical chain into one baseline), which
removes the per-branch number collisions entirely.

## Decisions (locked with the user, 2026-06-07)

1. **Ship circuit to prod FIRST, on the current SchemaManager. Then switch to
   dlight as a separate change.** Don't entangle a feature ship with a tooling
   migration — each is independently risky; together you can't tell which broke
   prod, and dlight wants a *stable, known* schema to adopt.
2. **No runtime feature flag for the schema tooling.** A DB has one physical
   schema; you can't run two migration systems against it behind a flag. The safe
   equivalent is a **validated one-shot cutover** (build both, diff, cut over with
   a tested rollback) — see Phase 3–4. *(This is separate from the **product**
   feature flags `CAREER_PROGRESSION_ENABLED` / `CAREER_VOUCH_ENABLED`, which DO
   gate the circuit narrative — that's a different ask.)*
3. **Squash from what PROD actually has**, not a dev DB. After the circuit deploy,
   confirm prod is at `v156` and snapshot *that* as dlight's baseline. Dev DBs can
   carry drift (renumbered branches, partial migrations); prod is the source of
   truth for the baseline.

## Current state to preserve (the contract dlight must honour)

- **Entry point:** `create_repos(db_path)` (`poker/repositories/__init__.py:46`)
  → `SchemaManager.ensure_schema()` (`schema_manager.py:390`). Whatever dlight
  becomes, this single call must still leave a correct, up-to-date schema (lots of
  code + tests call `create_repos`).
- **Applied-version record:** a `schema_version` table (rows of
  `version, description`); current version = `MAX(version)`. dlight will have its
  own ledger — the cutover must reconcile the two (Phase 4).
- **Fresh-build vs migrate:** fresh DBs are built at `SCHEMA_VERSION` directly;
  existing DBs migrate forward. dlight must support both (a from-scratch build for
  new sandboxes/tests, and a no-op "already current" for prod).
- **Test schema-template fast path:** `POKER_TEST_SCHEMA_TEMPLATE` builds the
  schema once and copies it per test (`schema_manager.py:22`, set by
  `tests/conftest.py`). The whole suite's speed depends on a cheap from-scratch
  build — dlight's baseline build must stay fast (a single baseline DDL is
  *faster* than replaying 156 migrations, so this should improve).
- **Multi-sandbox / per-owner DBs:** schema is applied per DB; new sandboxes build
  fresh constantly at runtime. dlight's from-scratch path is on the hot path, not
  just a one-time prod step.

## Plan (phased)

### Phase 0 — Ship circuit to prod (current tooling)
- Deploy `circuit-progression` → prod via the normal path (`docs/guides/OPS_RUNBOOK.md`).
- **Confirm prod is at `v156`** (`SELECT MAX(version) FROM schema_version` on the
  prod DB). This is the baseline anchor — do not proceed until prod == 156.
- Prod ships with `CAREER_PROGRESSION_ENABLED` / `CAREER_VOUCH_ENABLED` OFF (the
  narrative ships dark), so this deploy is schema + dormant code only.

### Phase 1 — Confirm dlight **[CONFIRM]**
Before any code: pin down what dlight actually is and how it models migrations.
Answer, in this doc, before Phase 2:
- What is dlight? (library? CLI? in-house?) Where do migration files live, what
  format (SQL? Python?), how does it record applied state, how does it build a DB
  from scratch vs migrate-forward?
- How does it **adopt an existing DB** (the "stamp/baseline" operation — mark a
  live prod DB as already at the baseline without re-running DDL)? This is the
  load-bearing capability for Phase 4; if dlight can't stamp, the cutover design
  changes.
- Does it run inside our Python process (so `create_repos` can invoke it) or only
  as a CLI? That decides how `ensure_schema` is rewired.

### Phase 2 — Squash to a v156 baseline
- Build a pristine DB from `SchemaManager` at `v156` (e.g. `create_repos` on a
  temp path) and **dump its full DDL** (`sqlite_master` — tables, indexes,
  triggers, views) as the **single baseline** dlight owns.
- Author that baseline as dlight's first migration **[CONFIRM format]**. All 156
  historical `_migrate_vN_*` steps collapse into this one snapshot; the numbered
  chain is retired.
- Keep the old `SchemaManager` in the tree (not wired) until Phase 5, as a
  reference + rollback.

### Phase 3 — Diff-validation harness (the gate)
A throwaway script (force-add under `scripts/`, per the gitignore convention):
1. Build DB **A** via current `SchemaManager` (`create_repos`) at v156.
2. Build DB **B** via dlight's baseline from scratch.
3. **Assert byte-identical schema**: compare normalized `sqlite_master` (sort
   rows; ignore `schema_version`/dlight-ledger tables and AUTOINCREMENT sqlite
   internals; normalize whitespace in DDL). Any diff blocks the cutover.
4. Also diff against a **prod schema dump** (Phase 0) to catch prod drift the dev
   build wouldn't show.
- This is the "no runtime flag" safety: we prove equivalence offline instead of
  toggling systems live.

### Phase 4 — Cutover
- **New DBs (tests, new sandboxes):** `create_repos`/`ensure_schema` builds from
  dlight's baseline instead of the migration chain. Must stay fast (template path).
- **Existing DBs (prod + dev):** **stamp** them as already-at-baseline so dlight
  doesn't try to re-create existing tables. **[CONFIRM]** the exact stamp op; the
  precondition is "DB is at SchemaManager v156" (verified in Phase 0).
- Reconcile the **`schema_version` table**: dlight has its own applied-ledger;
  decide whether to keep `schema_version` (read-only legacy marker) or migrate the
  marker into dlight's ledger. Don't drop `schema_version` until nothing reads it
  (grep first).
- **Deploy with a tested rollback:** rollback = redeploy the prior image (which
  still has `SchemaManager`); since the cutover is additive-only (no DDL change to
  existing prod data — the schema is identical by Phase 3), reverting the *code*
  reverts the tooling. **Back up the prod DB first** (it's large — ~5.5GB — so a
  targeted/quiesced backup per the ops runbook, not a naive copy).

### Phase 5 — Decommission SchemaManager
- Once dlight is live and a release has baked, remove the unused
  `_migrate_vN_*` chain (keep the doc/history). New schema changes are dlight
  migrations from here on — **and the per-branch renumber collision is gone.**

## Risks / watch-items
- **Prod is the baseline source of truth.** A dev-DB-derived baseline can bake in
  drift. Anchor on the prod v156 dump (Phase 0 → Phase 3 cross-check).
- **The from-scratch build is on the hot path** (every new sandbox + the test
  template), not a one-time step. dlight's baseline build must be fast and
  in-process-invokable, or `create_repos` latency / suite speed regress.
- **Partial-migration / legacy DBs.** Some dev DBs sit below v156 or carry
  renumbered branch history (the `schema_version` self-heal in
  `schema_manager.py` exists for exactly this). Decide: bring them to v156 with
  the *old* tool before stamping, or discard them. Prod is clean (Phase 0).
- **`schema_version` readers.** Grep before retiring it — the migration self-heal
  and any admin/debug surface may read it.
- **Don't squash before prod == v156.** Squashing from a behind/ahead DB bakes the
  wrong baseline.

## Open questions (fill in at Phase 1)
- What is dlight, exactly, and what's its baseline/stamp/from-scratch model?
- Can it stamp a live prod DB as already-at-baseline (no DDL re-run)?
- In-process or CLI-only — how does `ensure_schema` invoke it?
- Migration file format (SQL vs Python) and where they live in the repo.

## Related
- Current tooling: `poker/repositories/schema_manager.py`, `create_repos`
  (`poker/repositories/__init__.py`).
- The collision that motivated this: the main→circuit merge (schema v155↔v156),
  `docs/plans/CASH_MODE_CAREER_PROGRESSION.md` (career migration is v156).
- Deploy/rollback/backup mechanics: `docs/guides/OPS_RUNBOOK.md`.
