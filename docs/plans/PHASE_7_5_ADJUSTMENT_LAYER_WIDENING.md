---
purpose: Plan to widen the adjustment layer so the tiered bot realizes equity vs confirmed extreme opponents (maniacs)
type: design
created: 2026-05-13
last_updated: 2026-05-13
---

# Phase 7.5: Adjustment-layer widening

## Context (read before starting)

Phase 7 shipped HU preflop charts that play approximately balanced
chart-vs-chart (Baseline at -35.9 bb/100 vs GTO-Lite — well within
mirror-match noise). See
[`docs/analysis/PHASE_7_HU_RESULTS.md`](../analysis/PHASE_7_HU_RESULTS.md)
for full numbers.

**But vs ManiacBot, every archetype regresses by 130-200 bb/100**
including Baseline (the pure chart with no personality distortion).
Same structural pattern appears in the GTO-Lite sweep: ManiacBot
beats GTO-Lite by +990 bb/100. Balanced players fold too often
against opponents who never fold themselves.

The chart isn't broken — the **adjustment layer** is too small. The
existing pipeline (`poker/tiered_bot_controller.py:140-269`) is:

```
chart → modify_strategy (personality)
      → apply_exploitation_offsets (Phase 6)
      → compute_value_override_strategy (Phase 6.5, strong hands only)
      → apply_short_stack_heuristics (Phase 6 Step B)
      → apply_pot_odds_floor
```

Today's behaviors that bleed bb/100 vs a confirmed maniac:

1. **`exploitation` is capped at L1 shift 0.4** — even when we *know*
   opponent is hyper-aggressive (AF > 5 OR all_in_freq > 0.30) with
   high sample confidence, we only move our distribution ~40% toward
   the exploit response. Today's cap is correctly conservative for the
   normal case but too tight for high-confidence extremes.

2. **`value_override` only fires for strong hands** — its
   `_OVERRIDE_TRIGGER_CLASSES = {nuts, strong_made, strong}`. Vs an
   extreme aggressor, **medium-strength hands also need bluff-catch
   behavior** — convert folds into calls when the opponent's betting
   range is mostly air. Today when ManiacBot c-bets a K72 board and
   we have 88, we fold (`medium_made` vs `facing_bet`); the right play
   is to call.

3. **Postflop classifier is opponent-blind** — it buckets by texture +
   SPR + made-tier + facing-action. The same node ("facing-bet on a
   wet flop") has very different EV depending on whether the aggressor
   is a maniac or a nit, but our chart doesn't see that distinction.

## Goal — definition of done

A working Phase 7.5 produces these observable outcomes:

1. **HU vs ManiacBot regresses ≤ 50 bb/100 from Phase 6.5 baselines**
   for Baseline, TAG, LAG, Nit. Phase 6.5 baselines:

   | Hero | Phase 6.5 | Current Phase 7 | Phase 7.5 target |
   |---|---|---|---|
   | Baseline | n/a | -287.1 | improves by ≥150 |
   | TAG | -135.8 | -304.7 | within 50 of -135.8 (i.e. ≥ -185) |
   | LAG | -171.2 | -301.9 | within 50 of -171.2 (i.e. ≥ -221) |
   | Nit | -104.6 | -290.0 | within 50 of -104.6 (i.e. ≥ -155) |
   | Maniac | -119.9 | -319.5 | within 100 of -119.9 |

2. **HU vs GTO-Lite stays approximately break-even** for Baseline.
   Current Baseline -35.9 ± 24. Phase 7.5 must not push Baseline
   below -80 vs GTO-Lite (the wider clamps must not over-fire against
   balanced opponents).

3. **6-max behavior unchanged**: all existing 6-max-vs-rules gates
   still pass (currently in
   [`docs/analysis/PHASE_6_VALUE_OVERRIDE_RESULTS.md`](../analysis/PHASE_6_VALUE_OVERRIDE_RESULTS.md)).

4. **No regression in existing strategy tests** (currently 344
   passing in `tests/test_strategy/`).

5. **Adjustment-layer firing rate is observable** — extend the
   per-decision capture to record which adjustment(s) fired so we can
   measure how often each layer engages and confirm they're not
   double-counting.

## Approach overview

Three items, sequenced by leverage and effort:

```
Item 1: value_override bluff-catch extension  (1-2 days, biggest leverage)
   ↓
Item 2: two-tier exploitation clamp             (0.5 day, complementary)
   ↓ (validate with both 1+2)
Item 3: postflop classifier opponent-awareness  (3-4 days, optional)
```

Items 1+2 together should reclaim the bulk of the bleeding because
they fix the structural underreaction to confirmed extreme opponents
using stats already being computed. Item 3 is a deeper architectural
change — defer until 1+2 validation tells us how much residual leak
remains.

## Item 1: value_override bluff-catch extension

### Today

`poker/strategy/value_override.py:107` gates on
`hand_strength not in _OVERRIDE_TRIGGER_CLASSES`. Trigger classes are
`{nuts, strong_made, strong}`. Medium and weak hands never reach the
override path; they always go through exploitation offsets only.

For an open spot, override rewrites the distribution as 80-95%
raise / 5-20% check-call. For facing-bet, it's 50% call / 50% raise.
Facing-all-in: 100% call.

### Phase 7.5 change

Add a fourth trigger regime: `medium_made` and `weak_made` when
`facing_bet` and opponent is high-confidence hyper-aggressive. The
override REWRITES the strategy to "bluff-catch by calling" instead of
following the table's fold-heavy default.

```python
# New module-level constants
BLUFF_CATCH_TRIGGER_CLASSES = frozenset({
    HandStrengthClass.MEDIUM_MADE.value,
    HandStrengthClass.WEAK_MADE.value,
})

# High-confidence threshold — stricter than the existing MIN_HANDS_DEFAULT.
# Bluff-catch with bad reads is expensive; require more samples.
BLUFF_CATCH_MIN_HANDS = 100

# Extreme threshold (separate from the basic 'hyper_aggressive' flag) —
# bluff-catch only fires for the truly extreme aggressors so balanced
# opponents don't trigger this aggressive response.
BLUFF_CATCH_AF_THRESHOLD = 6.0   # vs HYPER_AGG_AF_THRESHOLD = 5.0
BLUFF_CATCH_ALL_IN_THRESHOLD = 0.40  # vs HYPER_AGG = 0.30
```

New gate function:

```python
def should_apply_bluff_catch_override(
    stats: AggregatedOpponentStats,
    hand_strength: str,
    decision_context: DecisionContext,
    adaptation_bias: float,
    tilt_factor: float = 1.0,
) -> bool:
    """Trigger conditions for bluff-catch mode (Phase 7.5).

    Distinct from the existing strong-hand override:
      - Hand class is medium_made or weak_made (not strong)
      - We're facing a bet (fold is legal)
      - Sample size is at least BLUFF_CATCH_MIN_HANDS (stricter)
      - Opponent crosses the EXTREME thresholds, not just hyper_aggressive
      - Standard gating (adaptation_bias × tilt_factor > GATING_FLOOR)
    """
```

And a new strategy builder:

```python
def compute_bluff_catch_strategy(
    strategy: StrategyProfile,
    decision_context: DecisionContext,
    hand_strength: str,
) -> StrategyProfile:
    """Replace strategy with bluff-catch distribution.

    Spot is always facing_bet (gate enforced upstream). Output:
      - medium_made: 80% call, 20% fold
      - weak_made:   50% call, 50% fold
    """
```

Controller wiring in `tiered_bot_controller.py`: after the existing
strong-hand value override fails to fire, check the bluff-catch
override. They are MUTUALLY EXCLUSIVE — a hand class can't be both
strong and medium_made — so wiring is sequential, not interleaved.

### Why mutual exclusivity matters

The existing value_override and the new bluff-catch override operate
on disjoint hand classes:

| Hand class | Override path |
|---|---|
| `nuts`, `strong_made`, `strong` | Existing strong-hand override |
| `medium_made`, `weak_made` | New bluff-catch override |
| Any other (`air`, `weak_draw`, etc.) | No override — table flows through |

This means we never get conflicting "raise for value" + "call to
bluff-catch" on the same hand. The classifier output picks the lane.

### Action vocabulary check

The strategy_table will emit `fold/call/check/raise_*/jam` for the
medium_made postflop nodes. Bluff-catch replaces this with a `call`/
`fold` split, both of which are always available when facing a bet.
No new action labels required.

## Item 2: Two-tier exploitation clamp

### Today

`poker/strategy/exploitation.py:51`:

```python
DEFAULT_MAX_TOTAL_SHIFT = 0.4
```

This is the L1 cap on the absolute change to the strategy
distribution. Applied unconditionally in
`apply_exploitation_offsets` (line 257). Means even with the
strongest possible detection (AF=10, fold-to-3bet=0, 500 hands),
the total probability mass moved is capped at 0.4.

### Phase 7.5 change

Promote `max_total_shift` to a function of detection strength,
*not* a constant:

```python
DEFAULT_MAX_TOTAL_SHIFT = 0.4           # normal aggressive
EXTREME_MAX_TOTAL_SHIFT = 0.8           # confirmed hyper-aggressive
EXTREME_CLAMP_MIN_HANDS = 150           # require strong sample
```

New helper:

```python
def _determine_clamp(
    stats: AggregatedOpponentStats,
    decision_context: DecisionContext,
) -> float:
    """Pick the L1 clamp based on detection confidence.

    Returns the EXTREME clamp when:
      - sample_size >= EXTREME_CLAMP_MIN_HANDS, AND
      - aggression_factor >= BLUFF_CATCH_AF_THRESHOLD (= 6.0), AND
      - all_in_frequency >= BLUFF_CATCH_ALL_IN_THRESHOLD (= 0.40)

    Note: both stats conditions required (not OR). This is the strictly
    stricter gate than the basic 'hyper_aggressive' pattern — a sample
    of 500 hands with AF=6.0 and all_in=0.40 is a real maniac, not a
    noisy estimate.
    """
```

Call site: `apply_exploitation_offsets(..., max_total_shift=...)` gets
threaded through from the controller, replacing the hard-coded
constant. The controller passes `_determine_clamp(stats, ctx)` value.

### Why a second tier, not a continuous function

A continuous "clamp = f(confidence)" sounds cleaner but has two
problems:

1. **Hysteresis** — small changes in stats would oscillate the clamp,
   making the bot's behavior unpredictable mid-session.
2. **Calibration cost** — a continuous function needs many validation
   points to tune; two tiers need exactly two. We can iterate the
   thresholds later if needed.

Discrete tiers also make the firing-rate diagnostic interpretable:
each decision is either "normal exploit" or "extreme exploit," not a
continuum.

## Item 3: Postflop classifier opponent-awareness (lower priority)

### Today

`poker/strategy/postflop_classifier.py:106` builds a `PostflopNode` from:
- `street`, `position`, `pot_type`, `board_texture`, `made_tier`,
  `draw_modifier`, `facing_action`, `spr_bucket`

It's opponent-blind. The strategy table is keyed on these fields and
returns the same distribution regardless of which opponent is firing.

### Phase 7.5 change (deferrable)

Add an optional `bettor_archetype` axis to the node, populated from
opponent stats at decision time:

```python
@dataclass(frozen=True)
class PostflopNode:
    ...existing fields...
    bettor_archetype: str = 'balanced'  # 'balanced', 'aggressive', 'tight'
```

Strategy table lookup ladder:
1. Exact match including bettor archetype
2. Fall back to `bettor_archetype='balanced'` (i.e. the existing chart)

This requires authoring postflop chart entries for non-balanced
bettors — or, simpler v1, a per-archetype multiplier on the existing
distribution that biases call vs fold based on aggressor type.

### Why defer

Items 1+2 don't need this axis to be effective. Item 3 is the right
long-term shape but has bigger data-shape implications. Score this
item against the bb/100 residual after 1+2 ship — if residual is < 50
bb/100, Item 3 may not be worth the complexity.

## Tests

### Unit tests

New: `tests/test_strategy/test_bluff_catch_override.py`
- `should_apply_bluff_catch_override` returns True for
  `medium_made + facing_bet + extreme aggressor + ≥100 hands`
- Returns False for: weak sample, balanced opponent, strong hands
  (use existing override path), no facing_bet (open spot), weak_draw
  (not in trigger classes)
- `compute_bluff_catch_strategy`:
  - medium_made facing bet → {'call': 0.8, 'fold': 0.2}
  - weak_made facing bet → {'call': 0.5, 'fold': 0.5}
  - All output rows sum to 1.0

New: `tests/test_strategy/test_exploitation_extreme_clamp.py`
- `_determine_clamp` returns `EXTREME_MAX_TOTAL_SHIFT` only when all
  three conditions met (sample ≥ 150, AF ≥ 6.0, all-in ≥ 0.40)
- Returns `DEFAULT_MAX_TOTAL_SHIFT` otherwise (each missing condition
  parameterized)
- `apply_exploitation_offsets` with extreme clamp produces a
  distribution that diverges further from baseline than with default
  clamp (same stats, same input — only clamp changes)

### Integration test

`tests/test_strategy/test_tiered_bot_bluff_catch.py`
- End-to-end: ManiacBot-pattern stats + medium_made flop hand →
  controller decision is `call` (not `fold`)
- Same stats + strong_made → controller still hits the existing
  strong-hand override (no regression)

### Existing test regression

All 344 strategy tests + the 27 HU chart/routing tests must pass
unchanged.

## Validation

### Primary gate: HU vs ManiacBot

```bash
for seed in 42 142 242; do
  docker exec my-poker-face-hybrid-ai-backend-1 \
    python -m experiments.simulate_bb100 \
    --hands 2000 --seed $seed --opponent ManiacBot --adaptation-bias 0.05 \
    > /tmp/phase7_5/maniac_seed${seed}.log 2>&1 &
done
wait
```

Targets (per archetype):
- Baseline: improves from -287 to ≥ -130 (+150 bb/100 improvement)
- TAG: improves from -305 to ≥ -185 (matches Phase 6.5 baseline within 50)
- LAG: improves from -302 to ≥ -221
- Nit: improves from -290 to ≥ -155
- Maniac: improves from -319 to ≥ -220

### Stability gate: HU vs GTO-Lite

```bash
docker exec my-poker-face-hybrid-ai-backend-1 \
  python -m experiments.simulate_bb100 \
  --hands 2000 --seed 42 --opponent GTO-Lite --adaptation-bias 0.05
```

Baseline must stay ≥ -80 bb/100 vs GTO-Lite (currently -35.9). If
the wider exploitation clamp pushes us below this, we're over-firing
on noisy detections and need to tighten the trigger conditions.

### Regression gate: 6-max-vs-rules

Re-run the 6-max-vs-rules harness from
[`PHASE_6_VALUE_OVERRIDE_RESULTS.md`](../analysis/PHASE_6_VALUE_OVERRIDE_RESULTS.md)
and verify no archetype regresses by more than 30 bb/100. The
exploitation clamp expansion in particular needs to NOT fire often
in 6-max games where the per-aggressor stats are much noisier
(fewer hands per opponent than HU).

### Diagnostic: adjustment firing rates

Extend `player_decision_analysis` schema (or add a sidecar log) to
record per-decision:
- Was strong-hand value override fired? (existing)
- Was bluff-catch override fired? (new)
- Which clamp tier did exploitation use? (new)

Expected firing pattern vs ManiacBot:
- Strong-hand override: 5-15% of decisions (rare strong hands)
- Bluff-catch override: 20-30% of decisions (medium hands facing bets)
- Extreme clamp tier: ~60% of decisions (after sample threshold)

Expected firing pattern vs GTO-Lite:
- Strong-hand override: 5-10%
- Bluff-catch override: < 5% (GTO-Lite doesn't cross the EXTREME thresholds)
- Extreme clamp tier: < 5% (same reason)

If bluff-catch fires often vs GTO-Lite, thresholds are too loose.

## Risks / gotchas

1. **Bluff-catch is asymmetric**: if our read is wrong, calling vs a
   value bet costs ~1 pot. If the read is right and we fold, we save
   nothing (we were going to fold anyway). So the EV breakeven point
   depends critically on read accuracy. The 100-hand sample minimum is
   conservative; even higher would be safer but reduces firing rate.

2. **Tilt suppression interaction**: today's `tilt_factor` multiplies
   `adaptation_bias` to suppress exploitation when hero is tilted. We
   need bluff-catch override to honor the same suppression — calling
   down with marginal hands while tilted is bad. The plan uses the
   same gating formula (`adaptation_bias × tilt_factor > GATING_FLOOR`).

3. **Multiway hands**: today's stats are aggregated across active
   opponents with a 60% rule. In a 3-way pot where one opponent is a
   maniac and two are tight, aggregating diluteshe signal. Bluff-catch
   should require that the SPECIFIC aggressor (the one we're facing)
   triggers the extreme thresholds, not the aggregate. Needs a small
   refactor to pass per-aggressor stats, not aggregated stats, into
   the bluff-catch gate.

4. **Extreme clamp + bluff-catch double-counting**: both fire on the
   same opponent. The clamp expands exploitation; bluff-catch replaces
   strategy entirely. They sequence (clamp first, override second), so
   bluff-catch wins when it fires — no double-count. But the unit test
   should confirm the override doesn't shift twice.

5. **Maniac archetype**: our own Maniac archetype distorts toward
   aggressive play. When Maniac plays vs ManiacBot, both apply
   extreme exploitation toward each other. The result should be a
   wide-vs-wide showdown contest dominated by hand strength, not
   strategic edge. Worth confirming in the sweep that Maniac doesn't
   gain MORE bb/100 vs ManiacBot than other archetypes — that would
   suggest exploitation is over-firing.

## Effort estimate

| Item | Implementation | Tests | Validation |
|---|---|---|---|
| 1: bluff-catch override | 1 day | 0.5 day | 0.5 day |
| 2: extreme clamp | 0.5 day | 0.5 day | (shared with 1) |
| 3: postflop opponent-aware (deferred) | 2-3 days | 1 day | 1 day |

**Total for 1+2: 3 days.** Item 3 deferred behind validation of 1+2.

## Out of scope

- **Postflop strategy table re-authoring per archetype** (Item 3 in
  its full form). v1 of Item 3 is a multiplier on existing entries.
- **Confidence-weighted firing** (Phase 6.6) — that's a separate plan
  already documented in
  [`PHASE_6_6_CBET_PLUS_CONFIDENCE.md`](PHASE_6_6_CBET_PLUS_CONFIDENCE.md).
  Phase 7.5 reuses existing exploitation infrastructure; Phase 6.6 is
  about adding new pattern detection.
- **Range-aware decisions** in the general case — adjusting our
  decisions based on the OPPONENT'S range, not just betting pattern.
  That's a much deeper change requiring per-spot range modeling.

## Files to modify

| File | Action | Description |
|---|---|---|
| `poker/strategy/value_override.py` | Modify | Add `should_apply_bluff_catch_override` + `compute_bluff_catch_strategy` + trigger constants |
| `poker/strategy/exploitation.py` | Modify | Add `EXTREME_MAX_TOTAL_SHIFT` + `_determine_clamp` helper |
| `poker/tiered_bot_controller.py` | Modify | Wire bluff-catch override after strong-hand override; thread `max_total_shift` from `_determine_clamp` |
| `tests/test_strategy/test_bluff_catch_override.py` | **NEW** | Unit tests for new override |
| `tests/test_strategy/test_exploitation_extreme_clamp.py` | **NEW** | Unit tests for clamp tier logic |
| `tests/test_strategy/test_tiered_bot_bluff_catch.py` | **NEW** | Integration test |
| `docs/analysis/PHASE_7_5_RESULTS.md` | **NEW** | Validation findings (post-implementation) |

Postflop classifier (Item 3) NOT in this file list — deferred.

## Reproducibility

Start from commit `ebf76152` or later. Phase 7 chart + routing must be
in place; this plan extends the adjustment layer on top of it.

Baseline numbers to compare against:
- HU vs ManiacBot: see [`PHASE_7_HU_RESULTS.md`](../analysis/PHASE_7_HU_RESULTS.md) table
- HU vs GTO-Lite: same source
- 6-max-vs-rules: see [`PHASE_6_VALUE_OVERRIDE_RESULTS.md`](../analysis/PHASE_6_VALUE_OVERRIDE_RESULTS.md)
