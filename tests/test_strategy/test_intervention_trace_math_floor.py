"""Phase 7.6 Step 4: math_floor layer trace tests.

math_floor uses operation='veto' (Codex r3 disambiguation): when it
fires, non-target actions are REMOVED from consideration entirely
(the resulting distribution is 100% on the target action), not just
clamped. This is the canonical example of the veto operation.

Covers:
  - short-stack push fires veto trace targeting all_in / call
  - pot-committed fires veto trace
  - tiny pot-odds fires veto trace
  - no-op paths (no call facing, call not legal, no rule triggered)
    emit fired=False with distinct reason_codes
  - JSON round-trip
"""

from __future__ import annotations

import json

from poker.strategy.intervention_trace import (
    InterventionOperation,
    trace_to_json_dict,
    validate_trace,
)
from poker.strategy.math_floor import apply_pot_odds_floor
from poker.strategy.strategy_profile import StrategyProfile


class TestMathFloorVetoTrace:
    def test_short_stack_emits_veto_trace_targeting_all_in(self):
        base = StrategyProfile(action_probabilities={
            'fold': 0.8, 'call': 0.15, 'raise': 0.05,
        })
        _result, trace = apply_pot_odds_floor(
            strategy=base, cost_to_call=200, pot_total=600,
            player_stack=200, player_bet=100, big_blind=100,
            legal_actions=['fold', 'call', 'raise', 'all_in'],
        )
        assert trace.layer == 'math_floor'
        # Plan §2 inserted defense_floor at slot 4; math_floor shifted to 6.
        assert trace.layer_order == 6
        assert trace.fired is True
        assert trace.operation == InterventionOperation.VETO.value
        assert trace.effect == 'distribution_replaced'
        assert trace.reason_code == 'short_stack'
        assert trace.inputs['target'] == 'all_in'
        validate_trace(trace)

    def test_short_stack_falls_back_to_call_when_no_all_in(self):
        base = StrategyProfile(action_probabilities={
            'fold': 0.8, 'call': 0.2,
        })
        _result, trace = apply_pot_odds_floor(
            strategy=base, cost_to_call=200, pot_total=600,
            player_stack=200, player_bet=100, big_blind=100,
            legal_actions=['fold', 'call'],
        )
        assert trace.fired is True
        assert trace.reason_code == 'short_stack'
        assert trace.inputs['target'] == 'call'

    def test_pot_committed_emits_veto_trace(self):
        base = StrategyProfile(action_probabilities={
            'fold': 0.7, 'call': 0.3,
        })
        _result, trace = apply_pot_odds_floor(
            strategy=base, cost_to_call=100, pot_total=5000,
            player_stack=400,  # 4 BB - above short_stack threshold
            player_bet=800,    # invested more than remaining stack
            big_blind=100,
            legal_actions=['fold', 'call'],
        )
        assert trace.fired is True
        assert trace.reason_code == 'pot_committed'
        assert trace.inputs['target'] == 'call'

    def test_tiny_pot_odds_emits_veto_trace(self):
        base = StrategyProfile(action_probabilities={
            'fold': 0.85, 'call': 0.15,
        })
        _result, trace = apply_pot_odds_floor(
            strategy=base, cost_to_call=200, pot_total=5000,
            player_stack=8000, player_bet=400, big_blind=100,
            legal_actions=['fold', 'call'],
        )
        assert trace.fired is True
        assert trace.reason_code == 'tiny_pot_odds'

    def test_action_changed_when_primary_was_fold(self):
        """When the floor fires and target is `call`, primary action
        changes from fold to call → action_changed=True."""
        base = StrategyProfile(action_probabilities={
            'fold': 0.8, 'call': 0.2,
        })
        _result, trace = apply_pot_odds_floor(
            strategy=base, cost_to_call=200, pot_total=5000,
            player_stack=8000, player_bet=400, big_blind=100,
            legal_actions=['fold', 'call'],
        )
        assert trace.action_changed is True
        assert trace.primary_action_before == 'fold'
        assert trace.primary_action_after == 'call'
        assert trace.replaced_prior_action is True


class TestMathFloorNoOpPaths:
    def test_no_call_facing_emits_no_op(self):
        base = StrategyProfile(action_probabilities={'check': 0.7, 'bet': 0.3})
        _result, trace = apply_pot_odds_floor(
            strategy=base, cost_to_call=0,  # no call facing
            pot_total=500, player_stack=10000, player_bet=0,
            big_blind=100, legal_actions=['check', 'bet'],
        )
        assert trace.fired is False
        assert trace.reason_code == 'no_call_facing'

    def test_call_not_legal_emits_no_op(self):
        base = StrategyProfile(action_probabilities={'fold': 1.0})
        _result, trace = apply_pot_odds_floor(
            strategy=base, cost_to_call=200, pot_total=600,
            player_stack=10000, player_bet=100, big_blind=100,
            legal_actions=['fold', 'all_in'],  # no call (short-stack call-off spot)
        )
        assert trace.fired is False
        assert trace.reason_code == 'call_not_legal'

    def test_no_rule_triggered_emits_no_op(self):
        """Normal deep-stack call: no math-floor rule applies."""
        base = StrategyProfile(action_probabilities={
            'fold': 0.5, 'call': 0.4, 'raise': 0.1,
        })
        _result, trace = apply_pot_odds_floor(
            strategy=base, cost_to_call=300,  # 3 BB call
            pot_total=900,    # ~25% pot odds
            player_stack=10000,  # 100 BB
            player_bet=100, big_blind=100,
            legal_actions=['fold', 'call', 'raise'],
        )
        assert trace.fired is False
        assert trace.reason_code == 'no_rule_triggered'


class TestMathFloorTraceJsonRoundTrip:
    def test_fire_trace_round_trips(self):
        base = StrategyProfile(action_probabilities={
            'fold': 0.8, 'call': 0.2,
        })
        _result, trace = apply_pot_odds_floor(
            strategy=base, cost_to_call=200, pot_total=600,
            player_stack=200, player_bet=100, big_blind=100,
            legal_actions=['fold', 'call', 'all_in'],
        )
        payload = trace_to_json_dict(trace)
        decoded = json.loads(json.dumps(payload))
        assert decoded['operation'] == 'veto'
        assert decoded['layer'] == 'math_floor'
        assert decoded['reason_code'] == 'short_stack'
