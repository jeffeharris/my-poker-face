"""Regression test for the rule_context → _analyze_decision contract.

`HybridAIController._build_rule_context` produces the dict that
`_analyze_decision` consumes via `context.get('call_amount', 0)`.
A prior version stored the value only under `cost_to_call`, so the
analyzer silently saw 0 and flagged every preflop fold facing a BB
as a mistake. This test pins the dict's contract so the bug can't
silently regress.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from poker.hybrid_ai_controller import HybridAIController
from poker.poker_game import initialize_game_state, setup_hand


def _bind(stub, method_name: str):
    """Bind a real HybridAIController method onto a MagicMock-spec stub."""
    method = getattr(HybridAIController, method_name)
    return method.__get__(stub)


def _build_hybrid_stub():
    stub = MagicMock(spec=HybridAIController)
    stub.player_name = 'Hero'
    stub.opponent_model_manager = None
    state_machine = MagicMock()
    state_machine.current_phase = MagicMock(name='PRE_FLOP')
    state_machine.current_phase.name = 'PRE_FLOP'
    stub.state_machine = state_machine
    stub._build_rule_context = _bind(stub, '_build_rule_context')
    return stub


@pytest.mark.parametrize('expected_cost', [0, 2, 50, 500])
def test_call_amount_mirrors_cost_to_call(expected_cost):
    """The dict MUST surface `call_amount` for `_analyze_decision`.

    _analyze_decision reads `context.get('call_amount', 0)` and stores
    that into player_decision_analysis.cost_to_call. If the hybrid
    rule_context only emits `cost_to_call`, the analyzer silently
    zeroes out — every fold facing a BB becomes a "should have checked"
    mistake.
    """
    state = setup_hand(initialize_game_state(['Villain1', 'Villain2']))
    stub = _build_hybrid_stub()

    with patch(
        'poker.hybrid_ai_controller.calculate_equity_vs_ranges',
        return_value=0.5,
    ):
        rule_context = stub._build_rule_context(
            game_state=state,
            player=state.players[0],
            context={'call_amount': expected_cost, 'valid_actions': ['fold', 'call']},
        )

    # Both keys present and equal — defends against either being
    # removed in isolation.
    assert rule_context['call_amount'] == expected_cost
    assert rule_context['cost_to_call'] == expected_cost
    assert rule_context['call_amount'] == rule_context['cost_to_call']
