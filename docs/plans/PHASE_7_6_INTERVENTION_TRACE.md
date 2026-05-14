---
purpose: Plan for a per-decision intervention-trace framework that unifies attribution and LLM narration
type: design
created: 2026-05-13
last_updated: 2026-05-14
---

# Phase 7.6: Intervention-trace framework

## Codex review history

### Round 3 revisions (v4, 2026-05-14)

Codex's round-3 verdict: "v3 is implementation-ready." Five small
refinements applied as v4 polish before code:

- **clamp vs veto disambiguation** — `veto` = explicit hard
  prohibition (action removed); `clamp` = bounded reduction (action
  retained, mass reduced). Invariant added: `operation == 'override'`
  ⇒ `replaced_prior_action == True`.
- **`_score_fact_importance` concrete ranking dimensions** —
  operation severity × action-class change × certainty × street
  importance × pipeline recency. Overwritten facts down-ranked.
- **Overwrite-chain tests** added to the test plan (multiple
  sequential overrides, not just one).
- **Performance budget target**: per-decision trace overhead must
  stay under 5% of decision latency. Measured during Step 1.
- **Trace write-failure policy**: persistence failures degrade
  gracefully — gameplay continues, the failure is logged, and the
  in-memory trace is dropped for that decision rather than blocking
  the engine.

### Round 2 revisions (v3, 2026-05-14)

- **`operation` enum on `InterventionTrace`** + `replaced_prior_action`
  / `prior_action_source` / `preserved_prior_intent` fields — without
  these, layer overwrites (e.g. bluff-catch replacing an exploitation
  result) make the superseded layer look causally responsible when it
  was overridden. Operation values: `no_op` / `suggest` / `adjust` /
  `clamp` / `override` / `veto`.
- **Poker-specific companions to `effect_size`**: `action_changed`
  (bool), `primary_action_before/after`, `amount_bucket_before/after`.
  L1 distance alone doesn't distinguish "flipped fold→call" from
  "shifted call probability by 30%."
- **NarrationFacts ranking + cap to top 2-3 facts** to prevent LLM
  rambling. Adapter now ranks facts by importance and selects a
  `primary_factor` for the narration prompt's lead.
- **NarrationFacts additional fields**: `action_intent`, `street`,
  `position_context`, `risk_posture`, `certainty_bucket` (separate
  from `intensity_bucket` — "strong effect" ≠ "high confidence"),
  `suppressed_facts_count` (debug, not sent to LLM).
- **Mode 1 legality check** — shadow-eval only valid when each
  variant produces a legal action in the frozen state. Otherwise
  fall back to Mode 2/3.
- **Post-divergence exclusion zone** — after the first action
  divergence in a matched-seed paired run, per-decision attribution
  is labeled "different trajectory context" or suppressed. Prevents
  Mode 3 leakage into decision-level claims.
- **`config_snapshot` bloat guardrail** — limit to stable knobs
  (thresholds, enabled flags, version ids). Don't dump full config
  objects or prompts.
- **Retention policy: experiment runs isolated from production
  pruning**. Privacy deletion alignment open question.
- **Reopened: exploitation rule_id completeness** — the existing 5
  rule_ids (hyper_aggressive, hyper_passive, tight_nit,
  high_fold_to_cbet, multiway_cbet) may not capture the Phase 7.5
  three-tier clamp's internal tier distinctions. Open question
  whether tier should be encoded as a separate rule_id, a
  `reason_code` value, or a field within exploitation traces.

### Round 1 revisions (v2)

Plan reviewed by Codex on 2026-05-13. Key revisions:

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
from enum import Enum
from typing import Any, Dict, List, Optional


TRACE_SCHEMA_VERSION = 1


class InterventionOperation(str, Enum):
    """How this layer's trace relates to the prior strategy.

    v3 (Codex r2): explicit overwrite semantics. `rule_id` + `layer_order`
    alone don't distinguish "refined the prior layer's work" from
    "threw it out." `operation` makes the relationship explicit so
    attribution doesn't mistakenly credit a superseded earlier layer.

    v4 (Codex r3) disambiguation: `clamp` and `veto` were overlapping
    when a clamp reduces an action to zero probability. The
    distinction is semantic, not just numeric:
      - `clamp` BOUNDS the prior distribution (action retained in
        the distribution with possibly reduced mass; the action is
        still in the legal/considered set)
      - `veto` REMOVES an action from consideration entirely (the
        action is treated as illegal/disallowed for this decision)
    A clamp that incidentally results in 0% mass is still `clamp`;
    only an explicit hard prohibition is `veto`.

    Invariant: `operation == OVERRIDE` ⇒ `replaced_prior_action == True`
    (override always replaces the prior layer's chosen action).
    Asserted in unit tests.
    """
    NO_OP = 'no_op'        # gates failed; strategy unchanged
    SUGGEST = 'suggest'    # produced advice but didn't modify
    ADJUST = 'adjust'      # additive offsets / nudges; prior intent preserved
    CLAMP = 'clamp'        # bounded the prior distribution (action
                           # mass capped; action still in the set)
    OVERRIDE = 'override'  # replaced the strategy distribution
                           # entirely; primary action changed
    VETO = 'veto'          # explicit hard prohibition; action removed
                           # from consideration (e.g. math floor on
                           # pot-committed spots)


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
    operation: str = InterventionOperation.NO_OP.value
                             # v3: see InterventionOperation. The analytical
                             # relationship to the prior strategy. Required
                             # for honest layer-overwrite attribution.
    effect: str = 'no_op'    # 'no_op' / 'offsets_applied' /
                             # 'distribution_replaced' / 'distribution_clamped'
                             # — legacy sub-categorization; may collapse
                             # into `operation` in a future schema version.
    effect_size: float = 0.0 # L1 distance between input and output
                             # distributions, in [0, 2]:
                             #   0 = no change, 2 = full swap.
                             # L1 alone doesn't say WHAT changed — see
                             # action_changed / primary_action_* below
                             # for poker-action semantics.

    # v3: poker-specific action-level companions to effect_size. L1 alone
    # doesn't distinguish "flipped fold→call" from "shifted call by 30%".
    action_changed: bool = False
                             # True if argmax action differs in/out
    primary_action_before: str = ''
                             # argmax of input_strategy_summary
    primary_action_after: str = ''
                             # argmax of output_strategy_summary
    amount_bucket_before: str = ''
                             # for raise/bet: 'small'/'medium'/'large'/'jam'.
                             # Empty for fold/call/check.
    amount_bucket_after: str = ''

    # v3: layer-overwrite tracking — what this layer did to the prior
    # pipeline state. Critical for attribution: prevents an overridden
    # earlier layer's trace from looking causally responsible.
    replaced_prior_action: bool = False
                             # True when operation in {OVERRIDE, VETO}
                             # AND primary_action_before != primary_action_after
    prior_action_source: str = ''
                             # 'layer.rule_id' of the layer that last set
                             # primary_action_before. Empty if no prior
                             # layer fired.
    preserved_prior_intent: bool = True
                             # False if this layer overrode the prior
                             # action. Defaults True for ADJUST/CLAMP/NO_OP.

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
    #
    # v3 (Codex r2): KEEP THIS SMALL. Limit to stable knobs that affect
    # this intervention's behavior — thresholds, enabled flags, version
    # ids. DO NOT dump full config objects, prompts, or computed state.
    # The trace builder's `_select_config_for_trace(layer)` helper
    # enforces an allowlist per layer (similar to NARRATION_ALLOWLIST).
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
    "operation": "adjust",
    "effect": "offsets_applied",
    "effect_size": 0.22,
    "action_changed": false,
    "primary_action_before": "fold",
    "primary_action_after": "fold",
    "amount_bucket_before": "",
    "amount_bucket_after": "",
    "replaced_prior_action": false,
    "prior_action_source": "",
    "preserved_prior_intent": true,
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
    "operation": "adjust",
    "effect": "offsets_applied",
    "effect_size": 0.45,
    "action_changed": true,
    "primary_action_before": "fold",
    "primary_action_after": "call",
    "amount_bucket_before": "",
    "amount_bucket_after": "",
    "replaced_prior_action": false,
    "prior_action_source": "personality.default",
    "preserved_prior_intent": true,
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
    "operation": "no_op",
    "effect": "no_op",
    "effect_size": 0.0,
    "action_changed": false,
    "primary_action_before": "",
    "primary_action_after": "",
    "amount_bucket_before": "",
    "amount_bucket_after": "",
    "replaced_prior_action": false,
    "prior_action_source": "",
    "preserved_prior_intent": true,
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
    "operation": "override",
    "effect": "distribution_replaced",
    "effect_size": 0.62,
    "action_changed": false,
    "primary_action_before": "call",
    "primary_action_after": "call",
    "amount_bucket_before": "",
    "amount_bucket_after": "",
    "replaced_prior_action": true,
    "prior_action_source": "exploitation.hyper_aggressive",
    "preserved_prior_intent": false,
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
blobs. At ~300 bytes per trace JSON (v3, slightly bigger), that's
~60MB per long session. Need a policy with explicit isolation:

- **Production (real games)**: persist only the LAST 100 hands' traces
  per game. Older traces can be summarized into aggregate counters
  (which already exist in `manager._exploitation_counters`) and the
  raw rows pruned. A cron-style cleanup script handles this.
- **Experiment runs**: persist everything for the session. **Critical
  (Codex r2): experiment runs MUST be isolated from production
  pruning** — distinguished by a `game.kind` field or game_id prefix
  (`exp_*`). `prune_old_traces()` checks this and skips experiment
  games unconditionally. Otherwise mid-experiment pruning corrupts
  analysis.
- **Validation artifacts** (the runs producing `PHASE_7_5_RESULTS.md`,
  etc.): same as experiment runs — never auto-pruned. Annotated with
  `game.kind = 'validation'`.
- **Dev / debugging**: persist everything indefinitely on the local
  database. Disk is cheap.

**Privacy deletion alignment (open question, v3, Codex r2)**: if the
project has GDPR-style "delete my data" support, do trace rows
delete together with user hand history, or do they get retained
because they're "derived analytics not PII"? Either policy is
defensible; needs alignment with the project's broader privacy
posture before shipping. Captured in §"Remaining open questions."

Implementation: a `prune_old_traces(game_id, keep_last_n_hands=100)`
function that:
1. Reads `game.kind` from the games table.
2. Skips entirely if `kind in {'experiment', 'validation'}`.
3. Otherwise keeps the last N hands' traces and prunes the rest.
4. Runs on `on_hand_end` for completed games (or as a nightly batch).

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

**Counterfactual legality check (v3, Codex r2)**: shadow-eval is only
valid when each variant produces an action that is legal in the
frozen state and comparable under the same legal-action mask. If
disabling a rule causes the shadow distribution to choose an
illegal action (e.g. raise when only fold/call/all-in are legal),
the comparison is invalid — that decision is excluded from Mode 1
analysis and labeled `'legality_invalid'` in the output. The script
falls back to Mode 2/3 signal for those decisions, with a clear
"sample size after legality filter" line in the report.

```bash
python -m experiments.analyze_intervention_traces \
  --mode shadow \
  --sweep-dir /tmp/phase7_5_3seed \
  --disable-rule bluff_catch_override.default
```

Cost: one extra pipeline invocation per decision per disabled rule.
Cheap (no game-tree advance), scales linearly with rules. Legality-
filter exclusion rate is reported alongside the attribution result.

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

**Post-divergence exclusion zone (v3, Codex r2)**: after the first
action divergence in a hand, the two trajectories are in different
states — pot size, stack, board, opponent response all differ.
Per-decision attribution AFTER that point is labeled
`'different_trajectory_context'` and excluded from layer-level
attribution claims. The output reports it as a separate diagnostic:

```
Mode 2 output:
  First-divergence decisions: 6000
    Layer attributions for first divergence: ...

  Post-divergence decisions: 18,400 (excluded from per-decision attribution)
    Labeled 'different_trajectory_context' — these MAY have layer
    differences but the comparison is no longer apples-to-apples.
```

This explicitly prevents Mode 3 (aggregate) leakage into Mode 2
(decision-level) claims.

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

NARRATION_MAX_FACTS = 3   # Cap top facts surfaced to LLM (Codex r2)


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
    action_intent: str      # v3: 'value_bet' / 'bluff' / 'bluff_catch' /
                            # 'pot_control' / 'protection' / 'steal' /
                            # 'induce' / 'give_up'. Derived from layer +
                            # primary_action_after + hand_strength.
    intensity_bucket: str   # 'subtle' / 'noticeable' / 'strong'
                            # — magnitude of distribution change
    certainty_bucket: str   # v3: 'tentative' / 'confident' / 'sure'
                            # — separate from intensity (Codex r2:
                            # "strong effect" ≠ "high confidence")
    importance: float       # 0-1 ranking score for top-N selection;
                            # never exposed to LLM directly


@dataclass(frozen=True)
class NarrationContext:
    """Decision-level context the LLM needs alongside the facts.

    v3: pulled out of per-fact entries to keep facts focused on
    'observations' and put state context in one place.
    """
    street: str             # 'preflop' / 'flop' / 'turn' / 'river'
    position_context: str   # 'in_position' / 'out_of_position' /
                            # 'big_blind' / 'small_blind' / 'button'
    risk_posture: str       # 'conservative' / 'balanced' / 'aggressive'
                            # — derived from hero's anchors


@dataclass(frozen=True)
class NarrationFacts:
    """The narration-safe view of one decision's trace.

    Built by `traces_to_narration_facts(traces, hero_anchors,
    decision_context) -> NarrationFacts` which:
      - filters to only fired traces
      - rejects layers/rule_ids not in the narration allowlist
      - maps `reason_code` to a player-facing observation via
        REASON_CODE_TO_OBSERVATION (a hand-curated dict)
      - maps `effect_size` to intensity bucket
      - maps `confidence` to certainty bucket
      - scores each candidate fact via `_score_fact_importance`
      - ranks by importance and CAPS to NARRATION_MAX_FACTS (= 3)
      - selects the top-1 fact as `primary_factor` for the lead
      - strips any input fields not in NARRATION_INPUT_ALLOWLIST
    """
    facts: List[NarrationFact]               # capped at NARRATION_MAX_FACTS
    primary_factor: Optional[NarrationFact]  # the lead — typically the
                                             # one with action_changed=True
                                             # at the highest layer_order
    context: NarrationContext
    summary_intensity: str                   # overall "how unusual was
                                             # this decision" signal —
                                             # max intensity_bucket across
                                             # surfaced facts
    suppressed_facts_count: int              # v3: how many facts were
                                             # filtered or capped. Debug-
                                             # only, NOT sent to LLM, but
                                             # useful for "narration feels
                                             # thin" diagnostics


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


def _score_fact_importance(
    trace: InterventionTrace,
    decision_context,
    later_layer_overrode_this: bool,
) -> float:
    """Rank facts so top-N selection is principled, not first-come.

    Scoring is a weighted sum of six dimensions (v4, Codex r3):

    1. Operation severity (weight 0.30):
       override/veto = 1.0, clamp = 0.7, adjust = 0.5, suggest = 0.2,
       no_op = 0.0
    2. Action change (weight 0.25):
       action-class change (fold→call, call→raise) = 1.0,
       sizing-only change (raise_small→raise_large) = 0.5,
       no change = 0.0
    3. Certainty bucket (weight 0.15):
       sure = 1.0, confident = 0.7, tentative = 0.3
    4. Street importance (weight 0.10):
       river = 1.0, turn = 0.7, flop = 0.5, preflop = 0.3
       (Later streets typically have higher SPR-adjusted impact;
       preflop is high-volume but low-per-decision-EV.)
    5. Layer recency (weight 0.10):
       Later pipeline layers' decisions are more consequential since
       they had final say. Score = layer_order / max_layer_order.
    6. Narrative priority (weight 0.10):
       Hand-curated per (layer, rule_id):
         bluff_catch_override = 1.0 (high human interest)
         strong_hand_override = 1.0
         exploitation.hyper_aggressive = 0.8
         exploitation.tight_nit = 0.6
         exploitation.hyper_passive = 0.5
         personality = 0.3 (mechanical, less narratable)

    Crucial v4 rule: if `later_layer_overrode_this` is True (a later
    pipeline layer with operation=OVERRIDE/VETO superseded this one),
    multiply the final score by 0.3. The primary_factor should align
    with the final output, not the strongest intermediate
    intervention. Overwritten facts are kept available (they may
    still appear in the top-3) but down-weighted so they don't
    crowd out the layer that actually drove the action.

    Returns float in [0, 1]. The top NARRATION_MAX_FACTS (= 3) by
    score are surfaced; the highest-scoring one becomes
    primary_factor (the prompt's lead).
    """
    # Operation severity
    op_score = {
        'override': 1.0, 'veto': 1.0,
        'clamp': 0.7, 'adjust': 0.5,
        'suggest': 0.2, 'no_op': 0.0,
    }.get(trace.operation, 0.0)

    # Action change
    if trace.action_changed:
        act_score = 1.0
    elif trace.amount_bucket_before != trace.amount_bucket_after:
        act_score = 0.5
    else:
        act_score = 0.0

    # Certainty (mapped from trace.confidence by the adapter)
    if trace.confidence >= 0.8:
        cert_score = 1.0
    elif trace.confidence >= 0.5:
        cert_score = 0.7
    else:
        cert_score = 0.3

    # Street importance
    street_score = {
        'river': 1.0, 'turn': 0.7, 'flop': 0.5, 'preflop': 0.3,
    }.get(getattr(decision_context, 'street', ''), 0.5)

    # Layer recency (assume max_layer_order known from pipeline)
    layer_score = trace.layer_order / max(MAX_LAYER_ORDER, 1)

    # Narrative priority (hand-curated dict)
    narr_score = LAYER_RULE_NARRATIVE_WEIGHT.get(
        (trace.layer, trace.rule_id), 0.5,
    )

    score = (
        0.30 * op_score
        + 0.25 * act_score
        + 0.15 * cert_score
        + 0.10 * street_score
        + 0.10 * layer_score
        + 0.10 * narr_score
    )

    # Critical v4 rule: down-rank if a later layer superseded this one
    if later_layer_overrode_this:
        score *= 0.3

    return score


LAYER_RULE_NARRATIVE_WEIGHT: Dict[Tuple[str, str], float] = {
    ('bluff_catch_override',     'default'): 1.0,
    ('strong_hand_override',     'default'): 1.0,
    ('exploitation',             'hyper_aggressive'): 0.8,
    ('exploitation',             'tight_nit'): 0.6,
    ('exploitation',             'hyper_passive'): 0.5,
    ('exploitation',             'high_fold_to_cbet'): 0.8,
    ('exploitation',             'multiway_cbet'): 0.6,
    ('value_vs_station',         'default'): 0.7,
    ('steal_pressure',           'default'): 0.7,
    # personality / short_stack / math_floor not in NARRATION_ALLOWLIST
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

def test_override_chain_attribution():
    """v4 (Codex r3): when MULTIPLE layers override sequentially,
    the trace correctly records each override's prior_action_source.

    Scenario: exploitation.hyper_aggressive adjusts (fold→call), then
    bluff_catch_override overrides (call→call but distribution
    replaced), then math_floor vetoes (call→all-in). Verify:
      - 3 traces emitted with action_changed reflecting the chain
      - bluff_catch's prior_action_source = 'exploitation.hyper_aggressive'
      - math_floor's prior_action_source = 'bluff_catch_override.default'
      - Only math_floor has replaced_prior_action=True at the final
        action level (override on top of override on top of adjust).
      - Earlier traces' fact-importance is down-ranked by
        _score_fact_importance because later layers superseded them.
    """

def test_trace_write_failure_does_not_block_gameplay():
    """v4 (Codex r3): persistence failures degrade gracefully.

    Simulate a DB write error during trace persistence:
      - The controller's action is still returned (gameplay continues)
      - The error is logged at WARN level (not silent)
      - The in-memory trace for that decision is dropped, not retried
      - Subsequent decisions are unaffected
    """
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
    correctly, but readers need to understand the overwrite.
    v3 fix: `operation` enum makes this explicit (override / veto
    vs adjust / clamp), and `prior_action_source` records the
    overwritten layer.

11. **Per-decision performance overhead** (v4, Codex r3): trace
    construction + JSON serialization runs on every decision in
    the hot path. **Budget target: <5% of decision latency**
    (typically a few milliseconds per decision today). Measured
    via:
    - Microbench: time `_apply_bluff_catch_override` with and
      without trace emission. Diff is the per-rule overhead.
    - End-to-end: time `simulate_bb100 --hands 2000` pre- and
      post-migration. Diff is the cumulative overhead.
    If the measured overhead exceeds 5%, mitigate by:
    - Skipping `output_strategy_summary` when `fired=False` (saves
      the no-op case)
    - Lazy `config_snapshot` (only emit when the layer fired)
    - Batched JSON serialization at hand end rather than per-decision
    These are optimizations, not architectural changes; they don't
    affect the trace shape.

12. **Trace persistence failure must not block gameplay** (v4,
    Codex r3): DB write errors, schema mismatch, JSON serialization
    failures — none of these should propagate to the controller's
    return path. Policy:
    - Persistence is wrapped in try/except in the capture step
    - Errors logged at WARN level with the decision_id for debugging
    - The in-memory trace for that decision is dropped (no retry,
      no in-memory queue — those create their own failure modes)
    - Subsequent decisions are unaffected
    - The aggregate counters (`manager._exploitation_counters`)
      keep working as a degraded-mode signal source
    Test `test_trace_write_failure_does_not_block_gameplay` enforces
    this contract.

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

## Implementation log

### Step 1 (2026-05-14): bluff_catch_override reference migration

**Status: complete.**

Files landed:
- `poker/strategy/intervention_trace.py` (NEW) — `InterventionTrace`,
  `InterventionOperation` enum, `_LAYER_NAMES`, `_RULE_IDS_BY_LAYER`,
  `l1_distance` / `primary_action` / `summarize_strategy` /
  `amount_bucket` helpers, `_safe_serialize` (handles enums,
  dataclasses, numpy-like scalars, non-finite floats), `validate_trace`
  (enforces canonical layer + OVERRIDE ⇒ replaced_prior_action
  invariant), `make_no_op_trace` convenience constructor.
- `poker/strategy/value_override.py` — `compute_bluff_catch_strategy`
  now returns `Tuple[StrategyProfile, InterventionTrace]` with a
  fire trace (`operation=OVERRIDE`, populated inputs / summaries /
  config snapshot, dynamic reason_code `{hand_class}_vs_{tier}_facing_bet`).
  New `_build_bluff_catch_trace` + `_select_bluff_catch_config`
  helpers; the strategy builder itself stays focused on producing
  the distribution.
- `poker/tiered_bot_controller.py` — `_apply_bluff_catch_override`
  returns `(strategy, trace)`. No-op traces emitted on each early-out
  path with distinct reason_code (`manager_unavailable`,
  `hand_class_not_eligible`, `gate_rejected`). Controller now stashes
  `self._last_intervention_trace: List[InterventionTrace]`, reset
  at the top of `_get_postflop_decision` and at `__init__`.
- `tests/test_strategy/test_intervention_trace.py` (NEW, 24 tests) —
  schema invariants, JSON round-trip with enum / dataclass / non-finite
  edge cases, pure-helper coverage.
- `tests/test_strategy/test_intervention_trace_bluff_catch.py` (NEW,
  15 tests) — fire-trace shape, reason_code encoding, strategy
  summaries, controller-level no-op early-outs.
- `tests/test_strategy/test_bluff_catch_gate.py` — updated 6 existing
  call sites to unpack the new tuple return.
- `tests/test_strategy/test_tiered_bot_bluff_catch.py` — updated 7
  existing call sites.

**Test results: 716 strategy tests pass** (up from 677 pre-Step-1;
+39 new trace tests). End-to-end smoke check: 200-hand
`simulate_bb100 --opponent ManiacBot` completed cleanly (exit 0,
164s wall-clock).

**Performance budget measurement** (Codex r3 risk #11, <5% of
decision latency):

| Path | ns/call | μs/call |
|---|---|---|
| Pre-7.6 strategy-only (baseline) | 7,853 | 7.85 |
| Strategy + trace (fire path) | 26,964 | 26.96 |
| Trace construction overhead | 19,111 | 19.11 |
| No-op trace (`make_no_op_trace`) | 3,936 | 3.94 |

The fire-path trace adds ~19μs per fire (~243% of the strategy
build's own cost in isolation, but absolute overhead is microseconds).
The dominant decision-latency budget in the tiered controller is the
LLM call upstream (~50ms typical, can reach 200ms+); the trace
overhead is therefore **0.0382% of a 50ms decision — 131× under
the 5% budget**. The much more common no-op path (hand class not
in trigger set) costs ~4μs.

No optimization mitigations from §Risks #11 applied — the budget
passes by a wide margin even on the fire path. The `_select_
bluff_catch_config` helper's use of `dir()` is the biggest single
contributor (~5-10μs) and could be lowered by hard-coding the
allowlist later if subsequent layers push overhead over the budget,
but that's not needed yet.

**Threading vs accumulator decision** (open question #2): threading
held up cleanly. Five test files updated, signature change was
mechanical, no controller state-reset gotchas. Recommend continuing
with threading for Step 2.

**Open observations carried forward:**
- `prior_action_source` is currently `''` on the bluff_catch trace
  because no other layer emits traces yet. Step 2 will add the
  post-hoc fill-in from the prior trace entry.
- Preflop path does not yet initialize `_last_intervention_trace`
  per-decision (only postflop does). Preflop has no bluff_catch
  call, so no leak today, but the symmetry should be added when
  Step 2 migrates the personality / exploitation layers (both
  fire preflop).
- `_BLUFF_CATCH_LAYER_ORDER = 3` is hard-coded in `value_override.py`.
  Step 2 should promote layer_order to a single source of truth
  (an enum or `_LAYER_ORDER` dict in `intervention_trace.py`).

### Step 2 (2026-05-14): strong_hand_override migration + carry-forwards

**Status: complete.**

Files updated:
- `poker/strategy/intervention_trace.py` — added shared `_LAYER_ORDER`
  dict, `MAX_LAYER_ORDER` constant, and `layer_order_for(layer)` helper.
  Single source of truth for pipeline ordinals; no more hard-coded
  layer_order constants at each migration site. Mapping:
  `personality=0, exploitation=1, value_vs_station=1, steal_pressure=1,
  strong_hand_override=2, bluff_catch_override=3, short_stack=4,
  math_floor=5`.
- `poker/strategy/value_override.py:142` — `compute_value_override_
  strategy` returns `Tuple[StrategyProfile, InterventionTrace]`. Per-
  spot reason codes: `facing_all_in_call/jam`, `facing_bet_call_or_
  raise/call_only/raise_only`, `open_value_bet_{nuts/strong_made/
  strong}`. Pathological "degenerate legal-action-set" branches emit
  `fired=False` traces with distinct codes (e.g. `facing_all_in_no_
  continuing_action`) so attribution doesn't mis-credit them as
  overrides.
- `poker/tiered_bot_controller.py:717` — `_apply_value_override`
  returns `(strategy, trace)` with no-op early-out traces
  (`manager_unavailable`, `gate_rejected`). Both postflop and preflop
  call sites updated; preflop now initializes `_last_intervention_
  trace = []` symmetrically with postflop.
- `poker/tiered_bot_controller.py:72` — new module-level `_fill_
  prior_action_source(current, earlier)` helper. Threads through
  `dataclasses.replace` (the trace is frozen) and walks `earlier` in
  reverse to find the most recent fired trace, populating
  `current.prior_action_source = f'{prior.layer}.{prior.rule_id}'`.
  Bluff_catch's trace now correctly records the overwrite chain when
  the strong-hand override fires earlier (mutually exclusive by hand
  class today, but the same helper will compose correctly with
  exploitation in Step 3).
- 19 new trace tests in `test_intervention_trace_strong_hand.py`
  covering each spot type, JSON round-trip, and 5
  `_fill_prior_action_source` semantics tests (fills from last fired,
  no_op left unchanged, no earlier fired layer leaves empty, does not
  clobber existing value, picks most recent fired when multiple).
- 19 existing call sites updated across `test_value_override.py` (14)
  + `test_tiered_bot_exploitation.py` (5).

**Test results: 733 strategy tests pass** (up from 716 after Step 1;
+17 new). End-to-end quick-suite: 3069 tests pass.

### Step 3 (2026-05-14): per-rule exploitation traces + Phase 8 layers

**Status: complete.**

The complex step — exploitation emits one trace per rule (5
exploitation sub-rules + 2 Phase 8 layers), not one trace per layer.
Backwards-compat is preserved through a wrapper because
`compute_exploitation_offsets` has 60+ test callers.

Files updated:
- `poker/strategy/exploitation.py:664` — added
  `compute_exploitation_offsets_with_traces` returning
  `Tuple[Dict[str, float], List[InterventionTrace]]`. Each rule
  tracks its own offset contributions in a `rule_offsets` accumulator
  separately from the combined `offsets` dict. Per-rule trace emits
  `operation='adjust'`, `effect='offsets_applied'`, `effect_size = L1
  norm of the rule's own offsets`, with `extra['offsets']` containing
  the rule's contribution dict.
  - Legacy `compute_exploitation_offsets` is now a thin wrapper that
    discards traces — 60+ existing callers keep working unchanged.
- **Tier-aware reason codes** (Codex r2 open Q #5 resolved via option 1
  — encode tier in `reason_code`, not a separate field). hyper_aggressive
  emits one of `extreme_tier_via_all_in_frequency`,
  `extreme_tier_via_aggression_factor`,
  `medium_tier_via_all_in_frequency`,
  `medium_tier_via_aggression_factor`. Tier threshold: intensity ≥ 0.7
  → extreme; else medium. Winning axis = whichever ramp reached higher
  intensity (`all_in_frequency` wins ties).
- **Always-7-traces invariant**: every call emits exactly 7 traces
  (5 exploitation + value_vs_station + steal_pressure), even early-
  outs. Reason codes distinguish paths: `gating_floor_blocked`,
  `aggregate_cold_start`, `intensity_below_threshold`, `not_open_spot`,
  `not_hu_cbet_spot`, `not_multiway_cbet_spot`, `intensity_zero_or_
  gated`. Downstream firing-rate analysis sees a consistent rule_id
  surface across decisions.
- **Phase 8 layer separation**: value_vs_station and steal_pressure
  emit traces with `layer='value_vs_station'` / `layer='steal_pressure'`,
  NOT under `layer='exploitation'`. They share `layer_order=1` with
  exploitation because they nest into the same pipeline step, but
  they're distinct layers for analysis grouping.
- `poker/tiered_bot_controller.py:72` — added module-level
  `_EXPLOITATION_RULE_ORDER` tuple + `_exploitation_no_op_traces`
  helper. Controller-level early-outs (manager None, anchors None)
  emit the same 7-rule surface as a normal evaluation that gated
  each rule out individually.
- `_apply_exploitation` returns `(strategy, List[InterventionTrace])`,
  switches to `compute_exploitation_offsets_with_traces`. Both
  postflop and preflop call sites `extend()` the controller-level
  trace accumulator.
- 16 new trace tests in `test_intervention_trace_exploitation.py`
  covering: always-7 invariant on all early-out paths, per-rule fire
  semantics for hyper_aggressive / hyper_passive / tight_nit, medium-
  vs-extreme tier reason codes, Phase 8 layer separation, combined-
  offsets attribution invariant (Σ per-rule offsets == combined
  dict), legacy wrapper backcompat, JSON round-trip.
- 6 existing call sites updated in `test_tiered_bot_exploitation.py`.

**Test results: 749 strategy tests pass** (up from 733 after Step 2;
+16 new). End-to-end quick-suite: 3084 tests pass (one Flask
game-routes test flaked under xdist concurrency — passes in
isolation, unrelated to trace migration).

**Carried forward into Step 4 (personality layer):**
- The `_fill_prior_action_source` helper now picks the most recent
  fired trace across the longer trace list (7 exploitation rules +
  strong_hand_override), so bluff_catch's prior_action_source is
  meaningful even when individual exploitation sub-rules fired.
- Combined-offsets attribution invariant is testable end-to-end:
  analysis can reconstruct combined behavior from per-rule trace
  decomposition.
- Per-rule effect_size uses L1 of the offset vector (logit space),
  not L1 of distribution change — documented in the rule's
  `extra['offset_l1']` for downstream filters. This differs from
  bluff_catch / strong_hand_override which use distribution L1.
  Acceptable difference: offsets compose multiplicatively, so per-
  rule isolated distribution L1 would be misleading.

### Step 4 (2026-05-14): personality + short_stack + math_floor migrations

**Status: complete. Postflop pipeline fully migrated.**

The final three layers per the plan's migration order. Each takes a
trace-shape that matches its semantic role:

- **personality**: `operation='adjust'` (logit-space distortion
  preserves prior intent). Simpler trace per plan recommendation —
  records deviation_profile name + emotional_state + L1 shift.
- **short_stack**: `operation='clamp'` (Codex r3 disambiguation:
  bounds medium-raise mass without VETOing it from consideration).
- **math_floor**: `operation='veto'` (when fired, removes all non-
  target actions from the distribution — the canonical example of
  the veto operation in this codebase).

Files updated:
- `poker/strategy/personality_modifier.py:240` — `modify_strategy`
  returns `Tuple[StrategyProfile, InterventionTrace]`. Trace records
  `deviation_profile_{name}` reason_code via reverse-lookup against
  `DEVIATION_PROFILES` (DeviationProfile is a frozen dataclass without
  an embedded `name` attribute). Degenerate-support early-outs emit
  no_op traces with `single_supported_action` or `zero_total_
  probability` reason codes.
- `poker/strategy/short_stack.py:83` — `apply_short_stack_heuristics`
  returns `(strategy, trace)`. Clamp trace records the suppression
  factor, sink action (jam or fold), redistributed mass, and which
  medium raises were affected. Three no_op paths: stack_deep,
  no_medium_raises_in_strategy, no_legal_sink_action.
- `poker/strategy/math_floor.py:37` — `apply_pot_odds_floor` returns
  `(strategy, trace)`. Old `(strategy, Optional[str])` signature
  collapsed — rule name is now `trace.reason_code` (one of
  `short_stack`, `pot_committed`, `tiny_pot_odds`). 9 test sites
  rewritten to read `trace.reason_code` / `trace.fired` instead of
  the legacy `rule` channel.
- `poker/tiered_bot_controller.py:303,472` — both pipelines append
  personality trace (real or distortion_skipped no_op). Postflop and
  preflop short_stack and math_floor sites updated to thread the
  trace return. `_fill_prior_action_source` now runs on the math_floor
  trace too so it correctly records the last fired layer.
- 30 new trace tests in 4 new files: `test_intervention_trace_
  personality.py`, `test_intervention_trace_short_stack.py`,
  `test_intervention_trace_math_floor.py`, `test_intervention_trace_
  e2e.py` (the integration test covering trace surface invariants,
  layer_order monotonicity, prior_action_source chaining).
- 35 existing call sites updated across personality / short_stack /
  math_floor test files.

**Test results: 778 strategy tests pass** (up from 749 after Step 3;
+29 new traces). Behavior-neutral — all pre-existing functional and
behavioral tests still pass.

**Postflop pipeline trace surface (post-Step-4):**

A single postflop decision now emits 12 traces per the canonical
`_LAYER_ORDER`:

| # | layer                  | rule_id           | operation |
|---|------------------------|-------------------|-----------|
| 0 | personality            | default           | adjust    |
| 1 | exploitation           | hyper_aggressive  | adjust    |
| 2 | exploitation           | hyper_passive     | adjust    |
| 3 | exploitation           | tight_nit         | adjust    |
| 4 | exploitation           | high_fold_to_cbet | adjust    |
| 5 | exploitation           | multiway_cbet     | adjust    |
| 6 | value_vs_station       | default           | adjust    |
| 7 | steal_pressure         | default           | adjust    |
| 8 | strong_hand_override   | default           | override  |
| 9 | bluff_catch_override   | default           | override  |
| 10| short_stack            | default           | clamp     |
| 11| math_floor             | default           | veto      |

Every layer/rule fires-or-no-ops consistently across decisions, so
firing-rate analysis sees a uniform shape. The mutual exclusivity
between strong_hand and bluff_catch (by hand class) shows up as
"exactly one of them fires per decision" — never both, never neither.

**Steps 1-4 = full pipeline migration complete.** What remains in the
Phase 7.6 plan: Step 3a (access-pattern audit) → Step 3b (persistence
schema) → Step 4 (analyze_intervention_traces.py with 4 attribution
modes) → Step 5a/b (NarrationFacts adapter + ExpressionGenerator
integration) → Step 6 (validation). Pure plumbing + analysis work
from here — no more strategy-pipeline migrations.

### Step 3a/3b (2026-05-14): persistence schema + capture wiring

**Status: complete.**

**Step 3a audit findings.** Cataloged consumers:

| Consumer | Pattern | Frequency |
|---|---|---|
| Analysis script (Mode 1-4) | Full trace read by `(game_id, hand_number, decision_index)`, in-memory Python | Batch, post-hoc |
| Real-time controller | In-memory only | Per-decision (already wired) |
| Narration prompt | Single decision at a time | Per-decision |
| Dashboard / aggregate firing rates | Group by `(layer, rule_id, archetype)` | Not yet a consumer |

**Schema decision: Option B (JSON column).** Reasons:
- Existing schema convention: 9 `*_json` columns on `player_decision_
  analysis` already (`opponent_ranges_json`, `zone_penalties_json`,
  etc.) — JSON-in-column is the established pattern.
- Mode 1 shadow-eval (the strongest attribution tool per plan) is
  in-memory Python; doesn't benefit from a normalized table.
- 12 traces × N decisions = high row count if normalized; one row
  per decision is cleaner for joins to existing analytics columns.
- Dashboard / aggregate queries aren't yet a consumer; can promote
  to Option A later via `json_each()` extraction if firing-rate
  dashboards become heavy.

**Step 3b files updated:**
- `poker/repositories/schema_manager.py:52` — SCHEMA_VERSION 80 → 81;
  new `_migrate_v81_add_intervention_trace_json` adds nullable
  `intervention_trace_json TEXT` column to `player_decision_analysis`.
  Existing rows lack the column and analysis code treats NULL as "no
  trace available" — no backfill needed.
- `poker/repositories/decision_analysis_repository.py` — write path
  adds the column to the `INSERT INTO player_decision_analysis` SQL;
  new `get_intervention_trace(analysis_id)` + `get_intervention_
  traces_for_game(game_id, hand_number=None)` read paths deserialize
  to lists of dicts. Both tolerate malformed JSON (log WARN, return
  None / skip row).
- `poker/decision_analyzer.py:174` — added `intervention_trace_json:
  Optional[str] = None` field to the `DecisionAnalysis` dataclass.
  Default None for hybrid AI controllers + pre-v81 rows.
- `poker/controllers.py:51` — new module-level
  `_serialize_intervention_trace(traces, *, player_name)` helper.
  Per Codex r3 risk #12: any error during serialization is logged at
  WARN and returns None; the analysis row still persists without the
  trace. Gameplay never blocked by trace persistence failure.
- `poker/controllers.py:1694` — capture path now reads
  `getattr(self, '_last_intervention_trace', None)` and attaches to
  `analysis.intervention_trace_json` via the serializer before
  `save_decision_analysis`. Hybrid AI controllers don't expose the
  attribute → `None` payload (matches the contract).
- 14 new tests in `tests/test_strategy/test_intervention_trace_
  persistence.py`: serializer round-trip + bad-input degradation,
  DecisionAnalysis dataclass field presence, schema v81 column
  presence, save/load round-trip via the repository, filter-by-hand,
  malformed-JSON graceful skip.

**Test results: 792 strategy tests pass** (up from 778 after Step 4;
+14 new persistence tests). Schema migration is forward-compatible
— existing tests pass against pre-v81 databases (column nullable,
defaults None).

**Persistence is now end-to-end:**

```
controller decision → _last_intervention_trace populated
                   ↓
_analyze_decision  → _serialize_intervention_trace → JSON string
                   ↓
DecisionAnalysisRepository.save_decision_analysis
                   ↓
SQLite: player_decision_analysis.intervention_trace_json column
                   ↓
analyze script ←   DecisionAnalysisRepository.get_intervention_
                   traces_for_game(game_id)
```

**Next step:** Step 4 in the plan's own numbering — the
`analyze_intervention_traces.py` script with 4 attribution modes
(shadow-eval, first-divergence, aggregate, ablation). The data is
now persisted and queryable; the analysis tooling is the next
deliverable.

### Step 4 (2026-05-14): analyze_intervention_traces.py with 4 modes

**Status: Modes 2 + 3 fully implemented; Modes 1 + 4 stubbed.**

Scoping decision: Modes 1 (shadow-eval) and 4 (ablation) both require
per-rule disable plumbing on the strategy pipeline — a separate
controller-level change. The data-only modes (2 + 3) ship in this
delivery; the plumbing for 1 + 4 is a self-contained follow-up.

**Files landed:**
- `experiments/analyze_intervention_traces.py` (NEW) — argparse CLI
  with subcommands per mode. Reads via `DecisionAnalysisRepository.
  get_intervention_traces_for_game()`. Output formats: `text` (default,
  human-readable table) and `json` (machine-readable for jq / notebooks).
- `tests/test_analyze_intervention_traces.py` (NEW, 11 tests):
  Mode 3 aggregation invariants (fire counts, mean effect_size,
  top reason codes), Mode 2 divergence detection + post-divergence
  exclusion, Mode 1 + 4 stub TODO messages, CLI smoke tests for both
  output formats and missing-argument errors.

**Mode 3 (aggregate firing rates) — implemented:**

For one game, or all games in the DB, reports per `(layer, rule_id)`:
- Total evaluations (always 12 per postflop decision after Step 4)
- Fired count + fire rate %
- Mean effect_size across firings
- Top 3 reason_codes with counts

Sample output (synthetic):

```
Mode: aggregate firing rates
Games: 1 (game_abc123)
Decisions analyzed: 47

layer                  rule_id              evaluated  fired  fire%   mean_size  top reasons
exploitation           hyper_aggressive            47     12   25.5%      0.4321  extreme_tier=8, ...
exploitation           hyper_passive               47      0    0.0%      0.0000  intensity_below_threshold=47
bluff_catch_override   default                     47      3    6.4%      0.6200  medium_made_vs_extreme=3, hand_class_not_eligible=44
math_floor             default                     47      2    4.3%      1.0000  pot_committed=1, short_stack=1, no_call_facing=44
...
```

**Mode 2 (first-divergence) — implemented:**

For matched-seed candidate/control runs, walks both decision streams
per `(hand_number, phase)` and identifies the first decision per hand
where chosen actions differ. Attributes divergences to (layer,
rule_id) entries where `fired`, `primary_action_after`, OR
`reason_code` differ between the two streams (effect_size and
rationale string aren't divergence signals — too noisy). Post-
divergence decisions on the same hand are counted as
`post_divergence_excluded_decisions` and excluded from per-decision
attribution claims per plan §"Mode 2 post-divergence exclusion zone."

**Mode 1 + Mode 4 stubs:**

Both modes emit a structured "not yet implemented" message describing
the per-rule disable plumbing needed. Exit code 2 (distinguishable
from CLI usage errors). Stub message documents the planned
implementation:

> Mode 'shadow' requires per-rule disable plumbing on the strategy
> pipeline:
>   - A `disable_rules: FrozenSet[Tuple[str, str]]` option on the
>     controller that propagates through `_apply_exploitation`,
>     `_apply_value_override`, `_apply_bluff_catch_override`, etc.
>   - Inside each layer, gate the rule's offset/override write on
>     `(layer, rule_id) not in disable_rules`.
>   - The trace for a disabled rule emits `fired=False` with
>     `reason_code='disabled_by_ablation'` so analysis sees the
>     counterfactual cleanly.

This way the message tells the next-session implementer exactly what
needs to land before Mode 1/4 work.

**Test results: 792 strategy tests + 116 trace tests + 11 analyze
tests pass.** No regressions.

**Carried forward:**
- The disable-rule plumbing (for Modes 1 + 4) is well-scoped: one new
  optional param on the controller, propagated through the 4 migrated
  layer functions, gated inside each rule. Estimated 0.5-1 day.
- Once Mode 1 lands, `experiments/simulate_bb100.py` can call the
  analysis script post-sweep to produce per-rule EV attribution.

### Step 5 (2026-05-14): per-rule disable plumbing + Mode 4 ablation

**Status: complete. Mode 1 (shadow) still stubbed pending persistence-replay.**

This unblocks Mode 4 (ablation analysis) by letting sweeps run with
specific (layer, rule_id) pairs suppressed. The same plumbing
underlies a future Mode 1 (shadow-eval) implementation — once
persistence-replay is wired, Mode 1 just re-invokes the pipeline
with `disable_rules={target}` and compares L1 distance.

**Files updated:**
- `poker/strategy/intervention_trace.py` — added `DISABLED_BY_ABLATION`
  constant, `make_disabled_trace(layer, rule_id, layer_order)`, and
  `is_rule_disabled(disable_rules, layer, rule_id)` helper. Disabled
  rules emit a fixed `fired=False` trace with the stable
  `disabled_by_ablation` reason_code so attribution analysis can
  isolate ablation effects from natural no-ops.
- All six layer functions accept `disable_rules=None` kwarg:
  `compute_exploitation_offsets_with_traces` (gates each of the 5
  rules + 2 Phase 8 layers individually),
  `compute_value_override_strategy`, `compute_bluff_catch_strategy`,
  `modify_strategy`, `apply_short_stack_heuristics`,
  `apply_pot_odds_floor`. Each layer short-circuits at the top if its
  rule is disabled, returning the strategy unchanged plus a
  `disabled_by_ablation` trace.
- `poker/tiered_bot_controller.py` — new `self.disable_rules:
  frozenset = frozenset()` attribute on `TieredBotController.__init__`.
  All six layer call sites in the postflop + preflop pipelines pass
  it through. Controller-level `_apply_*` methods short-circuit on
  disable BEFORE the natural early-out gates so the trace reports
  `disabled_by_ablation` (not `manager_unavailable`) for disabled
  rules. `_exploitation_no_op_traces` updated to emit per-rule
  disabled traces when relevant.
  - Defensive `getattr(self, 'disable_rules', frozenset())` access at
    call sites — test fixtures that bypass `__init__` via `__new__`
    continue to work without setting the attribute.
- `experiments/analyze_intervention_traces.py` — Mode 4 ablation
  implemented. Compares a baseline run (no disables) vs an ablation
  run (one or more rules disabled). Auto-detects ablated rules by
  scanning the ablation run's traces for the
  `disabled_by_ablation` reason_code. Reports: shared hands, paired
  decisions, action-changed decisions, action change rate, post-
  divergence excluded count. Mode 1 (shadow) stub updated with a
  more specific TODO message: distinguishes the now-existing disable
  plumbing from the still-missing persistence-replay piece.
- 15 new tests in `tests/test_strategy/test_intervention_trace_
  disable.py`: per-layer disable semantics (each layer's disable
  produces a `disabled_by_ablation` trace + unchanged strategy),
  exploitation per-rule isolation (disabling one rule doesn't affect
  others), legacy `compute_exploitation_offsets` wrapper propagates
  the disable_rules kwarg, controller-level disable through real
  fixtures.
- 4 new analyze tests covering Mode 4: ablation detection from
  trace reason_codes, action-change attribution, paired-decision
  walk, CLI argument validation.

**Sample CLI invocations (Mode 4):**

```bash
# Run a baseline sweep, then an ablation sweep with bluff_catch disabled
# (via setting controller.disable_rules in the sim driver), then:

docker compose exec backend python -m experiments.analyze_intervention_traces \\
    --mode ablation \\
    --db /app/data/poker_games.db \\
    --baseline-game game_baseline_seed42 \\
    --ablation-game game_ablation_seed42

# Output:
#   Mode: ablation comparison
#   Baseline game: game_baseline_seed42
#   Ablation game: game_ablation_seed42
#   Ablated rules: bluff_catch_override.default
#   Shared hands: 200
#   Paired decisions (pre-divergence): 412
#   Decisions where action changed:    23
#   Action change rate: 5.58%
#   Post-divergence decisions excluded: 89
```

**Test results: 806 strategy tests pass** (up from 792 after Step
3a/3b; +14 new disable tests). 12 analyze tests pass (one new
Mode 4 case set replacing the old shadow/ablation stub tests).

**Mode 1 (shadow) remaining work:**

Either (a) persist `(anchors, emotional_state, decision_context,
base_strategy)` per decision so the pipeline can be re-invoked
post-hoc — or (b) call the pipeline twice live during simulation
(once with empty `disable_rules`, once with target rule disabled)
and persist both distributions. (b) doubles per-decision pipeline
cost; only acceptable for experiment runs, not live games. (a) is
heavier on storage but doesn't affect live latency. Decision
deferred to the next implementation session.

### Step 6 (2026-05-14): Mode 1 (shadow-eval) via persistence-replay

**Status: complete. All four modes implemented.**

Approach: persistence-replay (Option (a) from Step 5's open
question). Each decision now persists a JSON snapshot of the pipeline
inputs, and the analysis script re-invokes the pipeline post-hoc
with `disable_rules={target}` to produce a counterfactual strategy.

**Files updated:**
- `poker/repositories/schema_manager.py` — SCHEMA_VERSION 81 → 82;
  new `_migrate_v82_add_strategy_pipeline_snapshot_json` adds a
  nullable `strategy_pipeline_snapshot_json TEXT` column to
  `player_decision_analysis`. Existing rows lack it and Mode 1
  treats them as `no_snapshot_coverage`.
- `poker/repositories/decision_analysis_repository.py` — write path
  includes the new column; new `get_strategy_pipeline_snapshot
  (analysis_id)` read helper.
- `poker/decision_analyzer.py` — added `strategy_pipeline_snapshot_
  json: Optional[str] = None` field to `DecisionAnalysis`.
- `poker/strategy/replay.py` (NEW) — stateless
  `replay_strategy_pipeline(snapshot, disable_rules) -> StrategyProfile`.
  Reconstructs `(anchors, emotional_state, decision_context, stats,
  intensities, ...)` from the JSON snapshot and re-runs the full
  pipeline (personality → exploitation → strong_hand_override →
  bluff_catch_override → short_stack → math_floor). Defensive
  against malformed snapshots — never raises, returns the base
  strategy on degenerate input.
- `poker/tiered_bot_controller.py` — new
  `self._last_pipeline_snapshot: Dict[str, Any]` accumulator, reset
  at the top of `_get_postflop_decision` / `_get_preflop_decision`.
  Three new helpers populate the snapshot at the right pipeline
  points: `_snapshot_personality_inputs(anchors, emotional_state)`,
  `_snapshot_exploitation_inputs(...)`,
  `_snapshot_math_floor_inputs(game_state, player_idx)`. The base
  strategy + legal_actions + hand_strength + effective_stack_bb are
  written directly inline.
- `poker/controllers.py` — new `_serialize_pipeline_snapshot(snapshot,
  *, player_name)` helper. Capture path attaches the JSON snapshot
  to the `DecisionAnalysis` row alongside the intervention trace.
  Codex r3 risk #12 contract: any serialization error is logged at
  WARN and returns None; gameplay never blocked.
- `experiments/analyze_intervention_traces.py` — Mode 1 (shadow)
  implemented. For each persisted decision with a snapshot:
    1. `replay_strategy_pipeline(snapshot, disable_rules=frozenset())` → live
    2. `replay_strategy_pipeline(snapshot, disable_rules={target})` → shadow
    3. L1 distance + action-flip check
  Reports: total decisions, evaluated count, no-snapshot count,
  mean / max L1 distance, action-flip count + rate. Decisions
  without snapshots count as `no_snapshot_coverage` (not failures).
- 9 new tests in `tests/test_strategy/test_replay_pipeline.py`:
  empty-snapshot safety, disable_rules propagation, math_floor
  disable changes output, personality runs vs disabled, garbage-input
  safety.
- 3 new tests in `tests/test_analyze_intervention_traces.py`:
  Mode 1 skips decisions without snapshots, evaluates decisions with
  snapshots (L1=0 for inert pipelines), correctly reports L1 +
  action_flip when math_floor flips the action.
- 3 new CLI tests for shadow mode argument validation.

**Sample CLI invocation:**

```bash
docker compose exec backend python -m experiments.analyze_intervention_traces \\
    --mode shadow \\
    --db /app/data/poker_games.db \\
    --game-id game_abc123 \\
    --disable-rule bluff_catch_override.default

# Output:
#   Mode: shadow-eval (same-state per-decision attribution)
#   Game: game_abc123
#   Disabled rule: bluff_catch_override.default
#   Decisions in game: 100
#     evaluated:           100
#     no snapshot coverage: 0
#   Mean L1 distance (live vs shadow): 0.0237
#   Max L1 distance:                   0.6200
#   Action flips (argmax differs):     3 (3.00%)
```

**Test results: 827 strategy tests pass** (up from 806 after Step 5;
+21 new across replay + Mode 1 + persistence). Mode 1 is now the
plan's intended same-state per-decision attribution tool — no
trajectory divergence concerns.

**Storage cost:** snapshot JSON is ~2-3KB per decision (depends on
opponent_stats field count). For a 1000-decision game that's
~2-3MB. Comfortable within the existing player_decision_analysis
table's size budget.

**All four attribution modes are now implemented:**

| Mode | Status | Per-decision causality |
|---|---|---|
| 1 (shadow) | **shipped** | strongest — same-state, no divergence |
| 2 (first-divergence) | shipped (Step 4) | medium — first-decision attribution only |
| 3 (aggregate) | shipped (Step 4) | n/a — firing-rate diagnostic |
| 4 (ablation) | shipped (Step 5) | medium — paired-sweep with rule(s) disabled |

### Step 5 narration (2026-05-14): NarrationFacts adapter + ExpressionGenerator integration

**Status: complete.** The "second motivation" of Phase 7.6 is now
wired end-to-end: traces become structured narration input for the
LLM expression layer.

**Files landed:**
- `poker/strategy/narration_facts.py` (NEW) — adapter module:
  - `NarrationFact`, `NarrationContext`, `NarrationFacts` frozen
    dataclasses
  - `NARRATION_ALLOWLIST` (9 surfaceable layer/rule pairs;
    personality + short_stack + math_floor explicitly absent —
    they're mechanical, not narratable)
  - `REASON_CODE_TO_OBSERVATION` hand-curated dict mapping ~20
    stable reason_codes to player-facing `(observation, why_it_matters)`
    tuples (e.g. `extreme_tier_via_all_in_frequency` →
    `("Opponent's been jamming a lot", "Their bet range is wider
    than usual here")`)
  - `LAYER_RULE_NARRATIVE_WEIGHT` priorities per plan
  - `LAYER_RULE_ACTION_INTENT` (steal / value_bet / bluff_catch /
    etc. per rule)
  - `_intensity_bucket` (effect_size → subtle/noticeable/strong)
    and `_certainty_bucket` (confidence → tentative/confident/sure)
    — kept independent per Codex r2 ("strong effect" ≠ "high
    confidence")
  - `_score_fact_importance` — 6-dim weighted scoring (operation
    severity 0.30, action_changed 0.25, certainty 0.15, street
    0.10, layer recency 0.10, narrative priority 0.10); overridden
    facts down-ranked 0.3× per Codex r3
  - `traces_to_narration_facts(traces, decision_context)` — main
    adapter. Filters → maps → scores → caps to NARRATION_MAX_FACTS=3
    → selects `primary_factor` as top score
  - `render_narration_prompt(facts)` — turns NarrationFacts into a
    structured "WHAT YOU NOTICED / WHAT YOU DECIDED" prompt block
  - `_fallback_observation` for unmapped reason_codes within
    allowlisted layers
- `poker/strategy/expression_context.py` — added
  `narration_facts: Optional[NarrationFacts] = None` field. Optional
  + default None ⇒ hybrid AI controller / pre-7.6 callers continue
  to produce identical prompts.
- `poker/strategy/expression_generator.py` — `_render_prompt`
  appends the rendered narration_facts block when present.
  `_render_narration_facts_block` wraps the call in try/except;
  any failure logs WARN + returns empty (the standard template
  still renders).
- `poker/tiered_bot_controller.py` — new `_build_narration_facts
  (phase)` helper. Reads `self._last_intervention_trace`, builds a
  `NarrationContext` with the street, calls the adapter. Returns
  None on any error (narration is observability, never blocks
  gameplay). Wired into `_attach_expression` so the
  ExpressionContext now carries narration_facts when the tiered bot
  has trace data available.

**Tests (24 new):** `tests/test_strategy/test_narration_facts.py`
covers:
- Allowlist filtering (personality / short_stack / math_floor never
  surface; unknown layers rejected)
- Reason-code lookups + fallbacks
- Top-3 cap + suppressed_facts_count accounting
- primary_factor is the highest-scoring fact
- Override-chain down-ranking (overridden layer is downranked 0.3×
  but may still appear in top-3)
- Bucket thresholds + intensity/certainty independence
- `_score_fact_importance` direct invariants
- Prompt rendering doesn't leak dev rationale strings or stat names
- Action-intent assignment per layer

**Test results: 154 narration + intervention-trace tests pass.**
Strategy regression overall: 1 pre-existing failure in
`test_passive_with_jams.py::test_casebot_aggregate_auto_suppresses_fold_mass`
— UNRELATED to Step 5 narration (verified by stashing all Step 5
files and re-running; the test fails identically). That test is an
untracked file whose body asserts a behavior change that the
exploitation.py code's own docstring says was reverted (Phase 8.1b
empirical regression).

**Sample integration flow:**

```
controller decides → self._last_intervention_trace populated
                  ↓
_attach_expression → _build_narration_facts('flop')
                  → NarrationFacts(facts=[3 top facts], primary=...)
                  ↓
ExpressionContext.narration_facts = facts
                  ↓
ExpressionGenerator._render_prompt
  appends:
    WHAT YOU NOTICED:
    - Opponent's been jamming a lot
    - I have showdown value against an over-aggressor

    WHAT YOU DECIDED:
    - I'm calling
    - Why: My pair beats most of their bluff range
    - Intensity: noticeable

    NARRATE THIS DECISION IN CHARACTER (1-2 sentences, present
    tense, no specific numbers or stats — just the read).
                  ↓
LLM produces authentic narration grounded in the bot's actual reads
```

**Remaining Phase 7.6 work: Step 6 validation** (behavior-neutrality
diff, attribution sanity check, narration smoke check). Implementation
is essentially complete — Step 6 closes the loop with empirical
validation.

## Resolved by Codex review (v2 + v3 + v4)

### v4 (round 3)

- ✅ **clamp vs veto disambiguation** — semantic distinction
  documented + invariant on `OVERRIDE` ⇒ `replaced_prior_action`.
- ✅ **`_score_fact_importance` concrete ranking** — six weighted
  dimensions; overwritten facts down-ranked 0.3×.
- ✅ **Overwrite-chain tests** — added
  `test_override_chain_attribution` covering sequential overrides.
- ✅ **Performance budget target** — < 5% of decision latency,
  measured during Step 1. Mitigation strategies if exceeded.
- ✅ **Trace write-failure policy** — gameplay continues; errors
  logged at WARN; in-memory trace dropped; aggregate counters
  remain as degraded-mode signal.

### v3 (round 2) + v2 (round 1)

- ✅ **Sub-layer attribution within `exploitation`** (v2) — multiple
  traces per layer with distinct `rule_id`, NOT in `extra`. See
  `_RULE_IDS_BY_LAYER` in the trace data type section.
- ✅ **Narration-vs-analysis separation** (v2) — `NarrationFacts`
  adapter is a distinct surface; the LLM never sees the raw
  analytical trace.
- ✅ **Attribution methodology causality limits** (v2 + v3) — Mode 1
  shadow-eval is the strongest per-decision tool with a legality-
  filter exclusion; Mode 2 has a post-divergence exclusion zone;
  Mode 3 paired-sweep gives aggregate signal only. Analysis script
  labels each number by mode.
- ✅ **Persistence schema choice** (v2) — deferred to Step 3a
  access-pattern audit.
- ✅ **Schema versioning** (v2) — `schema_version` field +
  minor/major versioning policy.
- ✅ **Retention/pruning + experiment isolation** (v2 + v3) —
  `prune_old_traces` for production (last 100 hands); experiments
  and validation runs skipped via `game.kind` field.
- ✅ **Layer-overwrite semantics** (v3) — `operation` enum + the
  three overwrite-tracking fields capture how each layer relates
  to the prior strategy.
- ✅ **Poker-action companions to `effect_size`** (v3) — `action_
  changed`, `primary_action_before/after`, `amount_bucket_*`.
- ✅ **NarrationFacts ranking + cap** (v3) — top-3 facts via
  `_score_fact_importance`; `primary_factor` lead selected for prompt.
- ✅ **`config_snapshot` bloat guardrail** (v3) — per-layer allowlist
  via `_select_config_for_trace`.

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

3. **Mode 1 shadow-eval cost** (still open): each shadow-eval call
   invokes the full strategy pipeline (minus the disabled layer)
   for one decision. For 6000 decisions × 6 layers to ablate,
   that's 36k extra pipeline runs. Estimate: ~5-15 min per analysis
   run, but bears measuring once a real sweep exists.

4. **Layer-overwrite semantics for `effect_size`** (still open):
   when one layer replaces a previous layer's output, what should
   the later layer's `effect_size` measure — L1 distance from the
   immediately prior strategy, or from the original chart baseline?
   The latter is more meaningful for narration ("this layer
   contributed X% of the total move"); the former is simpler.
   Default to "L1 from immediately prior strategy"; revisit after
   seeing real traces.

5. **Exploitation rule_id completeness** (v3, Codex r2): the five
   declared exploitation `rule_id`s (hyper_aggressive, hyper_passive,
   tight_nit, high_fold_to_cbet, multiway_cbet) may not capture
   Phase 7.5's three-tier clamp's internal tier distinctions. When
   the clamp escalates from MEDIUM to EXTREME tier, that's a
   meaningful behavioral shift in the same `hyper_aggressive` rule.
   Three options:
   - Add `tier` as a `reason_code` value (`extreme_tier_via_jam_open`
     vs `medium_tier_via_af`)
   - Add a sibling `clamp_tier` field at the InterventionTrace level
   - Split into separate `rule_id`s per tier
     (`hyper_aggressive_medium`, `hyper_aggressive_extreme`)
   Recommend option 1 (reason_code) for v1 — already structured for
   this kind of distinction; can promote to its own field if
   attribution needs the granularity.

6. **Trace volume** (v3, Codex r2): the 60MB per long session
   estimate hasn't been measured against real workloads. Confirm
   acceptable size on the production DB before committing to
   per-decision traces in production (vs experiment-only).
   Performance budget for the hot-path is now defined (< 5% of
   decision latency, see Risks #11) but storage budget still
   needs measurement.

7. **Privacy deletion alignment** (v3, Codex r2): GDPR-style "delete
   my data" — do trace rows delete with hand history, or are they
   retained as "derived analytics"? Either policy is defensible;
   needs decision before shipping to production. Doesn't block
   implementation.
