---
purpose: Test whether opponent-adaptive late-position RFI width (widen vs folders, tighten/hold vs stations) via adaptive preflop-table selection beats the static tight chart vs the opponent spectrum
type: experiment
status: planned
hypothesis_summary: <ONE-LINE TESTABLE CLAIM — to be committed by the user before any run>
created: 2026-05-27
last_updated: 2026-05-27
---

# Experiment 003 — Opponent-Adaptive Preflop RFI Width

> **Why this exists:** **UPDATE 2026-05-27 — static late-position widening
> SHIPPED.** A steal-aware A/B (`ab_preflop_width.py`, 24k hands) found wider
> CO/BTN/SB RFI is CI-clear +EV vs both a station (jeff, +15.97 bb/100) and a
> disciplined reg (punisher, +5.33), overturning the prior SNG-gate "keep tight"
> (that gate is steal-blind). So the **new baseline is the wide chart** — the
> "widen vs folders" half is already live.
>
> What remains is the **adaptive** refinement: does keying open width to the
> opponent read beat the *static wide* baseline? The static-width data shows
> even a station (jeff) rewarded wider opens — but jeff over-folds to c-bets
> (ftc .45), masking the postflop cost. A **pure never-fold station** would
> punish wide opens (they call, the bot's weak postflop bleeds). So the live
> question is narrower than before: (a) **tighten vs a detected pure station**
> (switch to the preserved `preflop_100bb_6max_tight_rfi.json`) — does that beat
> static-wide vs a calling station? and (b) **go even wider vs a detected pure
> folder** — marginal upside on top of the shipped wide.
>
> Pre-work this session established three load-bearing facts:
> 1. **Logit offsets cannot implement "widen vs folders."**
>    `apply_exploitation_offsets` masks out any action with `prob == 0`
>    (`exploitation.py:1697`) and preserves zeros — empirically a `{fold:1.0}`
>    hand stays `{fold:1.0}` under a large steal offset; only hands already
>    opened *sometimes* shift (0.5→0.7). GTO width vs a folder requires opening
>    hands the tight chart folds 100%, which offsets structurally cannot do.
>    ⇒ The implementation must be **adaptive _table selection_** (swap to the
>    wider chart when folders are detected), reusing the validated
>    `_select_preflop_table` / `hero_table` mechanism — NOT stronger offsets.
> 2. **The "tighten vs station" half does not exist.** `steal_pressure` /
>    `tight_nit` already nudge "widen vs folders" preflop; `hyper_passive` /
>    `value_vs_station` are postflop-only. No preflop rule tightens opens vs a
>    detected calling station.
> 3. **`measure_passivity` (Baseline + null model) cannot measure this layer.**
>    Baseline has `anchors=None` → `_apply_exploitation` early-outs
>    (`tiered_bot_controller.py:1201`); there is no `opponent_model_manager`.
>    The adaptive layer must be measured with a non-Baseline archetype (TAG) +
>    a persistent opponent model.

## Hypothesis

> **TO BE COMMITTED BY THE USER.** Do not run anything until H1/H2/H3,
> thresholds, and the falsifier are filled in and ratified.

**H1 (primary):** <the main testable claim — adaptive RFI width beats static
tight, with quantitative bb/100 thresholds per backdrop>

- <sub-claim 1a: vs FOLDER backdrop, adaptive-ON ≥ +X bb/100 (paired vs OFF)>
- <sub-claim 1b: vs STATION backdrop, adaptive-ON ≥ 0 bb/100 (no harm)>
- <sub-claim 1c: vs BALANCED backdrop, adaptive-ON within ±Y bb/100 of OFF>

**H2 (secondary — precondition):** <the adaptive selection actually changes
the hero's open range in the predicted direction per backdrop — e.g. RFI freq
↑ vs folder, ↓/flat vs station>

**H3 (null-validating — the mechanism fires):** <the opponent archetype read
matures and flips the table within the session — i.e. the adaptive code path
is exercised, not silently defaulting to tight>

**Falsifier:** <what outcome on each axis says the hypothesis is wrong. Keep
honest: e.g. "adaptive-ON is CI-neutral vs the folder backdrop after N hands"
falsifies the widen-half; "adaptive-ON loses vs the station backdrop"
falsifies the tighten-half / no-harm claim.>

## What we're testing

The **single variable**: preflop RFI table is selected by the live opponent
read instead of being the static wide `preflop_100bb_6max.json` for all spots.

- ON arm (adaptive): when the players **left to act behind** (blinds / limpers
  — the defenders of a late open) read as a **pure calling station**, switch the
  CO/BTN/SB RFI lookup to the preserved tight chart
  (`preflop_100bb_6max_tight_rfi.json`); when they read as **folders**, keep the
  shipped wide chart (optionally go wider); default/unknown → shipped wide chart.
- OFF arm (twin): always the shipped static **wide** chart (the new baseline).

Everything else identical: same archetype (TAG), same backdrop, same shared
opponent model, same decks. Depth-bucket selection (50/25bb) is orthogonal and
unchanged.

## Setup

**Sandbox:** DB-free. Hero archetype = TAG (has `anchors` so the exploitation
path is live, unlike Baseline). Stacks reset per hand so the fixed backdrop
never busts and the cross-hand opponent model matures to full confidence
(`CONFIDENCE_RAMP_HANDS = 100`).

**Instrument:** `experiments/exploit_bb100.py` (paired exploit-ON hero +
exploit-OFF twin of the same archetype at one table, fixed exploitable
backdrop, persistent shared opponent model). Headline = paired per-hand edge
(ON − OFF) bb/100 + CI. *Not* the self-play SNG gate (symmetric → blind to
steal value) and *not* Baseline `measure_passivity` (skips exploitation).

**Sim config (template — finalize with the user):**

```bash
# FOLDER backdrop (widen-half):
docker compose exec -T backend python -m experiments.exploit_bb100 \
    --archetype TAG --backdrop FoldyBot,FoldyBot,GTO-Lite,GTO-Lite \
    --hands 40000 --seeds 42,142,242
# STATION backdrop (tighten-half / no-harm):
#   --backdrop CallStation,CallStation,FoldyBot,FoldyBot   (or per design)
# BALANCED control:
#   --backdrop GTO-Lite,GTO-Lite,ABCBot,ABCBot
```

**Wiring status / preconditions:** NOT YET BUILT. Requires:
1. The wider chart available as a selectable "vs-folder" table (validated this
   session; lives at `poker/strategy/data/preflop_100bb_6max_wider_rfi.json`).
2. Adaptive selection in/around `_select_preflop_table` keyed off the
   players-left-to-act read (reuse `OpponentSpot.can_act_behind` / `is_blind`
   from the `steal_pressure` spot machinery — NOT the table-wide aggregate).
3. An A/B toggle on the controller so `exploit_bb100`'s ON/OFF twins differ
   only in adaptive-vs-static selection.

**Output destination:** `docs/experiments/EXP_003_...` Results section + a
scratch log under `/tmp`.

## Measurements

**Primary metrics (H1):**
- Paired per-hand edge (ON − OFF) in bb/100 + CI, per backdrop.

**Secondary metrics (H2):**
- Hero RFI frequency by position (CO/BTN/SB) ON vs OFF, per backdrop — should
  rise vs folder, hold/fall vs station.

**Diagnostic metrics (H3 / context):**
- Fraction of late-position RFI decisions where the adaptive path selected the
  wider chart (vs defaulted to base); archetype-read maturity (hands_observed,
  detected pattern) over the session.

**Captured via:** `exploit_bb100.py` headline + a preflop/selection diagnostic
(extend the harness, or a spy like this session's `dbg_lookup.py`).

## Comparison data

| Run | Source | paired bb/100 | RFI freq Δ | adaptive-fire % |
|---|---|---|---|---|
| **static wide (OFF)** | this experiment's OFF twin | 0 (ref) | — | — |
| **adaptive vs folder** | TBD | TBD | TBD | TBD |
| **adaptive vs station** | TBD | TBD | TBD | TBD |
| **adaptive vs balanced** | TBD | TBD | TBD | TBD |

**Reference — static wide-vs-tight A/B (shipped 2026-05-27, `ab_preflop_width.py`, 24k hands paired):**

| Roster | paired bb/100 (wide − tight) | 95% CI |
|---|---|---|
| jeff (station, ftc .45) | +15.97 | [+10.35, +21.59] |
| punisher (disciplined reg) | +5.33 | [+1.45, +9.21] |

## Caveats / Known Confounders

1. **Detection lag.** The archetype read matures over ~15–100 hands
   (`MIN_HANDS_DEFAULT=15`, full at `CONFIDENCE_RAMP_HANDS=100`). Early hands
   default to the tight chart. With per-hand stack resets + a fixed backdrop
   the model matures, but the *transient* is non-adaptive — report mature-window
   edge separately if it matters.
2. **Multiway gating.** "Widen the open" should gate on the *defenders behind*
   being folders, not the whole table. Gating on the table-wide aggregate would
   misfire (e.g. a station in the field but folders in the blinds). Must use the
   players-left-to-act signal.
3. **Backdrop realism.** FoldyBot is a pure folder (upper bound on steal value);
   a punisher-reg folds correctly preflop but barrels postflop (punishes weak
   continues). Run both — a win vs FoldyBot that vanishes vs the reg is
   over-fit to a non-barreling folder.
4. **Instrument is steal-aware but coarse.** bb/100 needs large N (the static
   A/B needed ~24k hands for a ±5 CI); size the run so the CI can actually
   resolve the threshold in H1.
5. **Stack-reset artifact.** exploit_bb100 resets stacks per hand — good for
   model maturity, but it removes stack-depth dynamics; the edge is a 100bb
   steal/realization edge, not an SNG/ICM edge.
6. **Conditional on the static-width verdict.** If the static A/B vs the
   punisher-reg is positive, the widen-half is pre-justified; if neutral, the
   widen-half may need a purer folder (FoldyBot) and the tighten-half carries
   more of H1.

## Validation criteria

> **TO BE COMMITTED BY THE USER** alongside the hypothesis.

| Outcome | Decision |
|---|---|
| H1 + H2 + H3 all met | <ship adaptive selection; update charts/README/provenance> |
| H2 met, H1 partial (one backdrop wins, others neutral) | <ship the winning half only? record + scope down> |
| H2 met, H1 not met | <keep static; record that adaptive width is not a lever even steal-aware> |
| H3 not met (mechanism never fires) | <debug selection/detection before any conclusion> |

## Results

*To be filled after running.*

## Conclusion

*To be filled after analysis.*

## Decisions made / next steps

*To be filled after conclusion.*
