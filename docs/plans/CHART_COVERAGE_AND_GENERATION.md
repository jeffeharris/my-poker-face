---
purpose: Map the v0 lookup-chart coverage gaps and the plan to fill them with hand/LLM-authored (not solver) charts
type: design
created: 2026-05-25
last_updated: 2026-05-25
---

# v0 chart coverage & generation plan

The tiered bot plays from hand-authored lookup charts. The "Phase 2 postflop
foundation" (`e16a42aa`, 2026-02-17) scaffolded a rich postflop node space but
only ever populated a **slice** of it; the gaps were silently filled by
degradation (passive defaults / SPR fallback). This doc maps what's covered,
what's missing, and how we fill the gaps **by hand + LLM, not a solver**
(compute cost, and licensing if we ever release the charts publicly).

This is the *complement* to `SOLVER_CHART_SCOPE.md` (the long-term solver
program) and `PUSH_FOLD_6MAX_SCOPE.md` (the short-stack push/fold table). Those
stay parked; this is the near-term, releasable, hand/LLM path.

## Current coverage (measured 2026-05-25)

### Preflop
| Chart | Depths | Status |
|---|---|---|
| `preflop_100bb_6max.json` | 100bb | Authored (the original 6-max chart) |
| `preflop_{50,25}bb_6max.json` | 50, 25bb | **Coarse, derived** by `generate_depth_charts.py` transforms (shipped `707ff03b`) |
| `preflop_100bb_hu.json` | 100bb | Authored (HU only) |
| `push_fold_hu.json` | 5–15bb HU | Authored (HU push/fold) |

Gaps: no HU depth charts below 100bb except push/fold; no 6-max push/fold
(see `PUSH_FOLD_6MAX_SCOPE.md`).

### Postflop — `postflop_strategies.json` = 2,160 entries
A **full grid** on six axes, but **frozen on the other two**:

| Axis | Values populated | Full space |
|---|---|---|
| street | flop, turn, river | 3 ✓ |
| position | IP, OOP | 2 ✓ |
| board_texture | dry_high, dry_low_static, monotone, two_tone_broadway, two_tone_connected, wet_rainbow | 6 ✓ |
| made_tier | air, weak/medium/strong_made, nuts | 5 ✓ |
| draw | no_draw, backdoor, weak/strong_draw | 4 ✓ |
| facing | unopened, facing_bet, facing_raise | 3 ✓ |
| **spr** | **high only** | high, medium, **low** ✗ |
| **pot_type** | **SRP only** | SRP, **3BP** ✗ |

So the populated chart is **1/6 of the intended space** (2,160 of 12,960).
The missing 5/6 is the two frozen axes:
- **SPR medium + low** — most turns/rivers after betting, *at every depth*. Was
  hitting the hand-blind passive default; now mitigated by the SPR fallback
  (`760d89e5`) which reuses the high-SPR entry. **Stopgap, not a real chart.**
- **3-bet pots (3BP)** — never populated, AND `postflop_classifier.py:133`
  **hardcodes `pot_type='SRP'`**, so 3BP isn't even detected. Every 3-bet pot
  plays single-raised-pot strategy.

## Gaps, prioritized

Priority = (how wrong the current degradation is) × (how often the spot occurs)
× (tractability by hand/LLM). The SPR fallback already recovered the bulk of
the low-SPR bb/100 — so the remaining value of a *real* chart is **precision**
(sizing, commit thresholds, draws/air lines), not the gross "stop folding the
nuts" win that's already banked.

**P1 — Real low/medium-SPR postflop entries.** Replace the SPR-fallback
stopgap. The fallback reuses high-SPR strategy verbatim, so the *sizing* is
wrong when shallow (bets 33/67% pot where it should jam; the commit layer
patches value hands but not draws/air/sizing generally). A real slice gets:
low SPR → collapse to {check, jam} (few sizings), commit value, give up air,
**semi-bluff-jam** strong draws; medium SPR → between high and low. ~4,320 new
entries (medium + low across the existing grid). Highest priority — finishes
the dimension the whole investigation tripped over.

**P2 — 3-bet-pot (3BP) postflop + un-hardcode the classifier.** Two parts:
(a) `postflop_classifier.py` must detect 3BP from the pot/preflop history
instead of hardcoding SRP; (b) author 3BP entries (more polarized, range/nut
advantage to the 3-bettor, lower SPR baseline). Note 3BP is inherently
lower-SPR, so P1's SPR work already covers part of the "plays too deep" error
in 3-bet pots — do P1 first, then measure whether 3BP still needs its own slice.

**P3 — Refine the coarse 50/25bb preflop charts.** Currently transform-derived
(`generate_depth_charts.py`). A per-spot authored / LLM-authored version would
sharpen sizing and the marginal-hand boundaries. Lower priority: the coarse
version already banked +13.8 (25bb) / +4.8 (50bb), and 25bb is near jam-or-fold
solved.

**P4 — SNG endgame charts (HU 50/25bb, 6-max push/fold).** For the eventual
full-SNG runner. Not exercised by the current 6-max eval; defer until the
runner exists.

## Generation approach (hand + LLM, no solver)

Two proven-or-promising mechanisms, used together:

1. **Transform generators (deterministic, cheap).** The pattern that worked
   twice (`generate_depth_charts.py`, `generate_push_fold_hu.py`): encode
   hand-authored *rules* that derive the missing slice from a populated one.
   - Low-SPR postflop: transform the high-SPR entries → collapse sizings to
     {check, jam}, route value→jam, air→check/fold, strong-draw→jam (fold
     equity + outs). Medium-SPR: a milder interpolation.
   - 3BP: transform SRP entries → upshift c-bet frequency/polarization, lower
     the SPR baseline.
   - Cheap, reviewable, deterministic; gets the structure right. Best first
     pass for P1/P2.

2. **LLM-authored grids (higher fidelity, the "real" chart).** Use the
   ASSISTANT-tier model to author per-node strategies given full context
   (street, texture, made_tier, draw, SPR, position, facing, pot_type). Batch
   over node groups → JSON → validate. This is the "LLM generate" path: it can
   express spot-specific nuance a uniform transform can't (e.g. monotone-board
   turn nut play vs dry-board). Requires:
   - a **validation harness**: probs sum to 1; value hands aggressive; air
     folds to raises; no jam where all-in illegal; monotonic aggression in
     made_tier; etc. (reject + re-prompt on violation),
   - **measurement** in the loop (below).

   Recommended: transform-derive the structure first (P1/P2), then LLM-refine
   the high-frequency node groups where the transform is visibly crude. Keep
   the generators as the source of truth (re-runnable), like the preflop charts.

## Validation

Every chart change measured with `experiments/measure_passivity.py`:
- **Primary:** vs `Jeff_clone` (`--opponents jeff`) at 25/50/100bb.
- **Generalization guard (new, important):** vs `gto` and `mix`. The postflop
  fix exposed the risk that a station eval *inflates* gains — a real chart must
  also help (or not hurt) vs folding/aggressive opponents, or it's overfit.
- Eventually: the full WTA-SNG runner (escalating blinds, elimination,
  win-rate) — the honest final eval.

Watch the leak-surface (`--leak-report`) and per-hand-class postflop splits
(unopened bet%, facing-bet fold/raise% by made_tier) to confirm the chart
expresses intent, not just moves bb/100.

## Non-goals / constraints

- **No solver.** Compute cost + licensing for public release. Charts must be
  original hand/LLM-authored to be redistributable.
- Don't chase the station's bb/100. +40–53 vs `Jeff_clone` is leak-recovery,
  not skill; target *correctness* (validated across opponent types), not the
  headline number.

## Progress

**P1 (low-SPR) — SHIPPED 2026-05-25.** `generate_postflop_spr.py` derives a
real **low**-SPR slice (`postflop_strategies_low_spr.json`, 2,160 entries)
from the high-SPR chart and merges it at load (the authored high-SPR file stays
pristine; the SPR fallback still covers `medium`). Transform: commit-worthy
hands (made value, or `strong_draw`) route bet/raise → jam; air/weak give up
the bluff (bet → check unopened, raise → fold facing).
- vs Jeff (3000h×3) vs the fallback+commit stopgap: 25bb +32.5→+31.7, **50bb
  +32.7→+37.0 (+4.3, all 3 seeds +)**, 100bb +49.1→+48.9. Neutral-to-positive —
  the stopgap already banked the gross win; this is a precision + correctness
  refinement (proper jam-sizing, semi-bluff-jamming draws, air folding rather
  than small-bluffing) and the editable foundation for the SPR dimension.
- The "+200 vs gto/mix" generalization run was **conservation-verified (no
  bug)** — real chips, just exploiting always-calling rule bots (the known
  "rule-bot bb/100 is misleading" caveat). Jeff remains the meaningful eval.

**Still open:** medium-SPR entries (currently fallback→high; revisit if a
medium-SPR leak shows). Then P2 (3BP + un-hardcode classifier), P3, P4.

## Next concrete step — DECISION NEEDED (not a quick win)

P2 (3-bet pots) turned out **harder and lower-value than first scoped**:
- **Detection isn't a one-line un-hardcode.** `raises_this_round` is reset each
  betting round (`poker_state_machine.py:206/242`), so the preflop raise count
  is gone by postflop — game_state carries no preflop history. Detecting 3BP
  needs either (a) **state-machine plumbing** (preserve a preflop-raise count
  through the round reset — touches the immutable core, higher blast radius) or
  (b) a pot-size-vs-blinds heuristic.
- **And the value may already be captured.** 3-bet pots are inherently low-SPR,
  so they already get the **low-SPR chart** (the SPR classifier fires
  regardless of `pot_type`). A separate 3BP slice only adds SRP-vs-3BP nuance
  *at the same SPR* (range/nut advantage to the 3-bettor) — second-order.
- Also note: naive un-hardcoding **regresses** 3-bet pots — `pot_type` has no
  fallback, so `3BP` lookups would miss → conservative default. A `3BP→SRP`
  fallback must land first.

**Recommended re-prioritization:** the bigger lever now is probably the **eval,
not more charts** — every postflop win is measured vs exploitable opponents
(Jeff station + always-calling rule bots; +200 vs the latter). A tougher
opponent (or the full-SNG runner) would tell us whether the postflop fixes are
*correct* or just *station-beating*, which gates whether finer charts (3BP,
medium-SPR, LLM-refined grids) are worth authoring at all. Alternatives if
staying on charts: medium-SPR entries (cheap, mirrors the low-SPR generator) or
LLM-refining the low-SPR grid.
