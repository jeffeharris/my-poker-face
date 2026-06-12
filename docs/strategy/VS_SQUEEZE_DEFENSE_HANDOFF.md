---
purpose: vs_squeeze over-fold — measured diagnosis, step-1 per-player EV (real but small, opponent-dependent leak), and the BUILT floor + read-gated widen (gated off)
type: guide
created: 2026-06-12
last_updated: 2026-06-12
---

# vs_squeeze defense — handoff

The chart-opportunity census's **#2 gap**: the hero "auto-folds to a squeeze,
concentrated vs maniac fields." Worked end-to-end per
[[feedback_measure_spot_before_building]]: **measured first** (diagnosis + per-player EV)
→ **built** the floor + read-gated widen, **gated OFF**. The EV turned out **small per
player** (≤0.83 bb/100 only vs a maniac field), so the value-floor (stop folding AA/KK/QQ
to a squeeze) is the real win and the widen is opportunistic. Skip to **Step 1** / **Step
2** below for the numbers and what shipped.

## Goal

The blinds fold ~100% of the time when facing a squeeze, because they have **no
squeeze-defense range** — so they over-fold to a wide squeeze and leave the dead
money (open + 3-bet + blinds) on the table. Goal: give the blinds a sensible
squeeze-defense (so they defend the top of their range instead of 0%), **only if and
to the extent the EV measurement says it's a real leak**, with an optional
per-opponent widen vs a read-wide squeezer.

## Measured diagnosis (the data, not an assumption)

Probe: `TAG` hero, 5000 hands at 100bb vs a squeeze-heavy field
(`Maniac, Maniac, LAG, Rock, Rock`), instrumenting the hero's preflop
`_last_pipeline_snapshot` (`node_key`, `chart_lookup_source`, `base_strategy_probs`,
final action). Reproduction at the bottom.

- **Recurring spot:** `vs_squeeze` = **3.3%** of the hero's preflop decisions
  (232/7061) — comparable to `vs_3bet` (4.9%). NOT a rare edge case (unlike the
  limper's ~1%).
- **Concentrated in the blinds:** BB 108, SB 58, BTN 44, CO 15, HJ 7 — the blinds are
  **72%** of squeeze spots.
- **The chart MISSES the blinds.** `chart_lookup_source` by position:

  | pos | hit | miss | miss % |
  |---|---|---|---|
  | HJ | 7 | 0 | 0% |
  | CO | 14 | 1 | 7% |
  | BTN | 39 | 5 | 11% |
  | SB | 21 | 37 | **64%** |
  | BB | 0 | 108 | **100%** |

- **Over-fold:** the hero folds **89%** of all squeeze spots (base-strategy fold mean
  88%); on a miss it falls to the conservative default = `{fold: 1.0}`.

## Root cause

`build_vs3bet_defense.SQUEEZE_CALLERS = [HJ, CO, BTN, SB]` — the vs_squeeze chart
models a **cold-caller** (flatted an open) facing a 3-bet, keyed
`{caller}_vs_{opener}_vs_{squeezer}` (the per-opener key, #316). But the classifier
labels **any non-opener at two raises** as `vs_squeeze` (`preflop_classifier`), which
includes a **blind that never cold-called** — open + 3-bet folds to the BB/SB, who
faces a squeeze it didn't enter. There is no chart node for that (the BB isn't a
caller; the SB only has `SB_vs_{opener}_vs_BB` nodes, so it misses whenever the
squeezer isn't the BB or it didn't cold-call) → **miss → conservative-default fold.**
The maniac field is not the cause; it just makes squeezes frequent enough to expose
the blinds' total lack of coverage.

## Step 1 — MEASURED (2026-06-12): a real but SMALL, opponent-dependent leak

Done with `experiments/vs_squeeze_ev_probe.py` (the per-decision EV instrument the plan
called for, not a chart). It records every BB/SB `vs_squeeze` spot the sharp hero
reaches in the handoff field (`Maniac, Maniac, LAG, Rock, Rock`, 4000 hands, seeds
7/107) with its actual hole cards + the pot it's folding into, then prices fold (EV 0)
vs the best **call** line vs a *sweep* of squeezer widths. EV of a call =
`eq·(pot+cost) − cost` in bb, `eq` = hero all-in equity vs the squeezer's continue
range (eval7 MC), with an **OOP equity-realization haircut** `r∈{1.0, 0.7}` (the blind
can't realize raw equity, so the `r=0.7` rows are the trustworthy ones). Leak =
`Σ max(0, EV_call)` over the hero's spots ÷ hands × 100 → bb/100.

> **Correction (per-player vs per-table):** an earlier pass of this probe wrapped *all
> six* TieredBot seats (the whole field is tiered) and so summed 6 players' blind spots,
> reporting a per-TABLE leak (~5.3 bb/100 vs maniac) mislabeled as per-player bb/100.
> The 6-max harness attaches an opponent-model only to the hero seat, so filtering on
> that isolates the hero. The corrected **per-player** numbers below are ~6× smaller.
> (The hero-only frequency — 4.6% of decisions — also lines up with the diagnosis's
> 3.3%, vs the 41.8% the all-seat pass produced.)

Coverage reproduced exactly: **BB 100% miss, SB 70%, BTN 7%, CO/HJ 0%** (hero-only;
123 blind spots). Mean pot folded = **12bb**, cost to call ~7bb (35% pot odds).

**Per-player leak by squeezer width** (`r=0.7` = realistic OOP / `r=1.0` = raw equity):

| squeezer width | defend% (r0.7) | **leak bb/100 (r0.7)** | leak bb/100 (r1.0) |
|---|---|---|---|
| tight_value ~3% | 1% | **0.01** | 0.34 |
| standard_3bet ~5% | 3% | **0.06** | 0.69 |
| wide ~22% | 12% | **0.58** | 3.05 |
| maniac ~35% | 15% | **0.83** | 4.26 |

**Verdict:** folding ~everything is **correct vs a tight/standard squeezer** (MDF is
high OOP — the null hypothesis holds, ≤0.06 bb/100; only AA/KK/QQ are clear continues).
The leak is **real but SMALL even vs a wide/maniac squeeze** (0.58–0.83 bb/100 after the
OOP haircut; 12–15% of the blind's range becomes a +EV defend). It IS opponent-dependent
(grows with width), exactly the shape the plan's "optional exploitation widen"
anticipated — but the per-player magnitude is marginal, so this is a small opportunistic
edge, NOT a big leak.

Caveats baked into the number: HU-vs-squeezer (multiway makes calling worse, so this is
an upper bound); call is the floor (a 4-bet/jam could beat it for the very top, so it
under-states premiums); the printed "defend range" is the subset of *dealt* hands that
priced +EV (premiums dominate vs tight; marginal stragglers are MC/blocker noise the
`r=0.7` haircut filters out), not a clean derived range. The sim "maniacs" are tiered
bots that squeeze tighter than their config (observed VPIP ~0.40, not 0.56), so the
field never exercises the genuinely-wide squeeze a live maniac would — the eval-instrument
limitation the handoff flagged (a real read may need a `Jeff_clone`-style squeezer).

**Recommended instrument (the EV's call):**
- **Cheap correctness FLOOR — a value-continue** so the blinds stop folding **AA/KK/QQ
  (+AK)** to *any* squeeze (a better degrade than conservative-fold). Tiny bb/100, but
  folding the nuts to a squeeze is an obvious correctness bug worth closing regardless
  of opponent. **This is the main justification.**
- **Read-gated WIDEN vs a wide squeezer** (same detect/exploit shape as `limp_exploit`):
  widens the defense as the squeezer's VPIP reads wider. Adds the ≤0.83 bb/100 vs wide
  fields — marginal, opportunistic, and only as good as the read.
- A wide *static always-on* blind defense is **not** justified — it would only pay off
  vs wide squeezers, where the read-gated widen captures it without over-defending vs
  tight ones (where flat-calling OOP realizes poorly = −EV).

## Step 2 — BUILT (2026-06-12, gated OFF): the floor + read-gated widen

Implemented as `TieredBotController._apply_vs_squeeze_defense`, a third preflop modifier
chained after `_apply_limp_exploit` (mirrors that detect/exploit shape exactly). Fires
ONLY on the conservative-fold case we measured: flag on, knob>0, `scenario==vs_squeeze`,
hero in BB/SB, and `chart_lookup_source ∈ {miss, masked_out}` (never stomps a real
squeeze node). Behavior:
- **Value FLOOR (tier 0: AA/KK/QQ/AKs/AKo)** — continues (flat-call) vs *any* squeeze.
- **Read-gated WIDEN** — `_squeezer_width_read` reads the last raiser's (largest live
  bet) `vpip_per_voluntary_opportunity` off the hero's opponent model; deeper
  `SQUEEZE_DEFENSE_TIERS` unlock as VPIP crosses `_SQUEEZE_WIDTH_BANDS` (0.28/0.38/0.50),
  scaled by the skill-graded `vs_squeeze_defense` knob (shark 0.85 … rec 0.0). No read →
  floor only.

Where: flag `VS_SQUEEZE_DEFENSE_ENABLED` (EXPERIMENTAL, off dev+prod, db-overridable,
`core/feature_flags.py`); knob on `SkillTier.vs_squeeze_defense` (`skill_tiers.py`) +
`_resolve_vs_squeeze_defense` + `__init__` wiring; constants/tiers/methods in
`tiered_bot_controller.py`. Off-path is byte-identical (the conservative-fold).

**Validation:**
- Unit: `tests/test_strategy/test_vs_squeeze_defense.py` (16 tests — floor/widen/knob/
  flag/position/chart-miss/fold-base gates + the last-raiser read). All green.
- Behavioral (`experiments/vs_squeeze_defense_validate.py`, OFF vs ON, hero-only): OFF
  blind-squeeze continue **0%** → ON **continues the value floor 100%** (AA/KK/QQ/AK no
  longer folded). Widen engages only modestly because the sim's tiered "maniacs" read
  VPIP ~0.40, not the genuinely-wide squeeze the bands' deep tiers target — consistent
  with the small per-player EV and the eval-instrument caveat above.

**Status: gated OFF (EXPERIMENTAL).** Given the corrected per-player EV is small
(≤0.83 bb/100 only vs a maniac field, ~0 in normal fields), the floor is the clear win
and the widen is opportunistic. Flip decision (and whether to curate the knob onto
specific sharks like `push_fold_nash`, vs leave it skill-graded) is open — see below.

## Gotchas / open questions

- **Worth flipping on?** The per-player leak is small (floor closes an obvious AA/KK/QQ
  bug; widen adds ≤0.83 bb/100 only vs wide fields). It ships dormant. Decide: flip the
  flag for the whole tiered field, curate the knob onto a few sharks, or leave parked.
- **4-bet the top?** The floor/widen flat-CALLS (matches what the probe priced). Value
  hands (AA/KK) 4-betting OOP would be higher-EV and more natural — a future enhancement.
- **The BB has no cold-call range**, so this is a *blind-defense-vs-squeeze* concept
  (closer to a vs_3bet defense from the blinds than to the cold-caller squeeze chart) —
  hence the degrade-style modifier rather than extending the per-opener squeeze chart.
- **eval instrument:** the tiered "maniacs" squeeze tighter than a live maniac (VPIP
  ~0.40), so the sim under-exercises the widen's deep tiers. A believable-field read may
  need a `Jeff_clone`-style wide squeezer (cf. the reshove "no foldy openers in sim"
  problem, [[project_push_fold_6max_validation]]).

## Reproduction

Two committed probes (both wrap `TieredBotController._get_ai_decision`, read
`_last_pipeline_snapshot`, hero-only via the opponent-model filter; ~1s/hand, run
backgrounded):

```bash
# Step-1 EV: coverage + per-player leak by squeezer width (the tables above)
docker compose exec -T -e HANDS=2000 -e SEEDS=7,107 backend \
    python3 -m experiments.vs_squeeze_ev_probe          # ~67 min
docker compose exec -T -e QUICK=1 backend python3 -m experiments.vs_squeeze_ev_probe  # fast

# Step-2 behavioral: does the feature FIRE + widen (OFF vs ON, forces flag + shark knob)
docker compose exec -T backend python3 -m experiments.vs_squeeze_defense_validate     # ~50 min
```
Unit tests: `docker compose exec -T backend python -m pytest tests/test_strategy/test_vs_squeeze_defense.py`.
