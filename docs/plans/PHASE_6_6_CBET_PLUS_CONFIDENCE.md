---
purpose: Plan to add c-bet exploitation pattern and confidence-weighted firing to Phase 6/6.5 exploitation
type: design
created: 2026-05-13
last_updated: 2026-05-13T14:00:00
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

The flag is True when ALL of these hold (per Codex review — looser conditions
mis-identify c-bet spots):

- Current phase is FLOP (postflop street #1)
- Hero was the **LAST** preflop aggressor (not just any raiser — if hero
  raised then got 3-bet, hero is NOT the aggressor anymore)
- No one has bet on this street yet (call_amount == 0)
- Hero has a legal bet action available
- Hero has not yet acted on this street

If any condition fails, the flag is False and the c-bet rule doesn't fire.

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

### Step 5: Detect "hero was the LAST preflop aggressor"

The hardest piece. Per Codex review, naive "did hero raise preflop?"
tracking is wrong — it can stay True even when hero raised then got
re-raised then folded, or for a hand where hero raised then opponent
4-bet (opponent is now the aggressor, not hero).

**Track last_preflop_aggressor_name on the manager (not just hero state).**

The cleanest signal: who put in the last preflop raise this hand. Stored
on `opponent_model_manager` (persists across the hand naturally):

```python
# On the manager:
self._current_hand_last_preflop_aggressor: Optional[str] = None
```

Set when we observe ANY player's preflop raise via `observe_action`:

```python
def observe_action(self, observer, opponent, action, phase, ...):
    if phase == 'PRE_FLOP' and action in ('raise', 'all_in'):
        self._current_hand_last_preflop_aggressor = opponent
    ...
```

Reset at hand start via `record_hand_dealt` (which already exists and is
called per-opponent at hand-start in both sim and production).

Hero's own preflop raises ALSO need to update this. **Note** (Codex
review): treating hero as their own "opponent" in `observe_action` is
architecturally awkward — the observer/opponent keyed structure is for
opponent TENDENCIES, not hand-level metadata. Use a separate API on the
manager for hand-level state:

```python
# On OpponentModelManager:
self._current_hand_last_preflop_aggressor: Optional[str] = None

def record_preflop_aggression(self, player_name: str):
    """Record that this player just raised preflop. Pure hand-level
    metadata — NOT stored in opponent_models[] (which tracks tendencies
    for cross-hand stats).
    """
    self._current_hand_last_preflop_aggressor = player_name

# Also extend observe_action to set this when phase=='PRE_FLOP' and
# action in ('raise', 'all_in') — for the sim observation flow that
# already feeds opponent actions through observe_action.
```

Called from controller after sampling a preflop raise:

```python
# In _get_ai_decision preflop path after sampling:
if abstract_action.startswith(('raise_', 'jam')):
    if manager is not None:
        manager.record_preflop_aggression(self.player_name)
```

At decision time on the flop, build the context:

```python
def _is_flop_as_preflop_aggressor(self, game_state):
    phase = self.state_machine.current_phase
    if phase is None or phase.name != 'FLOP':
        return False

    manager = getattr(self, 'opponent_model_manager', None)
    if manager is None:
        return False
    last_aggressor = getattr(manager, '_current_hand_last_preflop_aggressor', None)
    if last_aggressor != self.player_name:
        return False

    # No one has bet on this street yet
    call_amount = getattr(game_state, 'call_amount', 0) or 0
    if call_amount > 0:
        return False

    # Hero has a legal bet action.
    # NOTE for implementer: verify the actual attribute used by
    # TieredBotController._get_ai_decision for valid_actions. The pipeline
    # currently uses `game_state.current_player_options` but the postflop
    # path may normalize differently. Match the exact source the rest of
    # the pipeline uses to avoid stale-flag drift.
    valid_actions = game_state.current_player_options
    if 'raise' not in valid_actions and 'bet' not in valid_actions:
        # Allow 'raise' since bet/raise are sometimes conflated in our engine
        return False

    return True
```

Edge cases verified by tests:
- Hero raises → opp 3-bets → hero folds: flag stays False on flop (hero
  not active anyway, but the manager correctly reports opp as last aggressor)
- Hero raises → opp 3-bets → hero calls: opponent is last aggressor → flag False
- Hero raises → opp calls → flop: hero is last aggressor → flag True
- Hand restarts in preflop (someone busts, hand restarts): reset via
  record_hand_dealt at new hand_number
- Controller persists across games: manager's `_current_hand_last_preflop_aggressor`
  is on the MANAGER which is per-game; cross-game persistence via DB
  serializes hand-level state separately. Not affected.

### Step 6: Confidence-weighted firing

Replace binary pattern detection with continuous intensity. New function:

```python
def _ramp(value: float, zero_point: float, full_point: float) -> float:
    """Linear ramp clamped to [0, 1].

    Returns 0 when value <= zero_point, 1 when value >= full_point, and
    linear interpolation between. Order of (zero_point, full_point) sets
    the direction:
      _ramp(af, 5.0, 15.0): increases with af above 5
      _ramp(vpip, 0.15, 0.05): increases as vpip decreases below 0.15
    """
    if zero_point == full_point:
        return 1.0 if value >= zero_point else 0.0
    raw = (value - zero_point) / (full_point - zero_point)
    return max(0.0, min(1.0, raw))


def compute_pattern_intensity(stats: AggregatedOpponentStats) -> Dict[str, float]:
    """For each detected pattern, return intensity in [0, 1].

    Smooth ramp from threshold (0% intensity) to "clearly extreme" (100%).
    Replaces the binary _is_X functions for use in offset computation.
    Patterns with intensity 0 are not present in the returned dict
    (callers should treat absence as zero-intensity).
    """
    intensities = {}

    # Hyper-aggressive: take max of AF ramp and all-in-freq ramp
    af_intensity = _ramp(stats.aggression_factor, 5.0, 15.0)
    ai_intensity = _ramp(stats.all_in_frequency, 0.30, 0.70)
    hyper_agg = max(af_intensity, ai_intensity)
    if hyper_agg > 0.0:
        intensities['hyper_aggressive'] = hyper_agg

    # Hyper-passive: VPIP ramp from 0.6 to 0.9, AND AF < 0.8 hard gate
    if stats.aggression_factor < 0.80:
        passive_intensity = _ramp(stats.vpip, 0.60, 0.90)
        if passive_intensity > 0.0:
            intensities['hyper_passive'] = passive_intensity

    # Tight nit: VPIP ramp from 0.15 (0%) DOWN to 0.05 (100%)
    nit_intensity = _ramp(stats.vpip, 0.15, 0.05)
    if nit_intensity > 0.0:
        intensities['tight_nit'] = nit_intensity

    # High fold-to-cbet: ramp from 0.60 to 0.85, with sample size gate
    if stats.cbet_faced_count >= MIN_CBET_FACED_FOR_DETECTION:
        cbet_intensity = _ramp(stats.fold_to_cbet, 0.60, 0.85)
        if cbet_intensity > 0.0:
            intensities['high_fold_to_cbet'] = cbet_intensity

    return intensities
```

The `_ramp` helper handles the clamping cleanly (Codex flagged the
original `(x - threshold) / range` formula could go negative; the wrapped
`max(0.0, min(1.0, ...))` is the safe form).

Then in `compute_exploitation_offsets`, multiply each pattern's offsets by
its intensity:

```python
intensities = compute_pattern_intensity(stats)
# ... when applying hyper_aggressive offsets:
multiplier_with_intensity = multiplier * intensities.get('hyper_aggressive', 0.0)
```

Update `classify_detected_patterns` to delegate to intensity:

```python
def classify_detected_patterns(stats):
    """Return list of pattern names with intensity > 0.

    Delegates to compute_pattern_intensity. Used by the counter for
    diagnostic visibility (which patterns are firing at all). Magnitude
    information lives in the intensity dict and is consumed by
    compute_exploitation_offsets.
    """
    return list(compute_pattern_intensity(stats).keys())
```

This keeps a single source of truth: intensity tells you whether AND how
much a pattern fires.

### Step 7: Counter updates

Add to `_tally_exploitation_event`:
- `detected_high_fold_to_cbet` — opponent stats triggered the pattern
- `fired_high_fold_to_cbet` — pattern actually contributed offsets at a
  c-bet spot. **Critical**: detection alone isn't enough; the spot
  context (`is_flop_as_preflop_aggressor`) gates firing.
- `flop_as_preflop_aggressor_spots` — total count of decisions where
  the context flag was True (independent of pattern detection). Lets us
  see how often we actually reach c-bet spots.
- Average intensity per pattern when fired (optional, useful for tuning)

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

**Pass criteria** (mechanism gates first, outcome gates second — Codex
flagged that small-effect bb/100 changes are too noisy to be primary
gates):

**Mechanism gates (must pass first — confirm the wiring works):**
- `flop_as_preflop_aggressor_spots` > 50 per 1000 hands (validates we're
  REACHING c-bet spots — if this is 0, nothing else matters)
- `fired_high_fold_to_cbet` > 0 at least somewhere (cbet pattern actually
  contributed offsets)
- Existing tests still pass; no regressions in counter values

**Outcome gates (signal — bb/100 movement should be in the right direction):**
- Per-opponent BB transfer **vs ABCBot** improves by ≥15% from Phase 6.5
  baseline (was an ~25-27% drop from pre-Phase-6 baseline; recovering
  half is the realistic target)
- Per-opponent BB transfer **vs GTO-Lite** improves by ≥15%
- Per-opponent BB transfer vs ManiacBot unchanged (±10%) — c-bet doesn't
  fire vs them; confidence-weighting may produce slight shift
- Net bb/100 for TAG (bias=0.85) ≥ +20 (was +28 in Phase 6.5; small
  regression OK if c-bet helps mid-tier and confidence-weighting smooths
  edge cases)

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

2. **Last-aggressor tracking edge cases.** The updated design tracks the
   LAST preflop aggressor on the manager (overwritten by each subsequent
   preflop raise). Edge cases the tests should cover:
   - Hero raises → opp 3-bets → hero folds: manager records opp as last
     aggressor; hero is folded so doesn't act on flop anyway. Correct.
   - Hero raises → opp 3-bets → hero calls: manager records opp as last
     aggressor; on flop hero is NOT the last aggressor, so flag False.
     Correct (opp gets to c-bet, not hero).
   - Hero raises → opp calls → flop: manager records hero as last aggressor;
     on flop flag True. Correct.
   - Hand restarts in preflop (e.g. someone busts and the table reseats):
     `record_hand_dealt` resets the manager's hand-level state at new
     hand_number.

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
- Manager-level last-aggressor tracking + controller hook: **0.75 day**
  (was 0.5; bumped after Codex flagged edge cases)
- Tests (~15 new unit + integration tests): **0.5 day**
- Validation runs + analysis + chart calibration: **1 day**
  (was 0.5; bumped — validation may surface that c-bet pattern barely
  fires due to sparse fold_to_cbet samples; calibration to recover)
- Doc updates: **0.25 day**

**Total: ~4 days** (was 3 — Codex flagged the original was optimistic).

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
