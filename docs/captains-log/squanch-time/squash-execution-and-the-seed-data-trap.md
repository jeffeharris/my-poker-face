---
purpose: Narrative log of executing the v157 migration squash and the seed-data bug it surfaced
type: guide
created: 2026-06-08
last_updated: 2026-06-08
---

# Executing the squash — and the seed-data trap I'd have shipped

## Going from "deferred" to "go"

A day earlier we'd built the per-file migration system (the real collision fix) and
*verified but deliberately deferred* the chain squash — because more migrations were
going to land and a committed baseline would go stale. Jeff came back with "all v###
schema migrations are in" and that was the green light. The precondition we'd designed
for had arrived: the legacy chain was quiescent, so the baseline could be frozen.

`SCHEMA_VERSION` had moved to **157** (v155/v156/v157 landed via #237/#238 while we
worked) — exactly the "more migrations land first" we'd predicted. The generator reads
the version dynamically, so that cost nothing.

## The relocation (Step A) — mechanical and honest

`schema_manager.py` was 8,312 lines, ~4,800 of them the `_migrate_vN` chain. I refused
to hand-edit that. A scripted extraction with an assert on every transform moved the
chain into `legacy_migrations.py` (the methods only touch their `conn` arg, so they
lifted verbatim). **8,312 → 1,810 lines**, behaviour identical, verified green, committed
on its own so it was a clean checkpoint before the riskier cutover.

## The cutover (Step B) — where it got interesting

`_init_db` now replays a generated baseline instead of building a partial skeleton +
replaying 157 migrations. Wrote the generator, regenerated, rewired, updated the gate
test into a "baseline == chain head" guard. The schema tests went green. Felt done.

Then I ran the *broad* suite, and `test_user_repository` lit up: `'user' not in []`.

## The trap

A DDL-only baseline — built from `sqlite_master` — captures **schema, not data**. But
several migrations don't just create tables, they **seed rows**: default auth `groups`
and `permissions`, the `enabled_models` set, system `prompt_presets`. My baseline had
the tables and none of the rows. A fresh install would have booted with broken auth and
zero enabled models.

The uncomfortable part: this is *exactly* the failure mode I'd named out loud the day
before, when I argued the squash was "verified" — I'd said the one genuinely-silent risk
was a data-seeding migration, as opposed to a schema one (which fails loud). I'd proven
`base+chain == chain` at the *schema* level and called the approach safe. The schema
equivalence was real; it just wasn't the whole story. The seed data lived in a dimension
my verification never looked at. **The test suite saw what my equivalence check couldn't.**

Fix: the generator now also dumps every non-empty table's rows as `INSERT OR IGNORE`
seed, and `_init_db` replays them. 55 seed rows across 5 tables.

## Other landmines, briefly

- **Routing.** First cutover sent a *version-0 non-empty* DB to the legacy chain, whose
  `v1` does `ALTER TABLE games` — and `games` no longer exists pre-chain post-squash.
  "no such table: games." The fix: only route to the chain for a *positive* sub-baseline
  version (`current and current < V`); everything else builds the baseline.
- **Test semantics.** Fresh DBs now carry a *single* baseline version row, not the full
  1..157 history. Tests that did `DELETE FROM schema_version WHERE version >= N` to
  "simulate a pre-vN DB" were now emptying the table (→ baseline path) instead of landing
  at N-1. They had to set an explicit pre-vN version to exercise the chain.
- **Chicken-and-egg.** The generator called `_init_db`, which now imports `BASELINE_SEED`
  — absent while regenerating. Broke the cycle by bootstrapping DDL from the existing
  `BASELINE_STATEMENTS` and making the seed import graceful.
- **Root-owned file.** `schema_baseline.py` was generated *inside* the container (root),
  so the host pre-push ruff hook got permission-denied writing it. Re-owned it via a
  cp-through-`/tmp`, and excluded the generated file from ruff entirely.

## Where it landed

PR #241. `schema_manager.py`: **8,312 → ~560 lines.** 551 schema/repo/migration tests +
the full quick suite green before push. The chain is archived (not deleted) in
`legacy_migrations.py`, reachable only for a restored pre-baseline backup.

The lesson I want to keep: "I verified X" is only as good as the dimension X measures.
Schema-equivalence was a real proof of a real property — and still missed a class of
breakage entirely. The broad test run wasn't ceremony; it was the thing that caught the
bug my clever verification was structurally blind to.
