---
purpose: Plan to add c-bet exploitation pattern and confidence-weighted firing to Phase 6/6.5 exploitation
type: design
created: 2026-05-13
last_updated: 2026-05-13
---

# Phase 6.6: C-bet exploitation + confidence-weighted firing

## Context (read before starting)

Phase 6 (exploitation offsets) and Phase 6.5 (value override) are shipped and
validated. Net result: TAG goes from -62 bb/100 to +28 bb/100 vs the 5-rule_bot
mix. Aggressive humans can't farm the AI anymore. Full validation history:
`docs/analysis/PHASE_6_VALUE_OVERRIDE_RESULTS.md`.

This plan adds **two related improvements**:

1. **C-bet exploitation** — new exploitation pattern that exploits opponents
   who fold too often to continuation bets. The infrastructure already tracks
   `fold_to_cbet` per opponent, but no rule consumes it yet.
2. **Confidence-weighted firing** — Codex flagged that current step-function
   detection thresholds over-fire on borderline opponents. Smooth the step
   into a confidence ramp.

Both improvements live in the same module (`poker/strategy/exploitation.py`)
and complement each other — c-bet adds breadth (new pattern), confidence-
weighting adds depth (smoother application of all patterns).

### What's in the codebase

- `poker/strategy/exploitation.py` — three patterns live here:
  hyper_aggressive, hyper_passive, tight_nit. Plus `compute_exploitation_offsets`,
  `apply_exploitation_offsets`, `classify_detected_patterns`.
- `poker/strategy/value_override.py` — Phase 6.5 override (don't touch for this plan).
- `poker/strategy/short_stack.py` — depth-aware suppression (don't touch).
- `poker/tiered_bot_controller.py` — pipeline integration. `_apply_exploitation`,
  `_apply_value_override`, `_compute_effective_stack_bb` etc.
- `poker/memory/opponent_model.py` — `OpponentTendencies` has `fold_to_cbet`
  and `_fold_to_cbet_count`, `_cbet_faced_count`. Aggregated via
  `aggregate_active_opponents()`.
- `poker/memory/memory_manager.py` — production hook. `MemoryManager.on_action`
  tracks `self._preflop_raiser` and feeds `observe_fold_to_cbet` already.

### What the current pipeline looks like

```
strategy_table → personality → exploitation → value_override → short_stack → math_floor → sample
```

### Counter infrastructure

`_tally_exploitation_event` on `TieredBotController` tracks pattern detection
and firing rates. We log them in `analyze_6max_vs_rules.py` output. Any new
pattern needs counters added.

## Goal — definition of done

A working Phase 6.6 produces these observable outcomes:

1. **C-bet detection**: when opponent's `fold_to_cbet > 0.60` AND
   `cbet_faced_count >= 10`, the new pattern is detected per the counter.

2. **C-bet exploitation fires**: when hero is the preflop aggressor on the
   flop, and the opponent has high `fold_to_cbet`, bet-action probabilities
   increase, check probability decreases.

3. **Confidence-weighted intensity**: a borderline maniac (AF=5.1) produces
   ~10% of the offset intensity that a clear maniac (AF=15+) produces. Smooth
   ramp instead of binary firing.

4. **Net gains vs the 5-rule_bot mix**: with c-bet enabled, BB transfer vs
   ABCBot and GTO-Lite (high fold-to-cbet rule bots) increases by 20%+. Vs
   ManiacBot and CallStation (low fold-to-cbet), unchanged.

5. **No regression**: all existing 354+ strategy/memory tests pass. The
   Phase 6.5 validation (TAG vs 5-rule mix at bias=0.85) still produces
   net positive bb/100.

## Concrete design

### Step 1: Extend AggregatedOpponentStats and aggregation

Currently `AggregatedOpponentStats` in `exploitation.py` carries: `hands_observed`,
`vpip`, `pfr`, `aggression_factor`, `all_in_frequency`. Add:

```python
@dataclass(frozen=True)
class AggregatedOpponentStats:
    hands_observed: int = 0
    vpip: float = 0.5
    pfr: float = 0.5
    aggression_factor: float = 1.0
    all_in_frequency: float = 0.0
    fold_to_cbet: float = 0.5         # NEW
    cbet_faced_count: int = 0         # NEW — needed for sample-size gating
```

Update `OpponentModelManager.aggregate_active_opponents` to compute these
across active opponents using the same 60% / weight-average logic. Also
update `_select_exploitation_stats` in `tiered_bot_controller.py` to
populate them from the per-aggressor model when going down the per-aggressor
path.

### Step 2: New pattern detection

In `exploitation.py`:

```python
HIGH_FOLD_TO_CBET_THRESHOLD = 0.60
MIN_CBET_FACED_FOR_DETECTION = 10

def _is_high_fold_to_cbet(stats: AggregatedOpponentStats) -> bool:
    return (
        stats.fold_to_cbet > HIGH_FOLD_TO_CBET_THRESHOLD
        and stats.cbet_faced_count >= MIN_CBET_FACED_FOR_DETECTION
    )
```

Add to `classify_detected_patterns`:

```python
if _is_high_fold_to_cbet(stats):
    patterns.append('high_fold_to_cbet')
```

### Step 3: New DecisionContext flag

In `exploitation.py`:

```python
@dataclass(frozen=True)
class DecisionContext:
    is_preflop: bool = False
    facing_all_in: bool = False
    facing_big_bet: bool = False
    is_flop_as_preflop_aggressor: bool = False  # NEW
```

The flag is True when:
- Current phase is FLOP
- Hero was the preflop aggressor (raised preflop)
- Hero is acting first / can bet without facing aggression

### Step 4: Pattern offset table

In `compute_exploitation_offsets`:

```python
if _is_high_fold_to_cbet(stats):
    if decision_context.is_flop_as_preflop_aggressor:
        # Bluff cbet more often vs fold-happy opponent
        for action in available_actions:
            if action.startswith('bet_'):
                offsets[action] = offsets.get(action, 0.0) + 0.4 * multiplier
        if 'check' in available_actions:
            offsets['check'] = offsets.get('check', 0.0) - 0.3 * multiplier
```

### Step 5: Detect "hero was preflop aggressor"

The hardest piece. Several options:

**Option A: Controller-level tracking (recommended)**

Add `self._was_preflop_aggressor` to TieredBotController. Set/reset in the
preflop decision path:

```python
# In _get_ai_decision after sampling:
if is_preflop and abstract_action.startswith(('raise_', 'jam')):
    self._was_preflop_aggressor = True

# Reset on new hand — needs a hand-start hook on the controller, OR
# detect via hand number change.
```

Hand-start detection is awkward. Cleaner: reset when phase becomes PRE_FLOP
and hero hasn't yet acted this hand. The controller can detect this via:

```python
# At top of decide_action:
if self._last_decision_phase != PokerPhase.PRE_FLOP and current_phase == PokerPhase.PRE_FLOP:
    self._was_preflop_aggressor = False
self._last_decision_phase = current_phase
```

**Option B: Reuse MemoryManager._preflop_raiser**

The production MemoryManager already tracks the preflop raiser. But the
sim doesn't use MemoryManager. Wiring this in means either:
- Adding parallel tracking in the sim, OR
- Refactoring to use MemoryManager in both contexts

Option A is simpler and matches the existing pattern (the controller already
holds `_hero_max_bluff_likelihood`, `_current_hand_plans` etc).

### Step 6: Confidence-weighted firing

Replace binary pattern detection with continuous intensity. New function:

```python
def compute_pattern_intensity(stats: AggregatedOpponentStats) -> Dict[str, float]:
    """For each detected pattern, return intensity in [0, 1].

    Smooth ramp from threshold (0% intensity) to "clearly extreme" (100%).
    Replaces the binary _is_X functions for use in offset computation.
    """
    intensities = {}

    # Hyper-aggressive: AF ramp from 5 (0%) to 15 (100%)
    if stats.aggression_factor > 5.0 or stats.all_in_frequency > 0.30:
        af_intensity = min((stats.aggression_factor - 5.0) / 10.0, 1.0)
        ai_intensity = min((stats.all_in_frequency - 0.30) / 0.40, 1.0)
        intensities['hyper_aggressive'] = max(af_intensity, ai_intensity, 0.0)

    # Hyper-passive: VPIP ramp from 0.6 (0%) to 0.9 (100%)
    if stats.vpip > 0.60 and stats.aggression_factor < 0.80:
        vpip_intensity = min((stats.vpip - 0.60) / 0.30, 1.0)
        intensities['hyper_passive'] = vpip_intensity

    # Tight nit: VPIP ramp from 0.15 (0%) to 0.05 (100%)
    if stats.vpip < 0.15:
        intensities['tight_nit'] = min((0.15 - stats.vpip) / 0.10, 1.0)

    # High fold-to-cbet: ramp from 0.60 (0%) to 0.85 (100%)
    if stats.fold_to_cbet > 0.60 and stats.cbet_faced_count >= 10:
        intensities['high_fold_to_cbet'] = min((stats.fold_to_cbet - 0.60) / 0.25, 1.0)

    return intensities
```

Then in `compute_exploitation_offsets`, multiply each pattern's offsets by
its intensity:

```python
intensities = compute_pattern_intensity(stats)
# ... when applying hyper_aggressive offsets:
multiplier_with_intensity = multiplier * intensities.get('hyper_aggressive', 0.0)
```

Note: `classify_detected_patterns` stays as-is (returns list of patterns
where intensity > 0), used only for counter tracking.

### Step 7: Counter updates

Add to `_tally_exploitation_event`:
- `detected_high_fold_to_cbet`
- Track average intensity per pattern when fired (optional, useful for tuning)

In `analyze_6max_vs_rules.py`, add the new counter to the diagnostics dump.

## Tests

New unit tests in `tests/test_strategy/test_exploitation.py`:

- `test_high_fold_to_cbet_detected_above_threshold`
- `test_high_fold_to_cbet_skipped_when_low_sample_size` (cbet_faced_count < 10)
- `test_high_fold_to_cbet_offsets_fire_only_at_flop_as_aggressor`
- `test_confidence_intensity_zero_at_threshold` (AF=5.0 → 0% intensity)
- `test_confidence_intensity_full_at_extreme` (AF=15+ → 100% intensity)
- `test_confidence_intensity_linear_mid` (AF=10 → 50% intensity)
- `test_borderline_aggressor_emits_smaller_offsets` (AF=5.5 produces ~5%
  of AF=15 offsets)

New tests in `tests/test_strategy/test_tiered_bot_exploitation.py`:

- `test_was_preflop_aggressor_set_when_hero_raises`
- `test_was_preflop_aggressor_reset_at_new_hand`
- `test_cbet_pattern_fires_on_flop_with_aggression_history`
- `test_cbet_pattern_skipped_when_not_preflop_aggressor`

New test in `tests/test_memory/test_opponent_aggregation.py`:

- `test_aggregate_includes_fold_to_cbet_and_cbet_faced_count`

## Validation

Run the same parallel sweep as Phase 6.5 (4 archetypes × 2 biases × 3 seeds
× 1000 hands at 6-max-vs-rules):

```bash
mkdir -p /tmp/phase6_6
for archetype in Nit TAG LAG Maniac; do
  for bias in 0.05 0.85; do
    for seed in 42 142 242; do
      docker exec my-poker-face-hybrid-ai-backend-1 \
        python -m experiments.analyze_6max_vs_rules \
        "$archetype" --hands 1000 --seed $seed --adaptation-bias $bias \
        > /tmp/phase6_6/${archetype}_bias${bias}_seed${seed}.log 2>&1 &
    done
  done
done
wait
```

**Pass criteria**:
- Net bb/100 for TAG (bias=0.85) ≥ 0 (was +28 in Phase 6.5).
- Per-opponent BB transfer **vs ABCBot** improves by 20%+ from Phase 6.5 baseline.
- Per-opponent BB transfer **vs GTO-Lite** improves by 20%+.
- Per-opponent BB transfer vs ManiacBot unchanged (±5%) — c-bet doesn't fire vs them.
- Counter shows `detected_high_fold_to_cbet > 5%` and `value_override_fired`
  unchanged (~17%).

Also re-run HU sweep (6 runs at 2000 hands) to confirm c-bet helps HU:
```bash
for bias in 0.05 0.85; do
  for seed in 42 142 242; do
    docker exec my-poker-face-hybrid-ai-backend-1 \
      python -m experiments.simulate_bb100 \
      --hands 2000 --seed $seed --opponent ManiacBot \
      --adaptation-bias $bias \
      > /tmp/phase6_6_hu/hu_bias${bias}_seed${seed}.log 2>&1 &
  done
done
```

Expectation: HU vs ManiacBot unchanged (Maniac has fold_to_cbet ~0, so pattern
doesn't fire). HU vs a hypothetical "ABCBot HU" would show big improvement,
but we don't currently sim that.

## Risks / gotchas

1. **fold_to_cbet sample size is sparse.** It only updates on c-bet faced
   events. In 6-max where hero is hero, opponents are c-bet by other opponents,
   not by hero. The 10-sample minimum might not get hit in a 1000-hand sim.
   **Mitigation**: relax to `cbet_faced_count >= 5` if validation shows the
   pattern never fires.

2. **The "was preflop aggressor" state needs careful resetting.** Edge
   case: hero raises preflop in hand N, then opponent re-raises and hero
   folds. Did hero "win" the preflop aggression? Plan says yes (they
   raised), but they then folded. Need to decide: should the flag persist
   to the flop in this case? Hero won't act on flop anyway (they folded),
   so probably moot. But verify in tests.

3. **Confidence-weighting may reduce effects on extreme opponents.** Vs
   ManiacBot (AF~83), intensity = 100% so no change. Vs borderline (AF=6),
   intensity = 10% so much weaker firing. Phase 6.5 validation was done with
   binary firing — Phase 6.6 may show slightly different numbers due to the
   smoothing. Compare TAG result carefully.

4. **C-bet pattern only fires postflop.** The detection works generally
   (any time facing the opponent), but the offsets only fire in the specific
   "hero as preflop aggressor on flop" spot. Most decisions won't trigger
   it — by design.

## Effort estimate

- `AggregatedOpponentStats` + manager aggregation extension: **0.5 day**
- `compute_pattern_intensity` + integration: **0.5 day**
- New c-bet pattern + DecisionContext flag: **0.5 day**
- Controller-level preflop-aggressor tracking: **0.5 day**
- Tests (~15 new unit + integration tests): **0.5 day**
- Validation runs + analysis + doc updates: **0.5 day**

**Total: ~3 days** (one focused session).

## Out of scope

- Equity-gated value/bluff distinction (the bigger structural fix). Deferred
  to future work (~1 week).
- Per-opponent (not aggregate) exploitation. Already partly implemented via
  per-aggressor selection in `_select_exploitation_stats`; could refine
  further but not part of this plan.
- Delayed barrels (cbet on turn after checking flop). Same pattern principle
  but different decision context. Add later if validation justifies.

## Files to create / modify

| File | Action | Description |
|---|---|---|
| `poker/strategy/exploitation.py` | Modify | Add fold_to_cbet to stats, new pattern, confidence intensity |
| `poker/memory/opponent_model.py` | Modify | Extend `aggregate_active_opponents` with fold_to_cbet |
| `poker/tiered_bot_controller.py` | Modify | Add preflop-aggressor tracking + context flag |
| `experiments/analyze_6max_vs_rules.py` | Modify | Add new counter to diagnostics |
| `tests/test_strategy/test_exploitation.py` | Extend | Pattern + intensity tests |
| `tests/test_strategy/test_tiered_bot_exploitation.py` | Extend | Preflop-aggressor tests |
| `tests/test_memory/test_opponent_aggregation.py` | Extend | fold_to_cbet in aggregate |

## Reproducibility setup (for the next session)

Validation sweep command and expected baseline numbers are in the validation
section above. Current commit before starting this work should be at or
after `67b947bd docs: short-stack heuristic smoke validation`.

Key prior commits to be aware of (the foundation this builds on):
```
67b947bd docs: short-stack heuristic smoke validation
848f25c7 feat: short-stack heuristic (Phase 6 Step B)
dd33b514 feat: enable --adaptation-bias on HU sim path
7a3098b1 tune: Phase 6.5 override thresholds across archetypes (v4)
2d68c74a fix: persist opponent tendency state
5b5a5ac7 fix: call record_hand_dealt in MemoryManager.on_hand_start
385e3f8c feat: Phase 6 + 6.5 opponent exploitation
```
