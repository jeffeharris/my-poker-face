---
purpose: Honest narrative of the async-ticker-narration work — decoupling off-grid duration from the LLM, moving narration off the tick, and the two avoidable detours (lost uncommitted work, a CI red that wasn't mine)
type: design
created: 2026-06-06
last_updated: 2026-06-06
---

# Async ticker narration — and two detours

This was a scaling change with a clean spec (`docs/plans/ASYNC_TICKER_NARRATION.md`,
already approved): get the world ticker's vice / side-hustle flavor narration off
the single ticker greenlet's hot path, and stop coupling the off-grid economics to
an LLM call. Two steps:

- **Step 1** — the *system* picks how long an AI goes off-grid
  (`pick_duration_bucket` / `pick_hustle_duration_bucket`: deterministic, tunable,
  vice skews `long` under pressure, hustle under deficit). The narrators became
  flavor-only — they take the chosen bucket and return just the line. The headless
  sim now runs vice/hustle economics with **zero** LLM calls. Shippable on its own.
- **Step 2** — the (now flavor-only) narration moves off the tick. Economics commit
  in-tick with a placeholder; a `socketio` background greenlet runs the LLM and
  records the feed event when it returns; the next ticker poll emits it. Gated by
  `TICKER_ASYNC_NARRATION_ENABLED` (default on).

The work landed (PR #203, squash `4f85dc41`). The *technical* part went roughly to
plan. The part worth keeping is the two ways I made it harder than it needed to be.

## What Codex caught that I'd have shipped wrong

I ran the Step 2 plan past Codex before writing it, and that paid for itself twice:

1. **The marker/ordering trap.** The ticker emits only activity events with
   `created_at > last_marker`, and advances the marker to the newest event each
   tick. My first instinct was to record the deferred event with the *tick's* `now`.
   That's a silent bug: by the time the greenlet finishes, a later tick has already
   moved the marker past that timestamp, so the event is filtered out and never
   reaches the client. The fix — record with a fresh `utcnow()` at emit time — is
   obvious *once stated*, but I'd have written it the wrong way.
2. **The placeholder isn't internal.** I assumed the in-tick placeholder narration
   was throwaway. It isn't — the off-grid state row's `narration` is shown on the
   active-vice payload (`cash_routes`) and in `whereabouts` for the *whole*
   off-grid duration. So a placeholder there isn't a one-tick blip; it'd be the
   permanent text on those surfaces while only the feed got the good line. That's
   why there's now a narrow `update_narration` on both state repos.

Lesson reinforced: a second reader grounded in the actual code finds the
assumptions you've stopped questioning.

## Detour #1 — I told the user uncommitted work was safe. It wasn't.

The user asked me to keep everything uncommitted until we were back on the scaling
branch. I said fine — "the changes will follow you back." Then the branch shuffled
underneath me (scaling-stage1 → ops/ghcr-image-deploy → ci-ghcr-deploy), and the
reflog showed a `git reset --hard origin/main` in the mix. A hard reset discards
uncommitted working-tree changes that aren't stashed. Only `ai_vice_spending.py`
survived — the user had stashed *that one file* ("stray ai_vice_spending, restore
on scaling-stage1"); the other five Step-1 files were gone.

So I'd given a confident, wrong assurance. "Uncommitted changes travel across
`git checkout`" is true *only* for a plain checkout with no conflicts — it is not
true across `reset --hard`, `stash`, or a forced switch. When the working tree came
back, it was also *inconsistent*: `ai_vice_spending.py` had the new flavor-only
`NarrateFn`, but every caller still spoke the old tuple API. I rebuilt the five
files to match what survived, re-ran, green.

If I'm asked to hold work uncommitted again, the honest answer is: that's fragile,
here's a stash or a throwaway commit so it can't evaporate.

## Detour #2 — the CI red that wasn't my change

First PR run came back red: **one** test, `test_tiered_factory.py::
test_factory_attaches_expression_when_enabled` — `load_strategy_table` called 0
times, expected once. My reflex was to suspect my narration edits. It wasn't them
at all. It was the **Stage 1A memoization** already on the branch: the four
strategy tables are now process-shared module globals, so in the full suite an
earlier test warms the cache and `load_strategy_table` never runs again (and the
cache holds a stale mock). The test passed alone and failed ordered — a classic
memoization/test-isolation trap. Fixed with a `reset_strategy_tables_cache()` hook
and an autouse fixture that resets around each test.

The deeper miss was mine: I'd validated Step 1/2 by running `tests/test_cash_mode/`
and a hand-picked slice — all green — and let that stand in for "the suite passes."
It doesn't. Scoped-green and full-suite-green are different claims, and process-
shared caches are exactly where the gap bites. After fixing it I ran the *whole*
backend suite locally before re-pushing, rather than spending another ~6-minute CI
cycle to learn the same thing.

## Small calls

- **psych on the async path.** I first dropped the psych snapshot for the off-tick
  narrator (Codex agreed it was a marginal trade — the chosen bucket already
  encodes pressure). The user said include it, so `ViceStartResult` now carries
  `psychology_snapshot`, attached via `dataclasses.replace` at both fire sites. The
  right call — keeping the live-path flavor on par with the sync path cost almost
  nothing.
- **Merging on green.** Used the authoritative gate (`fail+pending == 0` from
  `gh pr checks`) rather than a bare `--watch` exit code, per the known false-green
  lesson. Backend + Frontend + ruff passed; E2E/build/deploy skip on a PR.

## State at close

On `main`. `TICKER_ASYNC_NARRATION_ENABLED` defaults on, so the next deploy runs
narration off the tick; the kill switch is there if the first prod look wants the
old synchronous path. Not yet live-verified on prod.
