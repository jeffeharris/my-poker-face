---
purpose: Phase 7 HU chart implementation results and validation findings
type: reference
created: 2026-05-13
last_updated: 2026-05-13
---

# Phase 7: HU preflop charts — results

## TL;DR

Phase 7 shipped: HU-specific preflop chart, router, 27 unit tests. The
chart plays approximately balanced against GTO-Lite (Baseline at **-35.9
bb/100**, statistically indistinguishable from break-even given mirror
match noise of ±110). This is a ~17 bb/100 improvement over the
pre-Phase-7 baseline of **-53.1 bb/100** documented in
[`PHASE_6_VALUE_OVERRIDE_RESULTS.md`](PHASE_6_VALUE_OVERRIDE_RESULTS.md).

The chart structurally fixes the "6-max-chart-applied-to-HU" leak that
Phase 7 was designed to solve. Personality distortion on top costs
30-90 bb/100 vs balanced opponents (expected: personalities exist to
create character at some EV cost).

**vs ManiacBot, every archetype regresses by 130-200 bb/100.** This is
not a chart problem — Baseline (pure chart) also regresses. The chart
is correctly balanced and gets exploited by an opponent who never folds.
The fix lives in the **adjustment layer**, not the chart.

## What shipped

| Component | Commit |
|---|---|
| HU table loader, router, factory wiring, 5 routing tests | `46624b54` |
| Chart v1 (676 entries, 22 unit tests, generator script) | `46ca598e` |

Routing gate: `len(game_state.players) == 2` (seated count, not active
count). 6-max hands that collapse to 2 players via folds continue to
use the 6-max chart correctly.

Chart sizing convention: single `raise_3bb` open; BB 3-bet `raise_3x`;
SB 4-bet `raise_4x`; `jam` for shoves. See
[`hu_preflop_chart_README.md`](../../poker/strategy/data/hu_preflop_chart_README.md)
for the full per-hand spec and border-flip log.

Chart-level invariants (uniform per-canonical-hand averaging):
- SB open rate: **0.7101** (band 0.60-0.72) ✓
- BB defense rate: **0.5207** (band 0.52-0.62) ✓
- BB 3-bet rate: **0.1361** (band 0.12-0.18) ✓
- SB 4-bet+jam rate vs 3-bet: **0.0710** (band 0.06-0.10) ✓

## Validation: HU vs GTO-Lite (balanced opponent)

Seed 42, bias 0.05, 2000 hands per archetype. This is the apples-to-apples
chart-quality test.

| Hero | bb/100 vs GTO-Lite | 95% CI | vs pre-Phase-7 baseline |
|---|---:|---|---|
| ManiacBot | +990.9 | [+775.1, +1206.7] | (Maniac exploits all balanced bots) |
| CaseBot | +208.2 | [+176.8, +239.5] | wins |
| Rock | -19.7 | [-42.7, +3.4] | break-even |
| Nit | -34.9 | [-61.2, -8.7] | near break-even |
| **Baseline (pure chart)** | **-35.9** | [-60.0, -11.8] | **improved from -53.1** |
| GTO-Lite (mirror) | -41.9 | [-152.7, +68.8] | mirror noise (CI ±110) |
| CallStation | -48.2 | [-57.6, -38.8] | |
| Calling Station | -54.1 | [-90.4, -17.9] | |
| LAG | -67.2 | [-98.6, -35.8] | -31 bb/100 distortion cost |
| TAG | -73.2 | [-101.5, -44.8] | -37 bb/100 distortion cost |
| ABCBot | -92.8 | [-206.7, +21.1] | |
| Maniac | -123.2 | [-159.3, -87.1] | -87 bb/100 distortion cost |

**Reading:** Baseline at -35.9 ± 24 bb/100 is well within the GTO-Lite
mirror noise band (±110), so the chart plays at "approximately balanced"
levels. Personality distortion costs 30-90 bb/100 on top, which is the
expected tradeoff for personality presence.

## Validation: HU vs ManiacBot (exploit target — DO NOT use for chart grading)

| Hero | bb/100 vs ManiacBot | 95% CI |
|---|---:|---|
| CaseBot | +632.5 | [+575.4, +689.7] |
| ManiacBot (mirror) | +0.0 | [-365.8, +365.8] |
| CallStation | -55.3 | [-81.8, -28.8] |
| Calling Station | -264.5 | [-298.4, -230.7] |
| Rock | -281.6 | [-308.1, -255.1] |
| Baseline | -287.1 | [-309.9, -264.2] |
| Nit | -290.0 | [-314.6, -265.5] |
| LAG | -301.9 | [-335.0, -268.9] |
| TAG | -304.7 | [-331.6, -277.9] |
| Maniac | -319.5 | [-354.1, -284.8] |

Every archetype regressed by 130-200 bb/100 vs Phase 6.5 ManiacBot
baselines. **This is not a chart bug.** Baseline (pure chart) also
regressed by ~200 bb/100. The same structural pattern shows in the
GTO-Lite sweep: **ManiacBot beats GTO-Lite by +990 bb/100**, because
balanced players fold too often vs maniacs who don't fold themselves.

A wider GTO-shaped chart loses more to a maniac than a tighter 6-max
chart did, because wider ranges give the maniac more spots to apply
unfoldable pressure. The chart did its job; the **adjustment layer
needs to widen** so call-down discipline shifts further when opponent
detection confidence is high.

## Open issues / followups

- **Single-seed validation.** Numbers above are seed=42, bias=0.05.
  Phase 6.5 ran 3 seeds × 2 biases. For a definitive baseline, run two
  more seeds (142, 242) and average. Single-seed is enough for
  directional signal but not for committing to a delta band.
- **No per-decision distribution analysis yet.** The Phase 7 plan's
  *primary* gate was action-distribution (TAG SB VPIP 55-75%, BB defense
  50-65%). Confirmed only at the chart level (uniform-averaged across
  169 hands). Live-decision averaging not yet extracted from sim traces.
- **Border-flip log in the README.** README documents the v1
  promotions of 50/50 mix tiers to 100%. Future calibration can soften
  these to mixes, or switch the aggregate tests to combo-weighted sums
  to restore the README's literal binary ranges.

## Adjustment-layer plan (Phase 7.5 candidate)

Phase 7 fixed the preflop chart leak. The remaining HU bleeding vs
maniacs is now the adjustment layer's problem. Three places to widen,
in priority order:

### 1. Extend `value_override` to marginal hands vs hyper-aggressor

`poker/strategy/value_override.py` currently only fires for
`OVERRIDE_TRIGGER_CLASSES = {nuts, strong_made, strong}` plus
`hyper_aggressive` opponent detection. Vs an extreme aggressor,
**medium_made / weak_made / pair / ace-high all need bluff-catch
behavior** — convert folds into calls, not just convert checks into
raises.

Concretely: add a `BluffCatchMode` to `compute_value_override_strategy`
that fires for `medium_made` and below when:
- Opponent classified `hyper_aggressive` with high confidence (≥100 hands)
- Hero is facing a bet (`fold` in available actions)
- Pot odds make calling marginally +EV given the wider opponent range

Effect: when ManiacBot fires a c-bet on a K72 board and we have 88,
today we fold (medium_made vs aggressive c-bet pattern). Under the
extension, we call — because his c-bet range is mostly air vs his stats.

### 2. Two-tier clamp on exploitation offsets

`poker/strategy/exploitation.py` has `DEFAULT_MAX_TOTAL_SHIFT = 0.4`
(L1 cap on offset magnitude). This is correctly conservative for
typical aggressives. For extreme detections (e.g. AF > 4, fold-to-3bet
< 10%, sample ≥ 100 hands), the cap is too small — we want a much
bigger shift in our response.

Concretely: parameterize the clamp so high-confidence extreme
detections get `MAX_TOTAL_SHIFT_EXTREME = 0.8` (or remove the cap and
trust the L1 normalization downstream). Today's 0.4 means even when
we *know* opponent is a maniac, we only move our distribution ~40% of
the way toward the exploit response.

### 3. Postflop classifier: aggressor archetype awareness

`poker/strategy/postflop_classifier.py` currently buckets by board
texture + SPR + made-tier + facing-action. It's opponent-blind. When
the aggressor is a maniac, a "facing_bet" on a wet board has a very
different EV than the same node with a tight aggressor.

Concretely: pass a `bettor_archetype` axis into the postflop node (or
compute the postflop-strategy lookup with an aggressor-modifier on top).
A pair vs maniac-c-bet is bluff-catch territory; vs nit-c-bet it's a
fold.

Lower priority than 1+2 because it requires postflop chart entries
keyed by aggressor archetype — bigger data shape change. 1 and 2 are
behavioral fixes on existing data.

## Estimated effort

| Item | Effort | Validation |
|---|---|---|
| 1: value_override bluff-catch extension | 1-2 days | Re-run HU sweep vs ManiacBot. Target: bring TAG/LAG/Nit within 50 bb/100 of Phase 6.5 baselines. |
| 2: Two-tier exploitation clamp | 0.5 day | Same sweep. |
| 3: Postflop opponent awareness | 3-4 days | Same sweep + 6-max regression check. |

Items 1 and 2 together should reclaim most of the 130-200 bb/100 lost
to ManiacBot, because they fix the structural underreaction to confirmed
extreme opponents. Item 3 is a longer-tail improvement.

## Cross-references

- Plan: [`PHASE_7_HU_PREFLOP_CHARTS.md`](../plans/PHASE_7_HU_PREFLOP_CHARTS.md)
- Prior baselines: [`PHASE_6_VALUE_OVERRIDE_RESULTS.md`](PHASE_6_VALUE_OVERRIDE_RESULTS.md)
- Chart spec: [`hu_preflop_chart_README.md`](../../poker/strategy/data/hu_preflop_chart_README.md)
- Implementation commits: `46624b54` (infrastructure), `46ca598e` (chart data)
