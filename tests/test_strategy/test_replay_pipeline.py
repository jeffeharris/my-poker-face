"""Phase 7.6 Step 6: replay_strategy_pipeline tests.

The replay function reconstructs the strategy pipeline from a
persisted snapshot. Mode 1 (shadow-eval) relies on replay producing
the same distribution as the live pipeline when `disable_rules=
frozenset()`, then a different distribution when a target rule is
disabled.

These tests use synthetic minimal snapshots — Mode 1 integration on
real persisted snapshots is covered in
test_analyze_intervention_traces.py.
"""

from __future__ import annotations

import pytest

from poker.strategy import phase_7_5_config as cfg
from poker.strategy.replay import replay_strategy_pipeline
from poker.strategy.strategy_profile import StrategyProfile


@pytest.fixture(autouse=True)
def reset_config():
    cfg.reset_for_testing()
    yield
    cfg.reset_for_testing()


class TestReplayEmptySnapshot:
    def test_empty_dict_returns_empty_strategy(self):
        result = replay_strategy_pipeline({})
        assert result.action_probabilities == {}

    def test_missing_base_strategy_returns_empty(self):
        result = replay_strategy_pipeline({'phase': 'POSTFLOP'})
        assert result.action_probabilities == {}

    def test_minimal_snapshot_returns_base_strategy(self):
        snapshot = {
            'base_strategy_probs': {'fold': 0.5, 'call': 0.5},
            'legal_actions': ['fold', 'call'],
        }
        result = replay_strategy_pipeline(snapshot)
        # No anchors/stats → pipeline layers all skip; result is base.
        assert result.action_probabilities == {'fold': 0.5, 'call': 0.5}


class TestReplayDisableRulesPropagation:
    """When disable_rules is passed, all relevant layers should skip the
    disabled rule. The simplest test: with no layers actually firing,
    disable should produce the same output as no-disable."""

    def test_no_change_when_target_rule_inert(self):
        """A snapshot with no anchors / stats / hand_strength → no layer
        fires. Disabling any rule has no effect → same result."""
        snapshot = {
            'base_strategy_probs': {'fold': 0.6, 'call': 0.4},
            'legal_actions': ['fold', 'call'],
        }
        live = replay_strategy_pipeline(snapshot)
        shadow = replay_strategy_pipeline(
            snapshot,
            disable_rules=frozenset({('bluff_catch_override', 'default')}),
        )
        assert live.action_probabilities == shadow.action_probabilities

    def test_math_floor_disable_changes_output(self):
        """A snapshot in a math_floor trigger (pot-committed) — disabling
        math_floor should keep the input strategy intact, where the
        live replay would have overridden to 100% call."""
        snapshot = {
            'phase': 'POSTFLOP',
            'base_strategy_probs': {'fold': 0.7, 'call': 0.3},
            'legal_actions': ['fold', 'call'],
            'cost_to_call': 100,
            'pot_total': 5000,
            'player_stack': 400,  # 4 BB - above short_stack
            'player_bet': 800,    # invested more than remaining stack → committed
            'big_blind': 100,
        }
        live = replay_strategy_pipeline(snapshot)
        # math_floor fires → 100% call
        assert live.action_probabilities.get('call') == pytest.approx(1.0)

        shadow = replay_strategy_pipeline(
            snapshot,
            disable_rules=frozenset({('math_floor', 'default')}),
        )
        # math_floor disabled → no override → strategy unchanged
        assert shadow.action_probabilities == {'fold': 0.7, 'call': 0.3}


class TestReplayPersonalityLayer:
    def test_personality_runs_when_inputs_present(self):
        """With anchors + emotional_state + deviation_profile, the
        personality layer runs and modifies the distribution."""
        snapshot = {
            'phase': 'POSTFLOP',
            'base_strategy_probs': {
                'fold': 0.3, 'call': 0.4, 'raise_2.5bb': 0.2, 'jam': 0.1,
            },
            'legal_actions': ['fold', 'call', 'raise', 'all_in'],
            'anchors': {
                'baseline_aggression': 0.9, 'baseline_looseness': 0.7,
                'ego': 0.6, 'poise': 0.5, 'expressiveness': 0.5,
                'risk_identity': 0.6, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.15,
            },
            'emotional_state': {
                'state': 'composed', 'severity': 'none', 'intensity': 0.0,
            },
            'deviation_profile_name': 'lag',
        }
        result = replay_strategy_pipeline(snapshot)
        # Probabilities should differ from base — LAG distortion shifts
        # mass toward aggression.
        assert result.action_probabilities != snapshot['base_strategy_probs']

    def test_personality_disabled_returns_base(self):
        snapshot = {
            'phase': 'POSTFLOP',
            'base_strategy_probs': {
                'fold': 0.3, 'call': 0.4, 'raise_2.5bb': 0.2, 'jam': 0.1,
            },
            'legal_actions': ['fold', 'call', 'raise', 'all_in'],
            'anchors': {
                'baseline_aggression': 0.9, 'baseline_looseness': 0.7,
                'ego': 0.6, 'poise': 0.5, 'expressiveness': 0.5,
                'risk_identity': 0.6, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.15,
            },
            'emotional_state': {
                'state': 'composed', 'severity': 'none', 'intensity': 0.0,
            },
            'deviation_profile_name': 'lag',
        }
        live = replay_strategy_pipeline(snapshot)
        shadow = replay_strategy_pipeline(
            snapshot,
            disable_rules=frozenset({('personality', 'default')}),
        )
        # Personality disabled → result equals base strategy.
        assert shadow.action_probabilities == snapshot['base_strategy_probs']
        # And it differs from the live (which ran personality).
        assert live.action_probabilities != shadow.action_probabilities


class TestReplayMalformedSnapshotIsSafe:
    """Replay must not raise on malformed snapshots — analysis script
    catches dozens per game."""

    def test_garbage_types_dont_raise(self):
        snapshot = {
            'base_strategy_probs': 'not a dict',
            'legal_actions': ['fold'],
        }
        # Should return empty strategy (degenerate base).
        result = replay_strategy_pipeline(snapshot)
        assert result.action_probabilities == {}

    def test_invalid_anchors_dict_skips_personality_silently(self):
        snapshot = {
            'phase': 'POSTFLOP',
            'base_strategy_probs': {'fold': 1.0},
            'legal_actions': ['fold'],
            'anchors': {'weird_key': 0.5},  # missing required fields
            'emotional_state': {'state': 'composed'},
            'deviation_profile_name': 'lag',
        }
        # Should return base unchanged, no exception.
        result = replay_strategy_pipeline(snapshot)
        assert result.action_probabilities == {'fold': 1.0}
