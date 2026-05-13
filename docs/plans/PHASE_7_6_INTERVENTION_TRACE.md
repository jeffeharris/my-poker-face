---
purpose: Plan for a per-decision intervention-trace framework that unifies attribution and LLM narration
type: design
created: 2026-05-13
last_updated: 2026-05-13
---

# Phase 7.6: Intervention-trace framework

## Sequencing & cross-plan dependencies

Independent of Phase 8 — they touch different layers. 7.6 instruments
the decision pipeline; 8 adds new exploitation rules. If 7.6 ships
first, Phase 8's new rules automatically participate in the trace
without extra work. If 8 ships first, those rules need to be retrofitted
later. Recommend 7.6 first.

Builds on:
- Phase 6 ([`PHASE_6_OPPONENT_EXPLOITATION.md`](PHASE_6_OPPONENT_EXPLOITATION.md))
  — has per-rule aggregate counters (`manager._exploitation_counters`)
  that this plan generalizes into per-decision structured records.
- Phase 7.5 ([`PHASE_7_5_ADJUSTMENT_LAYER_WIDENING.md`](PHASE_7_5_ADJUSTMENT_LAYER_WIDENING.md))
  — already records `clamp_tier`, `winning_axis`, `bluff_catch_fired`
  in the diagnostic schema. This plan generalizes that pattern.

Consumed by:
- LLM expression layer (Layer 3 of the tiered bot architecture). Today
  the expression generator produces flavor text post-decision but has
  weak structured input for *why* the bot decided. The trace fixes that.

## Context

### The problem

Today the tiered bot pipeline is:

```
chart → personality distortion → exploitation offsets → strong-hand override
       → bluff-catch override → short-stack heuristic → math floor → sample
```

Each layer modifies (or replaces) the strategy distribution. We track
aggregate counters like `exploitation_fired_high_fold_to_cbet=1245`,
but we have **no per-decision attribution**:

- Why did this specific decision become `call` instead of `fold`?
- Which intervention shifted the distribution, by how much?
- What stats / context did the intervention observe at firing time?

Phase 7.5's validation was hampered by this — the bb/100 deltas vs
ManiacBot are dominated by hand-by-hand variance, and we can't cleanly
isolate the EV contribution of the bluff-catch override alone. The
"matched-seed paired sweep" approach (Phase 7.5 Item 1d alternative)
can answer this, but only for the specific interventions we toggle
with a flag, and only at the aggregate-bb/100 level.

A per-decision trace gives a much sharper signal: **at decision N,
opponent stats were X, hero's hand was Y, intervention Z fired with
rationale R, shifting the distribution from D₀ to D₁**.

### The second motivation: LLM narration

The tiered bot's Layer 3 (`ExpressionGenerator`) produces narrated
flavor text. Today it gets weak input — basically the action taken
plus drama level. Output is generic ("Big bet. I'll think about it.")
instead of authentic poker thought ("Opponent's been jamming a lot
postflop — 32% open-jam rate over 80 spots. My medium pair is way
ahead of his bluff range. Calling.").

A structured trace of *why* the bot decided is exactly the input
needed for authentic narration. **The same artifact serves both
attribution and narration.**

## Goal — definition of done

A working Phase 7.6 produces these observable outcomes:

1. **Every intervention emits a structured `InterventionTrace` entry**
   per decision. The full trace per decision is a list of these entries
   in pipeline order.

2. **Aggregate per-decision counters keep working** — existing analysis
   scripts that read `manager._exploitation_counters` don't break.
   The trace is additive; counters can be derived from traces if needed.

3. **Trace persisted to per-decision schema**. Either as a JSON column
   on `player_decision_analysis` or a separate `decision_trace` table.
   Analysis scripts can query per-decision attribution.

4. **`experiments/analyze_intervention_traces.py` ships** with two
   analyses:
   - Firing rates per intervention per archetype
   - Per-intervention bb/100 attribution from matched-seed paired runs
     (when a "control" run with intervention disabled is also available)

5. **ExpressionGenerator consumes the trace** for narration prompts.
   A new prompt template renders the structured trace into
   natural-language commentary input. (Quality is measured separately;
   the integration just has to *work* in this phase.)

6. **All existing tests pass.** Pipeline behavior unchanged — trace is
   passive instrumentation, not behavior-modifying.

7. **Tests confirm** that each migrated layer correctly emits the
   expected trace shape on fire / no-op paths.

## Approach overview

```
Step 0: Define InterventionTrace + accumulator pattern (0.5 day)
   ↓
Step 1: Thread trace through one layer (bluff-catch) as the reference (0.5 day)
   ↓
Step 2: Migrate remaining layers one at a time (~0.5 day per layer × 5)
   ↓
Step 3: Persistence schema + capture wiring (1 day)
   ↓
Step 4: analyze_intervention_traces.py + paired-sweep attribution (1 day)
   ↓
Step 5: ExpressionGenerator integration + prompt template (1-2 days)
   ↓
Step 6: Validation — diff-vs-Phase-7.5 sweep should be identical (0.5 day)
```

**Total: ~6-7 days** (estimate; see §Effort).

## Concrete design

### InterventionTrace data type

```python
# poker/strategy/intervention_trace.py

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class InterventionTrace:
    """Structured record of one pipeline layer's contribution to a decision.

    The full trace per decision is List[InterventionTrace] in pipeline
    order. Even no-op layers emit a trace (fired=False) so the analysis
    script can see "this rule was evaluated but didn't trigger" vs
    "this rule wasn't on the path."

    Serializable: tests assert that dataclasses.asdict(trace) round-
    trips through JSON without losing structure.
    """
    layer: str               # canonical layer name — see _LAYER_NAMES
    fired: bool              # did this rule actually modify the strategy?
    inputs: Dict[str, Any]   # the stats / context that drove the decision
    effect: str              # 'no_op' / 'offsets_applied' /
                             # 'distribution_replaced' / 'distribution_clamped'
    rationale: str           # 1-2 sentence human-readable explanation,
                             # narration-ready
    confidence: float = 0.0  # how strong was the signal (0-1)
    extra: Dict[str, Any] = field(default_factory=dict)
                             # layer-specific structured data (e.g. the
                             # tier classification's winning_axis)


# Canonical layer names. Tests assert that every emitted trace uses
# one of these so analysis grouping stays consistent.
_LAYER_NAMES = frozenset({
    'personality',
    'exploitation',
    'strong_hand_override',
    'bluff_catch_override',
    'short_stack',
    'math_floor',
    'value_vs_station',    # Phase 8
    'steal_pressure',      # Phase 8
})
```

### Threading approach: explicit return-tuple

Each layer migrates from `f(strategy, ...) -> strategy` to
`f(strategy, ...) -> Tuple[StrategyProfile, InterventionTrace]`.

```python
# Before
def compute_value_override_strategy(strategy, ctx, hand_strength):
    ...
    return overridden_strategy

# After
def compute_value_override_strategy(strategy, ctx, hand_strength):
    ...
    trace = InterventionTrace(
        layer='strong_hand_override',
        fired=should_apply,
        inputs={'hand_strength': hand_strength, 'opp_pattern': '...'},
        effect='distribution_replaced' if should_apply else 'no_op',
        rationale=f"Strong hand ({hand_strength}) vs hyper-aggressive opp",
        confidence=tilt_factor * adaptation_bias,
    )
    return overridden_strategy, trace
```

**Why threading (not a global accumulator):**

- Makes "did this function fire?" explicit at every call site
- Type system enforces the trace contract — you can't forget to emit
  one without a compile-error-equivalent
- No hidden state on the controller that needs careful reset semantics
- Easier to test layers in isolation (assert returned trace shape)

**Why not a global accumulator:**

- Hidden mutation: `controller._current_trace.append(...)` is a
  side-effect that doesn't show in the function signature
- Reset is brittle: forget to clear at start of decision = leak from
  previous hand
- Composability: a layer that internally calls another layer's helper
  has to know about the accumulator

The downside of threading: 5+ functions' signatures change. Mitigated
by the migration being incremental (one layer at a time, each PR-sized).

### Controller aggregation

```python
# poker/tiered_bot_controller.py — _get_postflop_decision

trace: List[InterventionTrace] = []

modified_strategy, t = modify_strategy(base_strategy, anchors, ...)
trace.append(t)

modified_strategy, t = self._apply_exploitation(modified_strategy, ...)
trace.append(t)

modified_strategy, t = self._apply_value_override(modified_strategy, ...)
trace.append(t)

modified_strategy, t = self._apply_bluff_catch_override(modified_strategy, ...)
trace.append(t)

# ...rest of pipeline

# Stash on controller for the capture path
self._last_intervention_trace = trace
```

Existing aggregate counters (`manager._exploitation_counters`) stay —
the trace is additive. Counters can be reconstructed from traces in
the analysis script.

### Persistence schema

Two options. **Recommend B for simplicity, A for query power.**

**Option A: Separate `decision_trace` table**
```sql
CREATE TABLE decision_trace (
    decision_id INTEGER REFERENCES player_decision_analysis(id),
    layer TEXT,
    fired BOOLEAN,
    inputs_json TEXT,
    effect TEXT,
    rationale TEXT,
    confidence REAL,
    extra_json TEXT,
    order_idx INTEGER
);
CREATE INDEX idx_decision_trace_layer ON decision_trace(layer, fired);
```

Pros: queryable per-layer ("all firings of bluff_catch_override across
all decisions"), normalized.
Cons: extra table; joins for full-decision view.

**Option B: JSON column on `player_decision_analysis`**
```sql
ALTER TABLE player_decision_analysis ADD COLUMN intervention_trace_json TEXT;
```

Pros: simpler; one row per decision; trace is naturally a list.
Cons: SQL queries by layer require JSON extraction (slow at scale).

For Phase 7.6 v1, **B is enough**. The analysis script reads each row's
JSON trace and indexes in Python. If query patterns warrant it, migrate
to A later.

### Analysis: matched-seed paired-sweep attribution

`experiments/analyze_intervention_traces.py`:

```bash
python -m experiments.analyze_intervention_traces \
  --candidate-dir /tmp/phase7_5_3seed \
  --control-dir /tmp/phase7_5_3seed_control \
  --seeds 42,142,242
```

For each seed, opens both candidate and control runs. For each
decision-pair (matched by hand_number + decision_index within hand):

1. Extract the trace from both runs.
2. Find decisions where candidate has `fired=True` on a given layer
   AND control has the same layer with `fired=False` (or vice versa).
3. Compute the chip delta on those decisions (using
   `street_chip_delta` from the per-decision capture).
4. Aggregate per layer:
   ```
   Layer: bluff_catch_override
     Fire count (candidate): 145 / 6000 decisions = 2.4%
     Mean chip delta on firings: +28 bb/100
     Total layer contribution: +0.67 bb/100 (over all decisions)
   ```

**Caveat (important)**: hand-by-hand trajectory divergence (Phase 7.5
plan §Matched-seed limitation) means the same hand may reach a different
postflop spot in candidate vs control. The attribution counts *decisions
that exist in both runs*, paired by hand_number + decision_order. When
the trajectories diverge enough that a decision exists in one run but
not the other, it's excluded from the per-layer attribution (logged
separately as "divergent decisions"). For the 6000-hand sweeps, the
divergent fraction is typically <10% based on prior runs.

### LLM narration integration

Today the expression generator takes `ExpressionContext` plus a
prompt template, queries the LLM, and returns flavor text. The
trace becomes a new field on `ExpressionContext`:

```python
@dataclass
class ExpressionContext:
    # ... existing fields ...
    intervention_trace: List[InterventionTrace] = field(default_factory=list)
```

A new prompt template renders the trace into structured "thought
process" input:

```
DECISION CONTEXT:
- You have: medium pair (44 on K72 flop)
- Facing: pot-size bet from Maniac

WHAT YOU NOTICED:
- Opponent is jamming postflop a lot — 32% open-jam rate over 80 spots
- He's at EXTREME aggression tier
- Bet size is moderate (1.0x pot)

WHAT YOU DECIDED:
- Override the chart's fold recommendation
- Bluff-catch by calling at ~80% frequency
- Confidence: high (large sample, clear pattern)

NARRATE THIS DECISION IN CHARACTER...
```

The expression generator renders this into:
> "Mike's been mashing the bet button every flop — third time this
> orbit. My pair's got to be ahead of half his junk. Snap call."

(Quality testing is a separate exercise — what matters for 7.6 is
the integration works structurally.)

## Migration plan

One layer at a time, each PR-sized. Recommended order:

1. **`bluff_catch_override`** — newest layer, simplest invariants,
   covered by ample tests. Use as the reference implementation.
2. **`strong_hand_override`** (`compute_value_override_strategy`) —
   similar shape to bluff_catch.
3. **`exploitation` (`apply_exploitation_offsets`)** — more complex
   inputs (per-rule offset breakdown), but no behavior change.
4. **`personality` (`modify_strategy`)** — biggest semantic change
   (it's about distortion, not detection). May want a simpler trace
   here (just the deviation profile applied).
5. **`short_stack` (`apply_short_stack_heuristics`)** — small layer.
6. **`math_floor` (`apply_pot_odds_floor`)** — small layer.

Phase 8's `value_vs_station` and `steal_pressure` rules emit traces
from the start, no migration needed.

Each migration step:
- Convert function signature
- Update all call sites
- Add unit tests for trace shape on fire / no-op paths
- Verify full strategy + memory test suite still passes
- bb/100 sweep at seed 42 vs ManiacBot — must match pre-migration
  exactly (the trace is additive)

## Tests

### Unit tests per migrated layer

```python
# tests/test_strategy/test_intervention_trace_bluff_catch.py
def test_emits_trace_on_fire():
    strategy, trace = compute_bluff_catch_strategy(...)
    assert trace.layer == 'bluff_catch_override'
    assert trace.fired is True
    assert trace.effect == 'distribution_replaced'
    assert 'hand_strength' in trace.inputs
    assert trace.rationale  # non-empty string

def test_emits_trace_on_no_op():
    # ... gates fail ...
    strategy, trace = compute_bluff_catch_strategy(...)
    assert trace.fired is False
    assert trace.effect == 'no_op'
```

### Schema test

```python
# tests/test_strategy/test_intervention_trace_schema.py
def test_all_emitted_layer_names_are_canonical():
    """Every trace's layer field must be in _LAYER_NAMES."""

def test_trace_is_json_serializable():
    trace = InterventionTrace(...)
    json.dumps(dataclasses.asdict(trace))  # no exception

def test_reasoning_fields_are_non_empty_on_fire():
    """When fired=True, rationale must be non-empty."""
```

### Behavior-neutrality test

```python
# tests/test_strategy/test_intervention_trace_behavior_neutral.py
def test_sweep_results_match_pre_migration():
    """Running the migrated controller against a fixed seed produces
    the same chip deltas as the pre-migration controller. Confirms
    the trace is passive instrumentation."""
```

### Integration test

```python
# tests/test_strategy/test_intervention_trace_e2e.py
def test_full_pipeline_produces_complete_trace():
    """A full postflop decision produces 4-6 trace entries (one per
    pipeline layer that ran), all with valid layer names."""
```

## Validation

### Behavior neutrality

The migration is additive. Each layer's pre/post behavior must be
identical except for the new trace return. Validation gate:

```bash
# Pre-migration: run baseline 3-seed sweep, save logs
git checkout <commit-before-7.6>
docker compose exec backend python -m experiments.simulate_bb100 \
  --hands 2000 --seed 42 --opponent ManiacBot --adaptation-bias 0.05 \
  > /tmp/pre_7_6_seed42.log

# Post-migration: same seed, same opponent
git checkout <7.6-head>
docker compose exec backend python -m experiments.simulate_bb100 \
  --hands 2000 --seed 42 --opponent ManiacBot --adaptation-bias 0.05 \
  > /tmp/post_7_6_seed42.log

# Bb/100 per archetype must match within ±2 bb/100 (floating-point noise)
diff <(extract_summary /tmp/pre_7_6_seed42.log) \
     <(extract_summary /tmp/post_7_6_seed42.log)
```

### Attribution sanity check

After Phase 7.6 ships, re-run the Phase 7.5 paired sweep (candidate
with interventions on, control with them disabled). The per-layer
attribution sum should approximately equal the total bb/100 delta:

```
Sum(per-layer Δ) ≈ total candidate bb/100 - control bb/100
```

If they diverge by more than ~15%, the trace is missing layers or
the attribution methodology has a bug.

### Narration smoke check

Generate 50 sample narration outputs from a real game session. Eyeball
them for:
- Mention of specific stats (not generic "they bet a lot")
- Mention of the specific layer that fired (e.g. "bluff-catching")
- Coherent connection between "what hero noticed" and "what hero did"

Not pass/fail — qualitative review before committing to the prompt
template.

## Risks / gotchas

1. **Threading churn**: 5+ function signatures change. Mitigation:
   incremental migration, one layer per PR.

2. **Trace size**: Per-decision trace is 5-10 entries × ~200 bytes JSON
   = ~1-2KB per decision. For a 10K-hand session with 4 decisions per
   hand, that's ~40-80MB. Persistence cost is non-trivial. Mitigation:
   compress JSON in the column, or move to a normalized table if data
   volume is a problem.

3. **Existing aggregate counters can drift from traces**. Mitigation:
   add a test that asserts `sum(trace.fired for layer=X) ==
   counters[X]` for a sweep, to catch divergence early.

4. **LLM context pollution**: Stuffing the full trace into every
   narration prompt is expensive. Mitigation: prompt template only
   includes layers where `fired=True`. For typical decisions that's
   1-2 entries.

5. **Backwards-compat for persistence**: Old rows in
   `player_decision_analysis` won't have a trace. Mitigation:
   `intervention_trace_json` column is nullable; analysis script
   treats NULL as "no trace available" (no error).

6. **What about preflop?** The plan focuses on postflop (where most
   interventions fire). Preflop is simpler (chart + personality only)
   but should also get traces for consistency. Easy win — migrate
   preflop path same time as the strong_hand_override layer.

## Effort estimate

| Step | Effort |
|---|---|
| 0: Define InterventionTrace + accumulator pattern | 0.5 day |
| 1: First migration (bluff_catch as reference) | 0.5 day |
| 2: Migrate remaining 5 layers (~0.5 day each) | 2.5 days |
| 3: Persistence schema + capture wiring | 1 day |
| 4: analyze_intervention_traces.py | 1 day |
| 5: ExpressionGenerator integration + prompt template | 1-2 days |
| 6: Validation (behavior-neutrality + attribution sanity) | 0.5 day |

**Total: 6-7 days.** A "spike-first" alternative (Option C in the
discussion that led to this plan) trades a half-day Phase 7.6-lite
for a smaller win — see §"Out of scope" below.

## Out of scope

- **Multi-decision attribution** (across an entire hand). The per-
  decision trace is the unit; aggregating to hand-level attribution
  is an analysis-script feature, not a framework requirement.
- **Trace replay** — using the trace to re-simulate a decision in a
  debug tool. Useful but separate work.
- **Trace-driven A/B tests** — using traces to bucket decisions and
  compare strategy variants. Separate analysis tool, not framework.
- **Real-time narration during play** — the trace gives the data;
  whether the LLM call happens live or batched is a separate
  performance / latency decision.

## Files to create / modify

| File | Action | Description |
|---|---|---|
| `poker/strategy/intervention_trace.py` | **NEW** | `InterventionTrace` dataclass + canonical layer names + JSON helpers |
| `poker/strategy/value_override.py` | Modify | Both override functions return `(strategy, trace)` |
| `poker/strategy/exploitation.py` | Modify | `apply_exploitation_offsets` returns `(strategy, trace)` |
| `poker/strategy/personality_modifier.py` | Modify | `modify_strategy` returns `(strategy, trace)` |
| `poker/strategy/short_stack.py` | Modify | `apply_short_stack_heuristics` returns `(strategy, trace)` |
| `poker/strategy/math_floor.py` | Modify | `apply_pot_odds_floor` returns `(strategy, trace)` |
| `poker/tiered_bot_controller.py` | Modify | Aggregate traces per decision, stash on controller, pass to capture + expression generator |
| `poker/persistence.py` | Modify | Add `intervention_trace_json` column to `player_decision_analysis` |
| `poker/strategy/expression_context.py` | Modify | Add `intervention_trace: List[InterventionTrace]` field |
| `poker/strategy/expression_generator.py` | Modify | New prompt template that renders the trace |
| `poker/prompt_manager.py` | Modify | Register the new template |
| `experiments/analyze_intervention_traces.py` | **NEW** | Per-layer firing rate + matched-seed attribution analysis |
| `tests/test_strategy/test_intervention_trace.py` | **NEW** | Schema tests, JSON round-trip, canonical layer names |
| `tests/test_strategy/test_intervention_trace_*.py` | **NEW** | Per-layer migration tests (one file per migrated layer) |
| `tests/test_strategy/test_intervention_trace_e2e.py` | **NEW** | Full-pipeline integration test |
| `docs/analysis/PHASE_7_6_RESULTS.md` | **NEW** | Validation findings (post-implementation) |

## Reproducibility

Start from any commit at or after Phase 7.5 ships (the Item 1d sweep
in `/tmp/phase7_5_3seed/`). The trace framework is additive — once
shipped, all subsequent phases (Phase 8 etc.) automatically participate.

## Open questions (pre-implementation)

1. **Trace size in production**: at 1-2KB per decision × 1000 decisions
   per session × N sessions, persistence cost adds up. Need to confirm
   acceptable size before shipping; may need compression or sampling.

2. **Narration prompt cost**: the LLM call per decision is expensive.
   Need to decide whether narration is per-decision or per-hand-summary.
   Affects the trace prompt template design.

3. **Sub-layer attribution within `exploitation`**: today
   `apply_exploitation_offsets` runs 4-5 internal rules (hyper_aggressive,
   hyper_passive, tight_nit, c-bet, etc.). Should the trace emit ONE
   entry per rule, or one entry for the whole exploitation layer with
   the rule list as `extra`? Recommend the latter (one entry per layer)
   for simplicity; can refine if attribution needs the granularity.

4. **Threading vs accumulator decision** — open to revisiting if the
   migration churn turns out worse than expected. Spike Step 1
   (bluff_catch migration) before committing to threading for all
   layers.
