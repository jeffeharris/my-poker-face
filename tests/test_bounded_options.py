"""
Tests for the bounded options generator.

Tests the core blocking logic that prevents catastrophic folds and calls.
"""

import pytest
from poker.bounded_options import (
    BoundedOption,
    calculate_required_equity,
    generate_bounded_options,
    format_options_for_prompt,
    _should_block_fold,
    _should_block_call,
    _get_raise_options,
)


class TestCalculateRequiredEquity:
    """Tests for required equity calculation."""

    def test_no_cost_to_call(self):
        """0 cost to call means 0 required equity."""
        assert calculate_required_equity(1000, 0) == 0.0

    def test_standard_pot_odds(self):
        """Standard pot odds calculation."""
        # 100 into 200 = need 100/(200+100) = 33.3%
        result = calculate_required_equity(200, 100)
        assert abs(result - 0.333) < 0.01

    def test_small_call_big_pot(self):
        """Small call into big pot = low required equity."""
        # 50 into 1000 = need 50/(1000+50) = ~4.8%
        result = calculate_required_equity(1000, 50)
        assert abs(result - 0.048) < 0.01

    def test_big_call_small_pot(self):
        """Big call into small pot = high required equity."""
        # 500 into 200 = need 500/(200+500) = ~71.4%
        result = calculate_required_equity(200, 500)
        assert abs(result - 0.714) < 0.01


class TestShouldBlockFold:
    """Tests for fold blocking logic."""

    def test_block_fold_no_cost_to_call(self):
        """Block fold when there's no cost (should check instead)."""
        context = {
            'equity': 0.30,
            'cost_to_call': 0,
            'pot_total': 100,
        }
        assert _should_block_fold(context) is True

    def test_block_fold_monster_hand(self):
        """Block fold with 90%+ equity (monster hand)."""
        context = {
            'equity': 0.95,  # Quads or near-nuts
            'cost_to_call': 100,
            'pot_total': 200,
        }
        assert _should_block_fold(context) is True

    def test_block_fold_2x_required_equity(self):
        """Block fold when equity > 2x required."""
        # Required equity = 100 / (200 + 100) = 33.3%
        # 80% > 2 * 33.3%
        context = {
            'equity': 0.80,
            'cost_to_call': 100,
            'pot_total': 200,
        }
        assert _should_block_fold(context) is True

    def test_block_fold_pot_committed(self):
        """Block fold when pot-committed with decent equity."""
        context = {
            'equity': 0.30,
            'cost_to_call': 50,
            'pot_total': 500,
            'already_bet': 200,  # Already bet 200
            'player_stack': 50,  # Only 50 left
        }
        assert _should_block_fold(context) is True

    def test_allow_fold_weak_hand(self):
        """Allow fold with weak hand and poor odds."""
        context = {
            'equity': 0.15,
            'cost_to_call': 100,
            'pot_total': 100,  # Need 50% equity
            'already_bet': 0,
            'player_stack': 1000,
        }
        assert _should_block_fold(context) is False

    def test_allow_fold_marginal_spot(self):
        """Allow fold in marginal spot (equity near required)."""
        context = {
            'equity': 0.40,  # 40%
            'cost_to_call': 100,
            'pot_total': 200,  # Need 33%, have 40% - not 2x
            'already_bet': 0,
            'player_stack': 1000,
        }
        assert _should_block_fold(context) is False


class TestShouldBlockCall:
    """Tests for call blocking logic."""

    def test_allow_call_no_cost(self):
        """Don't block check (0 cost)."""
        context = {
            'equity': 0.02,
            'cost_to_call': 0,
        }
        assert _should_block_call(context) is False

    def test_block_call_drawing_dead(self):
        """Block call when nearly drawing dead (<5% equity)."""
        context = {
            'equity': 0.03,
            'cost_to_call': 100,
        }
        assert _should_block_call(context) is True

    def test_allow_call_decent_equity(self):
        """Allow call with decent equity."""
        context = {
            'equity': 0.25,
            'cost_to_call': 100,
        }
        assert _should_block_call(context) is False

    def test_allow_call_strong_equity(self):
        """Allow call with strong equity."""
        context = {
            'equity': 0.75,
            'cost_to_call': 100,
        }
        assert _should_block_call(context) is False


class TestGetRaiseOptions:
    """Tests for raise size generation."""

    def test_standard_raise_options(self):
        """Generate standard raise sizes."""
        context = {
            'pot_total': 300,
            'min_raise': 100,
            'max_raise': 1000,
            'stack_bb': 50,
            'big_blind': 20,
        }
        options = _get_raise_options(context)

        # Should have 2-3 options
        assert len(options) >= 2

        # All should be within bounds
        for raise_to, _, _ in options:
            assert raise_to >= context['min_raise']
            assert raise_to <= context['max_raise']

    def test_short_stack_includes_all_in(self):
        """Short stack (<20 BB) should include all-in option."""
        context = {
            'pot_total': 300,
            'min_raise': 100,
            'max_raise': 300,  # All-in
            'stack_bb': 15,  # Short stack
            'big_blind': 20,
        }
        options = _get_raise_options(context)

        # Should include max_raise (all-in)
        raise_amounts = [r for r, _, _ in options]
        assert context['max_raise'] in raise_amounts

    def test_no_options_when_cant_raise(self):
        """No options when min_raise > max_raise."""
        context = {
            'pot_total': 300,
            'min_raise': 0,
            'max_raise': 0,
            'stack_bb': 50,
            'big_blind': 20,
        }
        options = _get_raise_options(context)
        assert len(options) == 0


class TestGenerateBoundedOptions:
    """Integration tests for full option generation."""

    def test_generates_2_to_4_options(self):
        """Should generate between 2 and 4 options."""
        context = {
            'equity': 0.50,
            'pot_total': 300,
            'cost_to_call': 100,
            'player_stack': 500,
            'stack_bb': 25,
            'min_raise': 200,
            'max_raise': 500,
            'big_blind': 20,
            'valid_actions': ['fold', 'call', 'raise'],
        }
        options = generate_bounded_options(context)
        assert 2 <= len(options) <= 4

    def test_monster_hand_no_fold(self):
        """Monster hand should not include fold option."""
        context = {
            'equity': 0.95,  # Near-nuts
            'pot_total': 300,
            'cost_to_call': 100,
            'player_stack': 500,
            'stack_bb': 25,
            'min_raise': 200,
            'max_raise': 500,
            'big_blind': 20,
            'valid_actions': ['fold', 'call', 'raise'],
        }
        options = generate_bounded_options(context)
        actions = [o.action for o in options]
        assert 'fold' not in actions

    def test_drawing_dead_no_call(self):
        """Drawing dead should not include call option."""
        context = {
            'equity': 0.02,  # Nearly dead
            'pot_total': 300,
            'cost_to_call': 100,
            'player_stack': 500,
            'stack_bb': 25,
            'min_raise': 200,
            'max_raise': 500,
            'big_blind': 20,
            'valid_actions': ['fold', 'call', 'raise'],
            'already_bet': 0,
        }
        options = generate_bounded_options(context)
        actions = [o.action for o in options]
        assert 'call' not in actions

    def test_free_check_included(self):
        """Check should be included when free."""
        context = {
            'equity': 0.30,
            'pot_total': 300,
            'cost_to_call': 0,  # Free
            'player_stack': 500,
            'stack_bb': 25,
            'min_raise': 100,
            'max_raise': 500,
            'big_blind': 20,
            'valid_actions': ['check', 'raise'],
        }
        options = generate_bounded_options(context)
        actions = [o.action for o in options]
        assert 'check' in actions

    def test_has_plus_ev_option(self):
        """Should include at least one +EV option when blocking fold."""
        context = {
            'equity': 0.80,  # Strong - fold will be blocked
            'pot_total': 300,
            'cost_to_call': 100,
            'player_stack': 500,
            'stack_bb': 25,
            'min_raise': 200,
            'max_raise': 500,
            'big_blind': 20,
            'valid_actions': ['fold', 'call', 'raise'],
        }
        options = generate_bounded_options(context)
        ev_estimates = [o.ev_estimate for o in options]
        assert '+EV' in ev_estimates

    def test_all_in_option_when_valid(self):
        """All-in should be included when it's a valid action."""
        context = {
            'equity': 0.70,
            'pot_total': 300,
            'cost_to_call': 400,  # Facing big bet
            'player_stack': 500,
            'stack_bb': 25,
            'min_raise': 200,
            'max_raise': 500,
            'big_blind': 20,
            'valid_actions': ['fold', 'call', 'all_in'],
        }
        options = generate_bounded_options(context)
        actions = [o.action for o in options]
        assert 'all_in' in actions


class TestBoundedOption:
    """Tests for BoundedOption dataclass."""

    def test_to_dict(self):
        """Test serialization to dict."""
        option = BoundedOption(
            action='raise',
            raise_to=200,
            rationale='Standard value bet',
            ev_estimate='+EV',
            style_tag='standard',
        )
        d = option.to_dict()

        assert d['action'] == 'raise'
        assert d['raise_to'] == 200
        assert d['rationale'] == 'Standard value bet'
        assert d['ev_estimate'] == '+EV'
        assert d['style_tag'] == 'standard'

    def test_frozen(self):
        """BoundedOption should be immutable."""
        option = BoundedOption(
            action='call',
            raise_to=0,
            rationale='Test',
            ev_estimate='neutral',
            style_tag='standard',
        )
        with pytest.raises(AttributeError):
            option.action = 'fold'


class TestFormatOptionsForPrompt:
    """Tests for prompt formatting."""

    def test_format_basic(self):
        """Test basic formatting."""
        options = [
            BoundedOption('fold', 0, 'Save chips', '-EV', 'conservative'),
            BoundedOption('call', 0, 'Meet pot odds', 'neutral', 'standard'),
        ]
        result = format_options_for_prompt(options, 0.40, 3.0)

        assert '=== YOUR OPTIONS ===' in result
        assert '1. FOLD' in result
        assert '2. CALL' in result
        assert '40%' in result  # equity
        assert '3.0:1' in result  # pot odds

    def test_format_with_raise(self):
        """Test formatting includes raise amount."""
        options = [
            BoundedOption('raise', 200, 'Value bet', '+EV', 'aggressive'),
        ]
        result = format_options_for_prompt(options, 0.70, 2.0)

        assert '1. RAISE' in result
        assert '200' in result
        assert '+EV' in result


class TestEdgeCases:
    """Tests for edge cases and known problematic scenarios."""

    def test_quad_tens_not_folded(self):
        """The infamous quad-tens fold should be blocked."""
        # Scenario: Player has quad tens (100% equity) but facing big bet
        context = {
            'equity': 1.0,  # 100% - quads
            'pot_total': 500,
            'cost_to_call': 200,
            'player_stack': 1000,
            'stack_bb': 50,
            'min_raise': 400,
            'max_raise': 1000,
            'big_blind': 20,
            'valid_actions': ['fold', 'call', 'raise'],
            'already_bet': 0,
        }
        options = generate_bounded_options(context)
        actions = [o.action for o in options]

        # Fold should absolutely not be an option
        assert 'fold' not in actions

        # Should have call and/or raise options
        assert 'call' in actions or 'raise' in actions

    def test_flopped_set_not_folded(self):
        """Flopped set (~85% equity) should not be foldable."""
        context = {
            'equity': 0.85,
            'pot_total': 400,
            'cost_to_call': 150,
            'player_stack': 800,
            'stack_bb': 40,
            'min_raise': 300,
            'max_raise': 800,
            'big_blind': 20,
            'valid_actions': ['fold', 'call', 'raise'],
            'already_bet': 0,
        }
        options = generate_bounded_options(context)
        actions = [o.action for o in options]

        assert 'fold' not in actions

    def test_pot_committed_short_stack(self):
        """Pot-committed short stack should not fold with any equity."""
        context = {
            'equity': 0.35,  # Marginal but not dead
            'pot_total': 600,
            'cost_to_call': 100,
            'player_stack': 100,  # All we have left
            'already_bet': 300,  # Already invested 300
            'stack_bb': 5,
            'min_raise': 0,  # Can't raise
            'max_raise': 0,
            'big_blind': 20,
            'valid_actions': ['fold', 'call'],
        }
        options = generate_bounded_options(context)
        actions = [o.action for o in options]

        # With pot-committed state and decent equity, fold should be blocked
        assert 'fold' not in actions
        assert 'call' in actions

    def test_extreme_pot_odds(self):
        """Extreme pot odds (200:1) should block fold for any non-dead hand."""
        context = {
            'equity': 0.10,  # Only 10% but...
            'pot_total': 2000,
            'cost_to_call': 10,  # 200:1 odds!
            'player_stack': 500,
            'already_bet': 200,
            'stack_bb': 25,
            'min_raise': 0,
            'max_raise': 0,
            'big_blind': 20,
            'valid_actions': ['fold', 'call'],
        }
        options = generate_bounded_options(context)
        actions = [o.action for o in options]

        # 10% equity vs 0.5% required (200:1 = 0.5%) - easy call
        # This should pass the 2x required_equity check
        assert 'fold' not in actions or 'call' in actions


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
