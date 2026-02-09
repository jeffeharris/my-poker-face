"""Tests for the rule-based controller (chaos monkey bots)."""

import pytest
from poker.rule_based_controller import (
    RuleConfig,
    RuleBasedController,
    _evaluate_condition,
    _calculate_raise_size,
    _strategy_always_fold,
    _strategy_always_call,
    _strategy_always_raise,
    _strategy_abc,
    CHAOS_BOTS,
)


class TestRuleConfig:
    """Tests for RuleConfig dataclass."""

    def test_default_config(self):
        config = RuleConfig()
        assert config.strategy == "always_fold"
        assert config.rules == ()
        assert config.name == "RuleBot"

    def test_from_dict(self):
        config = RuleConfig.from_dict({
            'strategy': 'abc',
            'name': 'TestBot',
            'raise_size': 'pot',
        })
        assert config.strategy == 'abc'
        assert config.name == 'TestBot'
        assert config.raise_size == 'pot'

    def test_from_dict_with_rules(self):
        config = RuleConfig.from_dict({
            'strategy': 'custom',
            'rules': [
                {'condition': 'is_premium', 'action': 'raise'},
                {'condition': 'default', 'action': 'fold'},
            ],
        })
        assert config.strategy == 'custom'
        assert len(config.rules) == 2


class TestConditionEvaluation:
    """Tests for the condition evaluation system."""

    def test_default_condition(self):
        assert _evaluate_condition('default', {}) is True

    def test_equity_condition(self):
        context = {'equity': 0.75}
        assert _evaluate_condition('equity >= 0.70', context) is True
        assert _evaluate_condition('equity >= 0.80', context) is False
        assert _evaluate_condition('equity < 0.80', context) is True

    def test_pot_odds_condition(self):
        context = {'pot_odds': 3.0}
        assert _evaluate_condition('pot_odds >= 2', context) is True
        assert _evaluate_condition('pot_odds >= 5', context) is False

    def test_compound_condition(self):
        context = {'equity': 0.60, 'pot_odds': 3.0}
        assert _evaluate_condition('equity >= 0.50 and pot_odds >= 2', context) is True
        assert _evaluate_condition('equity >= 0.70 and pot_odds >= 2', context) is False
        assert _evaluate_condition('equity >= 0.70 or pot_odds >= 2', context) is True

    def test_hand_tier_conditions(self):
        context = {'canonical_hand': 'AA'}
        assert _evaluate_condition('is_premium', context) is True
        assert _evaluate_condition('is_top_10', context) is True

        context = {'canonical_hand': 'T9s'}
        assert _evaluate_condition('is_premium', context) is False
        assert _evaluate_condition('is_suited', context) is True

    def test_position_condition(self):
        context = {'position': 'button'}
        assert _evaluate_condition("position == 'button'", context) is True
        assert _evaluate_condition("position in ['button', 'cutoff']", context) is True

    def test_invalid_condition(self):
        # Should return False and not crash
        assert _evaluate_condition('invalid_syntax!!!', {}) is False


class TestRaiseSizeCalculation:
    """Tests for raise size calculation."""

    def test_min_raise(self):
        context = {'min_raise': 200, 'max_raise': 1000, 'pot_total': 500}
        assert _calculate_raise_size('min', context) == 200

    def test_pot_raise(self):
        context = {'min_raise': 200, 'max_raise': 1000, 'pot_total': 500}
        assert _calculate_raise_size('pot', context) == 500

    def test_half_pot_raise(self):
        context = {'min_raise': 200, 'max_raise': 1000, 'pot_total': 600}
        assert _calculate_raise_size('half_pot', context) == 300

    def test_all_in_raise(self):
        context = {'min_raise': 200, 'max_raise': 5000, 'pot_total': 500}
        assert _calculate_raise_size('all_in', context) == 5000

    def test_multiplier_raise(self):
        context = {'min_raise': 200, 'max_raise': 1000, 'big_blind': 100}
        assert _calculate_raise_size('3x', context) == 300

    def test_clamped_to_max(self):
        context = {'min_raise': 200, 'max_raise': 400, 'pot_total': 500}
        assert _calculate_raise_size('pot', context) == 400  # clamped to max


class TestBuiltInStrategies:
    """Tests for built-in strategy functions."""

    def test_always_fold_strategy(self):
        # With cost to call - should fold
        context = {'cost_to_call': 100, 'valid_actions': ['fold', 'call', 'raise']}
        result = _strategy_always_fold(context)
        assert result['action'] == 'fold'

        # Free check - should check
        context = {'cost_to_call': 0, 'valid_actions': ['check', 'raise']}
        result = _strategy_always_fold(context)
        assert result['action'] == 'check'

    def test_always_call_strategy(self):
        context = {
            'cost_to_call': 100,
            'valid_actions': ['fold', 'call', 'raise'],
        }
        result = _strategy_always_call(context)
        assert result['action'] == 'call'

    def test_always_raise_strategy(self):
        context = {
            'cost_to_call': 100,
            'max_raise': 1000,
            'valid_actions': ['fold', 'call', 'raise'],
        }
        result = _strategy_always_raise(context)
        assert result['action'] == 'raise'
        assert result['raise_to'] == 1000

    def test_abc_strategy_premium_hand(self):
        context = {
            'canonical_hand': 'AA',
            'equity': 0.85,
            'cost_to_call': 100,
            'pot_odds': 3.0,
            'min_raise': 200,
            'valid_actions': ['fold', 'call', 'raise'],
        }
        result = _strategy_abc(context)
        assert result['action'] == 'raise'

    def test_abc_strategy_trash_hand(self):
        context = {
            'canonical_hand': '72o',
            'equity': 0.30,
            'cost_to_call': 100,
            'pot_odds': 2.0,
            'valid_actions': ['fold', 'call', 'raise'],
        }
        result = _strategy_abc(context)
        assert result['action'] == 'fold'


class TestChaosBots:
    """Tests for pre-configured chaos bots."""

    def test_chaos_bots_exist(self):
        expected = [
            'always_fold', 'always_call', 'always_raise',
            'always_all_in', 'abc', 'position_aware', 'pot_odds_robot'
        ]
        for name in expected:
            assert name in CHAOS_BOTS
            assert isinstance(CHAOS_BOTS[name], RuleConfig)

    def test_chaos_bot_names(self):
        assert CHAOS_BOTS['always_fold'].name == 'FoldBot'
        assert CHAOS_BOTS['always_call'].name == 'CallStation'
        assert CHAOS_BOTS['always_raise'].name == 'AggBot'
        assert CHAOS_BOTS['always_all_in'].name == 'YOLOBot'


class TestGameHandlerCompatibility:
    """Tests for compatibility with game handler patterns."""

    def test_ai_player_stub(self):
        """RuleBasedController.ai_player should return a stub with required attributes."""
        config = RuleConfig(strategy='abc', name='TestBot')
        controller = RuleBasedController('TestPlayer', config=config)

        ai_player = controller.ai_player

        # These attributes are accessed by game_handler.py
        assert hasattr(ai_player, 'personality_config')
        assert ai_player.personality_config.get('nickname') == 'TestBot'
        assert hasattr(ai_player, 'confidence')
        assert hasattr(ai_player, 'attitude')
        assert hasattr(ai_player, 'assistant')
        assert ai_player.assistant is None
        assert hasattr(ai_player, 'is_rule_based')
        assert ai_player.is_rule_based is True

    def test_ai_player_chattiness(self):
        """RuleBot ai_player stub should have chattiness=0 to skip commentary."""
        config = RuleConfig(strategy='abc', name='TestBot')
        controller = RuleBasedController('TestPlayer', config=config)

        ai_player = controller.ai_player
        traits = ai_player.personality_config.get('personality_traits', {})

        assert traits.get('table_talk', 0) == 0.0
        assert traits.get('chattiness', 0) == 0.0

    def test_psychology_property(self):
        """RuleBasedController.psychology should return None (no psychology system)."""
        controller = RuleBasedController('TestPlayer')
        assert controller.psychology is None

    def test_emotional_state_property(self):
        """RuleBasedController.emotional_state should return None."""
        controller = RuleBasedController('TestPlayer')
        assert controller.emotional_state is None

    def test_assistant_property(self):
        """RuleBasedController.assistant should return None."""
        controller = RuleBasedController('TestPlayer')
        assert controller.assistant is None

    def test_prompt_config_property(self):
        """RuleBasedController.prompt_config should return None."""
        controller = RuleBasedController('TestPlayer')
        assert controller.prompt_config is None

    def test_session_memory_stub(self):
        """RuleBasedController should accept session_memory assignment without error."""
        controller = RuleBasedController('TestPlayer')

        # These are set by game_routes.py during game setup
        controller.session_memory = "some_memory_object"
        assert controller.session_memory is None  # Stub ignores the value

    def test_clear_decision_plans(self):
        """RuleBasedController.clear_decision_plans should return empty list."""
        controller = RuleBasedController('TestPlayer')
        result = controller.clear_decision_plans()
        assert result == []

    def test_clear_hand_bluff_likelihood(self):
        """RuleBasedController.clear_hand_bluff_likelihood should not raise."""
        controller = RuleBasedController('TestPlayer')
        controller.clear_hand_bluff_likelihood()  # Should not raise

    def test_current_hand_number_attribute(self):
        """RuleBasedController should have current_hand_number attribute."""
        controller = RuleBasedController('TestPlayer')
        assert hasattr(controller, 'current_hand_number')
        controller.current_hand_number = 5
        assert controller.current_hand_number == 5

    def test_get_last_decision_context(self):
        """RuleBasedController should track last decision context."""
        controller = RuleBasedController('TestPlayer')

        # Initially None
        assert controller.get_last_decision_context() is None

        # After a decision, context should be available
        controller._last_decision_context = {'strategy': 'abc', 'equity': 0.5}
        assert controller.get_last_decision_context() == {'strategy': 'abc', 'equity': 0.5}
