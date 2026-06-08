---
purpose: Narrative log of the schema-migration collision fix and the deferred squash prep
type: guide
created: 2026-06-07
last_updated: 2026-06-07
---

# Migration collision fix + verified (deferred) squash

## Why

`schema_manager.py` had grown to ~8,050 lines, ~5k of which were 154 hand-rolled
`_migrate_vN` methods. But the file size wasn't the real pain — the real pain was
**parallel worktrees colliding on every migration**. Two branches would each grab
`v155`, each bump the `SCHEMA_VERSION` constant, each edit the `migrations` dict,
and merging meant a renumber + reconciliation dance.

Jeff's instinct was a "central place each worktree reserves its version from." We
talked it through and landed somewhere better: a central *counter* concentrates
contention rather than removing it — a dense monotonic integer *requires*
coordination by definition. The fix is to make the identity **sparse and
independently assignable** so two branches never need to agree on anything. That's
what Alembic (revision hashes) and Django/Rails (timestamped filenames) actually do.

## The deeper bug nobody had named

The runner was `for version in range(current+1, SCHEMA_VERSION+1)` — a
**high-water-mark** model. That's *why* renumbering was mandatory, not just tidy:
a DB at v154 only runs `>154`, so a late-merged `v153` (merged after the DB passed
154) is **silently skipped forever**. Renumbering to 155 was the only way to make
it run. So the renumber wasn't cosmetic — it was load-bearing for correctness.

The applied-set model (track the *set* of applied ids, run anything not in it)
deletes that bug: a late-merged "earlier" id still runs. That, plus one-file-per-
migration, removes both root causes of the merge pain.

## What shipped (Step 1, `f7f6b283`)

- `migration_loader.FileMigrationLoader` — per-file migrations under
  `poker/repositories/migrations/`, applied-set tracking in `applied_migrations`,
  topological `DEPENDS_ON`, atomic per-migration commit.
- Wired after the legacy chain in `ensure_schema()`. Fully inert until the first
  file migration exists. Legacy chain untouched.
- 7 behavioural tests, incl. the headline `late_merged_earlier_id_still_applies`
  and `failed_migration_is_not_recorded` (atomicity).

A small honesty note: Docker was down and the host can't `import poker` (the
`anthropic` dep isn't installed), so I validated the loader with a standalone
stdlib harness that runs the exact test scenarios — 7/7 — rather than claiming the
in-container pytest had run. The pytest file is committed for CI.

## The squash: verified, then deliberately *not* done

Plan was C+D+E: collision fix (done above) **plus** squashing the v1..v154 chain
to a baseline so the file shrinks ~6k lines. I went to regenerate the baseline
(dump the chain's head DDL, have `_init_db` replay it) and did a full dry run in a
borrowed sibling container with the worktree mounted:

- ✅ `base+chain == chain` — replay the baseline, then run the whole chain on top,
  and you get today's exact head. **No migration guard re-fires destructively.**
  That's the only behavioural risk a squash carries, and it's clear.
- ✅ baseline replay is idempotent.
- ⚠️ the *only* diff was identifier quoting on two historically-rebuilt tables
  (`opponent_models`, `personality_snapshots`) — columns byte-identical, the chain
  just stored `CREATE TABLE "opponent_models"` after a rebuild. Fix is a two-line
  normalization in the gate test, not a schema change.

Then Jeff flagged the thing I'd have tripped on: **more migrations will land before
this deploys.** Generating and committing a baseline *now* means it's stale the
moment the chain advances. So the squash is deferred to the deploy window — and the
right deliverable isn't a baseline, it's a *proven, push-button procedure*. That's
what `b83d9c6b` is: the three scripts (generate / verify / diff) + a deploy-time
runbook in `SCHEMA_BASELINE_PLAN.md`, with the dry-run results recorded so the
eventual squash is mechanical, not a leap.

## Wrong turns worth remembering

- First commit attempt died on a pre-commit `ruff-format` reformat (it rewrites
  files and fails the run); re-stage and re-commit. Second one hit a `UP031`
  percent-format ruff couldn't auto-fix in the generator — had to hand-convert two
  `%`-formats to f-strings (awkward with `"""` literals inside).
- This worktree had **no `docker-compose.override.yml`**, so without it the
  container would've run a stale baked image missing the new loader entirely. Had
  to create one (bind-mount `.:/app`, pinned subnet `10.123.49.0/24` since
  `.47`/`.48` were taken by sibling stacks).
- Avoided building a fresh image for this worktree by mounting the code over a
  sibling's backend image — deps from the image, code from the mount. Fast.

## Where it stands

Step 1 is live and independently valuable — merge pain is gone *now*. The squash is
verified and waiting for the deploy window, baselined at whatever `SCHEMA_VERSION`
is current then (the generator reads it dynamically). Next time: freeze the chain,
run the generator, rewire `_init_db`, upgrade the gate-test normalization, extract
the chain into `legacy_migrations.py`.
