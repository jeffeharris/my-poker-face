---
purpose: Grounded narrative of a schema-drift bug (a v148 DB missing a v139 column) found while building a renown sim, and the migration-path hardening it prompted ahead of a prod cutover
type: reference
created: 2026-06-03
last_updated: 2026-06-03
---

<!-- newest entries at the bottom -->

# Captain's log — schema drift & the prod migration path (tournaments worktree)

How a "let's sim renown to tune the draw weights" task turned up a schema-drift
landmine right before a planned prod deploy, and what we did about it. Wrong turns
kept in. Newest at the bottom.

---

## 2026-06-03 — the renown sim that found a prod landmine

I set out to accrue real renown-v2 on a dev DB copy so the tournament draw's
renown/field weights could be tuned (instead of fabricating renown — we'd
explicitly rejected synthetic data). The headless driver reused the production
recompute (`_maybe_recompute_prestige`) so the renown math wasn't reimplemented.
First run died immediately: `sqlite3.OperationalError: no such column:
entity_kind` when persisting AI renown.

**The trace went somewhere I didn't expect.** `entity_kind` is a column the v139
migration adds to `prestige_snapshots`. The dev DB is stamped `schema_version =
148` — so the column *should* exist. It didn't. I'd half-assumed a one-off, but
the same "no such column: entity_kind" had shown up earlier as test-ordering
noise, so I stopped guessing and diffed the schema.

**The wrong turn I avoided:** my first instinct was "the base `CREATE TABLE`
omits the column → every fresh build is broken → this is systemic and prod is at
risk." Before acting on that, I built a fresh DB via `ensure_schema()` and
checked — it *has* `entity_kind`. So fresh builds are complete; the migration
system works. The dev DB was the anomaly, not the code. That one check flipped the
whole diagnosis (and the prod-risk assessment) from "systemic" to "drift on one
long-lived DB."

**Root cause (verified).** A full schema diff (live dev DB vs a fresh canonical
build) showed the drift was tiny and exact: one missing column
(`prestige_snapshots.entity_kind`) and two missing indexes. The dev DB's
`schema_version` history had **146 rows, max 148 — missing exactly rows 139 and
140**. Those migrations were *renumbered* on a branch merge (v138's own
description literally says "Renumbered from v133 on the renown→development merge")
to numbers the dev DB had **already passed**. The walk only runs versions `>
MAX(version)`, so a migration inserted below a live DB's current version never
runs on it. `schema_version = N` does not prove the schema is complete — that was
the real lesson, and it matters because **prod is about to migrate.**

**A bonus find:** while reading `PROD_MERGE_PLAN.md` to ground the prod advice, I
hit a claim that dev tracks versions in a `schema_migrations` table vs prod's
legacy `schema_version`, framing the cutover as a two-framework bridge. Verified
against the live DB: dev uses **`schema_version`** (no `schema_migrations` table
exists). Prod uses `schema_version` too. So it's very likely the *same* table and
numeric scheme, prod just far behind (v70) — the bridge is probably "run
migrations 71→148," not a framework rewrite. Corrected the plan (with a caveat to
confirm prod's column structure on a copy, since I can't see prod from here).

**What we shipped.**
1. **Fixed the dev DB** — backend stopped, WAL-safe backup, re-ran the two
   idempotent migrations + backfilled the missing version rows. Completeness gate
   now passes; renown can persist on dev.
2. **`scripts/schema_completeness_check.py`** — diffs any DB against a fresh
   canonical `ensure_schema()` build, fails on any missing table/column/index.
   This is the catch-all the dev DB needed: it goes into the prod dry-run as a
   required post-migration gate (step 2b in the plan). Extra/legacy prod tables
   are reported, not failed.
3. **A CI contiguity guard** (`test_schema_manager.py`) — migration version
   numbers must be a gapless, duplicate-free 1..SCHEMA_VERSION, so a future
   renumber can't silently leave a hole.
4. **Corrected + hardened `PROD_MERGE_PLAN.md`** with the root cause, the
   table-name correction, and the gate.

The honest framing for the deploy: prod is *probably* safe from this specific
gap (at v70 it'll walk through 139/140 and get the column), but "probably" isn't
a migration plan. The completeness gate turns it into a checkable invariant —
migrate the prod copy, run the gate, require zero missing before cutover. That's
the deliverable that outlasts this one bug.
