---
purpose: Strategy for consolidating the 7,840-line schema_manager.py by squashing the v1..v148 migration chain at the prod cutover
type: design
created: 2026-06-03
last_updated: 2026-06-08
---

# Schema Baseline / Migration Squash Plan

> **Status (2026-06-08): SQUASH COMPLETE at v157.** All phases done; the document
> below is retained as the design record.
> - **Collision fix (per-file applied-set migrations) — SHIPPED & merged** (PR #236).
>   New migrations are files under `poker/repositories/migrations/`, applied by
>   `migration_loader.FileMigrationLoader` (applied-set, not high-water-mark). This
>   removed the shared-edit-site conflicts and the renumber-on-merge skip-bug.
> - **Phase 1 + 3 (the squash) — DONE** on branch `squanch-time`:
>   - `poker/repositories/legacy_migrations.py` holds the frozen v1..v157 chain
>     (`LegacyMigrations`), extracted verbatim from `schema_manager.py`.
>   - `poker/repositories/schema_baseline.py` is the GENERATED head: 206 DDL
>     statements + 55 seed rows (`scripts/_gen_schema_baseline.py`).
>   - `_init_db` replays the baseline (DDL + seed) and stamps `SCHEMA_VERSION`;
>     `ensure_schema` routes a positive sub-baseline DB (restored old backup) through
>     the legacy chain, everything else through the baseline.
>   - **`schema_manager.py`: 8,312 → ~560 lines.**
>   - Permanent guard: `tests/test_schema_consistency.py` asserts the baseline equals
>     the chain head (replay the chain over the baseline → no-op).
> - **Key finding during the cutover:** a DDL-only baseline silently dropped the
>   chain's SEED rows (default groups/permissions/enabled_models/prompt_presets);
>   the generator now captures and replays them. Caught by the test suite.
> - **Phase 4 (retire `legacy_migrations.py`)** — deferred until no restorable backup
>   predates the baseline.

Connects: TRIAGE **T3-17** (no migration framework) and **T3-44** (`schema_manager.py`
monolith); the prod migration in [`PROD_MERGE_PLAN.md`](PROD_MERGE_PLAN.md); the gate
test `tests/test_schema_consistency.py`.

## The problem

`poker/repositories/schema_manager.py` is ~7,840 lines. ~5,000 of those are 148
`_migrate_vN_*` methods. We want to collapse that chain to a baseline so the file
shrinks to roughly the DDL (~1,500–2,000 lines) and new contributors aren't reading
two years of migration archaeology.

## How the system actually works today (verified 2026-06-03)

- `_init_db()` runs `CREATE TABLE IF NOT EXISTS …` for the tables it knows about.
- A brand-new DB has schema version 0, so `ensure_schema()` then runs
  `_run_migrations()`, which **replays the entire v1→v148 chain** over that fresh
  schema. The migrations are guarded (`if 'owner_id' not in columns: …`), so they're
  normally no-ops — but **every fresh install today = `_init_db` + full chain.**
- A squash deletes the chain. After that, new installs run **`_init_db()` alone**.

### The blocking finding: `_init_db` has drifted from the chain

`tests/test_schema_consistency.py::test_init_db_matches_full_migration_chain`
(currently `xfail`) builds a DB both ways and diffs `sqlite_master`. As of
2026-06-03 the chain creates, on top of `_init_db`:

- **~19 tables** missing from `_init_db` entirely — `cash_sessions`, `cash_tables`,
  `chip_ledger_entries`, `entity_presence`, `holdings_snapshots`, `stakes`,
  `prestige_snapshots`, `sandboxes`, `opponent_observation_lifetime`, `coach_tips`,
  `coach_session_evaluations`, `dossier_informant_unlocks`, `user_avatars`,
  `user_preferences`, `bounded_replay_results`, `cash_scalps`, `cash_idle_pool`,
  `cash_idle_metadata`, `cash_session_events`.
- **~41 indexes** missing from `_init_db`.
- **12 tables** whose `_init_db` CREATE is an *older shape* than the chain leaves it:
  `ai_bankroll_state`, `api_usage`, `experiment_games`, `experiments`,
  `opponent_models`, `personalities`, `personality_snapshots`, `player_career_stats`,
  `player_coach_profile`, `pressure_events`, `tournament_results`,
  `tournament_standings`.

**Interpretation:** essentially every system added since the cash-mode era was wired
migration-only and never back-ported into `_init_db`. The chain is **load-bearing** —
`_init_db` alone is a partial skeleton. This is invisible today (fresh installs run the
chain too), and it is the single reason the chain can't just be trimmed.

## Goal & strategy

Squash/baseline at a version where **no database below the baseline can ever appear
again**. The prod cutover is that moment (after it, the only DBs in existence are at
the baseline). Standard technique — Django `squashmigrations`, Rails `schema.rb`,
Alembic "compact to base".

## Sequencing

### Phase 0 — Gate in place (DONE 2026-06-03)
- `tests/test_schema_consistency.py` added. The equivalence test is the **squash
  precondition gate** (`xfail` until `_init_db` is reconciled; `strict=True` so it
  fails loudly the moment it starts passing → prompt to keep it as a permanent guard).

### Phase 1 — Reconcile `_init_db` (independent; can start now)
- Back-port the ~19 tables + ~41 indexes into `_init_db`, and update the 12 stale
  CREATE shapes to match the chain's end state. Work straight off the test's diff.
- Done when `test_init_db_matches_full_migration_chain` XPASSES → remove the `xfail`
  marker; it becomes a permanent guard against future migration-only drift.
- *Optional, parallel:* the structural split (T3-44) — move DDL into per-domain modules
  and migrations into a `migrations/` package. Pure refactor, gated by this same test.

### Phase 2 — Prod cutover (the [`PROD_MERGE_PLAN.md`](PROD_MERGE_PLAN.md) event)
- Prod is on the legacy `schema_version` system (~v70), a different mechanism, and is
  missing entire table families. **Don't** replay ~70 bridge migrations across two
  numbering systems.
- Instead: stand up a fresh DB via the **full `ensure_schema()`** (init + chain — *not*
  `_init_db` alone, which is still a skeleton until Phase 1 lands), **ETL the prod data
  into it**, stamp version = baseline. The new prod DB is born at head.
- This ETL approach is **robust to the `_init_db` drift** precisely because it builds
  via the full path — another reason to prefer it over a bridge-migration cutover.
- After this: no sub-baseline DB exists in production.

### Phase 3 — Squash (requires Phase 1 green AND Phase 2 done)
- Set `BASELINE_VERSION = 148` (or bump to a clean `149` floor).
- Move `_migrate_v1..v148` into `poker/repositories/legacy_migrations.py`, invoked
  **only** when a sub-baseline DB is detected. Keep `_init_db` as the canonical head.
- Add a loud guard: non-empty DB with `version < BASELINE` → **raise** (never silently
  run `_init_db` over it). Empty DB → `_init_db` at baseline. New chain starts at 149.
- `schema_manager.py` drops to ~1,500–2,000 lines.

### Phase 4 — Retire the legacy chain (after a safe interval)
- Once no backup older than the baseline is worth restoring (e.g. > backup-retention
  window), delete `legacy_migrations.py`. Git history retains it for archaeology.

## Guardrails

- **Archive, don't hard-delete** (Phase 3 keeps the chain reachable for old-backup
  recovery; Phase 4 removes it only once such backups are out of retention).
- **The version-below-baseline guard raises** rather than silently mis-initializing.
- **Don't conflate the squash with adopting Alembic** (T3-17). The squash is a low-risk
  deletion with a huge file win. Alembic (autogenerate + downgrade + a real version
  table) is a separate project — evaluate it *after* the squash, never on the critical
  path of the prod data migration.

## Acceptance

- `test_init_db_matches_full_migration_chain` passes with no `xfail` (Phase 1).
- A DB built from `_init_db` alone is byte-identical (schema) to a full `ensure_schema()`
  build (same test).
- Prod runs on a baseline-version DB; no sub-baseline DB exists (Phase 2).
- `schema_manager.py` no longer carries the v1..v148 chain inline (Phase 3).

## Incidental

`_init_db`'s docstring still says "Tables (25 total)" — it's ~130 now. Fix when
reconciling.

---

## Squash execution runbook (verified 2026-06-07)

**Decision: the squash is DEFERRED to deploy-readiness, not done now.** More
migrations (file migrations, and possibly legacy `_migrate_vN` from in-flight
branches) will land before this ships. Generating and committing a baseline now
would go stale the moment the chain advances. Instead the procedure below is
**proven push-button** and run once, at the deploy window, against whatever
`SCHEMA_VERSION` is current then. The collision-fix half (file loader) is already
live and is fully compatible with the chain continuing to grow.

### Proven via a dry run (2026-06-07, against v154)

Three force-added scripts under `scripts/` (run in a backend container with the
worktree mounted at `/app`):

- `_gen_schema_baseline.py` — builds a fresh DB via `_init_db` + the full chain,
  dumps every `sqlite_master` object in creation order, and writes
  `poker/repositories/schema_baseline.py` as `BASELINE_STATEMENTS` (a static list)
  + `BASELINE_VERSION = SCHEMA_VERSION` (read dynamically). Each statement is made
  idempotent (`IF NOT EXISTS`) so replay on an existing DB is a no-op.
- `_verify_schema_baseline.py` — proves `chain == base+chain` and that replaying
  the baseline twice is a no-op.
- `_schema_diff_tmp.py` — prints the raw `_init_db`-vs-chain object diff (the
  reconcile worklist: at v154 that was 60 missing objects + 12 shape-differs).

**Dry-run results (v154):**
- ✅ `base+chain == chain` — replaying the baseline then running the whole chain
  reproduces today's exact head. **No migration guard re-fires destructively.**
  This is the load-bearing safety property and it holds.
- ✅ baseline replay is idempotent (safe on existing DBs).
- ⚠️ `base != chain` on exactly **two tables** (`opponent_models`,
  `personality_snapshots`) — and *only* in identifier quoting. Those two were
  table-rebuilt by a historical migration, so the chain stored
  `CREATE TABLE "opponent_models"` (quoted); the generator emits it unquoted.
  Columns are byte-identical. **Fix is in the gate test's normalization, not the
  schema** (see below).

### Steps to run at squash time

1. **Freeze the chain.** Confirm no more legacy `_migrate_vN` will land. Note the
   current `SCHEMA_VERSION` — that is `BASELINE_VERSION`.
2. **Generate:** run `_gen_schema_baseline.py` → commit
   `poker/repositories/schema_baseline.py`.
3. **Rewire `_init_db`** to replay `BASELINE_STATEMENTS` and stamp
   `schema_version = BASELINE_VERSION` (so fresh installs do NOT replay the chain).
   Keep `IF NOT EXISTS` on every statement for safety on existing DBs.
4. **Upgrade the gate test normalization** in
   `tests/test_schema_consistency.py::_schema_objects`: additionally strip
   `IF NOT EXISTS ` and surrounding identifier quotes/backticks so logically-equal
   schemas compare equal. This resolves the two rebuilt-table cosmetic diffs. Then
   the test compares "fresh install via baseline" vs "old DB upgraded via chain"
   and they match. Remove the `xfail(strict=True)` marker — it becomes a permanent
   drift guard.
5. **Extract the chain (Phase 3):** move `_migrate_v1..vN` + the `migrations` dict
   into `poker/repositories/legacy_migrations.py`, invoked ONLY when a sub-baseline
   DB is detected. Add the loud guard: non-empty DB with `version < BASELINE` and
   no `applied_migrations` → run legacy chain to baseline, then the file loader;
   empty DB → baseline + stamp; at-baseline → file loader only.
6. **Verify** with `_verify_schema_baseline.py` + the full
   `tests/test_schema_consistency.py` + `tests/test_repositories/` bucket, in
   Docker. `schema_manager.py` drops ~6k lines.

### Why this is low-risk

The dry run already proved the only behavioural property that could bite
(`base+chain == chain`, i.e. no guard misbehaves on the head schema). Everything
remaining is mechanical (generate + rewire + a test-normalization tweak) and fully
gated by the equivalence test. The legacy chain is archived, never hard-deleted,
until backups age past retention (Phase 4).
