---
purpose: Hand-authored depth rules that derive the 50bb/25bb 6-max preflop charts from the 100bb baseline
type: spec
created: 2026-05-25
last_updated: 2026-05-25
---

# Depth-aware 6-max preflop charts

`preflop_50bb_6max.json` and `preflop_25bb_6max.json` are **generated** from
`preflop_100bb_6max.json` by `generate_depth_charts.py`. Do not hand-edit the
JSON — edit the rules/knobs in the generator and re-run:

```bash
docker compose exec backend python -m poker.strategy.data.generate_depth_charts
```

## Why these exist

The tiered bot was measured playing a **byte-identical preflop game at
100/50/25bb** (`docs/plans/SOLVER_CHART_SCOPE.md` → DIAGNOSED): VPIP 18 /
PFR 14 / jam 0.4% / open 3.3bb at every depth, flat-calling 18% vs opens.
That zero depth-adjustment is the diagnosed short-stack leak — **−18 to −22
bb/100 at 25–50bb** vs the Jeff_clone human model, ~4–5× the 100bb leak.

These charts are the cheap "100bb → fix" pass the diagnosis called for: a
*coarse* hand-authored depth correction expected to recover most of the leak
before (or instead of) committing to a 25–50bb solve.

## Governing principle

As effective stacks shorten, **flat less, jam/polarize more**. A 3-bet at
25bb commits ~⅓ of the stack, and flatting an open OOP with no implied odds
is a commitment error. The eval is a calling station (Jeff_clone, fold-to-cbet
≈0.45), so **bluff-jams are −EV** — depth rules jam *value* and fold marginal
holdings rather than bluff-shoving.

## Action labels (must match the 100bb chart)

| Scenario | Labels | `raise_*` means |
|---|---|---|
| `rfi` | `raise_2.5bb` / `fold` | the open |
| `vs_open` | `raise_3x` / `call` / `fold` | the 3-bet |
| `vs_3bet` | `raise_2.2x` / `call` / `fold` | the 4-bet |
| `vs_4bet` | `jam` / `call` / `fold` | — |

## Per-scenario rules

### `rfi` — unchanged
Opening ranges/sizes are already fine at these depths; the diagnosed leak is
in the *facing-action flats*, not RFI. Preserving RFI keeps the opening
aggression the bot already has.

### `vs_open` — value jams, marginal folds (you are NOT committed)
Facing a single open you can still fold/flat profitably, so only genuine
value wants to jam. Gate on **raise-dominance** (`raise_3x ≥ 0.50`, a
polarized value-3bet — *not* the thin 3-bet-bluff frequency the 100bb chart
sprinkles on speculative hands).
- **25bb, value:** whole continue range jams → `{jam: raise+call, fold}`.
- **25bb, marginal/bluff:** drop the bluff-3bet + most flats to fold; keep a
  thin flat (`J25_VSOPEN_SPEC_FLAT_KEEP = 0.30` of the original call).
- **50bb, value:** keep the 3-bet, push a little more value to it
  (`+0.20·call`), tighten flats (`+0.20·call → fold`).
- **50bb, marginal:** keep the 3-bet (incl. bluff), tighten flats
  (`J50_VSOPEN_SPEC_FOLD_FROM_CALL = 0.35`).

### `vs_3bet` — jam-or-fold (you ARE near-committed shallow)
Facing a re-raise, raise-dominance is the wrong gate — value hands like
JJ/TT *continue by calling* (raise ≈0.2). Gate instead on the 100bb **fold
frequency**.
- **25bb:** if `fold ≥ 0.50` the hand stays a fold (incl. its bluff-4bet —
  no jam vs a station); otherwise the whole continue range jams
  → `{jam: raise+call, fold}`. Folds 76s (fold 0.75), jams JJ/TT (fold ≤0.25).
- **50bb:** milder — keep the 4-bet, commit some flats
  (`J50_VS3BET_JAM_FROM_CALL = 0.25` of call → jam), tighten the rest
  (`+0.25·call → fold`).

### `vs_4bet` — already polarized; convert calls to jams
The fold mass already filters strength, so no extra gate.
- **25bb:** all calls → jam (`J25_VS4BET_JAM_FROM_CALL = 1.0`).
- **50bb:** half of calls → jam (`J50_VS4BET_JAM_FROM_CALL = 0.50`).

## Selection at runtime

`tiered_bot_controller.py` picks the chart by **effective stack** (nearest
published depth bucket: 100/50/25bb), mirroring `push_fold._nearest_bucket`.
Below ~15bb the HU push/fold chart and the `short_stack.py` heuristic take
over; these depth charts cover the 20–~75bb middle.

## Calibration

The `J*` / `VALUE_RAISE_THRESHOLD` constants at the top of the generator are
the tuning surface. Measure with:

```bash
docker compose exec backend python -m experiments.measure_passivity \
    --stack-bb 25 --opponents jeff --hands 3000 --seeds 42,142,242
```

### Measured (Baseline vs Jeff_clone, 3000h × seeds 42/142/242)

| Depth | Before (depth-agnostic) | After (these charts) | Δ bb/100 |
|---|---|---|---|
| 25bb | −21.8 | **−8.0** | **+13.8** |
| 50bb | −18.8 | **−14.0** | **+4.8** |
| 100bb | −4.2 | −4.2 | 0 (base table untouched) |

The 25bb win is large because jam-or-fold polarization removes the awkward
low-SPR postflop spots entirely. **50bb's residual (−14 vs the 100bb −4.2)
is postflop-bound, not preflop:** an aggressive 50bb preflop variant
(0.35/0.55/0.40) moved bb/100 by ~0 (−14.0 → −13.8, noise), so the next
lever for 50bb is low-SPR postflop commit logic, not more preflop folding.
100bb must stay ≈unchanged — the 100bb chart is not touched.
