---
purpose: Narrative log of the strategy-stack re-validation — distrusting stale results, an audit whose own findings needed auditing, and the +22.5 exploitation edge that evaporated against a believable opponent
type: design
created: 2026-06-12
last_updated: 2026-06-12
---

# Re-validation, and the +22.5 bb/100 that wasn't

## The call

After the reshove work, Jeff said to recheck everything — not to trust older
results — because "a lot was missed on chart quality and completeness." He was
right on the merits: nearly every "+EV, shipped" verdict in the tiered stack was
measured in late May against a `human_clone` that **never folded to a 3-bet**
(fixed only the day before, when the reshove proof forced a folding clone into
existence) and/or **before** the preflop chart regen (#295/#299/#300). Every
fold-equity-dependent feature had been priced against a non-folder.

## An audit whose findings needed auditing

I fanned out a 9-agent read-only audit: a provenance/staleness matrix over 26
layers + 17 charts, plus a chart quality pass, then a prioritized re-validation
plan. It was genuinely useful — but its own "quick wins" turned out to be the
lesson in miniature. On verification:

- "`wider_rfi` is byte-identical to base, delete it" — **false**: it differs and
  is used by `champion_challenger` as a comparison chart.
- "25bb BB-defense is 100% jam / 0% flat (generator bug)" — **wrong**: it flats
  5.2%; the jam-lean is plausibly deliberate at shallow depth.
- "`weak_station` vs_3bet inversion" — **unconfirmed**: the % is relative to each
  chart's own (different) opening range; needs combo-level analysis, not a fix.

So three of the five "quick wins" didn't survive contact with the data. The only
blind-safe one was additive infrastructure: **postflop lints** (the postflop
charts — 8,640 nodes — had *zero* lint coverage; `lints.py` was preflop-only).
The meta-lesson wrote itself: a fan-out of agents produces plausible-but-partly-
wrong claims, exactly the failure mode the whole exercise exists to catch. Treat
the matrix as a hypothesis list, not ground truth.

## Batch 1: the headline that halved, then vanished

The #1 most load-bearing, most-suspect claim was `_apply_exploitation`'s
**+22.5 bb/100**. I re-ran it on a fresh Hetzner box (ccx63, 48 dedicated cores,
6 jobs in parallel, bit-identical to local, torn down after), via `exploit_bb100`
paired-CRN, 8000 hands × 3 seeds, across three opponent regimes — the third of
which the original never had: a *folding* field.

| Field | TAG | LAG |
|---|---|---|
| old extreme station (vpip≈1.0) | +10.3 ✅ | +9.9 ✅ |
| competent (GTO-Lite/ABCBot) | +3.6 (CI∋0) | +4.8 (CI∋0) |
| realistic folding field (Punisher+Jeff clones) | **0% fires** | **0% fires** |

The "+22.5" didn't reproduce. Even vs the same extreme-station type it's now
+10.3 — the regen roughly halved it. Vs a competent field it's marginal. And vs
a believable folding field, exploitation **flipped 0 of 24,000 hands** — it never
engaged at all. The audit's "most suspect" flag was dead on: exploitation's value
is concentrated entirely on cartoonish, vpip≈1.0 opponents, and is inert against
anything that looks like a real player.

## What exploitation is even looking for

That 0% sent me back to the detector. `classify_detected_patterns` fires on five
EXTREME shapes: hyper_aggressive (AF≥3.5 or all-in≥0.30), hyper_passive (vpip/vol
>0.70 & AF<0.80), passive_with_jams (a station that also jams), tight_nit
(vpip/vol<0.30), high_fold_to_cbet (>60%). Every threshold is tuned to a
caricature. The clones sit in the gaps: Jeff (vpip 0.39) is neither a station nor
a nit — a mediocre reg in the dead zone; Punisher *should* trip tight_nit /
high_fold_to_cbet on paper, which is exactly why "0% fires" is the next question:
correct (the field truly isn't exploitable) or a gap (the detector only sees
extremes and the offsets never flip a sampled action)?

## Takeaways

- Distrust green that predates a tooling change. The clone fold-fix and the chart
  regen silently invalidated a season of "+EV" verdicts; only re-running on
  current code against a *believable* opponent tells the truth.
- Audit findings are hypotheses. Verify each cell before acting — the audit got
  3/5 quick-wins wrong.
- An edge measured only against a caricature is a fish-hunting number, not a
  robustness number. +22.5 vs a calling station said nothing about a competent
  human — and the re-run proved it (≈0 vs a real shape).
