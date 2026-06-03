---
purpose: Living plan + risk register for eventually bringing the modern dev work (cash mode, circuit, dossiers, chip ledger, tournament economy, presence) to the far-older production deployment.
type: guide
created: 2026-06-02
last_updated: 2026-06-03
---

# Prod Merge Plan — Bringing Dev to Production

> **Status: scoping / risk-gathering.** No prod migration has been attempted.
> This doc exists to collect "things we must be aware of when we finally merge,"
> so they aren't rediscovered under deploy pressure. Append to the **Awareness /
> Gotchas** register as new landmines surface.

## The core problem (discovered 2026-06-02)

Production is **far behind** `development`/`tournaments` and on a **different
migration framework**:

> **CORRECTION (2026-06-03):** an earlier draft of this table claimed dev tracks
> versions in a **`schema_migrations`** table. That is **wrong** — verified on the
> live dev DB: the modern `SchemaManager` reads/writes **`schema_version`**
> (columns `version, applied_at, description`; `SELECT MAX(version)`), and there
> is **no `schema_migrations` table**. So prod and dev use the **same table name
> and the same numeric scheme** — prod is just far behind (v70 vs 148). The
> cutover is therefore likely *"run migrations 71→148 on prod"*, NOT a
> two-framework bridge — **but confirm prod's `schema_version` STRUCTURE on a
> copy** (prod's may be a legacy human-readable variant under the same name; if
> its `version` column isn't the same numeric scheme, that IS the bridge work).

| | Production | Dev branches (`tournaments`, `development`, …) |
|---|---|---|
| Migration table | **`schema_version`** (v70 — confirm column structure on a copy) | **`schema_version`** (numeric; cols `version/applied_at/description`) |
| Version | **v70**, last applied **2026-02-06** | **`SCHEMA_VERSION = 148`** (`poker/repositories/schema_manager.py`) |
| `personalities.personality_id` | ❌ absent | ✅ (~v85) |
| `personalities.circulating` | ❌ absent | ✅ (v123) |
| `avatar_images.personality_id` | ❌ absent | ✅ (v137) |
| Cash mode tables | ❌ none | ✅ |
| Chip ledger / bankrolls / sandboxes | ❌ none | ✅ |
| Dossiers (`opponent_observation_lifetime`) | ❌ none | ✅ |
| Relationships (`relationship_states`) | ❌ none | ✅ |
| Presence (`entity_presence`) | ❌ none | ✅ |
| Tournament economy (`tournament_invites`/`_sessions`) | ❌ none | ✅ |
| Career (`career_progress`) | ❌ none | ✅ |

Prod liveness at discovery: 43 tables, 20 games, 81 personalities, 322 avatar
rows; last game updated **2026-05-01**, last `api_usage` **2026-05-08**.

**Consequence:** a prod cutover is NOT a routine `./deploy.sh`. It's a large,
planned, one-way schema upgrade that must bridge the legacy→numeric migration
systems and stand up ~20 new subsystems against existing prod data. Treat it as
its own project with a tested dry-run on a prod DB copy before touching prod.

## How prod tracks version (so the upgrade can detect it)

- BOTH prod and the modern code use a **`schema_version`** table — prod at max
  **v70**, dev at **148**. The modern `SchemaManager` reads `SELECT MAX(version)
  FROM schema_version` and applies every registered migration whose number is
  **greater than** that max. So pointing the modern code at the prod copy should
  run migrations **71→148** in order. **Prove on a copy** that: (a) prod's
  `schema_version.version` column is the same numeric scheme (not a text/legacy
  label that `MAX()` mis-orders), and (b) `_init_db`'s `CREATE TABLE IF NOT
  EXISTS` statements no-op cleanly over prod's populated legacy tables (they
  should — but the avatar/personality tables differ; watch those).
- **`schema_version = N` does NOT prove the schema is complete** (see the
  root-cause finding below). After the walk, run the **schema-completeness gate**
  (`scripts/schema_completeness_check.py`) on the copy and require ZERO missing
  tables/columns/indexes before cutover.

## Root cause finding (2026-06-03): a high version number can hide missing migrations

The dev DB was stamped `schema_version = 148` but was **missing the
`prestige_snapshots.entity_kind` column + the v139/v140 indexes** — the v139 and
v140 rows were absent from its history entirely. Cause: those migrations were
**renumbered to 139/140 on a branch merge** (`schema_manager.py` v138's own
description: *"Renumbered from v133 on the renown→development merge"*) **after**
this DB had already advanced past 140. The walk only runs versions `> MAX`, so a
migration inserted at a number below a live DB's current version **never runs on
that DB**. A *fresh* `ensure_schema()` build is complete (it walks 1→148), so this
is drift on long-lived DBs, not a fresh-build bug.

**Why prod is probably safe from THIS instance:** prod is at v70, so the walk will
run 139 and 140 (both > 70) → prod gets `entity_kind`. The hazard is general,
though: **the completeness gate is the catch-all** — run it post-migration, every
time. **Going forward:** never assign a migration a number ≤ any deployed DB's
current version; keep the registry numbers contiguous + monotonic (a CI test now
asserts this — see `tests/test_repositories/test_schema_manager.py`).

## Suggested sequence (high level — refine before executing)

1. **Snapshot prod** (WAL-safe: sqlite backup API + `integrity_check`, not `cp` —
   see `reference_sqlite_wal_backup`). Keep it; this is the rollback.
2. **Dry-run on the copy:** point the modern code at the prod DB copy in a
   throwaway container and let `SchemaManager` migrate. Capture every migration
   that runs, every destructive step, and any failure. Iterate until clean.
2b. **Schema-completeness gate (REQUIRED before cutover):** after the migrate,
   run `scripts/schema_completeness_check.py --db <prod-copy>` — it diffs the
   migrated copy against a fresh canonical `ensure_schema()` build and exits
   non-zero on any MISSING table/column/index. Require a clean (exit 0) result.
   This is the catch-all for the "high version number, missing migration" class
   (see the root-cause finding above). Extra/legacy prod tables are reported but
   don't fail — those are expected (e.g. the old single-table tournament tables).
3. **Data backfills to validate on the copy** (these run against REAL prod data,
   not the empty dev template the tests use):
   - v137 avatar `personality_id` backfill over prod's 322 rows — re-run the
     orphan/dupe safety check (unique `personalities.name`, non-null
     `personality_id`, zero orphan avatar rows). The check was MOOT on current
     prod (no `personality_id` yet); it becomes load-bearing the moment this
     migration runs.
   - `personality_id` assignment for prod's 81 personalities (slug collisions?).
   - Any chip-ledger / bankroll genesis backfill assumptions that expect dev's
     seeded universe.
4. **Decide branch topology:** which branch is the prod target? Several feature
   branches carry colliding schema numbers (e.g. `polish` and `tournaments` both
   touched **v136**; tournaments is at **v137**). Pick the integration branch,
   resolve schema-number collisions FIRST (see `project_p2_tournament_economy`
   and `project_emotion_families_v136` for the known v136 collision), then plan
   the prod upgrade off that single reconciled branch — never off a half-merged
   tree.
5. **Cutover window:** prod has been quiet since ~May 8, so a maintenance window
   is low-impact, but still announce + snapshot immediately before.

## Awareness / Gotchas register (APPEND as discovered)

> When you hit something that "future prod-merge me" needs to know, add it here
> with a date. Keep entries short; link to the deeper note.

- **2026-06-02 — Two migration systems.** Prod = legacy `schema_version` (v70);
  dev = numeric `schema_migrations` (v137). The bridge between them is unproven —
  prove it on a prod DB copy first. See `project_prod_schema_drift` (memory).
- **2026-06-02 — Schema-number collisions across feature branches.** `polish`
  (emotion families) and `tournaments` (economy) both used **v136**; whichever
  merges second must renumber. Resolve ALL collisions on the integration branch
  before planning the prod chain, or the numeric migration order will be wrong.
- **2026-06-02 — v137 avatar backfill runs fresh on prod.** Prod's `avatar_images`
  has no `personality_id`; the backfill (join avatar→persona on display name)
  will run against prod's real 322 rows. Orphan/dupe rows there would leave
  avatars unkeyed — run the safety check on the prod DB copy during the dry-run.
  Related: the legacy avatar `personality_name` dual-key surface is being removed
  on-branch (`MAIN_EVENT_TABLE_HUMANIZE_HANDOFF.md` §P3.9b); after that removal,
  the avatar reads/URLs are pid-only, so the prod backfill MUST succeed (no name
  fallback to fall back on) — verify on the copy before cutover.
- **2026-06-02 — Prod has the OLD single-table tournament tables**
  (`tournament_tracker`, `tournament_results`, `tournament_standings`) but none
  of the multi-table economy. The MTT work assumes the modern tables exist;
  confirm the migration adds them without conflicting with the legacy ones.
- **2026-06-02 — Feature flags default OFF in prod.** `TOURNAMENT_CIRCUIT_ENABLED`
  and `CHIP_CUSTODY_ENABLED` are enabled in the **dev** `.env` only; prod env
  must be set deliberately at/after cutover, not assumed. See
  `docs/guides/OPS_RUNBOOK.md`.
- **2026-06-03 — `schema_version = N` ≠ complete schema (renumbering hazard).**
  The dev DB sat at v148 missing the v139 `prestige_snapshots.entity_kind` column
  + v139/v140 indexes, because those migrations were renumbered below the DB's
  current version after it had passed them, so the walk skipped them (rows 139/140
  absent from its `schema_version`). Silently broke the renown AI fan-out
  (`record_ai_many` INSERTs `entity_kind`). Fixed on dev by re-running the two
  idempotent migrations + backfilling the version rows (backup:
  `data/poker_games.backup_pre_schemafix_20260603.db`). **Mitigation = the
  completeness gate (step 2b).** Dev `schema_migrations`-vs-`schema_version`
  table-name claim above was also corrected. See the captain's log
  `docs/captains-log/tournaments/schema-drift-and-migration-path.md`.

## Pointers
- **Schema-completeness gate:** `scripts/schema_completeness_check.py` (run on the
  prod copy after migrating — the required cutover gate; step 2b).
- Schema authority: `poker/repositories/schema_manager.py` (`SCHEMA_VERSION`,
  `migrations` dict, `_init_db`, `_migrate_vNNN`).
- Ops/deploy: `docs/guides/OPS_RUNBOOK.md`, `deploy.sh`, `docker-compose.prod.yml`.
- WAL-safe backups: `reference_sqlite_wal_backup` (memory).
- The branch-only modern work: `project_p2_tournament_economy`,
  `project_chip_custody_cutover`, `project_cash_state_model_freeze` (memory).
