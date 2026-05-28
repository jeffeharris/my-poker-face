---
purpose: Safe first-pass plan for heads-up c-bet exploitation and confidence-weighted offsets
type: design
created: 2026-05-13
last_updated: 2026-05-13T18:00:00
---

# Phase 6.6: HU c-bet exploitation + confidence-weighted offsets

## Sequencing & cross-plan dependencies

This plan ships **first** among the three open plans on the opponent-modeling
pipeline:

1. **Phase 6.6 (this plan)** — HU c-bet + confidence-weighted offsets.
2. [Phase 6.7](PHASE_6_7_OPPONENT_SPOTS.md) — independent multiway opponent spots.
3. [Phase 7.5](PHASE_7_5_ADJUSTMENT_LAYER_WIDENING.md) — adjustment-layer
   widening (three-tier clamp + bluff-catch override).

Shared types this plan touches (also touched by 6.7 and 7.5 later):

- `AggregatedOpponentStats` in `poker/strategy/exploitation.py` —
  6.6 adds `fold_to_cbet`, `cbet_faced_count`; 7.5 will add
  `all_in_per_decision`, `facing_bet_opportunities`. Order them in a stable
  block so the later PR has a clean diff.
- `DecisionContext` in `poker/strategy/exploitation.py` —
  6.6 adds `is_flop_as_preflop_aggressor`, `active_opponent_count`;
  6.7 will add `facing_aggressor_name`; 7.5 will add `bet_size_pot_ratio`.

Coordination notes:

- 6.6 introduces `compute_pattern_intensity()` (continuous [0,1]). 7.5 will
  introduce `_determine_clamp()` (discrete tiers). The two live at different
  pipeline layers (offset shaping vs L1 clamping) so they coexist, but 7.5's
  MEDIUM threshold (AF≥4) sits below 6.6's hyper_aggressive ramp start (AF=5).
  That's intentional — 7.5 calibrates after Step 0 instrumentation and may
  revisit 6.6's ramp start.
- 6.6's hyper_aggressive ramp uses the existing per-hand `all_in_frequency`.
  When 7.5 adds opportunity-normalized `all_in_per_decision`, the ramp will
  need to migrate to the new denominator. Out of scope for 6.6 but flagged
  here so the migration isn't missed.
- `classify_detected_patterns()` stays binary in 6.6 (Phase 6.5 value override
  depends on it). 7.5's bluff-catch override adds a separate tier-based gate;
  it does NOT consume softened intensity from 6.6.

## Context

Phase 6 (exploitation offsets) and Phase 6.5 (value override) are shipped and
validated in `docs/analysis/PHASE_6_VALUE_OVERRIDE_RESULTS.md`. The current
system is materially better against the 5-rule-bot mix, but the next plan needs
to avoid two failure modes:

- Multiway table averages can point the bot at the wrong opponent. A single
  foldy player should not make us bluff into a station.
- Reusing softened pattern detection in `classify_detected_patterns()` would
  silently change value-override eligibility, because value override consumes
  those binary classifications today.

Phase 6.6 is therefore intentionally narrow: add heads-up c-bet exploitation and
smooth the strength of exploitation offsets, without changing value override and
without introducing multiway aggregate c-bet bluffs.

## Goal

A completed 6.6 should produce these outcomes:

- Detect high fold-to-cbet opponents when `fold_to_cbet > 0.60` and
  `cbet_faced_count >= MIN_CBET_FACED_FOR_DETECTION`.
- Fire the c-bet exploit only in heads-up flop c-bet spots where hero is the
  last preflop aggressor and no bet is currently facing hero.
- Scale existing exploitation offsets by confidence intensity so borderline
  patterns apply weakly while extreme patterns still apply at full strength.
- Preserve Phase 6.5 value override behavior unless a separate, explicit
  override-retuning task changes it.
- Keep existing strategy and memory tests passing.

## Non-Goals

- No multiway c-bet exploitation in 6.6. That belongs in Phase 6.7, where each
  active opponent can be modeled independently.
- No `DecisionContext.aggressor_name` refactor yet. Fix only what this phase
  needs.
- No change to `value_override.py` eligibility semantics.
- No broad archetype-probability or Beta-prior rewrite.

## Resolved Decisions

- Use progressive sample confidence for c-bet. Start allowing weak signal at 5
  faced c-bets and reach full sample confidence at 10.
- Use mechanism/no-regression validation as the merge bar. Add HU foldy-opponent
  validation if it is cheap, but do not block 6.6 on a noisy bb/100 gain.
- Defer multiway c-bet exploitation to 6.7, where the default poker policy is
  conservative: bluff only when all relevant continuing opponents are foldy.
- Keep last-preflop-aggressor state on `MemoryManager`, not
  `OpponentModelManager`. `MemoryManager` already owns `_preflop_raiser`; adding
  a second hand-level source of truth would create drift risk.
- Treat accepted preflop `all_in` actions as aggression for the c-bet-aggressor
  flag. The current memory path already tracks `raise`; 6.6 should extend that
  condition to `("raise", "all_in")`.

## Design

### 1. Extend stats for c-bet samples

`poker/strategy/exploitation.py` should carry c-bet fields on the stats object
used by exploitation:

```python
@dataclass(frozen=True)
class AggregatedOpponentStats:
    hands_observed: int = 0
    vpip: float = 0.5
    pfr: float = 0.5
    aggression_factor: float = 1.0
    all_in_frequency: float = 0.0
    fold_to_cbet: float = 0.5
    cbet_faced_count: int = 0
```

`OpponentModelManager.aggregate_active_opponents()` can populate these for
diagnostics and compatibility, but 6.6 must not consume aggregate c-bet stats in
multiway pots. The c-bet rule should only use a selected opponent when exactly
one active opponent remains.

### 2. Keep binary classification stable

Add a new intensity function, but do not replace `classify_detected_patterns()`
with it yet.

```python
def compute_pattern_intensity(stats: AggregatedOpponentStats) -> Dict[str, float]:
    """Return per-pattern offset intensity in [0, 1]."""
```

Use `compute_pattern_intensity()` only inside
`compute_exploitation_offsets()`. Existing binary helpers and
`classify_detected_patterns()` should continue to represent hard detection for
diagnostics and Phase 6.5 value override.

Recommended ramps:

- `hyper_aggressive`: AF ramps from `5.0 -> 15.0`; all-in frequency ramps from
  `0.30 -> 0.70`; use the max.
- `hyper_passive`: require AF `< 0.80`, then VPIP ramps from `0.60 -> 0.90`.
- `tight_nit`: VPIP ramps down from `0.15 -> 0.05`.
- `high_fold_to_cbet`: c-bet sample confidence ramps from 5 faced c-bets to 10
  faced c-bets; fold-to-cbet rate ramps from `0.60 -> 0.85`; multiply the two.

Use a clamped helper so all intensities stay in `[0.0, 1.0]`.

### 3. Add a heads-up c-bet context

Extend `DecisionContext` with enough information to gate the c-bet exploit:

```python
@dataclass(frozen=True)
class DecisionContext:
    is_preflop: bool = False
    facing_all_in: bool = False
    facing_big_bet: bool = False
    is_flop_as_preflop_aggressor: bool = False
    active_opponent_count: int = 0
```

`is_flop_as_preflop_aggressor` is true only when all of these are true:

- Current phase is `FLOP`.
- Hero was the last preflop aggressor.
- `call_amount == 0`.
- Hero has a legal bet/raise action.
- Hero has not already acted on this flop betting round, if that signal is
  available.

The c-bet exploit additionally requires `active_opponent_count == 1`.

### 4. Track the last preflop aggressor from accepted actions

Do not set last-aggressor state from controller intent before action validation.
Set it from the same path that records accepted actions.

Recommended shape:

```python
class MemoryManager:
    _preflop_raiser: Optional[str] = None

    def record_preflop_aggression(self, player_name: str) -> None:
        self._preflop_raiser = player_name

    @property
    def last_preflop_aggressor(self) -> Optional[str]:
        return self._preflop_raiser
```

This is an API exposure and small condition expansion over existing state, not a
new state variable. `_preflop_raiser` already resets at hand start; keep that
reset path and wrap the existing internal field.

Call this after a preflop raise/all-in is accepted:

- Production path: from `MemoryManager.on_action()` or the existing
  post-`play_turn()` action-recording path. The current `action == "raise"`
  condition should become `action in ("raise", "all_in")`.
- Simulation path: from the same `MemoryManager` action-observation path when
  possible; only add a controller fallback if a sim bypasses memory/action
  recording.

Do not expand c-bet-facing detection in this phase. If current c-bet observation
only treats postflop `("bet", "raise")` as c-bets, all-in c-bets are a separate
latent gap and should be handled in a follow-up.

Reset this hand-level field at hand start via `record_hand_dealt()` or equivalent
hand-start state reset. This state should not be persisted across hands.

The opponent model manager can still own cross-hand opponent tendencies and
aggregate c-bet stats. It should not also own the current hand's last preflop
aggressor.

### 5. Add high-fold-to-cbet offsets

Detection:

```python
HIGH_FOLD_TO_CBET_THRESHOLD = 0.60
MIN_CBET_FACED_FOR_DETECTION = 5
FULL_CBET_SAMPLE_CONFIDENCE = 10

def _is_high_fold_to_cbet(stats: AggregatedOpponentStats) -> bool:
    return (
        stats.fold_to_cbet > HIGH_FOLD_TO_CBET_THRESHOLD
        and stats.cbet_faced_count >= MIN_CBET_FACED_FOR_DETECTION
    )

def _cbet_sample_confidence(cbet_faced_count: int) -> float:
    return _ramp(cbet_faced_count, 5, 10)
```

The intensity used by `compute_exploitation_offsets()` should be:

```python
rate_intensity = _ramp(stats.fold_to_cbet, 0.60, 0.85)
sample_confidence = _cbet_sample_confidence(stats.cbet_faced_count)
cbet_intensity = rate_intensity * sample_confidence
```

Offset behavior:

```python
if intensities.get("high_fold_to_cbet", 0.0) > 0.0:
    if (
        decision_context.is_flop_as_preflop_aggressor
        and decision_context.active_opponent_count == 1
    ):
        intensity = intensities["high_fold_to_cbet"]
        for action in available_actions:
            if action.startswith("bet_"):
                offsets[action] = offsets.get(action, 0.0) + 0.4 * multiplier * intensity
        if "check" in available_actions:
            offsets["check"] = offsets.get("check", 0.0) - 0.3 * multiplier * intensity
```

This is deliberately conservative. Multiway c-bet logic needs per-opponent
semantics and is deferred to 6.7.

### 6. Counters

Add or extend diagnostics so validation can separate detection from firing:

- `detected_high_fold_to_cbet`: hard pattern detection.
- `fired_high_fold_to_cbet`: c-bet pattern contributed non-zero offsets.
- `flop_as_preflop_aggressor_spots`: hero reached a potential c-bet spot.
- `heads_up_cbet_spots`: the potential c-bet spot was heads-up.
- Optional: average fired intensity by pattern.

## Tests

Add or update tests in these areas:

- `tests/test_strategy/test_exploitation.py`: c-bet detection threshold,
  progressive sample confidence at 5/7/10 samples, c-bet offsets only in c-bet
  context, confidence ramp zero/mid/full.
- `tests/test_strategy/test_tiered_bot_exploitation.py`: last preflop aggressor
  is set from accepted raises, reset at new hand, hero is not c-bet aggressor
  after calling a 3-bet, accepted preflop all-ins update last aggressor, c-bet
  exploit skips multiway.
- `tests/test_memory/test_opponent_aggregation.py`: c-bet fields survive the
  stats path used by exploitation.

Include a regression test that confirms `classify_detected_patterns()` remains
binary and does not expose softened intensity semantics to value override.

## Validation

Mechanism gates come before bb/100 outcomes and are sufficient for merge if
there is no regression:

- `flop_as_preflop_aggressor_spots > 0`.
- `heads_up_cbet_spots > 0`.
- `fired_high_fold_to_cbet > 0` in at least one run with a foldy opponent and
  enough c-bet samples.
- Existing Phase 6.5 value-override counters remain in the expected range.

Outcome gates:

- TAG 6-max at `adaptation_bias=0.85` remains net positive versus the 5-rule-bot
  mix.
- HU versus ManiacBot is unchanged within noise; ManiacBot should not trigger the
  c-bet exploit.
- If an ABCBot/GTO-Lite heads-up sim exists or is easy to add, use that as an
  extra c-bet improvement check. Do not expect HU versus ManiacBot to prove
  c-bet improvement, and do not block merge on noisy c-bet bb/100 movement.

## Risks

- `fold_to_cbet` samples may be sparse. Progressive sample confidence starts at
  5 faced c-bets and reaches full sample confidence at 10, so early firing is
  intentionally weak rather than binary.
- Last-aggressor tracking is hand-level metadata, not opponent tendency data.
  Mixing it into opponent model persistence would create stale state.
- Confidence weighting may reduce exploitation against borderline opponents. That
  is intended, but compare Phase 6.5 validation before merging.
- Aggregated active-opponent stats are still present for compatibility. Do not
  infer that aggregate c-bet exploitation is safe multiway.

## Effort Estimate

- C-bet fields and detection: 0.5 day.
- Confidence intensity function and offset integration: 0.5 day.
- Last-preflop-aggressor hand-state wiring: 0.5-1 day.
- Heads-up c-bet gating and counters: 0.5 day.
- Tests: 0.5-1 day.
- Validation and tuning: 1 day.

Expected total: 3-4 days.

## Files To Modify

| File | Action | Description |
|---|---|---|
| `poker/strategy/exploitation.py` | Modify | Add c-bet stats, c-bet pattern, intensity-weighted offsets |
| `poker/memory/opponent_model.py` | Modify | Surface c-bet fields for exploitation stats |
| `poker/memory/memory_manager.py` | Modify | Record accepted preflop raise/all-in as last aggressor |
| `poker/tiered_bot_controller.py` | Modify | Build heads-up c-bet context and counters |
| `experiments/analyze_6max_vs_rules.py` | Modify | Include new counters in diagnostics |
| `tests/test_strategy/test_exploitation.py` | Extend | Pattern, sample, and intensity tests |
| `tests/test_strategy/test_tiered_bot_exploitation.py` | Extend | Context and last-aggressor tests |
| `tests/test_memory/test_opponent_aggregation.py` | Extend | C-bet fields in stats path |

## Open Questions

- If validation shows progressive c-bet firing is still too sparse, should the
  ramp start lower than 5 or should we collect more c-bet observations first?
