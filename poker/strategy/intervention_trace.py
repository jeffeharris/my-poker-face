"""
InterventionTrace: per-decision structured record of one pipeline rule's
contribution to a tiered-bot decision.

Phase 7.6 of the tiered-bot architecture. See
docs/plans/PHASE_7_6_INTERVENTION_TRACE.md for the full design including
the four-mode attribution methodology, NarrationFacts adapter layer, and
overwrite-chain semantics.

Each pipeline layer emits one or more `InterventionTrace` entries per
decision (even on no-op paths — `fired=False` distinguishes "evaluated
but didn't trigger" from "wasn't on the path"). The full trace per
decision is `List[InterventionTrace]` in pipeline order, aggregated on
the controller.

A single LAYER may emit multiple trace entries when it has internal sub-
rules (e.g. exploitation emits one per rule: hyper_aggressive,
hyper_passive, tight_nit, c-bet, multiway-c-bet). Distinguish them via
`rule_id`; analysis groups by `(layer, rule_id)`.
"""

from __future__ import annotations

import dataclasses
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple


TRACE_SCHEMA_VERSION = 1


class InterventionOperation(str, Enum):
    """How this layer's trace relates to the prior strategy.

    String-valued so call sites can compare against literals without
    importing the enum, and so serialized traces round-trip through JSON
    cleanly.

    Semantic distinctions:
      - `no_op`: gates failed; strategy unchanged.
      - `suggest`: produced advice but didn't modify the distribution.
      - `adjust`: additive offsets / nudges; prior intent preserved.
      - `clamp`: bounded the prior distribution (action retained in the
        considered set, mass possibly reduced). A clamp that
        incidentally drives an action to zero probability is still
        `clamp`.
      - `override`: replaced the strategy distribution entirely.
      - `veto`: explicit hard prohibition; action removed from
        consideration (distinct from `clamp` even when clamp → 0%).

    Invariant (enforced in tests): `operation == OVERRIDE` implies
    `replaced_prior_action == True`.
    """
    NO_OP = 'no_op'
    SUGGEST = 'suggest'
    ADJUST = 'adjust'
    CLAMP = 'clamp'
    OVERRIDE = 'override'
    VETO = 'veto'


_LAYER_NAMES = frozenset({
    'personality',
    'exploitation',
    'induce_override',        # Phase A / Phase B (Items 2-5)
    'strong_hand_override',
    'bluff_catch_override',
    'multistreet_context',   # STRUCTURAL_PASSIVITY_PLAN.md
    'value_bet_floor',       # STRUCTURAL_PASSIVITY_PLAN.md §12
    'defense_floor',         # Plan §2
    'short_stack',
    'math_floor',
    'value_vs_station',      # Phase 8
    'steal_pressure',        # Phase 8
    'bluff_reduction',       # Plan §5
})

_RULE_IDS_BY_LAYER: Dict[str, frozenset] = {
    'personality':            frozenset({'default'}),
    'exploitation':           frozenset({
        'hyper_aggressive', 'hyper_passive', 'tight_nit',
        'high_fold_to_cbet', 'multiway_cbet',
    }),
    'induce_override':        frozenset({'default'}),
    'strong_hand_override':   frozenset({'default'}),
    'bluff_catch_override':   frozenset({'default'}),
    'multistreet_context':    frozenset({'default', 'barrel', 'fold_barrel'}),
    'value_bet_floor':        frozenset({'default'}),
    'defense_floor':          frozenset({'default'}),
    'short_stack':            frozenset({'default'}),
    'math_floor':             frozenset({'default'}),
    'value_vs_station':       frozenset({'default'}),
    'steal_pressure':         frozenset({'default'}),
    'bluff_reduction':        frozenset({'default'}),
}


# Pipeline order of the postflop tiered-bot decision. Used as the
# canonical `layer_order` on every trace so attribution analysis can
# sort/group consistently. Phase 8's value_vs_station and steal_pressure
# nest inside the exploitation step (they compute intensities that feed
# `compute_exploitation_offsets`) — they share layer_order=1 with a
# stable rule_id distinction.
#
# Promoting this to a single source of truth (was previously hard-coded
# at each layer migration site) prevents drift as more layers migrate.
_LAYER_ORDER: Dict[str, int] = {
    'personality':           0,
    'exploitation':          1,
    'value_vs_station':      1,  # Phase 8: feeds exploitation
    'steal_pressure':        1,  # Phase 8: feeds exploitation
    'bluff_reduction':       1,  # Plan §5: air-vs-station mirror of value_vs_station
    'induce_override':       2,  # Phase A: smooth-call vs barrelers (preempts strong_hand_override)
    'strong_hand_override':  2,
    'bluff_catch_override':  3,
    'multistreet_context':   4,  # STRUCTURAL_PASSIVITY_PLAN.md: hero's-own-line barrel / fold-to-barrel (runs just before defense_floor)
    'value_bet_floor':       4,  # STRUCTURAL_PASSIVITY_PLAN.md §12: unopened value-bet floor
    'defense_floor':         4,  # Plan §2: price-sensitive call floor
    'short_stack':           5,
    'math_floor':            6,
}
MAX_LAYER_ORDER = max(_LAYER_ORDER.values())


def layer_order_for(layer: str) -> int:
    """Canonical pipeline ordinal for `layer`. Raises on unknown layer."""
    if layer not in _LAYER_ORDER:
        raise ValueError(
            f"Unknown layer {layer!r}; expected one of {sorted(_LAYER_ORDER)}"
        )
    return _LAYER_ORDER[layer]


@dataclass(frozen=True)
class InterventionTrace:
    """Structured record of one pipeline rule's contribution to a decision.

    See module docstring for the per-decision aggregation contract. The
    dataclass is frozen so a layer cannot accidentally mutate a trace it
    received from an upstream call.

    Serializable: every field is JSON-safe via `trace_to_json_dict`.
    `effect_size` of 0.0 with `fired=True` is legal but unusual — a
    layer may emit a fire trace where the clamp envelope absorbed the
    full proposed shift.
    """

    layer: str
    rule_id: str = 'default'
    layer_order: int = 0
    decision_id: Optional[str] = None
    schema_version: int = TRACE_SCHEMA_VERSION

    fired: bool = False
    operation: str = InterventionOperation.NO_OP.value
    effect: str = 'no_op'
    effect_size: float = 0.0

    action_changed: bool = False
    primary_action_before: str = ''
    primary_action_after: str = ''
    amount_bucket_before: str = ''
    amount_bucket_after: str = ''

    replaced_prior_action: bool = False
    prior_action_source: str = ''
    preserved_prior_intent: bool = True

    reason_code: str = ''
    rationale: str = ''
    confidence: float = 0.0

    inputs: Dict[str, Any] = field(default_factory=dict)
    input_strategy_summary: Dict[str, float] = field(default_factory=dict)
    output_strategy_summary: Dict[str, float] = field(default_factory=dict)
    config_snapshot: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


# ── Helpers ──────────────────────────────────────────────────────────────


def l1_distance(
    a: Mapping[str, float],
    b: Mapping[str, float],
) -> float:
    """L1 distance between two action-probability distributions.

    Distributions may have disjoint action sets (a layer can introduce
    or remove actions). Missing keys are treated as 0.0. Result is in
    [0, 2]: 0 = identical, 2 = full mass swap onto disjoint actions.
    """
    keys = set(a.keys()) | set(b.keys())
    return sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in keys)


def primary_action(probs: Mapping[str, float]) -> str:
    """argmax of an action-probability distribution.

    Returns '' for empty / all-zero distributions so callers can
    distinguish "no decision yet" from a real action name.
    """
    if not probs:
        return ''
    best_action = ''
    best_prob = -1.0
    for action, prob in probs.items():
        if prob > best_prob:
            best_prob = prob
            best_action = action
    if best_prob <= 0.0:
        return ''
    return best_action


def amount_bucket(action: str) -> str:
    """Coarse sizing bucket for raise/bet actions.

    Maps action labels to {'small', 'medium', 'large', 'jam', ''}.
    Returns '' for non-sizing actions (fold/call/check).

    Action label conventions in this codebase:
      - `raise_2.5` / `raise_3` / `raise_4` — preflop BB multiples (value < 10)
      - `bet_33` / `bet_67` / `bet_100` — postflop pot-percent (value ≥ 10)
      - `raise_67` / `raise_150` — postflop pot-percent raises (value ≥ 10)
      - `all_in` / `jam` — full commit

    The suffix-vs-10 split is a reliable disambiguator because postflop
    sizings are never below 25% pot in the strategy table and preflop
    BB multiples are never above 12. Readers use the bucket to detect
    "sizing meaningfully changed", so the exact cutoffs aren't critical.
    """
    if action in ('all_in', 'jam'):
        return 'jam'
    if not (action.startswith('raise_') or action.startswith('bet_')):
        return ''
    suffix = action.split('_', 1)[1]
    try:
        value = float(suffix)
    except ValueError:
        return ''

    if value < 10.0:
        # Preflop BB multiple.
        if value <= 2.5:
            return 'small'
        if value <= 4.0:
            return 'medium'
        if value <= 8.0:
            return 'large'
        return 'jam'

    # Postflop pot percent.
    if value <= 50.0:
        return 'small'
    if value <= 100.0:
        return 'medium'
    if value <= 200.0:
        return 'large'
    return 'jam'


def summarize_strategy(
    probs: Mapping[str, float],
    top_n: int = 3,
) -> Dict[str, float]:
    """Top-N action probabilities, rounded to 4 decimals.

    Used for `input_strategy_summary` / `output_strategy_summary` to
    keep trace payloads light without losing the dominant actions.
    """
    if not probs:
        return {}
    items = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
    return {action: round(prob, 4) for action, prob in items[:top_n]}


def _safe_serialize(value: Any) -> Any:
    """Best-effort JSON-safe conversion.

    Handles enums, dataclasses, numpy scalars (via `.item()`), and
    nested mapping/iterable structures. Non-finite floats become
    None (JSON doesn't support NaN/Inf). Unknown types fall back to
    str(value) so a trace never raises on serialization — the trace
    is observability, not authoritative state.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return _safe_serialize(dataclasses.asdict(value))
    if hasattr(value, 'item') and callable(value.item):
        try:
            return _safe_serialize(value.item())
        except Exception:
            return str(value)
    if isinstance(value, Mapping):
        return {str(k): _safe_serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe_serialize(v) for v in value]
    return str(value)


def trace_to_json_dict(trace: InterventionTrace) -> Dict[str, Any]:
    """Convert a trace to a JSON-safe dict (no enum/dataclass internals)."""
    return _safe_serialize(dataclasses.asdict(trace))


def trace_to_json(trace: InterventionTrace) -> str:
    """Serialize a trace to a JSON string."""
    return json.dumps(trace_to_json_dict(trace))


def validate_trace(trace: InterventionTrace) -> None:
    """Raise ValueError if `trace` violates schema invariants.

    Cheap to call — used in tests and (optionally) hot-path debug
    builds. Production callers can skip this.
    """
    if trace.layer not in _LAYER_NAMES:
        raise ValueError(
            f"Trace.layer={trace.layer!r} not in canonical _LAYER_NAMES"
        )
    valid_rules = _RULE_IDS_BY_LAYER.get(trace.layer, frozenset())
    if trace.rule_id not in valid_rules:
        raise ValueError(
            f"Trace.rule_id={trace.rule_id!r} not valid for "
            f"layer={trace.layer!r}; expected one of {sorted(valid_rules)}"
        )
    if trace.operation == InterventionOperation.OVERRIDE.value:
        if not trace.replaced_prior_action:
            raise ValueError(
                "Invariant violated: operation=='override' requires "
                "replaced_prior_action=True"
            )
    if trace.fired and trace.operation == InterventionOperation.NO_OP.value:
        raise ValueError(
            "Inconsistent trace: fired=True with operation='no_op'"
        )
    if not trace.fired and trace.operation != InterventionOperation.NO_OP.value:
        raise ValueError(
            f"Inconsistent trace: fired=False with "
            f"operation={trace.operation!r} (expected 'no_op')"
        )


def make_no_op_trace(
    layer: str,
    rule_id: str = 'default',
    layer_order: int = 0,
    reason_code: str = '',
) -> InterventionTrace:
    """Convenience constructor for the common 'rule didn't fire' case.

    Returns a trace with `fired=False`, `operation='no_op'`, empty
    strategy summaries, and the supplied reason_code (e.g.
    `'hand_class_not_eligible'`, `'manager_unavailable'`).
    """
    return InterventionTrace(
        layer=layer,
        rule_id=rule_id,
        layer_order=layer_order,
        fired=False,
        operation=InterventionOperation.NO_OP.value,
        effect='no_op',
        reason_code=reason_code,
    )


# Sentinel reason_code emitted when a rule is suppressed via the
# ablation API (see disable_rules below). Distinct from natural-gate
# no-ops so attribution analysis can isolate ablation effects.
DISABLED_BY_ABLATION = 'disabled_by_ablation'


def make_disabled_trace(
    layer: str,
    rule_id: str = 'default',
    layer_order: int = 0,
) -> InterventionTrace:
    """Trace emitted when a rule was suppressed by ablation.

    Phase 7.6 Step 5: when the controller is configured with
    `disable_rules={(layer, rule_id), ...}`, each suppressed rule
    emits this trace shape instead of running its branch. Mode 1
    (shadow-eval) and Mode 4 (ablation matrix) in
    `experiments/analyze_intervention_traces.py` rely on the stable
    `DISABLED_BY_ABLATION` reason_code to distinguish suppressed
    rules from natural no-ops.
    """
    return make_no_op_trace(
        layer=layer,
        rule_id=rule_id,
        layer_order=layer_order,
        reason_code=DISABLED_BY_ABLATION,
    )


def is_rule_disabled(
    disable_rules,
    layer: str,
    rule_id: str = 'default',
) -> bool:
    """Check whether `(layer, rule_id)` is in `disable_rules`.

    Accepts a `FrozenSet[Tuple[str, str]]` (canonical), but also
    tolerates None / empty sequences for ergonomic call sites — the
    common case is "no rules disabled."
    """
    if not disable_rules:
        return False
    return (layer, rule_id) in disable_rules
