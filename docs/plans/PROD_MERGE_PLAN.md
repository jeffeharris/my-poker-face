---
purpose: Living plan + risk register for eventually bringing the modern dev work (cash mode, circuit, dossiers, chip ledger, tournament economy, presence) to the far-older production deployment.
type: guide
created: 2026-06-02
last_updated: 2026-06-02
---

# Prod Merge Plan — Bringing Dev to Production

> **Status: scoping / risk-gathering.** No prod migration has been attempted.
> This doc exists to collect "things we must be aware of when we finally merge,"
> so they aren't rediscovered under deploy pressure. Append to the **Awareness /
> Gotchas** register as new landmines surface.

## The core problem (discovered 2026-06-02)

Production is **far behind** `development`/`tournaments` and on a **different
migration framework**:

| | Production | Dev branches (`tournaments`, `development`, …) |
|---|---|---|
| Migration table | legacy **`schema_version`** (human-readable) | numeric **`schema_migrations`** |
| Version | **v70**, last applied **2026-02-06** | **`SCHEMA_VERSION = 137`** (`poker/repositories/schema_manager.py`) |
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

- Prod has a **`schema_version`** table (max v70) and **no `schema_migrations`**.
- The modern `SchemaManager` keys off `schema_migrations`. On a prod DB it will
  see no `schema_migrations` and must NOT assume a fresh DB (that would try to
  `_init_db` over a populated legacy DB). **Verify the bootstrap path handles
  "legacy `schema_version` present, `schema_migrations` absent"** — does it
  detect the legacy version and run the numeric chain from the right starting
  point, or does it need a one-time bridge migration? This is the first thing to
  prove on a prod DB copy.

## Suggested sequence (high level — refine before executing)

1. **Snapshot prod** (WAL-safe: sqlite backup API + `integrity_check`, not `cp` —
   see `reference_sqlite_wal_backup`). Keep it; this is the rollback.
2. **Dry-run on the copy:** point the modern code at the prod DB copy in a
   throwaway container and let `SchemaManager` migrate. Capture every migration
   that runs, every destructive step, and any failure. Iterate until clean.
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

## Pointers
- Schema authority: `poker/repositories/schema_manager.py` (`SCHEMA_VERSION`,
  `migrations` dict, `_init_db`, `_migrate_vNNN`).
- Ops/deploy: `docs/guides/OPS_RUNBOOK.md`, `deploy.sh`, `docker-compose.prod.yml`.
- WAL-safe backups: `reference_sqlite_wal_backup` (memory).
- The branch-only modern work: `project_p2_tournament_economy`,
  `project_chip_custody_cutover`, `project_cash_state_model_freeze` (memory).
