---
purpose: Narrative log of building the central feature-flag registry and retiring the scattered env-var arming
type: guide
created: 2026-06-07
last_updated: 2026-06-07
---

# A registry for the feature flags (2026-06-07)

## The ask, and how I misread it at first

Jeff: "I need to set up some better system for managing feature flags." My first
instinct was the textbook answer — a runtime toggle system, DB-backed, admin UI,
flip flags live. I sketched exactly that.

It was wrong. When Jeff spelled out the actual pain, it had nothing to do with
runtime toggling:

> "I want to lock in the changes. They are all over the place and I want them
> forced to be centralized. When I launch a new dev env I don't want to worry
> about which ones were enabled by default. And when I push to prod, I want to
> know that they are all enabled."

So the problem was **lifecycle and drift**, not toggling: flags accumulating and
being re-armed by hand every branch, no single place to declare them, no way to
see what was on where. The design pivoted accordingly — a declarative registry
with a lifecycle *stage* and *per-environment defaults*, not a toggle server.

He also asked, fairly, whether anything off-the-shelf already did this
(Unleash/Flagsmith/Flipt). The honest answer: those are built for runtime
targeting and rollout, and they'd add a service + network dependency to a
single-box deploy while *not* solving the lifecycle/cleanup problem. The in-repo
registry was less code and a better fit.

## What got built

`core/feature_flags.py` — each flag a `FeatureFlag(stage, dev, prod, …)`, resolved
`graduated/retired lock → env → DB → per-env default`, with the resolver
reporting *where* each value came from. `cash_mode/economy_flags.py` was rewired
to source its globals from the registry so the ~80 importers never changed.
`scripts/flags.py status|check|env` for visibility. Two test guards: a
centralization check (no raw env reads outside the registry) and a partition
invariant on the conftest baseline.

## Wrong turns (the useful part)

**The frozen-global vs live-call mismatch.** My first back-compat test asserted
`economy_flags.CHIP_CUSTODY_ENABLED == is_enabled("CHIP_CUSTODY_ENABLED")`. It
failed, and chasing it taught me something real: the conftest autouse fixture
pins the module globals to a test baseline *without touching `os.environ`*, so a
frozen import-time global legitimately diverges from a live `is_enabled()`. The
test premise was wrong, not the code. Fixed by comparing only env-stable flags.

**`git` isn't in the backend container.** The centralization guard used
`git grep`; the container has no git. Rewrote it as a pure-Python file walk.

**The drift guard had to be rethought twice.** An existing test parsed
`_env_flag("NAME")` out of the source to keep the conftest reset list complete. I
removed those calls, so it broke. First rewrite tied the reset list to
"EXPERIMENTAL economy flags" — which then broke again the moment I promoted flags
to STABLE. The right model was a *partition*: every non-locked economy flag must
be in exactly one of "forced off in tests" or "intentionally on," decoupled from
production stage entirely.

**I edited the prod compose in the wrong worktree.** When doing the cleanup I
opened `docker-compose.prod.yml` under `/home/jeffh/projects/my-poker-face` — the
*main* worktree, on a different branch — instead of the cleanup branch's copy.
Caught it, `git checkout --` to restore, reapplied in the right place.

**The dev `.env` trap.** The plan was "remove the redundant flag lines from the
dev `.env`." But dev `docker-compose.yml` had
`CHIP_CUSTODY_ENABLED=${CHIP_CUSTODY_ENABLED:-0}` — remove the flag from `.env`
and that interpolates to `0`, *forcing the flag off*. So the dev compose line had
to go in the same change. Exactly the kind of split-brain the registry is meant
to kill, and it nearly bit me on the way to fixing it.

**The "failed" deploys that hadn't failed.** Both merges showed a red ❌ on the
workflow. The failing job was Playwright E2E — a known flake that is *not* a
deploy gate. `Build & Push` and `Deploy to Production` both succeeded each time.
Worth checking the job breakdown before believing the top-level red.

## Sequencing, done carefully

The dangerous step was removing the env-var arming on prod. The env vars were the
safety net; if the registry resolved `current_env()` wrong on prod, the
prod-only thermostat flags (`dev=False, prod=True`) would flip **off**. So before
touching them I confirmed `docker-compose.prod.yml` sets `FLASK_ENV=production`
→ `current_env() == 'prod'` → `prod` defaults apply. Then verified by simulation
(registry + no env vars → 18/23 on, identical to the live container) *before*
merging the cleanup.

Order: merge registry (#207) → deploy → merge env-var cleanup (#210) → deploy →
SSH to prod and confirm the live container had **zero** flag env vars but still
resolved **18/23 on**, health 200. Dev (both worktrees) cleaned the same way →
**13/23 on**. No behaviour change anywhere; the registry is now the source.

## Result

Per-env defaults encode the *verified* live state. A new dev env just works.
`scripts/flags.py status --env prod` answers "what's on in prod, and why" in one
command — no SSH, no spelunking across `economy_flags.py` + `.env` +
`docker-compose.prod.yml`. And "locking in" a feature is now a one-line stage
change to `graduated`, with `scripts/flags.py check` tracking the dead-code
cleanup that used to never happen.
