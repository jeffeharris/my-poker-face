---
purpose: Narrative log of chasing the "systemic fold-to-3bet over-fold" and finding it was a metric bug (squeeze contamination), plus testing-and-disconfirming the shallow-stack over-commit hypothesis
type: reference
created: 2026-06-08
last_updated: 2026-06-08
---

# The fold-to-3bet that wasn't — "suspect the metric, then suspect it again"

Third session on archetype shaping. The handoff's top item was a real-looking
defect: **every archetype over-folds to 3-bets** (60–86%), and even the
distortion-OFF Baseline control folds 82%. A readable field should span ~50
points between a nit and a maniac; ours spanned ~20. It looked like the base
chart's 3-bet defence was just too fold-heavy.

## The control was failing, which is the tell

The thing that stopped me from reaching for the chart: the **Baseline** —
distortion off, the solver reference — was also "failing" at 82%. When your
control fails, you're usually measuring the instrument, not the subject. So I
built an attribution probe that split the `vs_3bet` bucket by whether the actor
had actually been the RFI opener.

The answer was unambiguous: **33–85% of every archetype's "vs_3bet" decisions
weren't fold-to-3bet at all — they were squeeze defence** (you cold-called an
open, then someone 3-bet over the top). The bots fold ~100% of those (weak
flatting ranges *should* fold to a squeeze), and `classify_preflop_scenario`
buckets purely by raise-count==2, with no check that you were the raiser. So the
wide-flatting archetypes — the stations and fish — had their fold-to-3bet
inflated by a flood of squeeze folds. Condition on "was the opener," and the
station goes 82.9 → 22.0, the maniac 67 → 20, and 6 of 7 archetypes land in
band. The "systemic over-fold" was ~90% a measurement artifact. Same lesson as
last session, learned again: **suspect the metric before the bot** — and a
failing control is the cheapest possible smell test.

## The hypothesis I was asked to test, and got to disconfirm

Mid-stream the question shifted: *"are we cutting to push/fold too quickly —
getting short-stacked too fast?"* The casino's buy-in floor is 40bb, and the
archetype charts are calibrated for 100bb and "win at every depth," so at 40bb a
3-bet→4-bet auto-commits. My first probe even showed all-in rate ~2× at 40bb. It
felt right.

But "feels right" isn't measured. Two things kept me honest. First, the prior
art: a doc sweep turned up `SOLVER_CHART_SCOPE` (parked 2026-05-26 — the exact
"shallow stacks collapse" concern was chased and found to be a `Jeff_station`
*measurement artifact*; honest eval was +6.7 bb/100 at 25bb) and Sweep A/D (jam%
stays low at 25bb; fish drain is *slower* shallow). Second, the right metric:
the all-in *rate* doesn't tell you if the commits are *bad*. So I built a
commit-quality probe that scores every committed hand by its eval7 equity-vs-
random. The 4-bet ranges came back **value-weighted (mean 0.61–0.71, 0% trash)**
— the original "Q2o 4-bet-shove" symptom does not reproduce on current code. The
elevated all-in is just correct low-SPR poker plus the fish's designed postflop
stickiness.

So I got to tell the user, with evidence, that the thing they were sure was
needed *isn't* — at least not as a strength fix. That's the part worth
recording: a strong prior ("I think it's clear it's needed") still gets measured,
not assumed, and the cheap probe both honored the previous team's explicit
"only revisit if a real leak shows up" bar and kept us from building a
depth-aware chart system to solve a problem that wasn't there. The remaining
value in depth-aware *sizing* is real but it's a feel/tell item, not a leak.

## The fix, and what it uncovered

The metric fix conditions fourbet/fold_to_3bet on the RFI opener everywhere it's
computed: the sim recorder (`record_decision(is_opener=…)` + `full_sim` tracks
`rfi_opener_name`), the live review route (which reconstructs opener-ness from
the rows — `preflop_node_key` is the *strategy* node and can't be repurposed for
a metric), and both probes. Three tests pin the squeeze-exclusion.

Cleaning the metric did what good measurement does — it revealed the *real*
residuals that the contamination had been hiding: tag over-folds (68 vs 58) and
tag/lag/maniac 4-bet a touch high as openers (maniac 48.5 vs 40). Those splits
had been tuned against the *contaminated* numbers, so they looked in-band when
they weren't. That's the next piece — and now there's an honest instrument to
tune them against.

## What I'd tell the next person

1. **A failing control is a failing instrument.** If the distortion-OFF baseline
   "fails" the same way as everything else, stop and check what you're counting.
2. **Suspect the metric, then suspect it again.** Two sessions running, the
   headline "bot defect" was a measurement definition. Squeeze ≠ fold-to-3bet.
3. **Don't repurpose the strategy node for a metric.** `preflop_node_key` is the
   chart key; opener-ness is a separate fact — reconstruct it, don't overload.
4. **Measure the strong prior too.** Being asked to fix something isn't evidence
   it's broken. The commit-quality probe cost ~10 minutes and saved a chart
   system.
5. **Clean metrics surface new truths.** The tag/lag/maniac 4-bet residuals were
   invisible until the squeeze noise was gone.
