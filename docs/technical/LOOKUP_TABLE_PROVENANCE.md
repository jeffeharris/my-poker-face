---
purpose: Single-page provenance index for every shipped tiered-bot lookup table — source, method, commit, calibration status
type: reference
created: 2026-05-26
last_updated: 2026-06-03
---

<!-- 2026-05-26: push_fold_hu.json reclassified from hand-authored placeholder to computed chip-EV HU Nash (no ante), validated vs HUNE anchors. -->

# Lookup-table provenance index

The tiered bot (`poker/tiered_bot_controller.py`) plays from hand/rule-authored
lookup charts in `poker/strategy/data/`. This page is the **one-stop index** of
where each shipped table came from. The authoritative *detail* for each chart
lives in its sibling `*_README.md` (taxonomy, per-hand rules, calibration logs,
known gaps); this page summarizes and cross-links them.

> **Only `push_fold_hu.json` is equilibrium-computed.** It is the exact chip-EV
> heads-up push/fold Nash equilibrium (no ante), solved by fixed-point iteration
> with eval7 all-in equities and validated against HoldemResources HUNE anchors.
> Every *other* table below is hand-authored (often with AI assistance) and
> validated by simulation, not produced by a CFR/postflop solver — treat those
> frequencies as calibrated heuristics, not equilibrium output. The long-term
> postflop solver program is scoped separately in
> `docs/plans/SOLVER_CHART_SCOPE.md`.

## Shipped tables

| Table (`poker/strategy/data/…`) | Entries | First shipped | Method | README |
|---|---:|---|---|---|
| `preflop_100bb_6max.json` | 8,450 | `b8ff5c30` (2026-02-16); CO/BTN/SB RFI widened toward GTO 2026-05-27 (`build_wider_rfi_chart.py`) after a steal-aware A/B (+16 bb/100 vs jeff, +5.3 vs punisher, CI-clear) | Hand-authored + AI assist, validation-tuned; late-position RFI then widened toward GTO pure-opens | `preflop_100bb_6max_README.md` |
| `preflop_100bb_6max_tight_rfi.json` | 8,450 | snapshot 2026-05-27 | The pre-widening tight chart, preserved as the "tighten-vs-station" table for opponent-adaptive width (`EXP_003`); also the `nit`/`rock` width-tier table | `preflop_100bb_6max_README.md` (log) |
| `preflop_100bb_6max_loose.json` | 8,450 | 2026-05-29 | **Archetype width-tier chart** — widest realistic opening envelope; the `maniac` / `maniac_overbluff` / `spewy_fish` preflop table. Hand/AI-authored loose-tier opens layered on the 100bb taxonomy | `preflop_100bb_6max_README.md` (log) |
| `preflop_100bb_6max_loose_mid.json` | 8,450 | 2026-05-29 | **Archetype width-tier chart** — between TAG and Maniac; the `lag` preflop table | `preflop_100bb_6max_README.md` (log) |
| `preflop_100bb_6max_station.json` | 8,450 | 2026-05-29 | **Archetype width-tier chart** — loose-passive caller shape; the `calling_station` (+ `calling_station_pblind`/`_overbluff` isolation variants) preflop table | `preflop_100bb_6max_README.md` (log) |
| `preflop_100bb_6max_weak_station.json` | 8,450 | 2026-05-29 | **Archetype width-tier chart** — widest passive-caller shape (flats almost anything vs a raise); the `weak_fish` ($2-tier trickle) preflop table. NOT reachable via anchor classification — explicit loadout. See `FISH_AS_CALLING_STATION.md` | `preflop_100bb_6max_README.md` (log) |
| `postflop_strategies.json` | 2,160 | `e16a42aa` (2026-02-17) | Hand-crafted node taxonomy (only `(pot_type=SRP, spr=high)` populated) | `postflop_strategies_README.md` |
| `preflop_50bb_6max.json` | 8,450 | `707ff03b` (2026-05-25) | **Generated** from the 100bb chart by `generate_depth_charts.py` (depth rules) | `depth_charts_README.md` |
| `preflop_25bb_6max.json` | 8,450 | `707ff03b` (2026-05-25) | **Generated** from the 100bb chart by `generate_depth_charts.py` (depth rules) | `depth_charts_README.md` |
| `preflop_100bb_hu.json` | 676 | `46ca598e` (2026-05-13) | Hand-authored per-hand rules + border-flip log + v2 mixed-freq calibration | `hu_preflop_chart_README.md` |
| `push_fold_hu.json` | 5 depth buckets (5/7/10/12/15bb) | `0575952a` (2026-05-17); recomputed 2026-05-26 | **Computed** chip-EV HU pure jam/fold Nash (no ante) by `generate_push_fold_nash.py` (fictitious play, eval7 all-in equities). SB-pusher validated vs HoldemResources HUNE anchors (the placeholder's A6o/KQo-fold-at-15bb bug is fixed); BB-caller independently verified as the exact pot-odds best-response to the SB jam range via fresh eval7 (wider than some circulating "caller charts," which are inconsistent with the wide jam range — see README). | `push_fold_hu_README.md` |

All entry counts re-verified live against the JSON on 2026-06-03 (per-hand leaf
count = `load_strategy_table().size`): the five preflop 6-max charts (base, tight,
loose, loose_mid, station, weak_station) are each **8,450**; `preflop_100bb_hu.json`
is **676**; `postflop_strategies.json` is **2,160**; `postflop_strategies_3bp.json`
is **4,320**; `postflop_strategies_low_spr.json` is **2,160**; `push_fold_hu.json`
is **5 depth buckets** (`5/7/10/12/15bb`) + a `meta` block.

> **Depth-chart footgun (2026-05-27):** the 50/25bb charts were **NOT**
> regenerated when the 100bb chart's RFI was widened. They were generated from
> the *pre-widening tight* 100bb chart and **intentionally retain tight RFI** —
> `generate_depth_charts.py`'s `t_rfi` is identity, so regenerating from the new
> wide base would silently propagate wide opens to short stacks, which is
> **unmeasured** (the A/B was 100bb-only) and theory-disfavored. If you
> regenerate the depth charts, you must separately A/B wide-RFI at 50/25bb
> first. The depth charts' `vs_*` flats are unaffected (byte-identical between
> the tight and wide 100bb charts).

## How a table is selected at decision time

`tiered_bot_controller._select_preflop_table(num_seated, effective_stack_bb)`
and the push/fold gate decide which chart serves a given spot:

- **Preflop, heads-up** (`num_seated == 2`): `preflop_100bb_hu.json`.
- **Preflop, 6-max/multiway, archetype WITH a width-tier chart** (loose / station
  / weak / tight): that **width-tier chart wins at every depth** — it does *not*
  fall through to the depth charts. The archetype's looseness is its identity, so
  a fish/maniac must not collapse to the standard depth chart at the shallow
  casino buy-in (~40bb). The mapping lives in `ARCHETYPE_WIDTH_TABLE`
  (`poker/strategy/deviation_profiles.py:238`): `nit`/`rock` → `tight_rfi`,
  `calling_station` → `station`, `lag` → `loose_mid`, `maniac` → `loose`,
  `weak_fish` → `weak_station` (plus isolation/validation variants); `tag`/baseline
  map to `None`. Selection: `_select_preflop_table`
  (`poker/tiered_bot_controller.py:2491`), which returns the width table directly
  when `_archetype_base_table()` resolves to a non-`'6max'` label.
- **Preflop, 6-max/multiway, archetype WITHOUT a width chart** (tag / baseline —
  the depth-aware competent bot): the depth chart nearest the effective stack —
  `preflop_100bb_6max.json` (≈100bb), `preflop_50bb_6max.json` (≈50bb), or
  `preflop_25bb_6max.json` (≈25bb), via `nearest_depth_bucket`.
- **Short stacks ≤ 15bb** (`PUSH_FOLD_THRESHOLD_BB`, `poker/strategy/push_fold.py`):
  `push_fold_hu.json` overrides via `lookup_push_fold_action`. **HU only today** —
  a 6-max push/fold table is future scope (`docs/plans/PUSH_FOLD_6MAX_SCOPE.md`).
- **Postflop** (all stack depths, 6-max and HU — there is no separate HU postflop
  chart): the single `postflop_strategies.json`, looked up via
  `lookup_postflop_with_fallback`. Only `(SRP, high)` is authored; every other
  SPR / pot_type rides the **degrade ladder** (`spr` low→high, `pot_type`
  3BP→SRP) plus the `postflop_commit` layer for genuinely-short SPR.

## Cut-from-play postflop slices (retained as eval/attribution harness)

The generated postflop *precision slices* (`postflop_strategies_low_spr.json`,
`postflop_strategies_3bp.json`) and their generators are **present in the repo on
`development`** but **not loaded by the live table** — they were cut from *play*
(the SPR/pot_type fallback ladder serves those nodes), and re-added as an
eval/attribution harness, not removed from the tree.

History:

1. **Generated + shipped** (2026-05-25): `postflop_strategies_low_spr.json`
   (`c5aa0d07`) and `postflop_strategies_3bp.json` (`4be11e93`).
2. **Cut from play** (`0164ce64`, 2026-05-26) after the hardened SNG
   champion-challenger gate measured them **neutral** (no win-rate benefit vs the
   bot itself — combined `slices` 49.2% [47.9, 50.5] @ 2,000 SNGs). See
   `docs/plans/SNG_RUNNER_HARDENING.md` and the `eval-harness-audit` memory note.
3. **Re-added to the repo** (`dd098d13`, "restore cut postflop slices +
   attribution harness"): the slice JSONs and their generators
   (`generate_postflop_spr.py`, `generate_postflop_3bp.py`) live again under
   `poker/strategy/data/`. They are **not wired into `lookup_postflop_with_fallback`
   for live play** — the live postflop lookup still rides the single
   `postflop_strategies.json` plus the degrade ladder (see "How a table is
   selected" above). The slices are retained for attribution/eval experiments
   that want to measure the contribution of an explicit slice vs the fallback.

Current state of the files (verified 2026-06-03):

| File | Entries | Status |
|---|---:|---|
| `postflop_strategies_low_spr.json` | 2,160 | In repo; cut from play, eval/attribution harness |
| `postflop_strategies_3bp.json` | 4,320 | In repo; cut from play, eval/attribution harness |
| `generate_postflop_spr.py` / `generate_postflop_3bp.py` | — | In repo (`poker/strategy/data/`) |

The 3BP *classification* (`preflop_raise_count` → `pot_type`) remains live; in
play it resolves to `postflop_strategies.json` via the 3BP→SRP fallback rather
than the dedicated slice.

## Build artifacts, config & generators (not shipped charts)

These files live under `poker/strategy/data/` but are **not** the shipped
runtime charts catalogued above — they are generators, build-time intermediates,
source fragments, or tuned config. They had no provenance row before 2026-06-03;
listed here so the directory is fully accounted for.

| File | Kind | Loaded at runtime? | Source / role |
|---|---|---|---|
| `phase_7_5_config.yaml` | Tuned config (YAML) | **Yes** — via `phase_7_5_config.py` | Single source of truth for the three-tier exploitation clamp caps (`default/medium/extreme_max_total_shift` = 0.4/0.6/0.8), `should_apply_bluff_catch_override` sizing/dampener thresholds, tier ratchet, and benchmark prior. Manually authored; the in-file header still says "PLACEHOLDERS for v1 ship" pending the Step 0.5 calibration sweep. Semantics: `docs/plans/PHASE_7_5_ADJUSTMENT_LAYER_WIDENING.md`. The bluff-catch override that consumes it is mapped in [`POSTFLOP_OVERRIDES.md`](POSTFLOP_OVERRIDES.md). |
| `push_fold_equity_matrix.json` | Build-time cache | No (build only) | Cached eval7 all-in equity matrix built/loaded by `generate_push_fold_nash.py` (`load_or_build_matrix`, `MATRIX_ITERS`/`MATRIX_SEED`) so the deterministic Nash fixed-point iteration that produces `push_fold_hu.json` is fast and reproducible. An intermediate of the push/fold generator, not a chart. |
| `preflop_100bb_6max_wider_rfi.json` | Source fragment | No | The CO/BTN/SB widened-RFI fragment (`meta` + `rfi`/`vs_open`/`vs_3bet`/`vs_4bet` blocks) produced by `experiments/build_wider_rfi_chart.py` (2026-05-27, `4f5fb311`). Its RFI rows were merged **into** the shipped `preflop_100bb_6max.json`; this standalone file is the build artifact/source, not a separately-loaded chart. |
| `generate_depth_charts.py` | Generator | No | Produces `preflop_50bb_6max.json` / `preflop_25bb_6max.json` from the 100bb chart. See `depth_charts_README.md` and the depth-chart footgun note above. |
| `generate_postflop_spr.py` | Generator | No | Produces the cut-from-play `postflop_strategies_low_spr.json` (see above). |
| `generate_postflop_3bp.py` | Generator | No | Produces the cut-from-play `postflop_strategies_3bp.json` (see above). |
| `generate_push_fold_nash.py` | Generator | No | Computes `push_fold_hu.json` (chip-EV HU Nash, fictitious play) using the cached equity matrix above. |
| `generate_push_fold_hu.py` | Generator | No | Earlier push/fold HU chart builder (superseded by the Nash recompute, `66586d76`). |
| `generate_hu_chart.py` | Generator | No | HU preflop chart builder for `preflop_100bb_hu.json`. |

`build_wider_rfi_chart.py` lives in `experiments/`, not `poker/strategy/data/`,
and is referenced from the `preflop_100bb_6max` row and README calibration log.

## Keeping this current

When a chart is added, regenerated, or retired:
1. Update/author its `*_README.md` (the detailed source-of-truth) and its
   calibration log.
2. Update the relevant table above (entries, commit, method) and bump
   `last_updated`.
3. If it's a generated chart, point at the generator + its rule doc; never
   hand-edit generated JSON.
