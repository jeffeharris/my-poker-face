#!/usr/bin/env python3
"""Seeded decision-equivalence harness for TieredBotController refactors.

PURPOSE
-------
The refactor of `_get_postflop_decision` (and possibly `_get_ai_decision`) must
be DECISION-IDENTICAL: the bot must make the exact same (action, amount)
decisions before and after, plus the same internal pipeline trace. This harness
proves that by running a deterministic, fixed-seed set of TieredBot matchups and
recording the full ordered decision trace.

HOW IT STAYS DETERMINISTIC
--------------------------
It reuses the *existing* machinery in `experiments/simulate_bb100.py`
(`run_matchup` / `run_6max_matchup`), which already:
  - re-seeds the global `random` module per hand (`random.seed(hand_seed)`),
  - seeds each controller's `self.rng = random.Random(rng_seed)` deterministically,
  - seeds the deck (`create_deck(..., random_seed=hand_seed)`),
  - seeds the per-action equity Monte Carlo.
The LLM/expression layer never runs here: `make_controller` builds the
controller without an expression_generator and `_attach_expression` short-circuits
when none is configured (no network, no nondeterminism). We additionally null out
`_run_expression_layer` defensively.

We do NOT modify simulate_bb100.py. We monkeypatch
`TieredBotController.decide_action` with a recording wrapper that, after each
real decision, snapshots the resulting (action, raise_to) and the key internal
pipeline-snapshot / intervention-trace fields. Every tiered controller in every
matchup (hero AND tiered opponent) is recorded, so both preflop and postflop
pipelines are exercised across HU + multiway, many board textures, and stack
depths.

USAGE
-----
    # Capture golden trace (run on the ORIGINAL, unrefactored code):
    docker compose exec -T backend python -m tests.tiered_bot_equivalence_harness capture /tmp/golden.json

    # After refactor, compare:
    docker compose exec -T backend python -m tests.tiered_bot_equivalence_harness compare /tmp/golden.json

    # Reproducibility self-check (capture twice, assert identical):
    docker compose exec -T backend python -m tests.tiered_bot_equivalence_harness selfcheck
"""

import json
import logging
import os
import sys

# Silence the controller's chatty logging during the sim.
logging.disable(logging.CRITICAL)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiments.simulate_bb100 import (  # noqa: E402
    run_6max_matchup,
    run_matchup,
)
from poker.strategy.strategy_table import load_strategy_table  # noqa: E402
from poker.tiered_bot_controller import TieredBotController  # noqa: E402

# ── Matchup plan ─────────────────────────────────────────────────────────────
# Chosen to exercise the postflop pipeline broadly:
#   - tiered-vs-tiered HU (both seats recorded → 2x tiered decisions/hand)
#   - varied archetypes (Rock/TAG/LAG/Maniac/Calling Station/Nit) → different
#     aggression/looseness anchors → different personality distortion, spot
#     tendencies, exploitation reads, value/bluff-catch overrides.
#   - 6-max tiered hero vs rule-bot field → multiway adjustment + multiway
#     suppression of override layers, varied depths as stacks swing.
#   - two seeds for each to widen board-texture / stack-depth coverage.
HU_MATCHUPS = [
    ('TAG', 'Calling Station'),
    ('LAG', 'Rock'),
    ('Maniac', 'Nit'),
    ('TAG', 'LAG'),
]
SIX_MAX = [
    ('TAG', ['Calling Station', 'Rock', 'LAG', 'Maniac', 'Nit']),
    ('LAG', ['CaseBot', 'GTO-Lite', 'ABCBot', 'Fish', 'CallStation']),
]
SEEDS = [42, 1337]
HU_HANDS = 120
SIX_MAX_HANDS = 80


def _trace_fingerprint(controller):
    """Stable, compact fingerprint of the last decision's internal pipeline.

    Records the ordered intervention-trace (layer, rule_id, fired, replaced
    flags + reason_code) plus the key pipeline-snapshot fields. This catches
    any change in layer order, firing, or side-effect accumulation — not just
    the final (action, amount).
    """
    snap = getattr(controller, '_last_pipeline_snapshot', None) or {}
    trace = getattr(controller, '_last_intervention_trace', None) or []

    def _jsonable(v):
        # Normalize unordered/sequence containers to a stable JSON-safe form.
        if isinstance(v, (set, frozenset)):  # noqa: UP038
            return sorted(str(x) for x in v)
        if isinstance(v, tuple):
            return [_jsonable(x) for x in v]
        if isinstance(v, dict):
            return {k: _jsonable(x) for k, x in v.items()}
        return v

    trace_fields = []
    for t in trace:
        trace_fields.append(
            {
                'layer': getattr(t, 'layer', None),
                'rule_id': getattr(t, 'rule_id', None),
                'layer_order': getattr(t, 'layer_order', None),
                'fired': getattr(t, 'fired', None),
                'reason_code': getattr(t, 'reason_code', None),
                'replaced_prior_action': getattr(t, 'replaced_prior_action', None),
                'prior_action_source': getattr(t, 'prior_action_source', None),
            }
        )

    return _jsonable(
        {
            'phase': snap.get('phase'),
            'node_key': snap.get('node_key'),
            'base_strategy_probs': snap.get('base_strategy_probs'),
            'hand_strength': snap.get('hand_strength'),
            'nut_status': snap.get('nut_status'),
            'danger_flags': snap.get('danger_flags'),
            'bet_bucket': snap.get('bet_bucket'),
            'required_equity': snap.get('required_equity'),
            'effective_stack_bb': snap.get('effective_stack_bb'),
            'sampled_abstract_action': snap.get('sampled_abstract_action'),
            'resolved_action': snap.get('resolved_action'),
            'resolved_raise_to': snap.get('resolved_raise_to'),
            'push_fold_routed': snap.get('push_fold_routed'),
            'legal_actions': snap.get('legal_actions'),
            'trace': trace_fields,
        }
    )


def run_capture():
    """Run the full deterministic plan, returning the ordered decision trace."""
    records = []

    orig_decide = TieredBotController.decide_action
    # Defensively null the expression layer (it does not affect action/amount,
    # but we never want it touching the network in this harness).
    orig_expr = TieredBotController._run_expression_layer

    def recording_decide(self, game_messages=None):
        decision = orig_decide(self, game_messages=game_messages)
        records.append(
            {
                'player': self.player_name,
                'action': decision.get('action'),
                'raise_to': decision.get('raise_to'),
                'fp': _trace_fingerprint(self),
            }
        )
        return decision

    def noop_expr(self, *a, **k):
        return None

    TieredBotController.decide_action = recording_decide
    TieredBotController._run_expression_layer = noop_expr
    try:
        strategy_table = load_strategy_table()
        for seed in SEEDS:
            for a, b in HU_MATCHUPS:
                run_matchup(a, b, HU_HANDS, strategy_table, base_seed=seed)
            for hero, opponents in SIX_MAX:
                run_6max_matchup(
                    hero, SIX_MAX_HANDS, strategy_table, base_seed=seed, opponents=opponents
                )
    finally:
        TieredBotController.decide_action = orig_decide
        TieredBotController._run_expression_layer = orig_expr

    return records


def _cmd_capture(path):
    records = run_capture()
    with open(path, 'w') as f:
        json.dump(records, f, indent=0, sort_keys=True)
    postflop = sum(1 for r in records if r['fp'].get('phase') == 'POSTFLOP')
    preflop = sum(1 for r in records if r['fp'].get('phase') == 'PRE_FLOP')
    print(
        f"Captured {len(records)} tiered decisions "
        f"({preflop} preflop, {postflop} postflop) -> {path}"
    )


def _cmd_compare(path):
    with open(path) as f:
        golden = json.load(f)
    current = run_capture()
    # Round-trip current through JSON to normalize types (e.g. tuples->lists)
    current = json.loads(json.dumps(current, sort_keys=True))
    if current == golden:
        print(f"IDENTICAL: {len(current)} decisions match the golden trace exactly.")
        return 0
    # Find first divergence for diagnosis.
    n = min(len(current), len(golden))
    first = None
    for i in range(n):
        if current[i] != golden[i]:
            first = i
            break
    print(f"DIVERGENCE: current={len(current)} golden={len(golden)} decisions.")
    if first is not None:
        print(f"First differing decision at index {first}:")
        print("  golden :", json.dumps(golden[first], sort_keys=True))
        print("  current:", json.dumps(current[first], sort_keys=True))
    elif len(current) != len(golden):
        print("Length mismatch only (one is a prefix of the other).")
    return 1


def _cmd_selfcheck():
    a = json.loads(json.dumps(run_capture(), sort_keys=True))
    b = json.loads(json.dumps(run_capture(), sort_keys=True))
    if a == b:
        print(f"REPRODUCIBLE: two runs produced identical {len(a)}-decision traces.")
        return 0
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            print(f"NON-REPRODUCIBLE: first diff at index {i}")
            print("  run1:", json.dumps(a[i], sort_keys=True))
            print("  run2:", json.dumps(b[i], sort_keys=True))
            return 1
    print(f"NON-REPRODUCIBLE: length mismatch {len(a)} vs {len(b)}")
    return 1


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    cmd = sys.argv[1]
    if cmd == 'capture':
        _cmd_capture(sys.argv[2])
        sys.exit(0)
    elif cmd == 'compare':
        sys.exit(_cmd_compare(sys.argv[2]))
    elif cmd == 'selfcheck':
        sys.exit(_cmd_selfcheck())
    else:
        print(f"unknown command: {cmd}")
        sys.exit(2)
