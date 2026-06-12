---
purpose: Handoff for the vs_squeeze over-fold gap — the measured diagnosis and the data-grounded initial plan (measure the EV before building a defense range)
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

## Initial plan (measure → choose instrument → build → validate)

1. **Measure the EV of the over-fold FIRST.** Is folding the BB's (and SB's) whole
   range to a squeeze actually leaking bb/100, and how much — especially vs a *wide*
   maniac squeeze where there's lots of dead money and the squeeze range is weak? A
   blind facing two raises OOP with a random hand *should* fold a lot (MDF is high),
   so 89% may be close to correct vs a tight squeeze and a real leak only vs a wide
   one. Quantify before building. (Per-decision EV / fold-equity is likely the right
   instrument again, since 3.3% × a few bb is a small aggregate vs bb/100 noise.)
2. **Then pick the instrument the EV justifies:**
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

- **Is it even a leak?** The blinds folding most of a random range to two raises is
  not obviously wrong. Step 1 must distinguish "correct tightness" from "exploitable
  over-fold," ideally split by squeezer width (tight vs wide).
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

```python
# hero preflop scenario mix + vs_squeeze chart hit/miss by position
docker compose exec -T backend python -c "...wrap TieredBotController._get_ai_decision,
read _last_pipeline_snapshot['node_key']/'chart_lookup_source'; field =
['Maniac','Maniac','LAG','Rock','Rock'], 5000 hands, seed 7..."
```
(The full one-liner used for this diagnosis is in the session transcript; re-derive
from the snapshot keys above.)
