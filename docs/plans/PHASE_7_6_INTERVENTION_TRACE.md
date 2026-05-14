---
purpose: Plan for a per-decision intervention-trace framework that unifies attribution and LLM narration
type: design
created: 2026-05-13
last_updated: 2026-05-14
---

# Phase 7.6: Intervention-trace framework

## Codex review history

Plan reviewed by Codex on 2026-05-13. Key revisions (v2):

- **Sub-rule granularity for exploitation** — `exploitation` has 4-5
  internal rules (hyper_aggressive, hyper_passive, tight_nit, c-bet,
  multiway-c-bet). Sub-rules now get their own trace entries with
  `rule_id`, not collapsed into a single layer trace's `extra` field.
- **Narration adapter layer** — added an explicit
  `InterventionTrace → NarrationFacts` step. Trace stays analytical
  (dev-facing); narration consumes an allowlisted, tone-safe view.
  Prevents leaks of model internals and opponent-card knowledge.
- **Attribution methodology honest about causal limits** — same-state
  shadow evaluation is the strongest tool for per-decision causality;
  matched-seed paired-sweep gives aggregate EV signal but NOT clean
  per-decision attribution after trajectory divergence. Added an
  ablation-matrix mode for interaction effects.
- **Trace field additions** — `schema_version`, `decision_id`,
  `rule_id`, `layer_order`, `reason_code`, normalized `effect_size`,
  `config_snapshot`, light-weight `input_strategy_summary` /
  `output_strategy_summary`.
- **Effort revised 6-7 → 8-12 days** for the full-scope version
  (per-rule exploitation + paired-sweep tooling + DB migration +
  narration adapter + behavior-neutral regression harness).
- **Worked trace-JSON example** added (was missing).
- **Retention/pruning + schema versioning policies** added.
- **Persistence storage decision deferred** — define access patterns
  before locking JSON-column vs separate table.

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
Step 0: Define InterventionTrace + InterventionResult wrapper +
        canonical layer/rule names (0.5 day)
   ↓
Step 1: Thread trace through one layer (bluff_catch) as the
        reference implementation (0.5 day)
   ↓
Step 2: Migrate remaining 5 layers, one PR-sized step each,
        emitting per-RULE traces for exploitation (~0.75 day each = 3.75)
   ↓
Step 3a: Access-pattern audit for persistence (~0.5 day)
Step 3b: Persistence schema + capture wiring (~0.75 day)
   ↓
Step 4: analyze_intervention_traces.py with 4 modes
        (shadow-eval, first-divergence, aggregate, ablation) (1.5 days)
   ↓
Step 5a: NarrationFacts adapter (~0.75 day)
Step 5b: ExpressionGenerator prompt template integration (~0.75 day)
   ↓
Step 6: Validation — behavior-neutrality diff + attribution sanity
        check (0.75 day)
```

**Total: 8-12 days** (revised from 6-7 after Codex review surfaced
hidden scope: per-rule exploitation traces, narration adapter layer,
4-mode attribution analysis, retention/pruning policy, and the
access-pattern audit before locking persistence schema).

A narrow v1 could ship in 6-7 days by deferring:
- Mode 1 shadow-eval (Mode 2/3/4 only — weaker causal attribution)
- Per-rule exploitation traces (one trace per layer for v1)
- Retention/pruning (defer cleanup until disk usage matters)
- Mode 4 ablation matrix (defer until interaction effects are
  needed)

Recommend the full 8-12 day scope unless there's time pressure —
the narrow v1 loses most of the architectural payoff.

## Concrete design

### InterventionTrace data type

```python
# poker/strategy/intervention_trace.py

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


TRACE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class InterventionTrace:
    """Structured record of one pipeline rule's contribution to a decision.

    The full trace per decision is List[InterventionTrace] in pipeline
    order. Even no-op rules emit a trace (fired=False) so the analysis
    script can see "this rule was evaluated but didn't trigger" vs
    "this rule wasn't on the path."

    A single pipeline LAYER may emit multiple trace entries when it
    has internal sub-rules (e.g. exploitation emits one per rule:
    hyper_aggressive, hyper_passive, tight_nit, c-bet, multiway-c-bet).
    Distinguish them via `rule_id`; analysis groups by `(layer, rule_id)`.

    Serializable: tests assert that dataclasses.asdict(trace) round-
    trips through JSON without losing structure.
    """
    # Identity + ordering
    layer: str               # canonical layer name — see _LAYER_NAMES
    rule_id: str             # 'default' for single-rule layers;
                             # 'hyper_aggressive' / 'high_fold_to_cbet' etc.
                             # for sub-rules inside exploitation
    layer_order: int         # 0-indexed ordinal in the pipeline
                             # (personality=0, exploitation=1, ...)
    decision_id: Optional[str] = None
                             # correlation id joining to player_decision_analysis
                             # (set by the controller's aggregation step)
    schema_version: int = TRACE_SCHEMA_VERSION

    # Outcome
    fired: bool = False      # did this rule actually modify the strategy?
    effect: str = 'no_op'    # 'no_op' / 'offsets_applied' /
                             # 'distribution_replaced' / 'distribution_clamped'
    effect_size: float = 0.0 # normalized magnitude of the change, in [0, 2]:
                             #   0   = no change
                             #   L1(output, input) for distribution edits
                             # Different units across layers but always
                             # comparable as "did this layer move the
                             # distribution a lot or a little?"

    # Why
    reason_code: str = ''    # categorical: 'hand_class_not_eligible',
                             # 'tier_too_low', 'tilt_suppressed',
                             # 'multiway_blocked', 'tier_decay_applied',
                             # 'low_sample', etc. Stable enum; the
                             # narration adapter maps to player-facing
                             # labels.
    rationale: str = ''      # 1-2 sentence dev-facing explanation.
                             # NOT directly used in narration prompts —
                             # NarrationFacts adapter rephrases.
    confidence: float = 0.0  # signal strength (0-1)

    # Selected inputs — typed, allowlisted features, NOT a full state dump
    inputs: Dict[str, Any] = field(default_factory=dict)

    # Lightweight before/after strategy summaries (top 3 actions w/ probs)
    input_strategy_summary: Dict[str, float] = field(default_factory=dict)
    output_strategy_summary: Dict[str, float] = field(default_factory=dict)

    # Config snapshot — relevant threshold values active at decision time.
    # Lets traces survive config changes without misinterpretation.
    # Layer-specific (e.g. exploitation logs the active clamp tier;
    # bluff-catch logs the call-prob matrix bands it used).
    config_snapshot: Dict[str, Any] = field(default_factory=dict)

    # Layer-specific structured extras that don't fit elsewhere.
    extra: Dict[str, Any] = field(default_factory=dict)


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

# Canonical rule_ids per layer. Single-rule layers use 'default'.
# Exploitation sub-rules get their own ids — they emit one trace each.
_RULE_IDS_BY_LAYER: Dict[str, frozenset] = {
    'personality':            frozenset({'default'}),
    'exploitation':           frozenset({
        'hyper_aggressive', 'hyper_passive', 'tight_nit',
        'high_fold_to_cbet', 'multiway_cbet',
    }),
    'strong_hand_override':   frozenset({'default'}),
    'bluff_catch_override':   frozenset({'default'}),
    'short_stack':            frozenset({'default'}),
    'math_floor':             frozenset({'default'}),
    'value_vs_station':       frozenset({'default'}),
    'steal_pressure':         frozenset({'default'}),
}
```

#### Worked example (real trace JSON for one decision)

```json
[
  {
    "schema_version": 1,
    "layer": "personality",
    "rule_id": "default",
    "layer_order": 0,
    "decision_id": "g123_h45_d2",
    "fired": true,
    "effect": "offsets_applied",
    "effect_size": 0.22,
    "reason_code": "deviation_profile_applied",
    "rationale": "LAG personality: +0.15 raise, -0.10 fold",
    "confidence": 1.0,
    "inputs": {"deviation_profile": "lag", "tilt_factor": 1.0},
    "input_strategy_summary": {"fold": 0.60, "call": 0.30, "raise_67": 0.10},
    "output_strategy_summary": {"fold": 0.50, "call": 0.25, "raise_67": 0.25},
    "config_snapshot": {},
    "extra": {}
  },
  {
    "schema_version": 1,
    "layer": "exploitation",
    "rule_id": "hyper_aggressive",
    "layer_order": 1,
    "decision_id": "g123_h45_d2",
    "fired": true,
    "effect": "offsets_applied",
    "effect_size": 0.45,
    "reason_code": "extreme_tier_via_jam_open",
    "rationale": "Opp postflop_jam_open_rate=0.32 (≥ extreme 0.20); call_prob nudged up.",
    "confidence": 0.85,
    "inputs": {
      "af_postflop": 4.5, "all_in_per_facing_bet": 0.18,
      "postflop_jam_open_rate": 0.32, "tier": "extreme"
    },
    "input_strategy_summary": {"fold": 0.50, "call": 0.25, "raise_67": 0.25},
    "output_strategy_summary": {"fold": 0.30, "call": 0.50, "raise_67": 0.20},
    "config_snapshot": {
      "extreme_max_total_shift": 0.8,
      "extreme_postflop_jam_open_rate": 0.20
    },
    "extra": {"winning_axis": "postflop_jam_open_rate"}
  },
  {
    "schema_version": 1,
    "layer": "exploitation",
    "rule_id": "hyper_passive",
    "layer_order": 1,
    "decision_id": "g123_h45_d2",
    "fired": false,
    "effect": "no_op",
    "effect_size": 0.0,
    "reason_code": "no_passive_opponent_detected",
    "rationale": "",
    "confidence": 0.0,
    "inputs": {},
    "input_strategy_summary": {},
    "output_strategy_summary": {},
    "config_snapshot": {},
    "extra": {}
  },
  {
    "schema_version": 1,
    "layer": "bluff_catch_override",
    "rule_id": "default",
    "layer_order": 3,
    "decision_id": "g123_h45_d2",
    "fired": true,
    "effect": "distribution_replaced",
    "effect_size": 0.62,
    "reason_code": "medium_made_vs_extreme_facing_bet",
    "rationale": "Medium pair vs extreme jammer, flop wet_rainbow, bet 0.5x pot",
    "confidence": 0.85,
    "inputs": {
      "hand_strength": "medium_made",
      "bet_size_pot_ratio": 0.5,
      "street": "flop", "board_texture": "wet_rainbow",
      "is_paired_board": false,
      "tier": "extreme"
    },
    "input_strategy_summary": {"fold": 0.30, "call": 0.50, "raise_67": 0.20},
    "output_strategy_summary": {"fold": 0.40, "call": 0.60},
    "config_snapshot": {
      "medium_made_le_50_pct": 0.95,
      "dangerous_texture_mult": 0.5,
      "street_flop": 1.0
    },
    "extra": {"composed_call_prob": 0.475}
  }
]
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

### Persistence schema (decision deferred to design step)

Codex's review correctly flagged that we shouldn't lock storage
without defining access patterns first. Step 3 of implementation
(persistence + capture wiring) explicitly includes an access-pattern
audit BEFORE writing the migration:

**Step 3a: Access-pattern audit (~half day)**

Catalog every consumer of the trace data and the query shape they
need:
- Analysis script (Mode 1/2/3/4): full per-decision trace read,
  grouped by (game_id, hand_number, decision_index)
- Real-time controller: trace produced in-memory, doesn't need
  persistence beyond the per-decision capture row
- Narration prompt build: typically one decision's trace at a time
- Dashboard / observability: aggregate firing rates per layer per
  archetype across a session

**Step 3b: Choose schema based on audit**

Two candidates:

**Option A: Separate `decision_trace` table**
```sql
CREATE TABLE decision_trace (
    decision_id INTEGER REFERENCES player_decision_analysis(id),
    schema_version INTEGER,
    layer TEXT,
    rule_id TEXT,
    layer_order INTEGER,
    fired BOOLEAN,
    effect TEXT,
    effect_size REAL,
    reason_code TEXT,
    rationale TEXT,
    confidence REAL,
    inputs_json TEXT,
    input_strategy_summary_json TEXT,
    output_strategy_summary_json TEXT,
    config_snapshot_json TEXT,
    extra_json TEXT
);
CREATE INDEX idx_decision_trace_layer_rule ON decision_trace(layer, rule_id, fired);
```

Pros: queryable per-(layer, rule_id) ("all firings of
exploitation.hyper_aggressive across all decisions"), normalized.
Cons: extra table; joins for full-decision view; row blowup
(5-10 traces × N decisions).

**Option B: JSON column on `player_decision_analysis`**
```sql
ALTER TABLE player_decision_analysis ADD COLUMN intervention_trace_json TEXT;
```

Pros: simpler; one row per decision; trace is naturally a list.
Cons: SQL queries by (layer, rule_id) require JSON extraction (slow
at scale); no native index on rule_id firing rates.

**Decision criterion**: if Mode 1 shadow-eval is the primary tool and
analysis is in-memory Python (one decision at a time), Option B wins.
If dashboards or aggregate-firing-rate queries get heavy SQL use,
Option A wins. **Defer the choice to Step 3b** after running a few
sample queries against a 1000-decision prototype dataset.

### Trace schema versioning

`schema_version` field on every trace lets us evolve the shape
without breaking old rows. Policy:

- **Minor field additions** (new optional `extra` key, new
  `reason_code` value) — no version bump. Old rows just lack the
  field; analysis script handles `None`.
- **Field removal or rename** — version bump + a migration in the
  analysis script that maps old → new.
- **Structural changes** (sub_interventions reorganization,
  layer rename) — version bump + a one-time backfill migration.

The analysis script reads `schema_version` and dispatches to the
correct parser. Versions are kept in `intervention_trace.py`:

```python
TRACE_SCHEMA_VERSION = 1

# When bumping, add a parser for the old version:
TRACE_PARSERS = {
    1: _parse_v1,
    # 2: _parse_v2,
}
```

### Retention / pruning policy

Per-decision traces are high-volume — a 10K-hand session × 4 decisions
per hand × ~5 traces per decision = 200K trace rows / 200K JSON
blobs. At ~200 bytes per trace JSON, that's ~40MB per long session.
Need a policy:

- **Production (real games)**: persist only the LAST 100 hands' traces
  per game. Older traces can be summarized into aggregate counters
  (which already exist in `manager._exploitation_counters`) and the
  raw rows pruned. A cron-style cleanup script handles this.
- **Experiment runs**: persist everything for the session, since the
  analysis script needs full traces. Disk usage is bounded by the
  experiment hand count.
- **Dev / debugging**: persist everything indefinitely on the local
  database. Disk is cheap.

Implementation: a `prune_old_traces(game_id, keep_last_n_hands=100)`
function that runs on `on_hand_end` for completed games (or as a
nightly batch). Skipped in experiment mode via a config flag.

### Attribution analysis: four complementary modes

Codex's review correctly pointed out that matched-seed paired-sweep
**at decision granularity is defensible ONLY before trajectory
divergence**. Once an intervention changes an action, later stack
sizes, pot sizes, board runouts, opponent responses, and even
available actions diverge. Matched seeds reduce variance for
aggregate signals but do not preserve identical decision states
after the first behavioral fork.

The analysis script ships with FOUR modes, ordered by causal strength:

**Mode 1: Same-state shadow evaluation (strongest per-decision causality)**

Given a frozen decision state (game_state snapshot + opponent stats
snapshot), call the controller in two configurations:
- "Live" — the actual pipeline ran in the sweep
- "Shadow" — same state, but with one specific layer/rule disabled

The shadow call does NOT advance the game; it just runs the strategy
pipeline and returns the would-be distribution. The delta between
Live's chosen-action probability and Shadow's gives a direct per-
decision attribution to the toggled rule with no trajectory
divergence concerns.

```bash
python -m experiments.analyze_intervention_traces \
  --mode shadow \
  --sweep-dir /tmp/phase7_5_3seed \
  --disable-rule bluff_catch_override.default
```

Cost: one extra pipeline invocation per decision per disabled rule.
Cheap (no game-tree advance), scales linearly with rules.

**Mode 2: First-divergence analysis**

For matched-seed candidate vs control runs, walk both decision streams
in parallel. Record the FIRST decision where they diverge in chosen
action, and attribute the divergence to whichever layer's trace
differs between candidate and control at that point. Aggregate
per layer:
```
Layer: bluff_catch_override
  Caused first divergence in: 87 / 6000 hands
  (Says nothing about the AVERAGE effect — only the
   point-where-things-start-to-differ effect.)
```

Cost: free if you already have the paired sweep. Useful for
"which layer is the most behavioral-change leverage?" but doesn't
tell you the EV impact.

**Mode 3: Matched-seed aggregate deltas (NOT per-decision causal)**

The original idea from the v1 plan. For each archetype, compute
`candidate_bb100 - control_bb100` per seed and report mean ± CI.
This is the **aggregate** EV signal — useful for ship-readiness gates
("does Phase 7.5 hurt anything overall?"), NOT for "did bluff-catch
contribute +X bb/100." Demoted from primary attribution tool to
"summary EV signal."

**Mode 4: Ablation matrix**

For interaction effects, run the sweep with combinations of
rules disabled:
- Baseline (everything on)
- Disable bluff-catch only
- Disable extreme-clamp only
- Disable both
- ...

Reports per-rule and per-rule-pair contributions. Expensive
(N runs for N combinations), but the only way to detect interaction
effects between rules (e.g. "bluff-catch + extreme-clamp together
produce more than the sum of their parts").

```bash
python -m experiments.analyze_intervention_traces \
  --mode ablation \
  --sweep-dir /tmp/phase7_5_3seed_ablation \
  --rules bluff_catch_override.default,exploitation.hyper_aggressive
```

### Honest framing of causal claims

The analysis script's output explicitly distinguishes:
- **"Per-decision EV contribution"** — Mode 1 only.
- **"First-divergence leverage"** — Mode 2 only. Says where things
  start to differ, not by how much on average.
- **"Aggregate EV signal"** — Mode 3 only. Total bb/100 delta with no
  layer-specific attribution beyond ablation.
- **"Interaction effects"** — Mode 4 only.

This prevents the common error of reading a Mode 3 number as if it
were Mode 1 ("bluff-catch contributed +X bb/100 per decision it fired").

### LLM narration: NarrationFacts adapter (separate from analytical trace)

**The expression generator does NOT consume `InterventionTrace`
directly.** The analytical trace is dev-facing: `rationale` strings
are written for debugging, `confidence` values are signal-strength
numbers (not emotional certainty), `inputs` may include opponent-
model internals that shouldn't surface in narration, and non-fired
layers would create noisy "I considered X" filler in prompts.

A dedicated adapter layer maps `List[InterventionTrace] →
NarrationFacts`, filtering and rephrasing for player-facing use:

```python
# poker/strategy/narration_facts.py

@dataclass(frozen=True)
class NarrationFact:
    """One narration-safe observation derived from a fired intervention.

    Allowlisted fields only — no opponent-model internals, no hidden-card
    knowledge, no `confidence` values that could be misread as
    emotional certainty.
    """
    observation: str        # "Opponent has been jamming postflop a lot"
                            # (rephrased from rule + reason_code)
    why_it_matters: str     # "Their bet range is mostly bluffs here"
                            # (player-facing, not "high all_in_per_facing_bet")
    decision_taken: str     # "I'm calling instead of folding"
    intensity: str          # 'subtle' / 'noticeable' / 'strong'
                            # (categorical mapping from effect_size,
                            #  NOT a raw float — LLMs misread numbers)


@dataclass(frozen=True)
class NarrationFacts:
    """The narration-safe view of one decision's trace.

    Built by `traces_to_narration_facts(traces) -> NarrationFacts` which:
      - filters to only fired traces
      - rejects layers/rule_ids not in the narration allowlist
      - maps `reason_code` to a player-facing observation via
        REASON_CODE_TO_OBSERVATION (a hand-curated dict)
      - maps `effect_size` to intensity bucket
      - strips any input fields not in NARRATION_INPUT_ALLOWLIST
    """
    facts: List[NarrationFact]
    summary_intensity: str  # 'subtle' / 'noticeable' / 'strong' — the
                            # overall "how unusual was this decision"
                            # signal for the expression layer's drama
                            # calibration.


# What can show up in narration. Anything not here is dev-facing only.
NARRATION_ALLOWLIST: frozenset = frozenset({
    ('exploitation', 'hyper_aggressive'),
    ('exploitation', 'hyper_passive'),
    ('exploitation', 'tight_nit'),
    ('strong_hand_override', 'default'),
    ('bluff_catch_override', 'default'),
    ('value_vs_station', 'default'),
    ('steal_pressure', 'default'),
    # Personality / math_floor / short_stack intentionally absent —
    # they're mechanical, not narratable observations.
})


# Maps stable reason_codes to player-facing observation templates.
# Hand-curated; the LLM never sees the dev `rationale` field.
REASON_CODE_TO_OBSERVATION: Dict[str, Tuple[str, str]] = {
    'extreme_tier_via_jam_open': (
        "Opponent's been jamming postflop a lot",
        "Their bet range is mostly bluffs here",
    ),
    'medium_made_vs_extreme_facing_bet': (
        "I have showdown value vs an over-aggressor",
        "My pair beats most of their bluff range",
    ),
    # ... etc
}
```

The expression generator's prompt template then renders
`NarrationFacts` into authentic poker thought:

```
WHAT YOU NOTICED:
- Opponent's been jamming postflop a lot
- I have showdown value vs an over-aggressor

WHAT YOU DECIDED:
- I'm calling instead of folding
- Intensity: noticeable

NARRATE THIS DECISION IN CHARACTER (1-2 sentences, present tense, no
mention of specific numbers or stats — just the read).
```

The expression generator renders this into:
> "Mike's been mashing the bet button every flop — third time this
> orbit. My pair's got to be ahead of half his junk. Snap call."

**Why this separation matters:**
- Trace schema can evolve (new `reason_codes`, new `extra` fields)
  without breaking narration — the adapter is the contract surface
- Narration can be A/B tested by swapping the adapter / prompt
  template without touching pipeline code
- Privacy / leak-safety is enforced at one chokepoint
  (`NARRATION_ALLOWLIST`) instead of distributed across every layer's
  `rationale` strings
- The `intensity` bucketing prevents LLM misreading of raw numbers

Quality testing of the narration output is separate work — what
matters for Phase 7.6 is the adapter exists and renders structurally
valid prompts.

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
   incremental migration, one layer per PR. Per-migration invariant:
   same seed/input produces byte-equivalent strategy output before
   and after threading (so each migration is provably behavior-neutral).

2. **Trace bloat**: Per-decision trace is 5-10 entries × ~300 bytes
   JSON = ~2-3KB per decision (now bigger because of input/output
   summaries + config_snapshot). For a 10K-hand session × 4 decisions
   per hand, that's ~80-120MB. Mitigation: the retention/pruning
   policy (§Trace schema versioning) keeps only last 100 hands in
   production. Experiments persist everything (bounded by hand count).

3. **Existing aggregate counters can drift from traces**. Mitigation:
   add a test that asserts `sum(trace.fired for layer=X, rule_id=Y)
   == counters[Y]` for a sweep, to catch divergence early.

4. **Narration overfitting to internal fields** (Codex): the
   expression generator must NOT depend on `rationale`, `confidence`,
   or raw `inputs` — those are dev-facing and may change without
   schema bumps. Mitigation: the NarrationFacts adapter is the only
   surface narration consumes, and its allowlist is curated.

5. **Backwards-compat for persistence**: Old rows in
   `player_decision_analysis` won't have a trace. Mitigation:
   trace column is nullable; analysis script treats NULL as "no
   trace available" (no error).

6. **What about preflop?** The plan focuses on postflop (where most
   interventions fire). Preflop is simpler (chart + personality only)
   but should also get traces for consistency. Easy win — migrate
   preflop path same time as the strong_hand_override layer.

7. **Schema churn during development** (Codex): adding `reason_codes`
   or `extra` fields is the common case as we learn what's useful.
   Mitigation: `schema_version` field + minor-vs-major version
   policy (§Trace schema versioning) lets the analysis script
   gracefully handle additions without forcing a backfill.

8. **Attribution misinterpretation from trajectory divergence**
   (Codex): readers seeing a Mode 3 "aggregate delta" number may
   mistake it for per-decision causal attribution. Mitigation: the
   analysis script's output explicitly labels each number by mode
   ("aggregate EV signal" vs "per-decision EV contribution"), and
   refuses to report per-decision attribution from Mode 3 alone.

9. **JSON-serialization surprises** (Codex): enums, dataclasses,
   decimals, numpy values, custom action objects in the strategy
   distributions don't all JSON-serialize cleanly. Mitigation: the
   trace builder uses a small `_safe_serialize(value) -> JSON-safe`
   helper, and a test runs every layer's emitted trace through
   `json.dumps(asdict(trace))` to catch type drift.

10. **Layer-overwrite semantics** (Codex): when one layer (e.g.
    bluff_catch_override) REPLACES the strategy from a previous
    layer (e.g. exploitation offsets), the earlier layer's
    `output_strategy_summary` no longer reflects the final
    distribution. The trace records both layers' before/after
    correctly, but readers need to understand the overwrite. The
    `effect` field's `distribution_replaced` value is the
    explicit marker.

## Effort estimate

| Step | Effort | Notes |
|---|---|---|
| 0: Define InterventionTrace + result wrapper + canonical names | 0.5 day | |
| 1: First migration (bluff_catch as reference) | 0.5 day | Validates the threading pattern |
| 2: Migrate remaining 5 layers | 3.75 days | Per-rule exploitation traces add ~1 day vs v1 estimate |
| 3a: Access-pattern audit | 0.5 day | NEW: gather queries before locking schema |
| 3b: Persistence schema + capture wiring | 0.75 day | |
| 4: analyze_intervention_traces.py with 4 modes | 1.5 days | Shadow-eval is the new heavy lift |
| 5a: NarrationFacts adapter | 0.75 day | NEW: separate from analytical trace |
| 5b: ExpressionGenerator prompt integration | 0.75 day | |
| 6: Validation (behavior-neutrality + attribution sanity) | 0.75 day | |

**Total: 9.75 days.** Revised range **8-12 days** to account for
discovery on each step.

Narrow v1 (defers shadow-eval, per-rule exploitation, narration
adapter polish, ablation matrix) ships in ~6 days but loses most
of the architectural value — recommend the full scope unless time-
pressured.

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

## Resolved by Codex review (v2)

- ✅ **Sub-layer attribution within `exploitation`** — multiple
  traces per layer with distinct `rule_id`, NOT in `extra`. See
  `_RULE_IDS_BY_LAYER` in the trace data type section.
- ✅ **Narration-vs-analysis separation** — `NarrationFacts` adapter
  is a distinct surface; the LLM never sees the raw analytical trace.
- ✅ **Attribution methodology causality limits** — Mode 1 shadow-eval
  is the strongest per-decision tool; Mode 3 paired-sweep gives
  aggregate signal only. Analysis script labels each number by mode.
- ✅ **Persistence schema choice** — deferred to Step 3a access-pattern
  audit before locking JSON-column vs separate table.
- ✅ **Schema versioning** — `schema_version` field + minor/major
  versioning policy.
- ✅ **Retention/pruning** — `prune_old_traces` for production
  (last 100 hands); experiments persist everything.

## Remaining open questions (pre-implementation)

1. **Narration prompt cost** (still open): the LLM call per decision
   is expensive. Whether narration is per-decision or
   per-hand-summary is an integration-design decision tied to the
   game's UX latency budget. Spike a few sample narrations during
   Step 5 and decide based on perceived quality / cost.

2. **Threading vs accumulator decision** (still open): commit to
   threading after Step 1 (bluff_catch reference migration) proves
   the pattern. If migration churn is worse than expected, fall
   back to a controller-held accumulator with explicit reset
   semantics — the trace data shape doesn't change.

3. **Mode 1 shadow-eval cost** (new): each shadow-eval call invokes
   the full strategy pipeline (minus the disabled layer) for one
   decision. For 6000 decisions × 6 layers to ablate, that's 36k
   extra pipeline runs. Estimate: ~5-15 min per analysis run, but
   bears measuring once a real sweep exists.

4. **Layer-overwrite semantics for `effect_size`** (new): when one
   layer replaces a previous layer's output, what should the later
   layer's `effect_size` measure — L1 distance from the immediately
   prior strategy, or from the original chart baseline? The latter
   is more meaningful for narration ("this layer contributed X% of
   the total move"); the former is simpler. Default to "L1 from
   immediately prior strategy" (simpler, locally measurable);
   revisit after seeing real traces.
