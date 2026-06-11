---
purpose: Handoff for the respect/likability regard rebaseline (neutral 0.5 → 0.35, earned/asymmetric) — what shipped, the full blast radius CI surfaced, and the remaining threshold re-tuning + test work to finish it
type: guide
created: 2026-06-06
last_updated: 2026-06-06
---

# Regard rebaseline (0.5 → 0.35) — handoff

## Goal & decisions (locked)

Make respect + likability **earned** with **bounded downside**: drop the neutral
baseline from `0.5` to **`0.35`** so the axes are asymmetric — ~0.35 of downside,
~0.65 of upside ("I don't know you" sits low; respect/likability are a ladder you
climb). One named constant, `REGARD_NEUTRAL`, is the single neutral point.

- **`REGARD_NEUTRAL = 0.35`** lives in `poker/memory/opponent_model.py`.
- **Clamp stays `[0, 1]`** — the asymmetry comes purely from where the baseline sits.
- **Heat is NOT a regard axis** — one-sided, 0-based (0 = neutral). Excluded.
- **No data migration** — tuning phase; trash + recreate sandboxes, start fresh at 0.35.
- **Renown's neutral-=0-regard invariant is preserved**: a neutral edge still
  contributes 0 (`0.35 − 0.35`); only the dynamic range shifts (more + headroom).

## Status

- **PR #202** (`feat/regard-neutral-baseline`, off `main`; worktree at
  `/home/jeffh/projects/my-poker-face-regard`, commit `b1be2e8a`). **CI: backend
  tests FAIL** — lint + frontend pass. The failures are the blast radius below.
- The first-cut diff was captured at `/tmp/regard_rebaseline.patch` (may be stale;
  regenerate from #202's branch when resuming).

## What's DONE (in PR #202 `b1be2e8a`)

The constant + the **renown/regard FORMULA-CENTERS** + the **no-edge DEFAULTS**:

- `opponent_model.py` — `REGARD_NEUTRAL = 0.35`; `RelationshipState.respect /
  likability` default to it.
- No-edge fallbacks → `REGARD_NEUTRAL`: `movement.py` (~`:959`),
  `sponsor_offers.py` (~`:598`), `player_staking.py` (~`:301`).
- Renown formula centers re-centered to `value − REGARD_NEUTRAL`:
  `prestige.py` regard (`:217`), beat-respected (`:340`, now the normalized
  `(respect − N)/(1 − N)`), table-fill sums (`:367-368`), renown sums (`:870-871`);
  `renown_field_repository.py` field means (`:185-186`).
- 4 tests already updated to reference `REGARD_NEUTRAL`: `test_cash_mode/test_prestige.py`,
  `test_cash_mode/test_prestige_v2.py`, `test_repositories/test_renown_field_repository.py`.
  (Local prestige/renown/relationship/staking subset = 128 green before pushing.)

## What's REMAINING (why CI is red)

### A. Threshold re-tuning — PRODUCT DECISIONS (~12 sites)

These encode `0.5` as a tuned neutral **BAR** (not a formula-center). With neutral
now `0.35`, a no-edge stranger (`0.35`) falls below them → **staking eligibility /
terms silently change** (e.g. strangers stop getting standard-tier offers). Each
needs a judgment call: does the bar mean *"above neutral"* (→ track `REGARD_NEUTRAL`)
or *"clearly above neutral / earned"* (→ a deliberate offset above 0.35)?

- `cash_mode/sponsor_offers.py`:
  - `:290` `TIER_STANDARD = {"likability": 0.4, "respect": 0.5}` (offer eligibility)
    — and the other tier rows nearby.
  - `:337` `if likability > 0.5`, `:343` `if respect > 0.5`, `:369` `if respect >
    0.6 and likability > 0.5`, `:371` `if respect > 0.5` (offer-term flavor/floor).
- `cash_mode/player_staking.py`:
  - `:318` `if respect > 0.6 and likability > 0.5`, `:320` `if respect > 0.5`,
    `:322` `if likability > 0.5` (tier/term gates).
  - NB **`RELATIONSHIP_HEAT_CEILING = 0.5` (`:87`) is HEAT — leave it.**
  - NB `_relationship_score = likability*0.5 + respect*0.4 − heat*0.3` (`:255`) are
    **weights, not a baseline — leave them.**
- Sweep again when resuming: `grep -rnE "(respect|likability)\s*[<>]=?\s*0\.5"
  cash_mode/ poker/memory/` (excluding `* 0.5` weights, heat, probabilities).

**Open product question to settle first:** is the reduced stranger-staking
eligibility (a natural consequence of "respect is earned") DESIRED, or should the
bars shift down to preserve today's eligibility? That answer drives every value here.

### B. Test expectations (~22 tests)

Pin exact `0.5 ± delta` post-event values / `> 0.5` "rose above neutral" checks.
Fix pattern: `0.5 ± delta` → `REGARD_NEUTRAL ± delta`; `> 0.5` → `> REGARD_NEUTRAL`;
seed "neutral" edges at `REGARD_NEUTRAL`. Files (from CI run 27073475924):

- `test_memory/test_relationship_state.py::...test_defaults_match_design`
- `test_memory/test_record_event.py` (BilateralUpdate ×2, StateAccumulation)
- `test_memory/test_relationship_integration.py` (StackDominance ×2)
- `test_chat_relationship_dispatch.py` (~10: props/gloat/needle/goad/flatter,
  temperament divergence ×3, sarcasm reception/gate ×3)
- `test_cash_sponsor_routes.py::...emits_sponsorship_offered_event`
  (mechanical: `assertGreater(respect, 0.5)` → `> REGARD_NEUTRAL`)
- `test_cash_forgiveness_route.py`, `test_cash_net_worth_route.py`,
  `test_cash_staker_forgive_route.py` (stake event respect/like shifts)
- `test_personality_offers_tier.py::test_standard_tier_bumps_rate`
  (BEHAVIORAL — `len(offers)==0`; fixed by the #A tier-eligibility re-tune, not a
  test edit)

### C. Sim validation (before merge)

Run the closed-economy + renown sims and compare flag-off vs the rebaseline:
- Renown distribution didn't distort (neutral=0 preserved, so should be ~same shape
  with more + headroom).
- Staking eligibility / offer rate didn't crater (the #A re-tune is the lever).

## Recipe to resume (fast local iteration)

The 6 core files are byte-identical on `main` and `circuit-progression`, so develop
on circuit's running container, then port to the #202 worktree:

1. On `circuit-progression`: `git apply --index /tmp/regard_rebaseline.patch`
   (or regenerate the diff from `origin/feat/regard-neutral-baseline`).
2. Settle the #A product question; re-tune the ~12 thresholds; fix the ~22 tests.
3. Iterate in the backend container until the full suite is green
   (`docker compose exec -T backend python -m pytest tests/ -q`).
4. `git diff > /tmp/regard.patch`; `git restore --staged --worktree .` to clean circuit.
5. In the worktree (`/home/jeffh/projects/my-poker-face-regard`): apply the diff,
   commit (`--amend` onto `b1be2e8a` or a new commit), push → #202. (Push-stage
   ruff/prettier may reformat: re-`git add -A` + amend, push again.)

## Gotchas

- Distinguish **neutral-bars** (`respect > 0.5` → re-tune) from **weights**
  (`* 0.5`, `likability*0.5+...`) and **probabilities** (leave both).
- **Heat** (`RELATIONSHIP_HEAT_CEILING`, `heat > 0.x`) is 0-based — never use
  `REGARD_NEUTRAL` for it.
- No migration: don't write any data-fixup; new sandboxes only.

## Relationship to the vouch system (circuit-progression)

When this lands on main and circuit syncs, the **vouch thresholds re-tune against
0.35**: `RESPECT_FLOOR` (currently 0.50 = the old neutral — should become *earned*,
e.g. ~0.55), `LIKE_THRESHOLD` (0.70 → the climb from 0.35 is now +0.35), and
`HOME_TABLE_REVEAL_LIKABILITY` (0.60). That's part of the #4 vouch live-tuning pass.
See `CASH_MODE_CAREER_M2_PLAN.md`.
