---
purpose: Plan for tuning tiered-bot postflop behavior (c-bet too low, WTSD too high) after the afq-wtsd-tuning measurement + band re-baseline, with the buy-in/deep-stack-table investigation
type: design
created: 2026-06-09
last_updated: 2026-06-09
---

# Archetype postflop tuning

> Continuation of the archetype-shaping line ([[project_archetype_shaping_overaggression]],
> `ARCHETYPE_SHAPING_FINDINGS.md`, `ARCHETYPE_SHAPING_HANDOFF.md`). This doc covers
> the `afq-wtsd-tuning` branch: what was a measurement/band artifact, what is real
> behavior, and the remaining postflop work.

## How we got here

Jeff flagged the prod Archetype Review showing very high AFq% and WTSD%, low
C-bet, mixed fold-to-CB, and (apparently) high fold-to-3bet for TAG/LAG/station.
Investigation split this into three layers:

1. **Measurement bug (fixed).** The sim-source AFq mixed a full-history
   numerator/call-count with a fold term that only counted folds since a later
   migration (`postflop_agg`/`postflop_call` landed 20260608_1600; the per-street
   `*_fold` columns 20260609_1200, ~20h later). Folds were under-weighted →
   AFq inflated for every archetype. Fixed by making per-street agg/call/fold the
   single source of truth (commit `395063ef`).
2. **Band miscalibration (fixed).** The AFq target bands ran ~10-15pts low for the
   tight/aggressive types: AFq excludes checks, so even a "tight" player's
   non-check actions (value-bet + fold, rarely call) skew aggressive. Re-baselined
   by poker judgment (commit `d5b64a0b`); WTSD bands widened slightly where too
   narrow for a noisy stat.
3. **Real behavior (this plan).** With measurement and bands corrected, two
   postflop problems survive under controlled 6-max conditions.

## The instrument

`scripts/archetype_mixedfield_probe.py` — 7 production archetypes, 6 seats, one
rotating out per hand, 9000 hands. This is the realistic 6-max field the
`ARCHETYPE_TARGETS` bands are written for, so it answers "do the archetypes hit
target under EXPECTED 6-max conditions?" independent of the live lobby sim (which
runs short-handed/heads-up tables that structurally inflate WTSD). Extended this
branch to measure the full postflop family — AFq/WTSD/W$SD/c-bet/fold-to-CB —
computed from the ordered decision stream (so AF and AFq share one timeline by
construction) and the end-of-hand state. Re-run:

```
docker compose run --rm --no-deps --entrypoint python backend scripts/archetype_mixedfield_probe.py
```

## Band-vs-behavior split (6-max, 9000 hands, post re-baseline)

| stat | verdict | meaning |
|---|---|---|
| preflop (VPIP/PFR/3bet/4bet/**fold-to-3bet**/all-in) | PASS | healthy. fold-to-3bet IS in band in 6-max — the lobby-sim "over-fold" was a short-handed regime artifact, not real. |
| AFq | PASS (only `tag` marginal WARN) | was ~all measurement + band, not behavior. **Resolved.** |
| **WTSD** | FAIL everywhere (nit 47, station 50, fish 57 vs 22-45 bands) | **real** — too sticky, calls down too light |
| **C-bet** | FAIL everywhere (nit 17, tag 33, maniac 46 vs 55-95 bands) | **real** — aggressor checks away most flops |
| W$SD | low (nit, lag, fish) | downstream of high WTSD (showing down too light) |

## Root cause (postflop strategy chart)

The tiered bots play from one hand/LLM-authored chart,
`poker/strategy/data/postflop_strategies.json` (2,160 SRP, high-SPR entries; the
low-SPR/3BP derived slices were cut after the SNG gate showed no benefit). It is
too passive both ways. Measured straight off the file:

- **Unopened flop (c-bet spot): 39.7% bet overall** — even *nuts* bet only 59%,
  strong_made 52%, medium_made 41%, air/weak ~23%. The aggressor checks most flops.
- **Facing a flop bet: medium_made calls 71% / folds 13%**, weak_made calls 48%.
  Calls far too wide → high WTSD / low W$SD.

## Lever 1 — calling discipline (WTSD/W$SD) [low risk, do first]

Tighten the wide `facing_bet` calls toward folds for medium_made (call 71→~40-50)
and weak_made (call 48→~30), shifting the freed mass to fold. Reproducible via a
transform script over the authored chart (the pattern `generate_postflop_spr.py`
uses), not by hand-editing 2,160 entries.

- No structural wrinkle: facing a bet, folding more is unambiguously correct for
  the over-sticky problem.
- Directly attacks the clearest failure (WTSD), and lifts W$SD as a side effect.
- Validate: 6-max probe (WTSD toward 22-45 band) + the SNG champion-challenger
  gate (this chart feeds every tiered bot, including the competent baseline used
  in evals — a calling change can move win-rate).

## Lever 2 — c-bet frequency [larger, structural]

Raise the aggressor's continuation-bet frequency on unopened flops (made hands
especially, toward 55-95 depending on tier). **Caveat that blocks a naive crank:**
the node has *no aggressor flag* (confirmed in `CHART_COVERAGE_AND_GENERATION.md`).
The `unopened` node conflates "I'm the preflop aggressor continuation-betting"
(want high bet) with "I'm the caller first to act, should check to the raiser"
(want low bet). Cranking `unopened` bet uniformly would turn callers into
donk-bettors.

Clean fix needs a new **aggressor-aware node dimension**: classify whether the
actor was the preflop aggressor (the data exists — `_last_preflop_aggressor()` in
`tiered_bot_controller.py`, and `is_flop_as_preflop_aggressor` is already computed
for the exploitation layer) and author/transform separate frequencies for
aggressor-c-bet vs caller-first-to-act. Larger change, lower confidence; do it
after Lever 1 lands and validates.

## Not a lever — table stakes / buy-in size

Considered requiring a bigger minimum buy-in to curb the prod over-3-betting.
Pulled the effective stack-depth distribution at prod cash decisions (BB derived
from `rfi` cost-to-call, snapped to the $2/$10/$50/$200/$1000 tiers):

| spot | n | median | <25bb | 25-50bb | 50-80bb | 80-120bb | 120bb+ |
|---|---|---|---|---|---|---|---|
| all decisions | 4222 | 67bb | 3% | 23% | 38% | 28% | 9% |
| 3-bet spots | 49 | **78bb** | 0% | 20% | 33% | 33% | 14% |
| all-in | 112 | 43bb | 23% | 36% | 24% | 12% | 4% |

**The 3-bets are happening deep (median 78bb, none under 25bb), so a bigger buy-in
will not move 3-bet frequency** — these are real deep-stack 3-bets, not short-stack
jams. The over-3-betting is a strategy/behavior matter (charts + tilt-conditioning,
now on in BETA + the human being the table's biggest 3-bettor), consistent with
the controlled 100bb sim showing healthy 3-bet. A deeper buy-in + auto-top-up would
only trim the all-in jamming that concentrates at 25-50bb (the smaller, separate
pathology). **Buy-in size is off the table as a 3-bet lever.**

### Feature idea (separate from the fix): depth-varied tables

Varied buy-in *depth* as an engagement feature is realistic and good on its own
merits, independent of the 3-bet problem. Stake tiers already exist ($2→$1000 BB);
the new axis is depth *within* a stake — the same $50 blinds as a 40bb "action"
table vs a 250bb "deep stack" table. Fits the tier/progression framing (opt into
deep-stack tables at your tier, where better players want to be), and deep-stack
tables are exactly where the postflop tuning in this doc matters most, so they'd
showcase a smarter bot rather than mask a dumb one. Tracked as a vision/feature
item, not part of this fix.

## Sequencing

1. **(done)** AFq measurement fix + 6-max probe extension (`395063ef`).
2. **(done)** AFq/WTSD band re-baseline + captain's log (`d5b64a0b`).
3. **Lever 1** — calling-discipline transform → regenerate → 6-max probe + SNG gate.
4. **Lever 2** — aggressor-aware c-bet node → author/transform → probe + SNG gate.
5. Re-check the live Archetype Review (sim source) after each lands.

## Validation gate

Every chart change is champion-challenger gated (it feeds all tiered bots). Use
the 6-max probe for behavioral movement (toward band) AND the SNG runner gate for
win-rate neutrality/benefit before merging. See `SNG_RUNNER_HARDENING.md`.

## Status

Shipped on `afq-wtsd-tuning` (local, not pushed): measurement fix, extended probe,
re-baselined bands, captain's log
(`docs/captains-log/afq-wtsd-tuning/measuring-before-tuning.md`). Levers 1-2 are
the remaining work.
