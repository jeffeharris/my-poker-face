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

**Medium-SPR — tried + REVERTED (2026-05-25).** A partial-commit medium slice
*regressed* −7 to −14 bb/100 vs Jeff (all seeds). Lesson: at medium SPR (2–6)
there are still streets to bet, so partial-jamming forgoes multi-street value
(esp. vs a station). The correct medium-SPR strategy *is* the high-SPR
multi-street strategy — which the SPR classifier already routes to. Medium
stays on the high fallback **by design** (correctness, not a gap); only LOW SPR
needed real entries.

**P2 (3-bet pots) — SHIPPED 2026-05-25.** `pot_type` dimension filled:
- Detection: hand-scoped `preflop_raise_count` on `PokerGameState`
  (incremented by preflop raises, survives street resets, resets per hand,
  serialized w/ old-save compat); `build_postflop_node` maps `≥2 raises → 3BP`.
- Entries: `generate_postflop_3bp.py` → `postflop_strategies_3bp.json` (4,320,
  derived from SRP: value more aggressive, air less bluffy), merged at load.
- Fallback: postflop lookup degrades a miss toward the populated SRP/high base
  (no regression possible).
- vs Jeff (3000h×3): 25bb +31.7 / 50bb +36.7 / 100bb +48.5 — **flat vs low-only
  (0 / −0.3 / −0.4, noise)**. Neutral + correct → kept (completes the
  dimension). Fires on ~21% of hands.

**Both frozen axes are now populated → 8,640 postflop entries (was 2,160).**
The grid is complete: high authored, low generated, 3BP generated; medium = high
by design.

## Eval: first non-pushover signal (2026-05-25)

Self-play / champion-vs-challenger (the user's `EVAL_HARNESS_PLAN.md` P0):
Baseline (pure charts) vs `TAG,LAG,Rock,Nit,GTO-Lite` at 100bb = **+10.3 bb/100
(per-seed +29.7 / +8.3 / −7.1 — within noise, ~parity)**, HU 73%. The honest
signal: against our *strongest* opponents the charts are roughly break-even — a
competent player, not a fish-beating artifact (cf. +48 vs Jeff station, +200 vs
rule bots). This is the eval that should gate further chart work.

## Next concrete step

The chart grid is complete; the open question is now **correctness vs strong
play**, which is the eval program (`EVAL_HARNESS_PLAN.md`): build out P0
champion-vs-challenger as the standing gate, P0.5 a non-station punisher clone,
P1 the full-SNG win-rate runner. Finer chart work (3BP precision, LLM-refined
grids, P3/P4 preflop+endgame) should be driven by what those evals expose.

→ **The eval plan is now its own doc: `docs/plans/EVAL_HARNESS_PLAN.md`**
(prioritized: **P0** champion-vs-challenger head-to-head [cheap, discriminating,
gates every change], **P0.5** a non-station "punisher" clone, **P1** the
full-SNG win-rate runner). Do P0 before authoring more charts.
