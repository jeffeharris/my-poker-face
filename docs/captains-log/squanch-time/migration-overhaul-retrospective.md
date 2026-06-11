---
purpose: Retrospective on the full migration-system overhaul — collision fix through chain squash
type: guide
created: 2026-06-08
last_updated: 2026-06-08
---

# The migration overhaul, end to end

This is the capstone for the `squanch-time` work. Two earlier entries cover the parts
([collision fix + deferred squash](migration-collision-fix-and-squash-prep.md),
[squash execution + the seed-data trap](squash-execution-and-the-seed-data-trap.md));
this one is the arc and what I'd carry forward.

## How it started

Not with "squash the migrations." It started with Jeff saying the migration process was
"a MESS" and asking what the *paths* were. The honest reframe mattered: the pain wasn't
the 8,000-line file, it was **parallel worktrees colliding on every migration**. His first
instinct — a central place to reserve version numbers — we talked past, because a central
counter concentrates contention rather than removing it. The real fix was making migration
identity *sparse* (per-file, applied-set) so two branches never have to agree on anything.

That conversation set the whole shape: a small, genuinely-useful collision fix first
(shippable on its own), and the big file-shrinking squash as a separate, deferrable thing.

## How it went

- **Collision fix** (per-file applied-set loader) — shipped and merged (#236). The
  load-bearing insight was naming the *high-water-mark skip-bug*: `range(current+1, MAX+1)`
  silently drops a late-merged lower-numbered migration, which is *why* renumbering was
  mandatory for correctness, not tidiness. Applied-set deletes that.
- **Deferred the squash on purpose** — because more migrations were landing and a committed
  baseline would go stale. Built the tooling + a verified runbook instead. Then Jeff came
  back with "all v### are in" and it was go.
- **Squash** (#241) — scripted the 6,500-line extraction (asserts on every transform),
  regenerated the baseline, cut `_init_db` over to replaying it. **8,312 → ~560 lines.**

## The three things that earned the trust

The squash is only believable because three separate checks each caught something I'd
have shipped wrong:

1. **The broad test sweep** caught the **seed-data trap** — a DDL-only baseline silently
   drops the rows the chain seeds (auth groups/permissions, enabled models, presets). My
   schema-equivalence proof was real but structurally blind to data.
2. **A routing bug** (version-0 non-empty DB → "no such table: games") surfaced the moment
   I ran beyond the schema bucket.
3. **Codex** (`pr` review) found the inverse routing edge — a non-empty *unversioned* DB
   getting baseline-stamped-and-frozen instead of migrated. One real P2, and it confirmed
   the parts I was most anxious about (seed completeness, sub-baseline replay) were sound.

The meta-lesson I keep landing on: *"I verified X" is only as strong as the dimension X
measures.* Equivalence proofs, broad test runs, and an independent reviewer each see a
different dimension; none of them is redundant.

## Smaller scars worth keeping

- Worktrees need their own gitignored `docker-compose.override.yml` or the container runs
  a stale baked image. Reusing a sibling backend image with the worktree bind-mounted beat
  a fresh build every time.
- Container-generated files are **root-owned** → the host pre-push ruff hook gets
  permission-denied. The generated baseline also belongs in ruff's exclude.
- `codex-assist` emits nothing without a TTY — wrap long runs in `script -qfc`.

## Where it ends

Collision fix merged, squash green and merged. New migrations are now one file in
`migrations/`. The chain is archived, not deleted, until no restorable backup predates the
baseline (Phase 4, someday). Closing out the branch and worktree from here.

Good run.
