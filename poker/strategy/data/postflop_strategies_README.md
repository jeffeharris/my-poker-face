---
purpose: Provenance and design spec for the postflop strategy table (postflop_strategies.json)
type: spec
created: 2026-05-17
last_updated: 2026-05-17
---

> **Retrospective README** — this file documents the existing
> `postflop_strategies.json` chart, written months after the chart
> shipped (commit `e16a42aa`, 2026-02-17). The chart was hand-crafted
> with a defined node taxonomy, not solver-derived; this README captures
> the taxonomy, the rules implicit in the data, and the known
> calibration gaps. Mirrors `preflop_100bb_6max_README.md` and
> `hu_preflop_chart_README.md`.

# Postflop strategy table spec

This document is the **retrospective source-of-truth** for
`poker/strategy/data/postflop_strategies.json`. The chart was authored
in February 2026 against a defined node taxonomy; this README was
written later to make the conventions explicit for future calibration
work.

## Provenance

| Aspect | Status |
|---|---|
| Authoring approach | Hand-crafted strategies + heuristic tables, AI-assisted |
| Original commit | `e16a42aa` — "feat: complete Phase 2 postflop foundation with hand-crafted strategies" (2026-02-17) |
| Entry count | 2,160 entries across the full taxonomy product |
| Solver provenance | None — this is **not** solver output |
| Validation | "All 23 directional checks passing" (per commit message); archetype c-bet rates from 30% (Nit) to 69% (Maniac) |
| Subsequent edits | None — chart frozen since Feb 2026 |

The commit message explicitly calls them "hand-crafted strategies" and
"heuristic tables for turn/river" — no claim of solver derivation.
2,160 entries enumerate the full product of the seven-segment node key.

## Taxonomy: node key shape

Each entry's key is a pipe-separated 8-segment string. The 8th segment
is currently a single value (`high`) which is a forward-compat slot
for future hand-class refinement — see "What's NOT in this chart"
below.

| Segment | Values | Notes |
|---|---|---|
| 1. Street | `flop`, `turn`, `river` | 3 |
| 2. Position | `IP`, `OOP` | 2 |
| 3. Pot type | `SRP` | 1 (only single-raised pots; 3-bet/4-bet/limped trees deferred) |
| 4. Board texture | `dry_high`, `dry_low_static`, `monotone`, `two_tone_broadway`, `two_tone_connected`, `wet_rainbow` | 6 |
| 5. Hero hand class | `air`, `weak_made`, `medium_made`, `strong_made`, `nuts` | 5 |
| 6. Draw status | `no_draw`, `backdoor`, `weak_draw`, `strong_draw` | 4 |
| 7. Action context | `unopened`, `facing_bet`, `facing_raise` | 3 |
| 8. Reserved | `high` | 1 (placeholder; see below) |

Total key combinations: 3 × 2 × 1 × 6 × 5 × 4 × 3 × 1 = **2,160**. This
matches the entry count exactly — every node has an entry, no
fallbacks needed for in-taxonomy lookups.

## Action vocabulary

| Action | Meaning | Used in |
|---|---|---|
| `check` | Check (free play) | `unopened` contexts |
| `fold` | Fold | `facing_bet`, `facing_raise` |
| `call` | Call | `facing_bet`, `facing_raise` |
| `bet_33` | Bet 33% pot | `unopened` |
| `bet_67` | Bet 67% pot | `unopened` |
| `bet_100` | Bet pot | `unopened` |
| `raise_67` | Raise to 67% of pot-after-call | `facing_bet`, `facing_raise` |
| `raise_150` | Raise to 150% of pot-after-call | `facing_bet`, `facing_raise` |
| `jam` | All-in | any context (typically river or short SPR) |

Per-row probabilities sum to ~1.0 (chart loader test enforces).
Frequencies are **mixed** throughout — most rows have 2-3 non-zero
actions with floating-point weights.

## Example entries

A few rows to illustrate the shape:

**IP nut hand on dry-high flop, action checked to hero:**
```
flop|IP|SRP|dry_high|nuts|no_draw|unopened|high
→ {'bet_33': 0.501, 'bet_67': 0.409, 'check': 0.09}
```
Mostly bet small or medium for value; check ~9% to balance / induce.

**IP air on monotone flop, facing a bet:**
```
flop|IP|SRP|monotone|air|no_draw|facing_bet|high
→ {'fold': 0.75, 'call': 0.15, 'raise_67': 0.1, 'jam': 0.0, 'raise_150': 0.0}
```
Mostly fold, occasional bluff-catch float or check-raise.

**OOP nuts on river, facing a bet:**
```
river|OOP|SRP|monotone|nuts|no_draw|facing_bet|high
→ {'call': 0.3, 'fold': 0.0, 'jam': 0.3, 'raise_150': 0.2, 'raise_67': 0.2}
```
Trap a third of the time; raise (various sizes) two-thirds. Never fold.

## Known gaps and calibration debts

These are real limitations of the current chart. A future solver
replacement (or further hand-authoring) would address them.

1. **No solver provenance.** Frequencies are heuristic estimates,
   validated by sim — not Nash output. Mid-spectrum spots (medium_made
   on connected boards, etc.) likely deviate from true equilibrium by
   5-15% in either direction.

2. **Only single-raised pots (SRP).** Pot type segment is always
   `SRP`. The chart does not encode separate trees for:
   - 3-bet pots (higher SPR-relative tightness, different bet sizing)
   - 4-bet pots (typically near-jam SPR; almost binary)
   - Limped pots (smaller starting pot, looser ranges)

   The Phase 1 caller routes 3-bet and 4-bet pots through the SRP tree
   as a coarse approximation. Limped pots also resolve here; this is
   a known leak when limped-pot opponents (CaseBot-style) are at the
   table.

3. **Hand-class bucketing is coarse.** Five classes (air → weak_made →
   medium_made → strong_made → nuts) collapse a wide spectrum:
   - "strong_made" includes top pair top kicker AND second pair
     overpair — different in practice
   - "air" includes both true air and gutshots-without-overcards
   - "medium_made" is the catchall for "second pair or weak top pair"

4. **Bet sizing is a 3-option menu.** Postflop bets are
   `bet_33` / `bet_67` / `bet_100`; no overbets (e.g., `bet_125`,
   `bet_200`), no min-bets. Modern solver play uses overbets on rivers
   in polarized spots. Adding overbets would require regenerating the
   chart with the new menu and recalibrating.

5. **Raise sizing is binary.** `raise_67` and `raise_150` are the only
   raise sizes. No 0.5x or 2x raise options. Sufficient for v1, narrow
   for solver-grade.

6. **Reserved 8th segment (`high`).** The 8th key segment is currently
   constant. It was introduced as a forward-compat slot — likely for
   board-height refinement (high vs low boards) or hand-quality
   refinement within a class (top-pair good kicker vs top-pair weak
   kicker). Not consumed by any code path today.

7. **No multi-way adjustments.** The chart assumes heads-up postflop.
   `poker/strategy/multiway.py` applies adjustments on top when the
   field is wider, but those are heuristic frequency shifts, not
   multi-way-Nash strategy.

8. **Position is binary (IP / OOP).** All non-headers-up postflop
   spots are squeezed into one of these two buckets. In 3-way+ pots,
   the middle seat's exact position matters but isn't captured.

## Range targets (chart-level invariants)

Aggregate behavior the chart aims to produce (per the original
validation harness):

| Metric | Target | Notes |
|---|---|---|
| Nit c-bet rate on dry flop, IP | ~30% | Tightest archetype |
| Maniac c-bet rate on dry flop, IP | ~65-70% | Widest archetype |
| Nut hand bet on flop, IP, unopened | ≥ 0.85 (any bet_*) | Always value-bet nuts |
| Air on river, facing big bet | ≥ 0.85 fold | Bluff-catchers fold air |
| Strong made on flop, facing bet, IP | ≥ 0.50 (call + raise) | Don't fold strong hands |

The chart was authored to satisfy these directional checks; specific
hand decisions within each cell are heuristic.

## Sources

Reference materials consulted during the original authoring:

- General postflop poker theory (intermediate-to-advanced texts)
- Standard solver-output shapes from public training-site materials
- Validation against the 6-archetype simulation harness (see
  `experiments/validate_postflop.py` if it still exists, or the Phase 2
  plan in `docs/plans/`)

The chart has **never** been verified against a clean Nash solver
output. The closest reference is the GTO-Lite preset's behavior, which
itself was hand-tuned to approximate published solver outputs.

## What's NOT in this chart

- **Solver-derived strategies.** Hand-authored heuristics; no Nash
  derivation.
- **3-bet / 4-bet / limped pot trees.** Pot type is always SRP.
- **Overbets.** Maximum bet is `bet_100` (pot-sized).
- **Donk bets.** OOP betting into the previous-street aggressor isn't
  modeled separately from standard OOP play.
- **Stack-depth variants.** The chart is "100 BB-ish" — SPR
  considerations bake into the action-context (`facing_bet` vs
  `facing_raise`) without explicit stack-depth segmentation.
- **Multi-way trees.** HU postflop only; multi-way handled by
  `multiway.py` heuristic on top.

## Calibration roadmap

The replacement workflow is the same as for the preflop charts:

1. Generate canonical Nash output via solver (whether paid or
   in-house CFR build)
2. Diff against the v1 placeholder ranges in this chart
3. Update json + document border-flips
4. Run aggregate-band tests to confirm macro shape preserved
5. Run sim-validation harness to confirm bb/100 doesn't degrade

The expansion priorities (per the NEXT_PHASE_VISION roadmap) are:

1. **Limped-pot SRP tree** — biggest current leak vs CaseBot-style
   opponents who limp constantly
2. **3-bet pot tree** — second-largest gap; affects all 3-bet-pots
3. **Overbet menu** — solver play uses 125-200% pot rivers regularly;
   current chart can't emit those sizes
4. **4-bet pot tree** — lowest leverage (rare in practice)

## File layout

```
poker/strategy/data/
  postflop_strategies.json         # the data
  postflop_strategies_README.md    # this file (retrospective spec)
  preflop_100bb_6max.json          # 6-max preflop
  preflop_100bb_6max_README.md     # 6-max spec
  preflop_100bb_hu.json            # HU preflop
  hu_preflop_chart_README.md       # HU spec
  push_fold_hu.json                # short-stack HU push/fold
  push_fold_hu_README.md           # push/fold spec
```
