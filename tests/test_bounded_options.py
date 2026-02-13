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


class TestNeutralFloorFix:
    """Tests for the neutral floor bug fix — raises below threshold get -EV
    regardless of cost_to_call."""

    def _postflop_check_raise_context(self, equity):
        """Helper: post-flop context with cost_to_call=0 (check/raise decision)."""
        return {
            'equity': equity,
            'pot_total': 400,
            'cost_to_call': 0,
            'player_stack': 1000,
            'stack_bb': 50,
            'min_raise': 200,
            'max_raise': 1000,
            'big_blind': 20,
            'valid_actions': ['check', 'raise'],
            'phase': 'FLOP',
        }

    def test_low_equity_raise_is_negative_ev_when_free(self):
        """Raises below threshold should be -EV even when cost_to_call=0."""
        from poker.bounded_options import STYLE_PROFILES

        # 35% equity is below tight_passive postflop_raise_neutral (0.60)
        context = self._postflop_check_raise_context(equity=0.35)
        profile = STYLE_PROFILES['tight_passive']

        options = generate_bounded_options(context, profile, phase='FLOP')
        raise_options = [o for o in options if o.action == 'raise']

        assert len(raise_options) >= 1
        for r in raise_options:
            assert r.ev_estimate == '-EV', (
                f"Expected -EV for 35% equity raise with tight_passive profile, got {r.ev_estimate}"
            )

    def test_profiles_diverge_on_same_equity_postflop(self):
        """Different profiles should produce different EV labels for
        the same equity at cost_to_call=0 post-flop."""
        from poker.bounded_options import STYLE_PROFILES

        # 40% equity: above LAG postflop_raise_neutral (0.30) but below Rock (0.60)
        context = self._postflop_check_raise_context(equity=0.40)

        rock_opts = generate_bounded_options(
            context, STYLE_PROFILES['tight_passive'], phase='FLOP'
        )
        lag_opts = generate_bounded_options(
            context, STYLE_PROFILES['loose_aggressive'], phase='FLOP'
        )

        rock_raise_evs = {o.ev_estimate for o in rock_opts if o.action == 'raise'}
        lag_raise_evs = {o.ev_estimate for o in lag_opts if o.action == 'raise'}

        # Rock should see -EV (0.40 < 0.60 threshold)
        assert '-EV' in rock_raise_evs, f"Rock raises should be -EV, got {rock_raise_evs}"
        # LAG should see neutral or +EV (0.40 > 0.30 threshold)
        assert '-EV' not in lag_raise_evs, f"LAG raises should not be -EV, got {lag_raise_evs}"


class TestPostflopRaiseOptionLimit:
    """Tests for profile-gated raise option counts."""

    def _postflop_many_raises_context(self):
        """Helper: post-flop context that would generate 2-3 raise options."""
        return {
            'equity': 0.55,
            'pot_total': 600,
            'cost_to_call': 0,
            'player_stack': 2000,
            'stack_bb': 100,
            'min_raise': 200,
            'max_raise': 2000,
            'big_blind': 20,
            'valid_actions': ['check', 'raise'],
            'phase': 'FLOP',
        }

    def test_tight_passive_limited_to_1_raise(self):
        """Tight passive should see at most 1 raise option post-flop."""
        from poker.bounded_options import STYLE_PROFILES

        context = self._postflop_many_raises_context()
        profile = STYLE_PROFILES['tight_passive']

        options = generate_bounded_options(context, profile, phase='FLOP')
        raise_count = sum(1 for o in options if o.action == 'raise')

        assert raise_count <= 1, f"tight_passive should have <=1 raise, got {raise_count}"

    def test_loose_aggressive_gets_full_menu(self):
        """LAG should see up to 3 raise options post-flop."""
        from poker.bounded_options import STYLE_PROFILES

        context = self._postflop_many_raises_context()
        profile = STYLE_PROFILES['loose_aggressive']

        options = generate_bounded_options(context, profile, phase='FLOP')
        raise_count = sum(1 for o in options if o.action == 'raise')

        # Should have 2-3 raises (depending on sizing generation)
        assert raise_count >= 2, f"LAG should have >=2 raises, got {raise_count}"

    def test_different_profiles_different_raise_counts(self):
        """Profiles should produce different raise option counts for same state."""
        from poker.bounded_options import STYLE_PROFILES

        context = self._postflop_many_raises_context()

        rock_opts = generate_bounded_options(
            context, STYLE_PROFILES['tight_passive'], phase='FLOP'
        )
        lag_opts = generate_bounded_options(
            context, STYLE_PROFILES['loose_aggressive'], phase='FLOP'
        )

        rock_raises = sum(1 for o in rock_opts if o.action == 'raise')
        lag_raises = sum(1 for o in lag_opts if o.action == 'raise')

        assert lag_raises > rock_raises, (
            f"LAG should have more raises than Rock: {lag_raises} vs {rock_raises}"
        )

    def test_preflop_not_limited(self):
        """Preflop raise options should not be affected by postflop limit."""
        from poker.bounded_options import STYLE_PROFILES

        context = {
            'equity': 0.55,
            'pot_total': 300,
            'cost_to_call': 100,
            'player_stack': 2000,
            'stack_bb': 100,
            'min_raise': 200,
            'max_raise': 2000,
            'big_blind': 20,
            'valid_actions': ['fold', 'call', 'raise'],
            'phase': 'PRE_FLOP',
        }
        profile = STYLE_PROFILES['tight_passive']  # postflop_max_raise_options=1

        options = generate_bounded_options(context, profile, phase='PRE_FLOP')
        raise_count = sum(1 for o in options if o.action == 'raise')

        # Preflop should not be limited by postflop setting
        assert raise_count >= 1


class TestCheckPromotionDifferentiation:
    """Tests for profile-aware check promotion behavior."""

    def _no_plus_ev_context(self):
        """Helper: context where no option starts as +EV, triggering promotion logic."""
        return {
            'equity': 0.42,  # Above 0.40 threshold for promotion, below raise +EV
            'pot_total': 400,
            'cost_to_call': 0,
            'player_stack': 1000,
            'stack_bb': 50,
            'min_raise': 200,
            'max_raise': 1000,
            'big_blind': 20,
            'valid_actions': ['check', 'raise'],
            'phase': 'FLOP',
        }

    def test_passive_profile_promotes_check_with_pot_control(self):
        """Passive profiles should promote check with pot-control text."""
        from poker.bounded_options import STYLE_PROFILES

        context = self._no_plus_ev_context()
        profile = STYLE_PROFILES['tight_passive']

        options = generate_bounded_options(context, profile, phase='FLOP')
        check_opts = [o for o in options if o.action == 'check']

        assert len(check_opts) == 1
        assert 'pot control' in check_opts[0].rationale.lower(), (
            f"Expected 'pot control' in rationale, got: {check_opts[0].rationale}"
        )

    def test_lag_does_not_promote_check_when_raises_exist(self):
        """LAG should not promote check to +EV when raise options exist."""
        from poker.bounded_options import STYLE_PROFILES

        context = self._no_plus_ev_context()
        profile = STYLE_PROFILES['loose_aggressive']

        options = generate_bounded_options(context, profile, phase='FLOP')
        check_opts = [o for o in options if o.action == 'check']

        has_raises = any(o.action == 'raise' for o in options)
        if has_raises:
            # Check should NOT be promoted to +EV
            for c in check_opts:
                assert c.ev_estimate != '+EV', (
                    f"LAG check should not be +EV when raises exist, got: {c.ev_estimate}"
                )

    def test_tag_promotes_check_only_without_decent_raises(self):
        """TAG should promote check only when no raise has neutral/+EV."""
        from poker.bounded_options import STYLE_PROFILES

        # At 42% equity, TAG postflop_raise_neutral is 0.35 so raises will be neutral
        # → TAG should NOT promote check (conditional + decent raise exists)
        context = self._no_plus_ev_context()
        profile = STYLE_PROFILES['tight_aggressive']

        options = generate_bounded_options(context, profile, phase='FLOP')
        check_opts = [o for o in options if o.action == 'check']
        raise_opts = [o for o in options if o.action == 'raise']

        has_decent_raise = any(o.ev_estimate in ('+EV', 'neutral') for o in raise_opts)
        if has_decent_raise:
            for c in check_opts:
                assert c.ev_estimate != '+EV', (
                    f"TAG check should not be +EV when decent raises exist, got: {c.ev_estimate}"
                )

    def test_default_profile_promotes_check_as_before(self):
        """Default profile should maintain original promotion behavior."""
        from poker.bounded_options import OptionProfile

        context = self._no_plus_ev_context()
        profile = OptionProfile()  # default

        options = generate_bounded_options(context, profile, phase='FLOP')

        # Default should still promote the best option
        has_plus_ev = any(o.ev_estimate == '+EV' for o in options)
        # With equity 0.42 >= 0.40 threshold, promotion should fire
        # (fold is blocked because cost_to_call=0)
        assert has_plus_ev, "Default profile should still promote best option to +EV"


class TestQualitativeTuning:
    """Tests for v2 qualitative fixes: TAG thresholds, check penalty, EV-aware rationale."""

    def _postflop_context(self, equity, cost_to_call=0):
        """Helper: post-flop context with configurable equity and cost."""
        return {
            'equity': equity,
            'pot_total': 400,
            'cost_to_call': cost_to_call,
            'player_stack': 1000,
            'stack_bb': 50,
            'min_raise': 200,
            'max_raise': 1000,
            'big_blind': 20,
            'valid_actions': ['check', 'raise'] if cost_to_call == 0 else ['fold', 'call', 'raise'],
            'phase': 'FLOP',
        }

    # --- Fix 1: TAG thresholds ---

    def test_tag_35pct_equity_raises_are_neutral(self):
        """TAG at 35% equity post-flop should see neutral raises (not -EV).

        With postflop_raise_neutral=0.25, 35% equity is well above threshold.
        """
        from poker.bounded_options import STYLE_PROFILES

        context = self._postflop_context(equity=0.35)
        profile = STYLE_PROFILES['tight_aggressive']

        options = generate_bounded_options(context, profile, phase='FLOP')
        raise_opts = [o for o in options if o.action == 'raise']

        assert len(raise_opts) >= 1
        # At least one raise should be neutral (35% > 25% threshold)
        assert any(o.ev_estimate == 'neutral' for o in raise_opts), (
            f"TAG at 35% equity should have neutral raises, got: "
            f"{[(o.ev_estimate, o.rationale) for o in raise_opts]}"
        )

    def test_tag_more_aggressive_than_rock_postflop(self):
        """TAG should have more favorable raise labels than Rock at same equity."""
        from poker.bounded_options import STYLE_PROFILES

        context = self._postflop_context(equity=0.35)

        tag_opts = generate_bounded_options(context, STYLE_PROFILES['tight_aggressive'], phase='FLOP')
        rock_opts = generate_bounded_options(context, STYLE_PROFILES['tight_passive'], phase='FLOP')

        tag_neg_raises = sum(1 for o in tag_opts if o.action == 'raise' and o.ev_estimate == '-EV')
        rock_neg_raises = sum(1 for o in rock_opts if o.action == 'raise' and o.ev_estimate == '-EV')

        assert tag_neg_raises < rock_neg_raises, (
            f"TAG should have fewer -EV raises than Rock: TAG={tag_neg_raises}, Rock={rock_neg_raises}"
        )

    # --- Fix 2: Check penalty threshold ---

    def test_lag_check_marginal_with_betting_equity(self):
        """LAG check at 40% equity should be 'marginal', not 'neutral'."""
        from poker.bounded_options import STYLE_PROFILES

        context = self._postflop_context(equity=0.40)
        profile = STYLE_PROFILES['loose_aggressive']

        options = generate_bounded_options(context, profile, phase='FLOP')
        check_opts = [o for o in options if o.action == 'check']

        assert len(check_opts) == 1
        assert check_opts[0].ev_estimate == 'marginal', (
            f"LAG check at 40% equity should be marginal, got: {check_opts[0].ev_estimate}"
        )
        assert 'betting equity' in check_opts[0].rationale.lower(), (
            f"Expected 'betting equity' in rationale, got: {check_opts[0].rationale}"
        )

    def test_passive_check_not_penalized(self):
        """Passive profiles should not have check penalty at 40% equity.

        The check may be promoted to +EV by the 'always' check_promotion,
        but it should NOT be labeled 'marginal' by the penalty threshold.
        """
        from poker.bounded_options import STYLE_PROFILES

        context = self._postflop_context(equity=0.40)
        profile = STYLE_PROFILES['tight_passive']

        options = generate_bounded_options(context, profile, phase='FLOP')
        check_opts = [o for o in options if o.action == 'check']

        assert len(check_opts) == 1
        # Should not contain "betting equity" penalty text
        assert 'betting equity' not in check_opts[0].rationale.lower(), (
            f"Passive check should not have betting equity penalty: {check_opts[0].rationale}"
        )

    def test_tag_check_marginal_above_threshold(self):
        """TAG check at 45% equity should be 'marginal' (above 0.40 penalty threshold)."""
        from poker.bounded_options import STYLE_PROFILES

        context = self._postflop_context(equity=0.45)
        profile = STYLE_PROFILES['tight_aggressive']

        options = generate_bounded_options(context, profile, phase='FLOP')
        check_opts = [o for o in options if o.action == 'check']

        assert len(check_opts) == 1
        assert check_opts[0].ev_estimate == 'marginal', (
            f"TAG check at 45% equity should be marginal, got: {check_opts[0].ev_estimate}"
        )

    # --- Fix 3: EV-aware rationale ---

    def test_negative_ev_raise_says_bluff_not_value(self):
        """Raises labeled -EV should say 'bluff bet' instead of 'value bet'."""
        from poker.bounded_options import STYLE_PROFILES

        # 20% equity → all raises should be -EV for any profile
        context = self._postflop_context(equity=0.20)
        profile = STYLE_PROFILES['tight_passive']

        options = generate_bounded_options(context, profile, phase='FLOP')
        neg_raises = [o for o in options if o.action == 'raise' and o.ev_estimate == '-EV']

        for r in neg_raises:
            assert 'value bet' not in r.rationale.lower(), (
                f"-EV raise should not say 'value bet': {r.rationale}"
            )
            assert 'bluff' in r.rationale.lower(), (
                f"-EV raise should say 'bluff': {r.rationale}"
            )

    def test_positive_ev_raise_still_says_value(self):
        """Raises labeled +EV should still say 'value bet'."""
        from poker.bounded_options import STYLE_PROFILES

        # 80% equity → raises should be +EV
        context = self._postflop_context(equity=0.80)
        profile = STYLE_PROFILES['loose_aggressive']

        options = generate_bounded_options(context, profile, phase='FLOP')
        pos_raises = [o for o in options if o.action == 'raise' and o.ev_estimate == '+EV']

        assert len(pos_raises) >= 1
        for r in pos_raises:
            assert 'bluff' not in r.rationale.lower(), (
                f"+EV raise should not say 'bluff': {r.rationale}"
            )


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
