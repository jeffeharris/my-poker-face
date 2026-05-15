---
purpose: Single entry point for resuming pre-main implementation work — orients to branch state, final decisions, and recommended order
type: guide
created: 2026-05-15
last_updated: 2026-05-15
---

# Start here — pre-main implementation

If you're picking up the work that needs to land before merging `development` → `main`, this is the entry point. Read this first, then `PRE_MAIN_SCOPING.md` for the full list.

## Branch state

- Working branch: `development`. ~330 commits ahead of `origin/main`.
- The big merge already happened — `development` absorbed `hybrid-ai` plus all the recent narration/coach/recovery work. That merge was pushed as commit `19d5c9ad`.
- Last full test run: 3678 passed, 8 failed, 60.39% coverage. TypeScript clean. Of the 8 failures, 5 trace to T1-39 (admin gate added in T1-24 broke test mocks), 2 are pre-existing flakes, 1 is T2-54 (already validated as stale test).
- Worktree: `/home/jeffh/projects/my-poker-face-tieredbot-messages` (sibling worktree of the main `my-poker-face`).

## What needs to ship before main

55 items were added to `docs/TRIAGE.md` in the pre-main batch (T1-28..T1-39, T2-36..T2-65, T3-61..T3-74). Each has severity, file:line targets, and an approach. Of those:

- **48 items** are S-scope mechanical fixes ready to implement (full table in `PRE_MAIN_SCOPING.md`).
- **7 items** required design investigation; each has a plan doc.

## Final decisions (round-2 verdicts override round-1 plans)

These are the calls the user made after reviewing both rounds of investigation:

| Item | Decision | Authoritative doc |
|---|---|---|
| T1-29 psychology persistence | New `psychology_json` column via schema migration v83 | `PSYCHOLOGY_STATE_NOT_PERSISTED_PLAN.md` |
| T1-32 broken DB retry | Apply existing `@retry_on_lock` decorator. Prerequisite: change `save_personality_snapshot` INSERT → INSERT OR IGNORE. | `RETRY_DECORATOR_DEEP_DIVE.md` (validates `BASE_REPO_RETRY_REWRITE_PLAN.md`) |
| T1-33 recover_stuck_runout race | Lock + re-check pattern around DB load. Add 50-iteration cap to `run_until_player_action` (bundles T2-57). | `RECOVER_STUCK_RUNOUT_RACE_PLAN.md` |
| T1-34 HU equity offsets | **DEMOTED to T2-65, gate behind `PromptConfig.hu_equity_offset: bool = False`**. The values are range-pct offsets, not equity — reusing as equity is a category error. | `HU_EQUITY_OFFSET_CALIBRATION_CHECK.md` (supersedes `HU_EQUITY_OFFSET_PLAN.md`) |
| T1-36 / T3-66 detect_fold_events / detect_chat_events | **Delete both methods + caller block at game_routes.py:1638-1652.** User: "the idea wasn't going to work which is why it was abandoned." | `PRESSURE_DETECTOR_ORPHANS_EVALUATION.md` |
| T2-38 strategy mapper min-raise | Two 1-line fixes at `action_mapper.py:49` and `:102`: `highest_bet + big_blind` → `highest_bet + game_state.min_raise_amount` | `STRATEGY_MAPPER_MIN_RAISE_PLAN.md` |
| T2-42 / T3-67 zone gravity | **Delete completely.** Never executed despite doc claim. ~10 lines across 4 files. | `ZONE_GRAVITY_DECISION.md` |
| T2-54 personality test | Apply Option A (assert diversity) + add anchor-zone guard test. Game behavior is more correct now, test is stale. | `PERSONALITY_REGRESSION_EMPIRICAL_CHECK.md` (validates `PERSONALITY_DETERMINISM_INVESTIGATION.md`) |

For the 48 mechanical items not in this table, the approach is in `PRE_MAIN_SCOPING.md`.

## Recommended implementation order

From `PRE_MAIN_SCOPING.md` bottom:

1. **Quick T1 batch** (~3 hours): T1-28 (coach route ownership), T1-30 (c-bet detector), T1-31 (OpponentModel serialization), T1-35 (+EV guarantee), T1-36 (delete orphans + caller), T1-37 (frontend JSON.parse), T1-38 (PipelineTracePanel), T1-39 (experiment chat test mocks). No dependencies.
2. **Agent-blueprinted T1 batch**: T1-29, T1-32, T1-33 — follow their plan docs.
3. **Quick T2 batch** (~12 hours): everything not behind an agent. Prioritize security (T2-36, T2-37, T2-55) and orphans (T2-58).
4. **Agent-blueprinted T2**: T2-38, T2-42 — follow their plan docs.
5. **T3 cleanup batch** (~6 hours): mechanical.

T3-70 through T3-74 are post-release; skip for now.

## Reading order for fresh context

1. **This file** (`START_HERE.md`) — orientation
2. **`PRE_MAIN_SCOPING.md`** — full table of 55 items with effort estimate and approach
3. **`../TRIAGE.md`** — full T1-39 list with descriptions (only if you need to look up a specific item by ID)
4. **The specific plan doc** for whichever investigated item you're implementing

Plan docs (all in `docs/triage/`):
- `PSYCHOLOGY_STATE_NOT_PERSISTED_PLAN.md` (T1-29)
- `BASE_REPO_RETRY_REWRITE_PLAN.md` + `RETRY_DECORATOR_DEEP_DIVE.md` (T1-32)
- `RECOVER_STUCK_RUNOUT_RACE_PLAN.md` (T1-33 + T2-57)
- `HU_EQUITY_OFFSET_CALIBRATION_CHECK.md` (T1-34, demoted)
- `PRESSURE_DETECTOR_ORPHANS_EVALUATION.md` (T1-36 + T3-66)
- `STRATEGY_MAPPER_MIN_RAISE_PLAN.md` (T2-38)
- `ZONE_GRAVITY_DECISION.md` (T2-42 + T3-67)
- `PERSONALITY_REGRESSION_EMPIRICAL_CHECK.md` (T2-54)

Background context:
- `COACH_ROUTE_OWNERSHIP_GAP.md`, `CBET_DETECTOR_ALLIN_GAP.md`, `PSYCHOLOGY_STATE_NOT_PERSISTED.md`, `BASE_REPO_RETRY_BROKEN.md` — bug write-ups (the *what's broken*; the plan docs are *how to fix*)
- `CODEX_REVIEW_OF_PLANS.md` — codex's pushback, partly addressed by round-2 investigations
- `PERSONALITY_DETERMINISM_INVESTIGATION.md` — round-1 verdict, validated by the empirical check

## What's already shipped vs. what needs to ship

This is the *pre-main fix phase*. Nothing in this batch has been implemented yet — only the analysis and planning are committed. The merge from `hybrid-ai` and all the narration/coach work *is* in `development`, but the 55 items here are gaps and bugs identified during pre-main review of that merge.

## After all these items land

Re-run `python3 scripts/test.py --all`. Expected outcome: T1-39 fixed (5 chat tests pass), T2-54 fixed (Scrooge test passes with new assertion), `test_call_type_count` and `test_owner_can_act_on_human_turn` still pre-existing flakes (track separately, not blockers). Then merge `development` → `main`.

## Last set of commits

```
b96e3f44 docs(triage): T1-36 chat-events — delete instead of fix-in-place
81c5ec05 docs(triage): round 2 follow-up investigations + final verdicts
16c9913e docs(triage): codex review of pre-main plans
10ccc06a docs(triage): pre-main scoping + 7 agent investigation plans
a6d8f855 docs(triage): pre-main merge review — 55 new findings
```

Everything in this chain is documentation. Implementation begins on the next commit.
