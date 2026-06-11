---
purpose: Spec for the multi-way (6-max) short-stack push/fold chart (push_fold_6max.json) used by the tiered bot below ~15 BB
type: spec
created: 2026-05-24
last_updated: 2026-06-11
---

# Multi-way (6-max) Push/Fold Chart (≤15 BB short-stack)

This document is the **authoritative source-of-truth** for
`poker/strategy/data/push_fold_6max.json`. It is the multi-way sibling of
`push_fold_hu_README.md` (heads-up) and follows the same review pattern so
both push/fold charts share a common shape.

The build directive is `docs/plans/PUSH_FOLD_6MAX_SCOPE.md` — that doc holds
the full research ranges (with per-cell confidence tags), the integration
map, and the build sequence. This README records the *conventions* and the
*calibration status* of the encoded data.

## Background

The tiered bot already has a HU push/fold chart (`push_fold_hu.json`) that
fires below ~15 BB heads-up. **Multi-way** short-stack spots (3–6 players
seated, ≤15 BB effective) currently fall through to the
`poker/strategy/short_stack.py` heuristic, which merely suppresses
medium-raise probability mass rather than enforcing a Nash push/fold range.

The real game is a **winner-take-all sit-and-go**, where most consequential
decisions happen at 25 BB and shorter. This chart is the hard fix for the
multi-way short-stack leak: an explicit Nash-style push/fold lookup keyed on
position × depth that bypasses the deep-stack table entirely below 15 BB.

## The model: chip-EV Nash, ICM OFF

Winner-take-all ⇒ one payout ⇒ tournament equity is linear in chips ⇒
$EV = chip-EV ⇒ the ICM correction term vanishes. So the encoded ranges are
the **chip-EV Nash push/fold equilibrium with ICM = OFF** — the classic
SHAL / Mathematics-of-Poker tables apply exactly, no bubble/pay-jump
tightening. ICM is **not** applied.

## Conventions (read before encoding)

- **Effective stack** = min(hero stack, largest stack still to act), in BB,
  including blinds posted. The lookup key is **hero's effective BB**, which
  matches how every published chart is indexed. The controller already
  computes this (`min(hero, max active opp) / BB`).
- **No ante.** These are the no-ante equilibrium ranges; antes widen
  everything ~3–8%. If the SNG has antes, these are a tight-side
  approximation. (`meta.ante = false`.)
- **Positions (6-max):** UTG (4 players behind), HJ/MP (3), CO (2),
  BTN (1), SB (1, only BB behind). **BB never open-shoves** unopened
  (folded-to-BB is a walk) — BB appears only as a *caller*, so it is absent
  from the `unopened` section.
- **Early-position tightening is the dominant effect:** more players behind
  ⇒ a much tighter unopened jam than the HU SB chart at the same depth
  (UTG at 10 BB jams ~6%, vs SB ~37%).
- **Pure jam-or-fold.** Where real solvers mix in min-raises (≥~12 BB), the
  pure-jam frequency is the tight-side component listed — correct for a
  jam-or-fold bot.
- **Binary 100/0 frequencies per hand** for v1 (same as the HU chart). Mixed
  strategies can come later when calibration against true Nash output reveals
  which hands need mixing.

## Stack depth buckets

| Depth | Source rationale |
|---|---|
| 4 BB | Any-two regime for BTN/SB; early positions still tight |
| 6 BB | Wide push-fold band |
| 8 BB | Cross-validated textbook anchor |
| 10 BB | Standard published reference depth |
| 12 BB | Transition zone |
| 15 BB | Upper bound; above this the deep-stack table takes over |

Note the bucket set `[4, 6, 8, 10, 12, 15]` differs from the HU chart's
`[5, 7, 10, 12, 15]` — the published 6-max Nash tables are anchored at
4/6/8/10/12/15 BB, so we mirror those anchors directly rather than
re-interpolating.

## Scenarios in scope

| Section | Key shape | Hero faces | Hero acts |
|---|---|---|---|
| `unopened` | position → depth → hands | (acts first, folded to) | jam or fold |
| `call_vs_shove.bb_vs_sb` | depth → hands | SB jam (HU-style) | call or fold |
| `call_vs_shove.bb_vs_late` | depth → hands | a BTN/CO jam | call or fold |
| `reshove` | depth → hands | a single non-all-in open | jam or fold |

`unopened` covers UTG, HJ, CO, BTN, SB (not BB). The two call tables cover
the blind defending against a jam.

`reshove` (added after the v1 validation showed facing-a-single-open is the
*dominant* short-stack spot, ~66% of preflop decisions — see
`docs/plans/PUSH_FOLD_6MAX_SCOPE.md`) is jam-or-fold over a single non-all-in
open. It is **`[L]` extrapolated** and **gated behind the
`PUSH_FOLD_6MAX_RESHOVE_ENABLED` feature flag** (stable/on by default, still
kill-switchable; off → the spot falls through to the deep-stack /
`short_stack.py` path, byte-identical). Notable v1
simplifications:
- **Depth-keyed only** (8/10/12/15 BB; sub-8 clamps to 8). No 4/6 BB rows — at
  ≤6 BB facing an open the blind is committed and the decision degenerates.
- **Opener-position-agnostic.** Reshoving vs a tight UTG open should be tighter
  than vs a BTN open; v1 uses one table regardless (a future refinement).
- **Any hero position, including BB** (a BB reshove over an open is standard —
  unlike `unopened`, which excludes BB).
- **Single opener only.** A 3-bet+ war, a cold-caller (multiway), or an all-in
  in front falls through (the detector `reshove_action_6max` fail-closes).

The reshove **detector is controller-agnostic** (`push_fold.reshove_action_6max`,
a pure read of game state). The sharp bot wires it behind the flag; other bot
types can opt in independently — the 6-max *charts* are sharp-only, but
reshoving a short stack over an open is a generally useful skill.

## Action vocabulary

| Action | Meaning |
|---|---|
| `jam` | All-in for the effective stack |
| `fold` | Fold |
| `call` | (caller tables only) Call the all-in |

Per-row probabilities sum to 1.0.

## Range targets (chart-level invariants)

Approximate aggregate jam/call frequencies the chart aims to hit, drawn from
the scope doc's research ranges. Tests in
`tests/test_strategy/test_push_fold_6max.py` assert against these bands
(with a few-percent tolerance, since hand-list expansion lands on whole
combos rather than exact published percentages).

### Unopened jam % by position × depth

| Depth | UTG | HJ | CO | BTN | SB |
|---|---|---|---|---|---|
| 4 BB | 18% | 24% | 38% | 100% | 100% |
| 6 BB | 12% | 14% | 22% | 52% | 60% |
| 8 BB | 9% | 9% | 30% | 40% | 52% |
| 10 BB | 6% | 10% | 16% | 27% | 38% |
| 12 BB | 6% | 11% | 12% | 20% | 30% |
| 15 BB | 5% | 8% | 10% | 16% | 22% |

Monotonicity expectations: at a fixed depth, jam% widens from UTG → SB
(more players behind ⇒ tighter). At a fixed position, jam% widens as depth
shrinks (less fold equity needed; shorter ⇒ wider).

### Call % by depth

| Depth | bb_vs_sb | bb_vs_late |
|---|---|---|
| 4 BB | 55% | (n/a) |
| 6 BB | 42% | 28% |
| 8 BB | 33% | 24% |
| 10 BB | 24.5% | 18% |
| 12 BB | 19% | 14% |
| 15 BB | 13% | 9% |

The caller is **tighter** than the pusher at every depth (no fold equity).
`bb_vs_late` is tighter than `bb_vs_sb` at matching depth (late openers are
not as wide as an SB shoving into one player). `bb_vs_late` has no 4 BB row
in the source (at 4 BB the blind is committed against any late jam); the
lookup clamps a 4 BB late-jam call to the 6 BB row.

### Reshove jam % by depth (flag-gated, `[L]`)

| Depth | jam % |
|---|---|
| 8 BB | 16% |
| 10 BB | 13% |
| 12 BB | 10% |
| 15 BB | 7% |

Tightens monotonically as depth grows (more behind to lose, less need to
reshove light). Tighter than the SB unopened jam at matching depth — facing
a live open there is no dead money to win uncontested.

## Confidence tags

Each table carries a `conf` tag in the JSON `meta.confidence` block, taken
from the scope doc:

- **[H]** cross-validated across multiple published sources.
- **[M]** single-source / interpolated.
- **[L]** extrapolated (4 BB early-position cells, vs-tight-opener calls, the
  entire `reshove` table).

The lookup logs at DEBUG when it returns an action from an `[L]`-tagged cell
so low-confidence routing is auditable. Per-cell tags are not stored on each
hand (that would 6×-bloat the file); the tag is per (table, depth) and lives
in `meta.confidence`.

## v1 calibration status

**These ranges are published-Nash-derived approximations, not a fresh solver
output.** They are expanded deterministically from the readable hand-list
ranges in `docs/plans/PUSH_FOLD_6MAX_SCOPE.md` via
`generate_push_fold_6max.py`. `meta.calibration_status =
"v1_from_published_nash"`.

**Shape-list + target-% calibration (important design note):** the scope
doc gives, per cell, *both* a readable hand list *and* a cross-validated
`~%`. On audit, most unopened hand lists transcribe materially **looser**
than their stated `~%` (e.g. UTG/HJ 8 BB list expands to ~19% combo-weighted
but the published anchor is 9%; the lists over-use "any-ace"/"any-king"
shorthand). The `~%` values are the cross-validated [H] anchors (Sources),
so the generator treats the **hand list as the eligible candidate pool /
shape** and **trims it down to the published target combo-%** by dropping the
weakest combos (by the HU strength proxy, `_hand_strength_rank`). It never
*adds* hands the doc didn't list. Result: every cell lands within ~2% of its
published target while keeping the doc's range shape. This is a v1 decision
made because the doc's two signals disagreed; the scope doc names aggregate
jam/call% as the primary validation gate, so the percentages win.

Known approximations:
- Frequencies are combo-weighted (offsuit=12, suited=4, pair=6 of 1326),
  matching how published Nash percentages are stated. A few cells land ~2-5%
  under target where the doc's own list under-expands (SB 6 BB → ~56% vs
  60%; bb_vs_sb 15 BB → ~11% vs 13%) — still inside the test bands.
- Stack-depth granularity is buckets, not continuous; the lookup snaps to the
  nearest bucket (no interpolation).
- `bb_vs_late` is the same regardless of whether the late jammer is CO or
  BTN (the scope doc's "nudge ~1–2% wider vs CO" refinement is not encoded
  in v1). vs a UTG/HJ jam, callers should tighten further; v1 routes
  early-position jams through `bb_vs_late` as a conservative (slightly loose)
  approximation and flags it.
- ICM never applies (WTA SNG = chip EV throughout).

Calibration roadmap (deferred, not blocking ship): diff the expanded ranges
against a clean HRC / ICMIZER 6-max no-ante Nash solve and log per-hand
border flips in the section below.

## What's NOT in this chart

- **Reshove vs a tight (UTG/HJ) opener.** The `reshove` table is
  opener-position-agnostic; reshoving should tighten vs an early opener.
- **Multiway / 3-bet-pot reshoves.** The `reshove` detector fires only on a
  clean single-opener spot; everything else falls through.
- **ICM-adjusted ranges.** WTA SNG = chip EV throughout.
- **Mixed frequencies.** Binary 100/0 per hand for v1.
- **Stack-depth interpolation.** Nearest-bucket snap only.
- **Ante-on variant.** Ranges are no-ante; antes would widen them.
- **Position-of-jammer resolution beyond SB-vs-late.** The caller tables
  collapse all non-SB jammers into `bb_vs_late`.

## Border-flip log (v1)

(Populate when a calibration pass against a clean Nash solve reveals
specific hand-level deviations from the ranges this file shipped with.)

## File layout

```
poker/strategy/data/
  push_fold_6max.json            # the data, machine-readable
  generate_push_fold_6max.py     # generator (deterministic from the scope-doc ranges)
  push_fold_6max_README.md       # this file (conventions + calibration status)
```

## Sources

Published Nash chip-EV (ICM-off) push/fold references compiled in the scope
doc: Mathematics of Poker (Chen & Ankenman), HoldemResources HUNE tables,
gamblingcalc 6-max-by-depth no-ante Nash chart, mypokercoaching / Upswing /
888poker push-fold charts, pokerstrategy SNG Nash ranges. The SB-unopened,
BB-vs-SB-call, and the 8/10/15 BB position anchors are cross-validated [H];
the 4 BB early cells and the vs-tight-opener calls are extrapolated [L].
