---
purpose: Spec for the heads-up push/fold chart (push_fold_hu.json) used at short-stack depths
type: spec
created: 2026-05-17
last_updated: 2026-05-26
---

# HU Push/Fold Chart (≤15 BB short-stack)

This document is the **authoritative source-of-truth** for `poker/strategy/data/push_fold_hu.json`. Mirrors the format of `hu_preflop_chart_README.md` so the two charts (deep 100 BB and short-stack push/fold) share a common review pattern.

The chart is **computed from the exact chip-EV heads-up push/fold Nash equilibrium (no ante)** by `poker/strategy/data/generate_push_fold_nash.py` and validated against canonical HoldemResources HUNE anchors (below). It is **HU-only**.

## Background

Stacks below ~15 BB effective change correct play fundamentally: implied odds collapse, postflop SPR is near zero, and the EV of medium-sized raises drops below pure push (jam) or fold. At these depths, the strategy table that ships in `preflop_100bb_hu.json` is mis-calibrated — it emits standard raise sizes that are structurally bad when the bot is committing 30-40% of its stack on a single preflop raise.

The short-stack heuristic in `poker/strategy/short_stack.py` already does the soft version of the fix — it suppresses medium-raise probability mass and redistributes to jam/fold below 20 BB. This chart is the **hard version**: an explicit Nash push/fold lookup that bypasses the deep-stack table entirely below 15 BB.

## The equilibrium model (chip-EV, HU, no ante)

Button = SB posts 0.5 BB; BB posts 1.0 BB. Effective stack = `S` BB (both have `S`). SB acts first: jam (all-in for `S`) or fold. BB, facing a jam: call or fold. Net chips relative to the moment before posting blinds:

| Outcome | Net chips |
|---|---|
| SB fold | −0.5 |
| SB jam, BB folds | +1.0 |
| SB jam, BB calls | `2·S·eq − S` (eq = SB equity vs BB's calling range) |
| BB fold | −1.0 |
| BB call | `2·S·eq − S` (eq = BB equity vs SB's jamming range) |

Decision rules (a hand is in the range iff the inequality holds):

- **SB jams** `h` iff `f·(+1.0) + (1−f)·(2·S·eqSB − S) > −0.5`, where `f = P(BB folds)` (combo-weighted) and `eqSB` = equity of `h` vs BB's **calling** range.
- **BB calls** `h` iff `2·S·eqBB − S > −1.0`, i.e. `eqBB > 0.5 − 1/(2·S)`, where `eqBB` = equity of `h` vs SB's **jamming** range.

Solved by fixed-point iteration per depth (start with BB calling everything → compute SB jam range → recompute BB call range → repeat to convergence). Combo weighting: pair = 6, suited = 4, offsuit = 12. All-in equities come from a seeded, deterministic 169×169 class-vs-class matrix built with `eval7.py_all_hands_vs_range` (cached to `push_fold_equity_matrix.json`).

No ICM: the project's tournament mode is winner-take-all SNG, which is chip-EV throughout (see `SOLVER_CHART_SCOPE.md`), so chip-EV equilibrium is the correct target.

## Stack depth buckets

| Depth | Source rationale | Computed SB jam % / BB call % |
|---|---|---|
| 5 BB | Wide jam regime (true any-two is ~2-3 BB) | 73.8% / 62.9% |
| 7 BB | Top of the push-fold sweet spot in HU | 66.8% / 50.8% |
| 10 BB | Standard textbook depth | 58.7% / 37.6% |
| 12 BB | Transition zone | 52.3% / 33.0% |
| 15 BB | Upper bound; above this the deep-stack table takes over | 46.3% / 28.8% |

The chart publishes binary jam/fold (call/fold) per hand at each bucket: SB jams `h` at bucket `D` iff the equilibrium push threshold for `h` is `≥ D`; BB calls `h` iff its call threshold is `≥ D`.

## Scenarios in scope

| Scenario | Position | Hero faces | Hero acts |
|---|---|---|---|
| `sb_open` | `SB` | (acts first) | jam or fold |
| `bb_vs_jam` | `BB` | SB jam | call or fold |

This matches the canonical "SB jams or folds; BB calls or folds" decomposition all Nash push/fold solvers compute. No 3-bet handling — at these depths a 3-bet IS an all-in.

## Action vocabulary

| Action | Meaning |
|---|---|
| `jam` | All-in for whatever the effective stack is |
| `fold` | Fold |
| `call` | (BB only, vs SB jam) Call the all-in |

Per-row probabilities sum to 1.0. Binary 100/0 frequencies (the chip-EV push/fold equilibrium is a pure strategy at the per-hand level for these depths — the indifference set is a measure-zero boundary, so no mixing is required).

## HARD validation anchors (HoldemResources HUNE, chip-EV, no ante)

The generator prints a PASS/FAIL table against these. They are NOT tunable — if the computation misses them, the math is wrong.

**SB pusher (max BB to push):**

- `32o ≈ 1.7 BB` (±0.4) — SB folds 32o at 2 BB+, jams only when ultra-short. Computed threshold ≈ **1.45 BB** at high iteration count (within tolerance); on the coarse validation grid it reads 1.0–1.25 because 32o is near-indifferent there and the fictitious-play snapshot jitters at that measure-zero boundary. Either way 32o folds at every published bucket (≥5 BB), so the chart is unaffected.
- `AA, KK, A6o, KQo, KJo, KTo, QJo, JTo, 76s` all push at 20 BB+ — i.e. they jam at every published bucket (5…15). All PASS.

**BB caller (max BB to call):**

- HARD gate (model-consistent): `A2o ≈ 15.0` (computed 15.5, PASS), `AA, KK` call at 20 BB+ (PASS).

**Structural sanity:** at ~2 BB SB jams ~100% (any two; at 5 BB the bottom offsuit junk already folds, so 5 BB is ~74%); pushing/calling ranges widen monotonically as `S` decreases. Verified by the generator.

### Why the BB caller range is wider than some circulating "caller charts"

HoldemResources HUNE is itself a **pure jam-or-fold chip-EV** solve — the same
model as this chart — so its **SB pusher** thresholds are our validation gate
and they PASS (32o ≈ 1.7 BB; A6o/KQo/KJo/76s/JTo all jam well past 15 BB; this
is the fix for the placeholder's bug of folding them at 15 BB).

The **BB caller** side here is the **exact pot-odds best-response to that
(validated) SB jam range** — BB is last to act with no fold equity, so it simply
calls iff `equity(hand vs SB jam range) > 0.5 − 1/(2·S)`. That was verified by an
**independent eval7 recomputation** (not the generator's cached matrix): at 15 BB
the SB jams ~46% of hands, against which e.g. KQo has **53.9%** equity vs the
~46.7% price → a clear +~2 BB call. Every BB call/fold in the chart agrees with
that fresh best-response check.

Some published "Nash caller" figures circulate that are **tighter** (e.g. a
"KQo calls only ≤ ~8 BB"). Those are **inconsistent with HUNE's own wide SB jam
range** (you cannot both jam 76s/A6o/KQo at 15 BB *and* have the caller fold KQo
to it — KQo crushes the bottom of a 46%-wide range), so they come from a
different scenario (ICM, ante, full-ring, or a min-raise model) or are simply
mis-transcribed (an early web scrape for this work returned exactly such numbers
alongside a garbled SB column). For our engine's pure jam/fold chip-EV HU game,
the computed best-response above is the correct answer; we did not distort it to
match an inapplicable chart.

## The bug this chart fixed

The previous `push_fold_hu.json` was a **hand-guessed placeholder** (`calibration_status: "v1_placeholder_needs_nash_verification"`, generated by `generate_push_fold_hu.py` from round-number top-N range sizes). It was systematically too **tight**: it folded `A6o`, `KQo`, `KJo`, `KTo` at 15 BB — hands the chip-EV Nash equilibrium shoves at 20 BB+. The Nash chart is substantially wider, especially at the deeper buckets (10–15 BB).

## What's NOT in this chart

- **Multi-way push/fold.** HU only. Multi-way short-stack scenarios use the legacy `short_stack.py` heuristic.
- **ICM-adjusted ranges.** WTA SNG = chip EV throughout, so ICM never applies for the current game mode.
- **Stack-depth interpolation.** Lookup picks the nearest published bucket; no smooth interpolation. Sufficient since the lookup is gated to ≤15 BB.

## File layout

```
poker/strategy/data/
  push_fold_hu.json                 # the data, machine-readable (generated)
  push_fold_equity_matrix.json      # cached 169×169 all-in equity matrix (generated)
  generate_push_fold_nash.py        # equilibrium generator (chip-EV Nash, no ante)
  push_fold_hu_README.md            # this file (authoritative spec)
```

Re-generate after edits (rebuilds the chart from the equilibrium; reuses the
cached equity matrix unless `--rebuild-matrix` is passed):

```
docker compose exec backend python -m poker.strategy.data.generate_push_fold_nash
```

The legacy `generate_push_fold_hu.py` placeholder generator is retained only
for historical reference; it no longer produces the shipped chart.
