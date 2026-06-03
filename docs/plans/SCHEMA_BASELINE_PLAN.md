---
purpose: Strategy for consolidating the 7,350-line schema_manager.py by squashing the v1..v140 migration chain at the prod cutover
type: design
created: 2026-06-03
last_updated: 2026-06-03
---

# Schema Baseline / Migration Squash Plan

Connects: TRIAGE **T3-17** (no migration framework) and **T3-44** (`schema_manager.py`
monolith); the prod migration in [`PROD_MERGE_PLAN.md`](PROD_MERGE_PLAN.md); the gate
test `tests/test_schema_consistency.py`.

## The problem

`poker/repositories/schema_manager.py` is ~7,350 lines. ~5,000 of those are 140
`_migrate_vN_*` methods. We want to collapse that chain to a baseline so the file
shrinks to roughly the DDL (~1,500–2,000 lines) and new contributors aren't reading
two years of migration archaeology.

## How the system actually works today (verified 2026-06-03)

- `_init_db()` runs `CREATE TABLE IF NOT EXISTS …` for the tables it knows about.
- A brand-new DB has schema version 0, so `ensure_schema()` then runs
  `_run_migrations()`, which **replays the entire v1→v140 chain** over that fresh
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
- Set `BASELINE_VERSION = 140` (or bump to a clean `141` floor).
- Move `_migrate_v1..v140` into `poker/repositories/legacy_migrations.py`, invoked
  **only** when a sub-baseline DB is detected. Keep `_init_db` as the canonical head.
- Add a loud guard: non-empty DB with `version < BASELINE` → **raise** (never silently
  run `_init_db` over it). Empty DB → `_init_db` at baseline. New chain starts at 141.
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
- `schema_manager.py` no longer carries the v1..v140 chain inline (Phase 3).

## Incidental

`_init_db`'s docstring still says "Tables (25 total)" — it's ~130 now. Fix when
reconciling.
