---
purpose: Handoff for the per-node preflop chart regeneration — state, decisions, how to continue
type: guide
created: 2026-06-11
last_updated: 2026-06-11
---

# Preflop Chart Regen — Handoff

Pick-up doc for the preflop-chart regeneration effort. Read this top-to-bottom
before touching anything; the work spans the base charts, a new lint module, the
depth transforms, and the archetype layer, and several decisions are load-bearing.

## TL;DR

We replaced the **position-invariant** `vs_open` / `vs_3bet` / `vs_4bet` ranges
(the root cause of "AI jams 47o/89o into a 4-bet all-in" and the BB overfold) with
**per-node generators** that read each prior branch as a self-consistent villain
model. A new executable lint module (`poker/strategy/lints.py`) gates them. The
regen made the base **tighter/concentrated**, which starved the **archetype**
transforms (they amplify existing mass) — so the archetype layer was re-tuned
(masked promote-to-raise + invent-call) and a few target bands re-baselined.

**State:** code is on branch `feat/preflop-chart-generators`. Two commits landed
(generators+lints, depth fix). The **regenerated chart DATA + archetype-layer
tuning are NOT committed yet** — held pending the final archetype believability
probe, which now passes (only 2 deprioritized fails). The next action is to
**commit the data** (see §Commit plan), then play-test.

## Branch & commit state

Branch: `feat/preflop-chart-generators` (cut from a clean base; do NOT rebase onto
the stale chart commit without re-checking).

Committed:
- `38f041e3` feat(strategy): per-node generators (`build_vs_open` / `build_vs3bet_defense`
  / `build_vs4bet_defense`) + `lints.py` + per-generator tests + `docs/strategy/` specs.
- `10a20594` fix(strategy): 25bb BB-defense floor at depth (`generate_depth_charts`
  `is_bb` jam) + `lint_depth_bb_defense` + reconciled gradient tests.

**Uncommitted (the pending data commit):**
- 10 chart JSONs: `preflop_100bb_6max.json`, `preflop_50bb_6max.json`,
  `preflop_25bb_6max.json`, and the 7 `preflop_100bb_6max_*.json` archetype variants.
- `experiments/build_archetype_charts.py` — promote-to-raise + invent-call mask.
- `poker/archetype_targets.py` — re-baselined TAG/LAG/maniac 3-bet bands.
- `poker/strategy/data/build_vs4bet_defense.py` — Ax bluff-pool reservation (review #3).
- `poker/strategy/data/build_vs_open.py` — stale-messaging fix (review #5).

**NOT ours — leave them out of any commit:** `experiments/simulate_bb100.py` (modified)
and `experiments/preflop_aggression_report.py` (untracked) are a pre-existing WIP
diagnostic pair from someone else; they were already in the working tree.

## Validation state (as of handoff)

- `python -m poker.strategy.lints` → **0 failures** (against the on-disk regenerated charts).
- `pytest tests/test_strategy/` → **1706 passed**.
- Archetype believability probe (9000 hands, mixed field): **61 in-band, 21 WARN, 2 FAIL**.
  The 2 fails are deprioritized (see §Remaining fails).

## The pipeline (strict order, each step gated + stale-guarded)

```
build_vs_open  →  build_vs3bet_defense  →  build_vs4bet_defense
   →  generate_depth_charts  →  build_archetype_charts  →  build_wider_rfi_chart
```
Run inside the backend container:
```
docker compose exec -T backend python -m poker.strategy.data.build_vs_open
docker compose exec -T backend python -m poker.strategy.data.build_vs3bet_defense
docker compose exec -T backend python -m poker.strategy.data.build_vs4bet_defense
docker compose exec -T backend python -m poker.strategy.data.generate_depth_charts
docker compose exec -T backend python -m experiments.build_archetype_charts
docker compose exec -T backend python -m experiments.build_wider_rfi_chart
```
Each generator **writes the chart in place** and **refuses to run against a stale
upstream** (e.g. `build_vs3bet` checks `vs_open`'s BB-defend floors). To inspect
without writing: `build_vs_open --diff` (read-only old→new per-node report).

## How each generator works (and the per-branch wrinkles)

- **`build_vs_open.py`** (§3 of `PREFLOP_DEFENSE_REGEN_SPEC.md`): BB defense
  MDF-anchored to the lint floors (fixes the ~45% overfold); non-BB cold-defense
  keeps its width but the 3-bet is **opener-keyed** + **merged-vs-polarized by
  opener width** (wide opens → merged value top, tight opens → polarized
  suited-wheel bluffs); weights are **bimodal** (value 0.85 / bluff 0.35) so they
  straddle the depth value/bluff cliff cleanly.
- **`build_vs3bet_defense.py`** (§2): per-node; villain 3-bet range read from
  `vs_open[villain_vs_hero].raise_3x`; MDF anchor with the **taper floored at MDF**
  (the raw taper over-folded into exploitability); 4-bet bluffs carry `fold ≥ 0.50`
  (the depth gate); **non-open hands get a thin-call junk floor** so a cold-caller
  facing a squeeze isn't pure-folded (the classifier routes squeezes here by
  `raises==2`, blind to opener-vs-caller).
- **`build_vs4bet_defense.py`** (§4): per-node; hero's 3-bet range (`vs_open`) is
  the live range, villain 4-bet range from `vs_3bet`; jam-dominant with suited-Ax
  5-bet bluffs (**reserved from the value pass**); **junk is PURE-FOLD and that is
  LOAD-BEARING** — `build_archetype_charts` and `generate_depth_charts.t_vs_4bet`
  skip `fold>=0.999`, so a thin call here would reopen the trash-jam bug.

## Lints — `poker/strategy/lints.py`

Dependency-free (pure JSON, no eval7) so it runs in CI in ms. `python -m
poker.strategy.lints` prints a PASS/FAIL report. The generators import the shared
guards (write-time refusal) so write-time and CI can't diverge. Covers: weights-sum,
legal-vocab, completeness, **anti-clone (cross-opener only** — same-opener nodes may
legitimately share, the charts are opener-keyed), BB-defend floors, fold-to-3bet /
fold-to-4bet ceilings, 4-bet band, cliff band (vs_open only — `t_vs_3bet` uses a
**fold-gate**, not a raise cliff), depth RFI passthrough / flat retention (50bb) /
BB-defense floors.

## Load-bearing design decisions (do NOT undo without re-reading why)

1. **Base stays concentrated.** Do NOT put broad raise-spray back into the base to
   feed archetypes — `build_vs3bet_defense` reads any `raise_3x > 0` as the field's
   3-bet range, so a base seed pollutes the villain model. Archetype looseness lives
   in the archetype layer, never the base.
2. **`vs_4bet` junk is pure-fold** (load-bearing mask). `vs_3bet`/`vs_open` flats get
   a thin call (station mask); `vs_4bet` must not (or archetype widening reopens the
   trash-jam). Opposite policies on purpose.
3. **`t_vs_3bet` gates on FOLD weight, not raise.** Only `vs_open` has the
   implicit-API raise cliff (`DEPTH_INTENT_TAG_TECHDEBT.md`). The lint and the
   tech-debt doc were corrected to reflect this.
4. **Bands follow the chart, not vice-versa.** The old TAG/LAG/maniac 3-bet bands
   encoded the removed raise-spray; they were re-baselined down (TAG 7–11, LAG 15–22,
   maniac 22–30). See the comments in `archetype_targets.py`.

## The archetype layer (the second half of the effort)

The base concentration starved the archetype transforms (`_loosen_facing` /
`_station_facing` only amplify *existing* mass; "hands the base pure-folds stay
folded"). Three mechanisms were added in `build_archetype_charts.py`:
- **`_promote_3bet`** (loose tiers, `vs_open`): promote a curated suited/pair/playable
  pool's call mass into 3-bets — masked (`fold>=0.999` untouched), larger for maniac
  (1.0) than LAG (0.45). Restores LAG/maniac 3-bet without base pollution.
- **`_invent_call`** (station/fish + loose-tier `vs_3bet`): invent calls on a wide
  curated pool **overriding the pure-fold mask** — the station's wide call range is
  its identity, independent of the concentrated base. Restores station/fish VPIP and
  cuts loose-tier fold-to-3bet. Calls only, never raises.
- All threaded through `_transform_facing` via `promote_3bet_by_scenario` /
  `invent_call_by_scenario`.

Current knob values (tuned against the probe): maniac promote `vs_open`=1.0 +
`keep_fold` `vs_open`=0.30 + invent `vs_3bet`=0.45; LAG promote=0.45 + invent
`vs_3bet`=0.35; station invent `{vs_open:0.90, vs_3bet:0.42}`; weak_fish invent
`{vs_open:0.78}` + `vs_3bet` keep_fold 0.62.

## Remaining fails (2, both deprioritized by the chart owner)

- **nit VPIP 20.2** (band 10–16) and **rock fold-to-3bet 52.1** (band 65–85). These
  are the *inverse* of the station problem: the tight tiers inherit the base's now-
  **wider** BB defense (the correct BB-overfold fix), so they're a touch too loose
  facing aggression. The owner said don't prioritize unless nit/rock distinctness is
  gone. The clean fix is a **symmetric `_tighten_facing`** (route call→fold on a
  narrow nit-keep pool) applied to `build_tight` — left as a follow-up.

## Open items / next steps (priority order)

1. **Commit the chart data** (see §Commit plan) — the regen is shippable.
2. **Play-test** — the real believability check the probe only approximates.
3. **`_tighten_facing` for nit/rock** — clears the last 2 fails (optional).
4. Tracked code follow-ups (all noted in-code):
   - Proper **squeeze node** in `vs_3bet` (the classifier conflates opener-faces-3bet
     with caller-faces-squeeze; the thin-call floor is the interim).
   - **Bluff-frequency-aware taper** in `vs_3bet` (currently MDF-floored, nearly inert OOP).
   - Hoist `_playability` / `_norm` / bluff pools to a shared `_chart_gen.py` (now rule-of-three).
   - Migrate the explicit **`intent: value|bluff` tag** (`DEPTH_INTENT_TAG_TECHDEBT.md`)
     to retire the `vs_open` weight-cliff implicit API.

## Commit plan

Stage explicitly (NOT the two stray files):
```
git add poker/strategy/data/preflop_*6max*.json \
        experiments/build_archetype_charts.py poker/archetype_targets.py \
        poker/strategy/data/build_vs4bet_defense.py poker/strategy/data/build_vs_open.py
```
Suggested message: `feat(strategy): regenerate preflop charts via per-node pipeline +
archetype re-tune` — body: position-invariance/trash-4bet/BB-overfold fixed; base
concentrated; archetype looseness via promote-to-raise + invent-call mask; TAG/LAG/
maniac 3-bet bands re-baselined; probe 61 in-band / 21 WARN / 2 deprioritized fails.

## Gotchas

- **The committed chart JSONs are STALE** (the old position-invariant ones) until the
  data commit lands — so a fresh checkout has generators that disagree with the data;
  `lints` will show ~53 failures. That's expected; regenerate or commit the data.
- **Probe is slow** (~10 min at 9000 hands; `PROBE_HANDS` env to scale). `fold-to-3bet`
  / `4bet` stats have small n (rare spots) even at 9000 — treat single-stat deltas
  under a few pp as noise; trust VPIP/3-bet (n in the thousands).
- Test-summary lines get buried in PromptManager/eval7 log noise — `grep -oE "[0-9]+
  passed"` to extract.
- Pyright "import unresolved" / "splat arg" warnings on these files are **host-only**
  resolution noise; everything runs in the backend container.
- The reviews that shaped this (external agents) are reflected in the specs and the
  in-code comments; the key finding history is the captain's-log convention if one is kept.
