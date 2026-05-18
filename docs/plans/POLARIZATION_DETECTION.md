---
purpose: Spec for opponent-aggression-polarization detection and its consumer rules
type: spec
created: 2026-05-17
last_updated: 2026-05-18
---

# Polarization Detection

## Background

TieredBot's `hyper_passive` exploit treats every "calling station" the same: push raises to extract value, **and** reduce folds because stations bet/lead light. The fold-reduction half is correct against noisy callers (a fish who calls too much AND bets/raises too much with junk) but disastrous against polarized value-callers (CaseBot-style: calls too much, but raises only with strong hands).

We confirmed this empirically against game `h66vGnzs4ccmLq0UcDaSMA`:

- CaseBot median equity when **raising** postflop: 0.82 river, 0.66 flop — pure value
- CaseBot median equity when **calling** postflop: 0.30–0.44 — marginal
- TieredBots' biggest single-decision EV losses came from calling these raises with junk equity

The frequency-only opponent model can't distinguish polarized from noisy. Both look like "loose passive with low aggression factor." Phase A instruments the missing signal (equity-at-action means, populated at showdown). Phase B gates the existing `hyper_passive` fold-reduction on that signal. Phases C and D add affirmative rules for the polarized and bluffer extremes.

---

## Phase A status: shipped

`feat(memory): Polarization Phase A — equity-at-action instrumentation` (commit `41b6ab27`).

`OpponentTendencies` now carries:

- `equity_when_betting_postflop: float = 0.5` (neutral prior)
- `equity_when_raising_postflop: float = 0.5`
- `equity_when_calling_postflop: float = 0.5`
- Plus matching `_*_sum` and `_*_count` for incremental running-mean updates

Populated by `AIMemoryManager._record_showdown_equity_at_actions` after every showdown: walk each revealed player's postflop actions, compute their equity-vs-random at that decision point (using `DecisionAnalyzer.calculate_equity_vs_random`), credit into every observer's `OpponentModel` of them. Round-trip through `to_dict` / `from_dict`. Legacy snapshots predating Phase A restore cleanly with neutral defaults.

12 dedicated tests; full memory suite (175 tests) clean.

---

## The signal: `aggression_polarization`

```
aggression_polarization = equity_when_raising_postflop - equity_when_calling_postflop
```

- **High positive** (e.g., > 0.30): polarized value-caller. CaseBot signature.
- **Near zero** (e.g., −0.05 to +0.05): noisy aggression. Standard fish.
- **Negative** (e.g., < −0.05): bluffer. Raises weaker than they call.

### Threshold proposal

| Threshold | Value | Meaning |
|---|---|---|
| `POLARIZATION_HIGH` | **0.25** | Confirmed value-polarized — gate the hyper_passive fold-reduction off |
| `POLARIZATION_LOW` | **−0.05** | Confirmed bluffer-leaning — Phase D candidate |
| `MIN_SAMPLE_FOR_GATE` | **8 observations** per bucket | Below this, the signal stays at neutral; rule fires unchanged |

Rationale:
- 0.25 chosen well below the CaseBot data point (~0.45 measured) so partially-polarized players still get caught, but above the noise floor of measurement variance with small samples.
- −0.05 is conservative for the bluffer side — we'd rather mis-classify a balanced player as noisy than mis-classify a value-better as a bluffer (which would trigger the symmetric exploit against them, costing chips).
- 8 observations per bucket gives a stable enough mean to distinguish 0.25 polarization from noise. At fewer samples the standard error of the mean is too large to gate on confidently.

Thresholds will calibrate further once Phase A has accumulated real sim data; the values above are reasonable defaults to ship Phase B against.

---

## Phase B status: shipped (code), measurement gate pending

`compute_aggression_polarization` helper + hyper_passive split landed
2026-05-18. The value-extraction half (`raise_like += 0.3 * scale`)
always fires when `hyper_passive_intensity > 0` and the
non-all-in-station-continuing flag is set. The fold-reduction half
(`fold -= 0.2 * scale`) fires only when the per-opponent polarization
signal is below `POLARIZATION_HIGH = 0.25` or insufficient sample is
available — in which case `polarization_gate ∈ {'noisy_station',
'insufficient_sample'}` and the rule's behavior is unchanged.

When `polarization_gate == 'polarized_station'`, only the raise push
fires and the §5.5 budget (`('exploitation', 'hyper_passive'): 0.80`)
continues to clamp the value-extraction half if it overshoots — the
test `test_budget_clamp_still_applies_to_polarized_half` pins this.

`AggregatedOpponentStats` now propagates the Phase A equity fields
(`equity_when_raising_postflop`, `equity_when_calling_postflop`, plus
matching `_equity_*_count`) end-to-end:

- `_build_aggregate_from_single` → verbatim copy from `OpponentTendencies`
- `_build_aggregate_from_multi` → equal-weight average / MIN counter
  (legacy path)
- `aggregate_from_spots` → stake-weighted average / MIN counter
  (canonical spot-aware path)
- `_copy_stats` → verbatim for single-opponent / 60%-dominant branches
- `TieredBotController._build_opponent_spots` → reads from tendencies
  with `getattr`-with-default for SimpleNamespace test compatibility

Diagnostic surface on `rule_context[('exploitation', 'hyper_passive')]`
gains `polarization_gate`, `polarization`, `equity_raising_count`,
`equity_calling_count` (under `inputs`, so they propagate to
`InterventionTrace.inputs`).

Tests: `tests/test_strategy/test_polarization.py` (24 tests, green).
Memory + strategy regression suites green except for two pre-existing
Track B `apply_relationship_modifier` failures unrelated to Phase B.

The measurement gate (bb/100 sims per archetype vs CaseBot) is the
next step.

---

## Phase B: gate `hyper_passive` fold-reduction

The two halves of `hyper_passive` ship today as a coupled rule:

```python
if hyper_passive_intensity > 0.0:
    for action in raise_like:
        _add(key, action, +0.3 * scale)         # value extraction (always correct)
    if 'fold' in available_actions:
        _add(key, 'fold', -0.2 * scale)         # fold reduction (wrong vs polarized)
```

Phase B splits them:

```python
if hyper_passive_intensity > 0.0:
    # Value extraction always fires — works against any flavor of station
    for action in raise_like:
        _add(key, action, +0.3 * scale)

    # Fold reduction fires ONLY when the station is not polarized
    polarization = compute_aggression_polarization(stats)
    if polarization < POLARIZATION_HIGH:
        if 'fold' in available_actions:
            _add(key, 'fold', -0.2 * scale)
        rule_context[key]['polarization_gate'] = 'noisy_station'
    else:
        rule_context[key]['polarization_gate'] = 'polarized_station'
        rule_context[key]['polarization'] = round(polarization, 3)
```

### `compute_aggression_polarization` helper

A pure function on `AggregatedOpponentStats`:

```python
def compute_aggression_polarization(stats: AggregatedOpponentStats) -> float:
    """Returns equity_when_raising - equity_when_calling, gated on sample.

    Returns 0.0 (neutral) when either bucket has fewer than
    MIN_SAMPLE_FOR_GATE observations — same neutral-prior shape as
    other gated stats in this module.
    """
    if (stats._equity_raising_count < MIN_SAMPLE_FOR_GATE
            or stats._equity_calling_count < MIN_SAMPLE_FOR_GATE):
        return 0.0
    return (
        stats.equity_when_raising_postflop
        - stats.equity_when_calling_postflop
    )
```

**Aggregation across multi-way pots**: `aggregate_from_spots` extends to stake-weight the equity-at-action means the same way it already does for VPIP / AF (per the stake-weighted aggregation work that landed in commit `11dd7d7a`). `_equity_*_count` uses MIN across active opponents (limiting-factor sample), matching the existing `cbet_faced_count` policy.

### Diagnostic surface

`rule_context` for `hyper_passive` gains:

- `polarization_gate`: one of `'noisy_station'` (fold-reduction fired), `'polarized_station'` (fold-reduction suppressed), or `'insufficient_sample'` (gate inactive)
- `polarization`: the computed signal (rounded to 3 decimals)
- Existing `inputs` block keeps its current keys (vpip, AF, all_in_frequency, passive_with_jams)

### Measurement gate

After Phase B ships, run a 2000-hand sim per archetype (Rock / TAG / LAG) vs CaseBot and compare bb/100 to the **post-patch baseline measured 2026-05-18** (commits `11dd7d7a` + `2c09c686` shipped):

| Archetype | Pre-patch | Post-patch baseline | Phase B expectation |
|---|---|---|---|
| Rock | −58.8 | **−82.2** [−112.8, −51.5] | Recover toward / past pre-patch baseline. The all-in gate's edge cases are clipping some of Rock's value extraction; the polarization gate preserves the raise-push half so this should reverse. |
| TAG | −84.3 | **−17.8** [−58.9, +23.3] | Already near-breakeven from the upstream patches. Phase B should hold or modestly improve — limited room to push the signal that's mostly fired already. CI already includes zero. |
| LAG | −129.0 | **−94.4** [−142.7, −46.0] | Modest gain expected. LAG's residual leak is structural (base strategy spews vs tight-back opponents) — Phase B helps marginally but won't close. The real LAG fix is upstream in the deviation profile. |

The Rock recovery is the most telling Phase B signal — it isolates the value-extraction vs fold-reduction split. If Rock comes back to ~−60 or better while TAG stays at/around zero, the polarization gate is doing exactly what it's designed to do.

---

## Phase C: affirmative `polarized_value_caller` rule

Once Phase B is shipped and measured, add a new exploitation rule that **actively encourages folds** against polarized stations rather than just suppressing the fold-reduction. The asymmetry matters: hyper_passive treats stations like "call wide because they call wide," but the polarized variant should treat their bets like a tight player's bets — the right response is to fold marginals.

```python
# In RULE_ORDER, between hyper_passive and value_vs_station:
('exploitation', 'polarized_value_caller'),
```

Rule body:

```python
polarization = compute_aggression_polarization(stats)
if (
    polarization > POLARIZATION_HIGH
    and ('exploitation', 'polarized_value_caller') not in disabled_keys
    and decision_context.facing_bet  # only when hero is deciding whether to call/fold
):
    scale = multiplier * confidence_ramp_for_polarization(stats)
    key = ('exploitation', 'polarized_value_caller')
    if 'fold' in available_actions:
        _add(key, 'fold', +0.2 * scale)  # actively fold more
```

`confidence_ramp_for_polarization` scales by `min(min_sample, RAMP_END) / RAMP_END` where RAMP_END is something like 24 observations — full strength at 24+ samples per bucket, ramping in from 8. This prevents the rule from firing at full strength with thin data.

**Budget**: add `('exploitation', 'polarized_value_caller'): 0.30` to `MAX_L1_SHIFT_BY_RULE`. The §5.5 framework already handles the rest.

---

## Phase D: `bluffer_detection`

Symmetric to Phase C, for the negative-polarization end of the spectrum. When `polarization < POLARIZATION_LOW`, the opponent raises with junk more than they call with strong — i.e., they're bluffing. Hero should fold less and call more vs their aggression.

```python
polarization = compute_aggression_polarization(stats)
if (
    polarization < POLARIZATION_LOW
    and decision_context.facing_bet
    and ('exploitation', 'bluffer_detection') not in disabled_keys
):
    scale = multiplier * confidence_ramp_for_polarization(stats)
    key = ('exploitation', 'bluffer_detection')
    if 'call' in available_actions:
        _add(key, 'call', +0.15 * scale)
    if 'fold' in available_actions:
        _add(key, 'fold', -0.15 * scale)
```

Phase D is gated behind a feature flag (`enable_bluffer_detection`) until we have data confirming the rule doesn't conflict with the existing `hyper_aggressive` detector. A maniac is also a bluffer; we don't want both rules firing on the same opponent and double-counting the fold-reduction.

---

## What's still ambiguous (open questions)

1. **Stake-weighted vs equal-weight aggregation of equity means**. The current `aggregate_from_spots` (commit `11dd7d7a`) stake-weights rate fields. The equity-at-action means *could* follow that pattern, or stay equal-weight on the theory that a player with more chips committed isn't necessarily a better representative of "the station I care about." Default to stake-weighting (consistent with the rest of the aggregate) unless Phase B sims reveal a problem.

2. **MIN_SAMPLE_FOR_GATE = 8 is a guess**. Could be 5 (more reactive, more noise) or 12 (more confident, slower). Pick 8 to ship and tune from sim data.

3. **Confidence ramp endpoint**. RAMP_END = 24 means full rule strength at 24 observations per bucket. A 2000-hand sim against a varied opponent set should yield 30-60 postflop showdown samples per archetype per bucket, so 24 should hit easily. May need to drop to 16 if real-game sample sizes turn out smaller.

4. **Multi-way variance**: with 6 players each watching every other, the per-observer count grows quickly, but per-(observer, opponent) pair samples are sparser. The polarization signal needs the per-pair view (to credit ids properly). 8 samples per bucket per pair is a real constraint.

---

## Approval gate

Phase B coding begins after the user reviews this spec and approves. Specifically requesting sign-off on:

- The threshold values (`POLARIZATION_HIGH = 0.25`, `POLARIZATION_LOW = −0.05`, `MIN_SAMPLE_FOR_GATE = 8`)
- The Phase B integration shape (split hyper_passive into two halves, gate the fold-reduction half)
- The Phase C and D names + magnitudes (these will be coded after Phase B's measurement gate)
- The default to stake-weighted aggregation for the new equity fields

Once approved, Phase B is ~half a day of coding + one sim run + bb/100 comparison.

---

## Related code surfaces

| File | What it owns |
|---|---|
| `poker/memory/opponent_model.py` (Phase A) | OpponentTendencies fields, update_equity_at_action, serialization |
| `poker/memory/memory_manager.py` (Phase A) | Showdown replay, equity-vs-random computation, observer iteration |
| `poker/strategy/exploitation.py` (Phase B) | compute_aggression_polarization helper, hyper_passive split, polarized_value_caller rule (Phase C) |
| `poker/strategy/exploitation.py` `aggregate_from_spots` (Phase B) | Extend stake-weighting to the new equity fields |
| `poker/strategy/intervention_trace.py` (Phase B) | Add `polarized_value_caller` to `_RULE_IDS_BY_LAYER` |
| `tests/test_strategy/test_polarization.py` (Phase B, new file) | Threshold gate tests, edge cases, integration with the §5.5 budget |
