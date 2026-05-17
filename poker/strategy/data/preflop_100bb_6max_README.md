---
purpose: Provenance and design spec for the 6-max preflop strategy chart (preflop_100bb_6max.json)
type: spec
created: 2026-05-17
last_updated: 2026-05-17
---

> **Retrospective README** — this file documents the existing
> `preflop_100bb_6max.json` chart, written months after the chart
> shipped (commit `b8ff5c30`, 2026-02-16). The chart was hand-authored
> with validation-tuning, not solver-derived; this README captures what
> we know about its origins, the rules implicit in the data, and the
> known calibration gaps. Mirrors `hu_preflop_chart_README.md`.

# 6-max preflop chart spec (100 BB cash, 2.5bb opens)

This document is the **retrospective source-of-truth** for
`poker/strategy/data/preflop_100bb_6max.json`. Unlike the HU README,
this file was not authored up front — it was written after the chart
shipped, to document conventions and known gaps for future
calibration work.

## Provenance

| Aspect | Status |
|---|---|
| Authoring approach | Hand-authored with AI assistance, validation-tuned |
| Original commit | `b8ff5c30` — "feat: complete Phase 1 preflop tiered bot with validation tuning" (2026-02-16) |
| Follow-up tuning | `68042e5e` — "tune: lower trash hand frequencies in preflop charts" (same day, ~1 hour later) |
| Solver provenance | None — this is **not** solver output |
| Validation harness | 10k-hand simulation across 6 archetypes; aggregate VPIP/PFR bands matched targets |
| Subsequent edits | None — chart frozen since Feb 2026 |

The Phase 1 commit message describes "solver baselines + personality
distortion," but the practical authoring used heuristic ranges
calibrated by sim-running and tuning, not solver output. Same pattern
as the HU README's "v1 binary frequencies" approach.

## Stack depth and sizing

| Parameter | Value |
|---|---|
| Effective stack | 100 BB |
| Game type | Cash (no ICM) |
| Open size (RFI) | **2.5 BB** (different from HU's 3 BB; tighter open-and-fold ratio matches 6-max convention) |
| 3-bet size | 3x the opener's raise |
| 4-bet size | ~2.2x the 3-bet |
| All-in | `jam` |

## Scenarios in scope

| Scenario | Position(s) | Sub-scenarios | Hero acts |
|---|---|---|---|
| `rfi` | UTG / HJ / CO / BTN / SB | 5 | open (`raise_2.5bb`) or fold |
| `vs_open` | HJ / CO / BTN / SB / BB | 15 | call, 3-bet (`raise_3x`), or fold |
| `vs_3bet` | UTG / HJ / CO / BTN / SB | 15 | call, 4-bet (`raise_2.2x`), jam, or fold |
| `vs_4bet` | HJ / CO / BTN / SB / BB | 15 | call, jam, or fold |

BB is intentionally omitted from `rfi` (BB never opens — they're in the
blinds when the action gets to them; their preflop decision happens
only in `vs_open` when someone else opened).

The 15 sub-scenarios per defense/3-bet/4-bet branch enumerate every
(defender × opener) pairing: UTG_vs_HJ, UTG_vs_CO, UTG_vs_BTN,
UTG_vs_SB, UTG_vs_BB, HJ_vs_CO, ..., SB_vs_BB. Each cell encodes how
hero responds given who attacked first.

## Action vocabulary

The action labels match `poker/strategy/action_mapper.py`:

| Action | Meaning | Used in |
|---|---|---|
| `raise_2.5bb` | Open to 2.5 BB | `rfi.*` |
| `raise_3x` | 3-bet to 3× current bet | `vs_open.*` |
| `raise_2.2x` | 4-bet to 2.2× current bet | `vs_3bet.*` |
| `jam` | All-in | `vs_3bet`, `vs_4bet` |
| `call` | Flat | all defense scenarios |
| `fold` | Fold | all scenarios |

Per-row probabilities sum to ~1.0 (the chart loader test enforces this
strictly). Frequencies are **mixed** in 6-max (unlike HU's binary
encoding): a hand like `K9o` from UTG is encoded as
`{raise_2.5bb: 0.1, fold: 0.9}` — open 10% of the time, fold 90%.

## Range shape (aggregate frequencies, observed)

Per the original validation harness output cited in commit `b8ff5c30`,
the chart produces these archetype frequencies in 10k-hand sim against
mixed opponents:

| Archetype | VPIP | PFR |
|---|---|---|
| Rock | ~23% | ~13% |
| TAG | ~28% | ~22% |
| LAG | ~50% | ~40% |
| Maniac | ~54% | ~47% |

These numbers depend on personality distortion (applied on top of the
base chart), not the chart alone — the chart's own raw openness varies
by position (UTG tightest, BTN widest, SB looser than UTG due to
steal-equity but tighter than BTN due to OOP).

Position-by-position raw RFI rates from the chart (approximate):

| Position | RFI rate (chart base) | Wider than HU? |
|---|---|---|
| UTG | ~13% | N/A — HU doesn't have UTG |
| HJ | ~16% | N/A |
| CO | ~22% | N/A |
| BTN | ~40% | Tighter than HU SB (~65%) — narrower table = wider open |
| SB | ~25% | Much tighter than HU SB — multiway risk vs single BB |

## Known gaps and calibration debts

These are issues that exist today but are not blocking production use.
A future solver-output replacement would address most of them.

1. **No solver provenance.** Aggregate frequencies were validated by
   sim, but per-hand decisions are heuristic — no canonical Nash check.
   A solver replacement (e.g., MonkerSolver output for 6-max preflop)
   would tighten or loosen specific hands at specific positions in
   ways heuristics can't anticipate.

2. **Mixed frequencies are coarse.** Most non-binary frequencies are
   round numbers (0.1 / 0.25 / 0.5 / 0.75 / 0.9). Real solver mixes
   land at irregular ratios (e.g., 0.43 / 0.57). The chart's
   coarseness is good for readability but loses ~5-10% EV at marginal
   spots.

3. **vs_open coverage is asymmetric.** 6 (defender × opener) pairings
   are missing under the v1 generator — specifically, BB_vs_* combos
   when BB is the closer aren't enumerated. The fallback path in
   `StrategyTable.lookup_with_fallback` substitutes nearest-position
   ranges when a sub-scenario isn't found.

4. **No 5-bet handling.** Once both opener and defender have committed,
   any further raise collapses to jam. Acceptable for 100 BB depth
   where 5-bet pots are <2% of hands.

5. **Position model assumes 6 seats always.** If the table has fewer
   than 6 players (e.g., 5-handed games), the lookup uses the closest
   6-max position by chair-distance-from-BB. Adequate but not optimal —
   real Nash play differs slightly when the field is short of full.

## Range targets (chart-level invariants)

The chart's aggregate frequencies are validated by tests in
`tests/test_strategy/test_strategy_table.py` and related sim runs.
Current bands (validated by the Phase 1 sim):

| Metric | Target | Notes |
|---|---|---|
| AA / KK / QQ open from any position | ≥ 0.95 | Premium opens always |
| 72o / 32o / 82o open from any position | ≤ 0.05 | Absolute trash folds |
| BTN RFI rate | 35-45% | Widest open seat |
| UTG RFI rate | 10-18% | Tightest open seat |
| Total fold rate facing UTG open from BB | 55-70% | Defense narrower vs early raisers |

A future solver replacement should preserve these macro shapes even if
individual hands flip.

## Sources

Reference materials consulted during the original authoring:

- General 6-max poker theory (any standard intermediate poker text)
- Empirical sim validation: 10k hands × 6 archetypes × multiple seeds
- The original Phase 1 plan in `docs/plans/` (mid-Feb 2026)

The chart has **never** been verified against a clean Nash solver
output. A border-flip log in the style of the HU README would be the
natural place to record specific hand-level deviations once
calibration is done.

## What's NOT in this chart

- **Solver-derived mixed frequencies.** Per-hand frequencies are
  hand-authored round-numbered approximations, not Nash equilibrium
  output.
- **Multiple sizings.** Only `raise_2.5bb` for opens, `raise_3x` for
  3-bets, `raise_2.2x` for 4-bets. No 3 BB or 2 BB open mix.
- **Stack-depth variants.** 100 BB only. The
  `poker/strategy/short_stack.py` heuristic handles depths below ~20 BB
  independently of this chart; the new `poker/strategy/push_fold.py`
  handles ≤15 BB HU spots via the dedicated push/fold chart.
- **ICM adjustments.** Cash-style throughout.
- **Limped-pot postflop trees.** If preflop folds through to BB's
  option without a raise, postflop play uses the standard postflop
  strategy table without limp-specific adjustments.

## File layout

```
poker/strategy/data/
  preflop_100bb_6max.json         # the data
  preflop_100bb_6max_README.md    # this file (retrospective spec)
  preflop_100bb_hu.json           # HU equivalent
  hu_preflop_chart_README.md      # HU spec
  postflop_strategies.json        # postflop tables
  push_fold_hu.json               # short-stack HU push/fold
  push_fold_hu_README.md          # push/fold spec
```

## Calibration roadmap

If/when solver-quality ranges become available (whether via paid
solver output, public Nash databases, or a from-scratch CFR build),
the replacement workflow is:

1. Generate canonical ranges per (scenario, position) tuple
2. Diff against the v1 placeholder ranges in this chart
3. Update json + document border-flips in a section appended here
4. Re-run aggregate-band tests to confirm macro shape preserved
5. Re-run sim-validation harness to confirm bb/100 vs reference bots
   doesn't degrade
