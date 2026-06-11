---
purpose: Implementation spec for a 6-max short-stack push/fold table (extends the HU Nash solver)
type: design
created: 2026-06-10
status: proposed
depends_on: push_fold_hu.json solver (fictitious play + eval7), PREFLOP_DEFENSE_REGEN_SPEC.md (branching table changes)
---

# 6-Max Push/Fold Spec

Closes the known gap: short-stack 6-max currently rides the 25bb depth chart,
which has no jam-first-in concept below 25bb and leaves obvious EV on the table
at 5–15bb effective. The HU solver (fictitious play, eval7 all-in equities,
validated vs HoldemResources anchors) already exists — this extends it to six
seats.

## 1. Scope and model

- **Game model**: first-in jam-or-fold. A seat either open-jams or folds;
  players behind respond with call-or-fold. Chip-EV cash, no antes, no ICM.
- **Trigger**: 6-max/multiway, pot unopened, effective stack ≤ **12bb**.
  (10–15bb is genuinely mixed open/jam territory; 12 is the cutoff where the
  jam-only simplification costs least. Below the trigger this table overrides
  the depth chart, mirroring how `push_fold_hu.json` overrides the HU chart.)
- **Positions**: UTG, HJ, CO, BTN, SB jam tables; BB never first-in jams
  (checks option). Call tables for every (caller, jammer) pair behind.
- **Stack bands**: solve at 2, 3, 4, 5, 6, 8, 10, 12bb effective; runtime picks
  the nearest band (same convention as the HU table).

### 1.1 Multiway simplification

Exact multiway Nash is intractable; use the standard first-in approximation:

- Jammer EV is computed against **independent** call decisions of each player
  behind (each calls with their current call range; no collusion).
- **Overcall model**: once one player has called, subsequent callers use their
  call-vs-jam range intersected with a top-X tightening factor
  (`overcall_tighten = 0.45` — call only the top 45% of the normal call range,
  by all-in equity vs {jammer ∪ caller}). Three-way+ pots beyond the first
  overcall: pot-odds backstop only (existing eval7 veto), not charted.
- This matches the HoldemResources multi-seat "first-in Nash" construction, so
  their published tables remain usable as validation anchors.

## 2. Solver

Reuse the HU fictitious-play loop, generalized:

```
ranges = init(all seats: jam/call = top 20% heuristic)
repeat until max range delta < epsilon:
    for seat in [UTG, HJ, CO, BTN, SB]:
        jam_range[seat] = best_response_jam(seat, call_ranges_behind, eval7_eq)
    for (caller, jammer) in pairs_behind:
        call_range[caller][jammer] = best_response_call(pot_odds, jam_range[jammer], eval7_eq)
```

- Equities from the existing precomputed 169×169 eval7 all-in matrix.
- Damped updates (e.g. 0.5 mixing) to avoid oscillation — same trick as HU.
- Convergence target: no hand's EV classification flips by more than 0.01bb
  between iterations. Expect minutes, not hours: 5 jam ranges × 8 bands plus
  15 call pairs × 8 bands, each a 169-vector best response.

## 3. Output format

`poker/strategy/data/push_fold_6max.json`, mirroring the HU schema:

- `jam_first_in[position][stack_band][hand] = {jam: w, fold: 1-w}`
  (Nash is near-pure; weights mostly 0/1 with a thin mixed margin.)
- `call_vs_jam[caller][jammer][stack_band][hand] = {call: w, fold: 1-w}`
- Provenance block in the header (solver version, epsilon, anchor validation
  results) — same convention that earned `push_fold_hu.json` its "GTO-grade"
  labeling in the review packet.

## 4. Branching integration

New rows in the chart-selection table:

| Situation | Chart |
|---|---|
| 6-max, unopened pot, eff ≤ 12bb | `push_fold_6max.json` jam table |
| 6-max, facing a first-in jam, eff ≤ 12bb | `push_fold_6max.json` call table |

- The call table **replaces** the raw pot-odds backstop for this specific spot
  (pot-odds-only calling is too loose vs tight jam ranges — it ignores that a
  9bb UTG jam range crushes the hands pot odds say to call). The backstop
  remains for every spot the table doesn't cover (limped pots, raised pots,
  3+ way).
- **Precedence vs archetypes**: unlike the depth charts, push/fold wins over
  identity *for the range envelope* — but apply a per-tier width multiplier so
  characters stay distinguishable:

| Tier | Jam-range width multiplier | Call-range width multiplier |
|---|---|---|
| nit/rock | 0.80 | 0.85 |
| TAG (base) | 1.00 | 1.00 |
| LAG | 1.15 | 1.05 |
| maniac | 1.35 | 1.15 |
| station / weak-fish | 0.90 | 1.30 |

Width multiplier = take the Nash range ordered by jam (or call) EV and extend/
truncate to multiplier × Nash size. Deliberate, bounded EV sacrifice for
believability — same philosophy as the width tiers, but anchored to a solved
baseline so the cost is measurable (report bb/100 cost per tier in the solver
output).

## 5. Validation

- **Anchors**: spot-check ≥20 cells against published HoldemResources 6-max
  Nash numbers (BTN 10bb jam ≈ 40–45%; UTG 10bb ≈ 25%; SB 10bb ≈ 50%+).
  Tolerance: ±2 range-percentage points.
- **Monotonicity lints** (CI, every regen):
  - Jam range widens as stack shrinks (per position).
  - Jam range widens with later position (UTG ⊆ HJ ⊆ CO ⊆ BTN at every band;
    SB widest).
  - Call range vs a late jam ⊇ call range vs an early jam.
- **Probe sims**: vs the current 25bb-chart fallback at 8bb effective, the
  Nash table should show a clearly positive bb/100 head-to-head (this is the
  acceptance evidence the gap was worth closing).

## 6. Out of scope

- ICM / tournament adjustments (cash only).
- Limped-pot and vs-open short-stack trees (still the depth charts + backstop).
- Stop-and-go or jam-over-limp lines.
