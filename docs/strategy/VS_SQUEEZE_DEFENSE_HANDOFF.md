---
purpose: Handoff for the vs_squeeze over-fold gap — measured diagnosis + step-1 EV measurement (real but opponent-dependent leak) + the recommended instrument
type: guide
created: 2026-06-12
last_updated: 2026-06-12
---

# vs_squeeze defense — handoff

The chart-opportunity census's **#2 gap**: the hero "auto-folds to a squeeze,
concentrated vs maniac fields." This doc is the **measured** diagnosis (done — the
spot was instrumented before any build, per
[[feedback_measure_spot_before_building]]) and the initial plan. **Nothing is built
yet** — the next step is an EV measurement, not a chart.

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

## Step 1 — MEASURED (2026-06-12): the over-fold is a real leak, but opponent-dependent

Done with `experiments/vs_squeeze_ev_probe.py` (the per-decision EV instrument the plan
called for, not a chart). It records every BB/SB `vs_squeeze` spot the sharp hero
reaches in the handoff field (`Maniac, Maniac, LAG, Rock, Rock`, 4000 hands, seeds
7/107 → **1235 blind spots**) with its actual hole cards + the pot it's folding into,
then prices fold (EV 0) vs the best **call** line vs a *sweep* of squeezer widths. EV
of a call = `eq·(pot+cost) − cost` in bb, `eq` = hero all-in equity vs the squeezer's
continue range (eval7 MC), with an **OOP equity-realization haircut** `r∈{1.0, 0.7}`
(the blind can't realize raw equity, so the `r=0.7` rows are the trustworthy ones).
Leak = `Σ max(0, EV_call)` over spots → bb/100.

Coverage reproduced exactly: **BB 100% miss, SB 95%, BTN 75%, CO 66%, HJ 10%.** The
spot is **6.8% of hero preflop decisions** (even MORE common than the diagnosis's 3.3%
— "not rare" understated). Mean pot folded = **12bb**, cost to call ~7bb (37% pot odds).

**Leak by squeezer width** (`r=0.7` = realistic OOP / `r=1.0` = raw all-in equity):

| squeezer width | defend% (r0.7) | **leak bb/100 (r0.7)** | leak bb/100 (r1.0) |
|---|---|---|---|
| tight_value ~3% | 1% | **0.85** | 3.6 |
| standard_3bet ~5% | 3% | **1.18** | 5.8 |
| wide ~22% | 9% | **3.79** | 25.1 |
| maniac ~35% | 12% | **5.26** | 35.3 |

**Verdict:** folding ~everything is *roughly correct vs a tight/standard squeezer*
(MDF is high OOP; only AA/KK/QQ are clear continues — the null hypothesis holds there,
≤1.2 bb/100). The leak is **real and large only vs a WIDE/maniac squeeze** (3.8–5.3
bb/100 after the OOP haircut; 9–12% of the blind's range becomes a +EV defend). So the
leak is **opponent-dependent**, exactly the shape the plan's "optional exploitation
widen" anticipated — *not* a uniformly-mispriced static fold.

Caveats baked into the number: HU-vs-squeezer (multiway makes calling worse, so this is
an upper bound); call is the floor (a 4-bet/jam could beat it for the very top, so it
under-states premiums); the printed "defend range" is the subset of *dealt* hands that
priced +EV (premiums dominate vs tight; marginal stragglers are MC/blocker noise that
the `r=0.7` haircut filters out), not a clean derived range.

**Recommended instrument (the EV's call):**
- **High value — exploitation widen vs a read-wide squeezer** (same detect/exploit
  shape as `limp_exploit` / `vs3bet_exploit`): this is where the 3.8–5.3 bb/100 lives.
  Defend the blinds wider as the squeezer's 3-bet/squeeze frequency reads high.
- **Cheap correctness floor — a tiny static value-continue** so the blinds stop folding
  **AA/KK/QQ (+AK)** to *any* squeeze (a better degrade than conservative-fold). Small
  bb/100, but folding the nuts to a squeeze is an obvious correctness bug worth closing
  regardless of opponent.
- A wide *static always-on* blind defense is **not** justified — it would only pay off
  vs wide squeezers, where the read-gated widen captures it without over-defending vs
  tight ones.

### Remaining plan

2. **Build the instrument(s) above** —
   - **A blind squeeze-defense range** — either a real `vs_squeeze` node set for
     BB/SB (extend the generator; note the BB has no cold-call range, so it'd be a
     blind-defense-vs-two-raises range, not a capped cold-call range), OR a better
     **degrade** than conservative-fold (e.g. route a blind squeeze-miss to a tight
     value-continue range) so the blinds defend the top ~20-30% instead of 0%.
   - **Optional exploitation widen** vs a read-wide squeezer (the same detect/exploit
     shape as `limp_exploit` / `vs3bet_exploit`): defend the blinds wider when the
     squeezer's 3-bet frequency reads high (maniac), tighter vs a tight squeezer.
3. **Gate off, validate, flip on** — the established pattern.

## Gotchas / open questions

- **Is it even a leak?** ANSWERED (step 1 above): yes, but opponent-dependent — ~0
  vs a tight/standard squeeze (folding is correct, MDF high OOP), 3.8–5.3 bb/100 vs a
  wide/maniac squeeze. Plus a cheap correctness floor: stop folding AA/KK/QQ to *any*
  squeeze.
- **The BB has no cold-call range**, so this isn't "extend the per-opener squeeze
  chart" — it's a new *blind-defense-vs-squeeze* concept (closer to a vs_3bet defense
  from the blinds than to the cold-caller squeeze chart).
- **Classifier vs chart mismatch:** the classifier calls the blind spot `vs_squeeze`,
  but semantically it's "a blind facing an open + a 3-bet." Decide whether to (a)
  give `vs_squeeze` BB/SB nodes, or (b) reclassify the blind-didn't-cold-call case.
- **eval instrument:** rule-bot squeezers (maniacs) squeeze wide — good for exposing
  the gap. But a believable-field EV read may need a `Jeff_clone`-style squeezer (cf.
  the reshove "no foldy openers in sim" problem, [[project_push_fold_6max_validation]]).

## Reproduction

Coverage diagnosis **and** the step-1 EV measurement now live in one committed probe,
`experiments/vs_squeeze_ev_probe.py` (wraps `TieredBotController._get_ai_decision`,
reads `_last_pipeline_snapshot` node_key/source/pot/cost, prices fold vs call over a
squeezer-width sweep):

```bash
docker compose exec -T backend python3 -m experiments.vs_squeeze_ev_probe
# fast wiring check (1 seed, fewer hands):
docker compose exec -T -e QUICK=1 backend python3 -m experiments.vs_squeeze_ev_probe
# custom: HANDS=2000 SEEDS=7,107  (the run that produced the table above)
```
~1s/hand for the 6-max sharp sim — the 4000-hand run is ~67 min; run it backgrounded.
