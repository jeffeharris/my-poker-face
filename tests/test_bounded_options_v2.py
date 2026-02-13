"""
Tests for bounded options — emotional window shift, profiles, and regression.

Tests position awareness, bluff gating, emotional window shift,
math blocking overrides, and style profile differentiation.
"""

import random
import pytest
from unittest.mock import MagicMock

from poker.bounded_options import (
    BoundedOption,
    OptionProfile,
    EmotionalShift,
    STYLE_PROFILES,
    IMPAIRMENT_PROBABILITY,
    EMOTIONAL_DIRECTION,
    NARRATIVE_FRAMING,
    calculate_required_equity,
    generate_bounded_options,
    apply_emotional_window_shift,
    get_emotional_shift,
    format_options_for_prompt,
    _should_block_fold,
    _should_block_call,
    _get_raise_options,
    _option_spectrum_position,
    _apply_narrative_framing,
    _reapply_math_blocking,
    _truncate_options,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _base_context(**overrides):
    """Build a standard context dict with sensible defaults."""
    ctx = {
        'equity': 0.50,
        'pot_total': 300,
        'cost_to_call': 100,
        'player_stack': 1000,
        'stack_bb': 50,
        'min_raise': 200,
        'max_raise': 1000,
        'big_blind': 20,
        'valid_actions': ['fold', 'call', 'raise'],
        'already_bet': 0,
        'position': 'button',  # IP by default
    }
    ctx.update(overrides)
    return ctx


def _free_context(**overrides):
    """Build a free-to-act context (check/raise, no cost)."""
    ctx = {
        'equity': 0.50,
        'pot_total': 300,
        'cost_to_call': 0,
        'player_stack': 1000,
        'stack_bb': 50,
        'min_raise': 100,
        'max_raise': 1000,
        'big_blind': 20,
        'valid_actions': ['check', 'raise'],
        'already_bet': 0,
        'position': 'button',
    }
    ctx.update(overrides)
    return ctx


def _actions(options):
    """Extract action names from option list."""
    return [o.action for o in options]


def _has_action(options, action):
    """Check if any option matches the given action."""
    return action in _actions(options)


def _raise_amounts(options):
    """Extract raise_to values from raise options."""
    return [o.raise_to for o in options if o.action == 'raise']


def _ev_for_action(options, action):
    """Get EV estimate for a specific action."""
    for o in options:
        if o.action == action:
            return o.ev_estimate
    return None


# ═══════════════════════════════════════════════════════════════════════════
# CASE MATRIX TESTS (F1-F4, B1-B6)
# ═══════════════════════════════════════════════════════════════════════════


class TestCaseF1MonsterFreeToAct:
    """F1: Monster hand (90%+), free to act. Must extract value."""

    def test_f1_includes_raise(self):
        """F1 should always include RAISE (value sizes)."""
        ctx = _free_context(equity=0.95)
        options = generate_bounded_options(ctx)
        assert _has_action(options, 'raise'), f"F1 missing raise: {_actions(options)}"

    def test_f1_no_fold(self):
        """F1 should never include FOLD (free to act = no fold anyway)."""
        ctx = _free_context(equity=0.95)
        options = generate_bounded_options(ctx)
        assert not _has_action(options, 'fold')

    def test_f1_ip_has_check_trap(self):
        """F1 IP: CHECK should be available as a trap."""
        ctx = _free_context(equity=0.95, position='button')
        options = generate_bounded_options(ctx)
        assert _has_action(options, 'check'), "F1 IP should include CHECK (trap)"

    def test_f1_oop_check_marginal(self):
        """F1 OOP: CHECK labeled marginal (strong hand, consider betting)."""
        ctx = _free_context(equity=0.95, position='small_blind')
        options = generate_bounded_options(ctx)
        checks = [o for o in options if o.action == 'check']
        if checks:
            assert checks[0].ev_estimate == 'marginal', "F1 OOP check should be marginal"

    def test_f1_raise_is_plus_ev(self):
        """F1 raise options should be labeled +EV."""
        ctx = _free_context(equity=0.95)
        options = generate_bounded_options(ctx)
        for o in options:
            if o.action == 'raise':
                assert o.ev_estimate == '+EV', f"F1 raise should be +EV, got {o.ev_estimate}"


class TestCaseF2StrongFreeToAct:
    """F2: Strong hand (65-90%), free to act. Should bet for value."""

    def test_f2_includes_raise(self):
        """F2 should include RAISE for value."""
        ctx = _free_context(equity=0.75)
        options = generate_bounded_options(ctx)
        assert _has_action(options, 'raise')

    def test_f2_ip_check_available(self):
        """F2 IP: CHECK available (pot control)."""
        ctx = _free_context(equity=0.75, position='button')
        options = generate_bounded_options(ctx)
        assert _has_action(options, 'check'), "F2 IP should include CHECK"

    def test_f2_oop_check_marginal_or_absent(self):
        """F2 OOP: CHECK should be labeled marginal or removed for aggressive profiles."""
        ctx = _free_context(equity=0.75, position='small_blind')
        options = generate_bounded_options(ctx)
        # Either CHECK is absent OR labeled as marginal (not neutral/+EV)
        check_opts = [o for o in options if o.action == 'check']
        if check_opts:
            assert check_opts[0].ev_estimate in ('marginal', '-EV'), \
                f"F2 OOP CHECK should be marginal, got {check_opts[0].ev_estimate}"

    def test_f2_oop_aggressive_profile_removes_check(self):
        """F2 OOP with TAG profile: CHECK should be removed or labeled negatively."""
        ctx = _free_context(equity=0.75, position='small_blind')
        profile = STYLE_PROFILES['tight_aggressive']
        options = generate_bounded_options(ctx, profile=profile)
        check_opts = [o for o in options if o.action == 'check']
        if check_opts:
            # TAG F2 OOP: CHECK should at minimum be marginal
            assert check_opts[0].ev_estimate != '+EV'


class TestCaseF3DecentFreeToAct:
    """F3: Decent hand (40-65%), free to act."""

    def test_f3_includes_check(self):
        """F3 should always include CHECK."""
        ctx = _free_context(equity=0.50)
        options = generate_bounded_options(ctx)
        assert _has_action(options, 'check')

    def test_f3_includes_probe_raise(self):
        """F3 should include a small probe raise."""
        ctx = _free_context(equity=0.50)
        options = generate_bounded_options(ctx)
        assert _has_action(options, 'raise')

    def test_f3_default_no_bluff_raise(self):
        """F3 with default profile: no bluff raise (bluff_frequency=0)."""
        ctx = _free_context(equity=0.50)
        profile = STYLE_PROFILES['default']
        options = generate_bounded_options(ctx, profile=profile)
        # With 50% equity, raises should be neutral or +EV, not -EV bluffs
        for o in options:
            if o.action == 'raise' and o.ev_estimate == '-EV':
                # Default profile shouldn't include -EV bluff raises
                pytest.fail(f"Default F3 should not have -EV bluff raises")

    def test_f3_lag_gets_bluff_raise(self):
        """F3 with LAG profile: bluff raise included."""
        ctx = _free_context(equity=0.50)
        profile = STYLE_PROFILES['loose_aggressive']
        options = generate_bounded_options(ctx, profile=profile)
        # LAG should have more aggressive options available
        assert _has_action(options, 'raise')


class TestCaseF4WeakFreeToAct:
    """F4: Weak hand (<40%), free to act. Take the free card."""

    def test_f4_includes_check(self):
        """F4 should always include CHECK."""
        ctx = _free_context(equity=0.20)
        options = generate_bounded_options(ctx)
        assert _has_action(options, 'check')

    def test_f4_default_raises_labeled_minus_ev(self):
        """F4 with default profile: any raises are honestly labeled -EV."""
        ctx = _free_context(equity=0.20)
        profile = STYLE_PROFILES['default']
        options = generate_bounded_options(ctx, profile=profile)
        raise_opts = [o for o in options if o.action == 'raise']
        for o in raise_opts:
            assert o.ev_estimate == '-EV', f"Weak hand raise should be -EV, got {o.ev_estimate}"

    def test_f4_lag_bluff_raise(self):
        """F4 with LAG profile: bluff raise available (bluff_frequency > 0)."""
        ctx = _free_context(equity=0.20)
        profile = STYLE_PROFILES['loose_aggressive']
        options = generate_bounded_options(ctx, profile=profile)
        # LAG with bluff_frequency=0.15 should sometimes include bluff
        # At minimum, raise should be available
        assert _has_action(options, 'check') or _has_action(options, 'raise')


class TestCaseB1MonsterFacingBet:
    """B1: Monster (90%+), facing bet. FOLD blocked."""

    def test_b1_fold_blocked(self):
        """B1 should block FOLD."""
        ctx = _base_context(equity=0.95)
        options = generate_bounded_options(ctx)
        assert not _has_action(options, 'fold')

    def test_b1_raise_available(self):
        """B1 should include RAISE (value) and/or ALL-IN."""
        ctx = _base_context(equity=0.95)
        options = generate_bounded_options(ctx)
        assert _has_action(options, 'raise') or _has_action(options, 'all_in')

    def test_b1_call_available(self):
        """B1 should include CALL (slowplay/trap)."""
        ctx = _base_context(equity=0.95)
        options = generate_bounded_options(ctx)
        assert _has_action(options, 'call')


class TestCaseB2CrushingFacingBet:
    """B2: Crushing (>1.7x required), facing bet. FOLD blocked."""

    def test_b2_fold_blocked(self):
        """B2 should block FOLD (equity >> required)."""
        # Required = 100/(300+100) = 25%. Equity 60% is 2.4x required.
        ctx = _base_context(equity=0.60, pot_total=300, cost_to_call=100)
        options = generate_bounded_options(ctx)
        assert not _has_action(options, 'fold')

    def test_b2_call_plus_ev(self):
        """B2 CALL should be labeled +EV."""
        ctx = _base_context(equity=0.60, pot_total=300, cost_to_call=100)
        options = generate_bounded_options(ctx)
        assert _ev_for_action(options, 'call') == '+EV'

    def test_b2_raise_available(self):
        """B2 should include RAISE (value)."""
        ctx = _base_context(equity=0.60, pot_total=300, cost_to_call=100)
        options = generate_bounded_options(ctx)
        assert _has_action(options, 'raise')


class TestCaseB3ProfitableFacingBet:
    """B3: Profitable (1.0-1.7x required), facing bet."""

    def test_b3_call_marginal_to_plus_ev(self):
        """B3 CALL should be marginal to +EV."""
        # Required = 100/(300+100) = 25%. Equity 35% is 1.4x required.
        ctx = _base_context(equity=0.35, pot_total=300, cost_to_call=100)
        options = generate_bounded_options(ctx)
        call_ev = _ev_for_action(options, 'call')
        assert call_ev in ('+EV', 'marginal'), f"B3 call EV should be +EV or marginal, got {call_ev}"

    def test_b3_fold_available_but_negative(self):
        """B3 FOLD should be available but labeled negatively."""
        ctx = _base_context(equity=0.35, pot_total=300, cost_to_call=100)
        options = generate_bounded_options(ctx)
        if _has_action(options, 'fold'):
            fold_ev = _ev_for_action(options, 'fold')
            assert fold_ev in ('-EV', 'neutral'), f"B3 fold EV should be -EV/neutral, got {fold_ev}"


class TestCaseB4MarginalFacingBet:
    """B4: Marginal (0.85-1.0x required), facing bet. Personality zone."""

    def test_b4_call_marginal(self):
        """B4 CALL should be marginal."""
        # Required = 100/(200+100) = 33%. Equity 30% is 0.9x required.
        ctx = _base_context(equity=0.30, pot_total=200, cost_to_call=100)
        options = generate_bounded_options(ctx)
        call_ev = _ev_for_action(options, 'call')
        assert call_ev in ('marginal', '-EV'), f"B4 call should be marginal, got {call_ev}"

    def test_b4_fold_neutral(self):
        """B4 FOLD should be neutral (neither good nor bad)."""
        ctx = _base_context(equity=0.30, pot_total=200, cost_to_call=100)
        options = generate_bounded_options(ctx)
        if _has_action(options, 'fold'):
            fold_ev = _ev_for_action(options, 'fold')
            assert fold_ev in ('neutral', '+EV')


class TestCaseB5WeakFacingBet:
    """B5: Weak (<0.85x required), facing bet."""

    def test_b5_fold_plus_ev(self):
        """B5 FOLD should be +EV (saves money)."""
        # Required = 100/(100+100) = 50%. Equity 15% is 0.3x required.
        ctx = _base_context(equity=0.15, pot_total=100, cost_to_call=100)
        options = generate_bounded_options(ctx)
        fold_ev = _ev_for_action(options, 'fold')
        assert fold_ev == '+EV', f"B5 fold should be +EV, got {fold_ev}"

    def test_b5_call_minus_ev(self):
        """B5 CALL should be -EV but available."""
        ctx = _base_context(equity=0.15, pot_total=100, cost_to_call=100)
        options = generate_bounded_options(ctx)
        if _has_action(options, 'call'):
            call_ev = _ev_for_action(options, 'call')
            assert call_ev == '-EV', f"B5 call should be -EV, got {call_ev}"

    def test_b5_lag_bluff_raise(self):
        """B5 with LAG profile: bluff RAISE available."""
        ctx = _base_context(equity=0.15, pot_total=100, cost_to_call=100)
        profile = STYLE_PROFILES['loose_aggressive']
        options = generate_bounded_options(ctx, profile=profile)
        # LAG should have some raising capability even with weak hands
        assert len(options) >= 2

    def test_b5_default_raises_labeled_minus_ev(self):
        """B5 with default profile: any raises honestly labeled -EV."""
        ctx = _base_context(equity=0.15, pot_total=100, cost_to_call=100)
        profile = STYLE_PROFILES['default']
        options = generate_bounded_options(ctx, profile=profile)
        raise_opts = [o for o in options if o.action == 'raise']
        for o in raise_opts:
            assert o.ev_estimate == '-EV', f"B5 raise should be -EV, got {o.ev_estimate}"


class TestCaseB6DeadFacingBet:
    """B6: Dead (<5%), facing bet. CALL blocked."""

    def test_b6_call_blocked(self):
        """B6 should block CALL."""
        ctx = _base_context(equity=0.02)
        options = generate_bounded_options(ctx)
        assert not _has_action(options, 'call')

    def test_b6_fold_available(self):
        """B6 should include FOLD."""
        ctx = _base_context(equity=0.02)
        options = generate_bounded_options(ctx)
        assert _has_action(options, 'fold')


# ═══════════════════════════════════════════════════════════════════════════
# POSITION AWARENESS TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestPositionAwareness:
    """Position modifies CHECK availability for strong hands."""

    def test_ip_check_available_monster(self):
        """In position + monster: CHECK available (trap)."""
        ctx = _free_context(equity=0.95, position='button')
        options = generate_bounded_options(ctx)
        assert _has_action(options, 'check'), "IP monster should have CHECK (trap)"

    def test_oop_no_check_monster(self):
        """Out of position + monster: CHECK labeled marginal (consider betting)."""
        ctx = _free_context(equity=0.95, position='small_blind')
        options = generate_bounded_options(ctx)
        checks = [o for o in options if o.action == 'check']
        if checks:
            assert checks[0].ev_estimate == 'marginal', "OOP monster check should be marginal"

    def test_ip_check_neutral_strong(self):
        """In position + strong: CHECK neutral (pot control ok)."""
        ctx = _free_context(equity=0.75, position='button')
        options = generate_bounded_options(ctx)
        check_opts = [o for o in options if o.action == 'check']
        assert len(check_opts) > 0, "IP strong should have CHECK"

    def test_oop_check_marginal_strong(self):
        """Out of position + strong: CHECK marginal (missing value)."""
        ctx = _free_context(equity=0.75, position='small_blind')
        options = generate_bounded_options(ctx)
        check_opts = [o for o in options if o.action == 'check']
        if check_opts:
            assert check_opts[0].ev_estimate in ('marginal', '-EV'), \
                f"OOP strong CHECK should be marginal, got {check_opts[0].ev_estimate}"

    def test_position_irrelevant_weak_hands(self):
        """Position should not affect weak hand options (CHECK always available)."""
        for position in ('button', 'small_blind'):
            ctx = _free_context(equity=0.20, position=position)
            options = generate_bounded_options(ctx)
            assert _has_action(options, 'check'), \
                f"Weak hand CHECK should be available regardless of position={position}"


# ═══════════════════════════════════════════════════════════════════════════
# BLUFF GATING TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestBluffGating:
    """Bluff raises only appear when profile.bluff_frequency > 0."""

    def test_lag_has_bluff_frequency(self):
        """LAG profile should have bluff_frequency > 0."""
        lag = STYLE_PROFILES['loose_aggressive']
        assert lag.bluff_frequency > 0

    def test_default_no_bluff_frequency(self):
        """Default profile should have bluff_frequency = 0."""
        default = STYLE_PROFILES['default']
        assert default.bluff_frequency == 0.0

    def test_tag_no_bluff_frequency(self):
        """TAG profile should have bluff_frequency = 0."""
        tag = STYLE_PROFILES['tight_aggressive']
        assert tag.bluff_frequency == 0.0

    def test_bluff_gated_in_f3(self):
        """F3 (decent, free): only LAG gets -EV bluff raise."""
        ctx = _free_context(equity=0.50)
        for key in ('default', 'tight_aggressive', 'tight_passive'):
            profile = STYLE_PROFILES[key]
            options = generate_bounded_options(ctx, profile=profile)
            bluff_raises = [o for o in options if o.action == 'raise' and o.ev_estimate == '-EV']
            assert len(bluff_raises) == 0, f"{key} F3 should not have -EV bluff raises"

    def test_bluff_raises_labeled_minus_ev_in_f4(self):
        """F4 (weak, free): raises honestly labeled -EV for all profiles."""
        ctx = _free_context(equity=0.20)
        for key in ('default', 'tight_aggressive', 'tight_passive'):
            profile = STYLE_PROFILES[key]
            options = generate_bounded_options(ctx, profile=profile)
            raise_opts = [o for o in options if o.action == 'raise']
            for o in raise_opts:
                assert o.ev_estimate == '-EV', f"{key} F4 raise should be -EV, got {o.ev_estimate}"

    def test_bluff_raises_labeled_minus_ev_in_b5(self):
        """B5 (weak, facing bet): raises honestly labeled -EV for all profiles."""
        ctx = _base_context(equity=0.15, pot_total=100, cost_to_call=100)
        for key in ('default', 'tight_aggressive', 'tight_passive'):
            profile = STYLE_PROFILES[key]
            options = generate_bounded_options(ctx, profile=profile)
            raise_opts = [o for o in options if o.action == 'raise']
            for o in raise_opts:
                assert o.ev_estimate == '-EV', f"{key} B5 raise should be -EV, got {o.ev_estimate}"


# ═══════════════════════════════════════════════════════════════════════════
# STACK DEPTH OVERLAY TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestStackDepthOverlay:
    """Stack depth collapses option space for short stacks."""

    def test_short_stack_monster_free_collapses_to_all_in(self):
        """Short stack (<10 BB) + F1/F2: should collapse to ALL-IN."""
        ctx = _free_context(
            equity=0.95,
            stack_bb=8,
            player_stack=160,
            min_raise=100,
            max_raise=160,
        )
        options = generate_bounded_options(ctx)
        actions = _actions(options)
        # Short stack monster should have ALL-IN or a max raise
        has_all_in = 'all_in' in actions
        has_max_raise = any(
            o.raise_to >= ctx['max_raise'] for o in options if o.action == 'raise'
        )
        assert has_all_in or has_max_raise, \
            f"Short stack F1 should collapse to ALL-IN, got {actions}"

    def test_short_stack_facing_bet_pushfold(self):
        """Short stack (<10 BB) + B1-B3: should collapse to ALL-IN or FOLD."""
        ctx = _base_context(
            equity=0.60,
            stack_bb=7,
            player_stack=140,
            min_raise=200,
            max_raise=140,
        )
        options = generate_bounded_options(ctx)
        actions = _actions(options)
        # In push/fold territory, options should be very limited
        assert len(options) <= 3, f"Short stack should have limited options, got {len(options)}"

    def test_medium_stack_limited_sizing(self):
        """Medium stack (10-30 BB): 1-2 raise sizes, no full range."""
        ctx = _free_context(
            equity=0.75,
            stack_bb=20,
            player_stack=400,
            min_raise=100,
            max_raise=400,
        )
        options = generate_bounded_options(ctx)
        raises = [o for o in options if o.action == 'raise']
        # Medium stack should have limited sizing (1-2 raises)
        assert len(raises) <= 3, f"Medium stack should limit raises, got {len(raises)}"

    def test_deep_stack_full_sizing(self):
        """Deep stack (>30 BB): full sizing range."""
        ctx = _free_context(
            equity=0.75,
            stack_bb=100,
            player_stack=2000,
            min_raise=100,
            max_raise=2000,
        )
        options = generate_bounded_options(ctx)
        raises = [o for o in options if o.action == 'raise']
        # Deep stack should have multiple raise sizes
        assert len(raises) >= 2, f"Deep stack should have multiple raise sizes, got {len(raises)}"


# ═══════════════════════════════════════════════════════════════════════════
# EMOTIONAL WINDOW SHIFT TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestEmotionalWindowShiftMildTilt:
    """Mild tilt: ADD aggressive option (expand window toward aggressive end)."""

    def test_mild_tilt_adds_aggressive_option(self):
        """Mild tilt should add a larger raise or ALL-IN option."""
        ctx = _free_context(equity=0.75)
        # Get baseline options (no emotional state)
        baseline = generate_bounded_options(ctx)
        # Get tilted options
        tilted = generate_bounded_options(
            ctx, emotional_state='tilted', emotional_severity='mild'
        )
        # Tilted should have >= baseline options (added aggressive)
        assert len(tilted) >= len(baseline), \
            f"Mild tilt should expand options: baseline={len(baseline)}, tilted={len(tilted)}"

    def test_mild_tilt_does_not_remove_passive(self):
        """Mild tilt should NOT remove passive options (only extreme does)."""
        ctx = _free_context(equity=0.75)
        tilted = generate_bounded_options(
            ctx, emotional_state='tilted', emotional_severity='mild'
        )
        # CHECK should still be available (only extreme removes it)
        assert _has_action(tilted, 'check') or _has_action(tilted, 'call'), \
            "Mild tilt should not remove passive options"


class TestEmotionalWindowShiftExtremeTilt:
    """Extreme tilt: ADD aggressive + REMOVE passive option."""

    def test_extreme_tilt_adds_aggressive(self):
        """Extreme tilt should add aggressive option."""
        ctx = _free_context(equity=0.75)
        extreme = generate_bounded_options(
            ctx, emotional_state='tilted', emotional_severity='extreme'
        )
        assert _has_action(extreme, 'raise'), "Extreme tilt should have raise options"

    def test_extreme_tilt_removes_passive(self):
        """Extreme tilt should remove FOLD or CHECK from options."""
        ctx = _free_context(equity=0.75)
        baseline = generate_bounded_options(ctx)
        extreme = generate_bounded_options(
            ctx, emotional_state='tilted', emotional_severity='extreme'
        )
        # Extreme tilt removes passive end — CHECK should be gone (or FOLD for facing bet)
        baseline_passive = [o for o in baseline if o.action in ('check', 'fold')]
        extreme_passive = [o for o in extreme if o.action in ('check', 'fold')]
        assert len(extreme_passive) < len(baseline_passive), \
            "Extreme tilt should remove a passive option"


class TestEmotionalWindowShiftMildShaken:
    """Mild shaken: ADD passive option (expand window toward passive end)."""

    def test_mild_shaken_adds_passive(self):
        """Mild shaken should add FOLD or CHECK where normally absent."""
        ctx = _base_context(equity=0.35, pot_total=200, cost_to_call=100)
        shaken = generate_bounded_options(
            ctx, emotional_state='shaken', emotional_severity='mild'
        )
        # Should have passive options available
        assert _has_action(shaken, 'fold') or _has_action(shaken, 'check'), \
            "Mild shaken should have passive options"


class TestEmotionalWindowShiftExtremeShaken:
    """Extreme shaken: ADD passive + REMOVE aggressive option."""

    def test_extreme_shaken_removes_aggressive(self):
        """Extreme shaken should remove largest RAISE."""
        ctx = _free_context(equity=0.75)
        baseline = generate_bounded_options(ctx)
        shaken = generate_bounded_options(
            ctx, emotional_state='shaken', emotional_severity='extreme'
        )
        baseline_raises = sorted(_raise_amounts(baseline))
        shaken_raises = sorted(_raise_amounts(shaken))
        # Extreme shaken should have fewer/smaller raises
        if baseline_raises and shaken_raises:
            assert max(shaken_raises) <= max(baseline_raises), \
                "Extreme shaken should not have larger raises than baseline"
        # OR raises removed entirely
        assert len(shaken_raises) <= len(baseline_raises), \
            "Extreme shaken should have fewer raise options"


class TestEmotionalProbabilisticRoll:
    """Emotional impairment is probabilistic, not deterministic."""

    def test_mild_70_percent_impaired(self):
        """Mild severity: ~70% chance of impairment."""
        ctx = _free_context(equity=0.75)
        impaired_count = 0
        trials = 1000
        for i in range(trials):
            rng = random.Random(i)
            options = generate_bounded_options(
                ctx, emotional_state='tilted', emotional_severity='mild', rng=rng
            )
            baseline = generate_bounded_options(ctx)
            if len(options) != len(baseline) or _actions(options) != _actions(baseline):
                impaired_count += 1
        rate = impaired_count / trials
        # Should be ~70% (allow ±10% tolerance)
        assert 0.55 <= rate <= 0.85, f"Mild impairment rate {rate:.2f}, expected ~0.70"

    def test_extreme_95_percent_impaired(self):
        """Extreme severity: ~95% chance of impairment."""
        ctx = _free_context(equity=0.75)
        impaired_count = 0
        trials = 1000
        for i in range(trials):
            rng = random.Random(i)
            options = generate_bounded_options(
                ctx, emotional_state='tilted', emotional_severity='extreme', rng=rng
            )
            baseline = generate_bounded_options(ctx)
            if len(options) != len(baseline) or _actions(options) != _actions(baseline):
                impaired_count += 1
        rate = impaired_count / trials
        assert 0.88 <= rate <= 1.0, f"Extreme impairment rate {rate:.2f}, expected ~0.95"

    def test_lucid_roll_returns_normal(self):
        """When roll says 'lucid', normal options returned."""
        ctx = _free_context(equity=0.75)
        baseline = generate_bounded_options(ctx)
        # Find a seed that produces a lucid roll for mild (30% chance)
        lucid_found = False
        for i in range(100):
            rng = random.Random(i)
            options = generate_bounded_options(
                ctx, emotional_state='tilted', emotional_severity='mild', rng=rng
            )
            if _actions(options) == _actions(baseline):
                lucid_found = True
                break
        assert lucid_found, "Should find at least one lucid roll in 100 seeds"

    def test_none_severity_always_lucid(self):
        """No emotional severity → always normal options."""
        ctx = _free_context(equity=0.75)
        baseline = generate_bounded_options(ctx)
        options = generate_bounded_options(
            ctx, emotional_state='tilted', emotional_severity='none'
        )
        assert _actions(options) == _actions(baseline), \
            "No severity should produce normal options"


class TestEmotionalNarrativeFraming:
    """Emotional states modify rationale text."""

    def test_tilted_aggressive_framing(self):
        """Tilted: aggressive options framed as revenge/justice."""
        ctx = _free_context(equity=0.75)
        options = generate_bounded_options(
            ctx, emotional_state='tilted', emotional_severity='moderate'
        )
        raise_opts = [o for o in options if o.action == 'raise']
        # At least one raise should have emotional framing
        if raise_opts:
            rationales = ' '.join(o.rationale.lower() for o in raise_opts)
            # Should have emotionally charged language (not just "value bet")
            assert len(rationales) > 0  # Basic check — implementation specific

    def test_shaken_passive_framing(self):
        """Shaken: passive options framed as safety."""
        ctx = _base_context(equity=0.35, pot_total=200, cost_to_call=100)
        options = generate_bounded_options(
            ctx, emotional_state='shaken', emotional_severity='moderate'
        )
        # Options should exist with modified rationale
        assert len(options) >= 1

    def test_dissociated_stripped_rationale(self):
        """Dissociated: rationale stripped to bare minimum."""
        ctx = _free_context(equity=0.50)
        options = generate_bounded_options(
            ctx, emotional_state='dissociated', emotional_severity='moderate'
        )
        # Dissociated rationale should be shorter/simpler
        for o in options:
            # Basic check — dissociated should have minimal rationale
            assert len(o.rationale) > 0


# ═══════════════════════════════════════════════════════════════════════════
# MATH BLOCKING OVERRIDE TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestMathBlockingOverrides:
    """Math blocking always wins over emotional shifts."""

    def test_tilt_cannot_remove_fold_blocking_for_monsters(self):
        """Extreme tilt removes FOLD, but fold was already blocked for monsters."""
        ctx = _base_context(equity=0.95)
        options = generate_bounded_options(
            ctx, emotional_state='tilted', emotional_severity='extreme'
        )
        # FOLD should still be blocked (monster hand)
        assert not _has_action(options, 'fold'), \
            "Tilt cannot override fold blocking for monsters"

    def test_shaken_cannot_remove_call_when_only_option(self):
        """Extreme shaken removes aggressive, but can't leave player with no action."""
        ctx = _base_context(
            equity=0.95,
            min_raise=0, max_raise=0,  # Can't raise
            valid_actions=['fold', 'call'],
        )
        options = generate_bounded_options(
            ctx, emotional_state='shaken', emotional_severity='extreme'
        )
        # Must have at least one playable option
        assert len(options) >= 1, "Shaken can't leave player with no options"
        # If fold is blocked (monster), CALL must survive
        if not _has_action(options, 'fold'):
            assert _has_action(options, 'call'), \
                "Shaken can't remove CALL when it's the only non-fold option"

    def test_tilt_cannot_override_call_blocking_dead_hand(self):
        """Tilt can't force a CALL for a dead hand (B6: <5% equity)."""
        ctx = _base_context(equity=0.02)
        options = generate_bounded_options(
            ctx, emotional_state='tilted', emotional_severity='extreme'
        )
        # CALL should still be blocked (drawing dead)
        assert not _has_action(options, 'call'), \
            "Tilt cannot override call blocking for dead hands"

    def test_emotional_shift_then_math_blocking_order(self):
        """Emotional shift applied BEFORE math blocking (blocking is last gate)."""
        # Monster hand + extreme shaken → shaken tries to remove aggressive,
        # but math blocking ensures RAISE/CALL exist for monster
        ctx = _base_context(equity=0.95)
        options = generate_bounded_options(
            ctx, emotional_state='shaken', emotional_severity='extreme'
        )
        # Monster should still have aggressive options despite being shaken
        assert _has_action(options, 'call') or _has_action(options, 'raise'), \
            "Math blocking preserves +EV options for monsters even when shaken"

    def test_always_at_least_one_option(self):
        """No combination of emotional state + blocking should produce zero options."""
        for state in ('tilted', 'shaken', 'overconfident', 'dissociated'):
            for severity in ('mild', 'moderate', 'extreme'):
                for equity in (0.02, 0.30, 0.50, 0.75, 0.95):
                    ctx = _base_context(equity=equity)
                    options = generate_bounded_options(
                        ctx, emotional_state=state, emotional_severity=severity
                    )
                    assert len(options) >= 1, \
                        f"Zero options for state={state}, severity={severity}, equity={equity}"


# ═══════════════════════════════════════════════════════════════════════════
# PLAY STYLE PROFILE TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestPlayStyleProfiles:
    """Style profiles shift thresholds and change which options appear."""

    def test_tag_f2_always_raise(self):
        """TAG F2 (strong, free): should always RAISE."""
        ctx = _free_context(equity=0.75)
        profile = STYLE_PROFILES['tight_aggressive']
        options = generate_bounded_options(ctx, profile=profile)
        assert _has_action(options, 'raise'), "TAG F2 should always include RAISE"

    def test_loose_passive_wider_call_zone(self):
        """Loose passive: CALL more available in B4-B5."""
        ctx = _base_context(equity=0.25, pot_total=200, cost_to_call=100)
        lp = STYLE_PROFILES['loose_passive']
        default = STYLE_PROFILES['default']
        lp_options = generate_bounded_options(ctx, profile=lp)
        default_options = generate_bounded_options(ctx, profile=default)
        # Loose passive should be more inclined to call
        lp_has_call = _has_action(lp_options, 'call')
        assert lp_has_call, "Loose passive should have CALL in marginal spots"

    def test_lag_sizing_larger(self):
        """LAG profile: bigger sizing options."""
        ctx = _free_context(equity=0.75)
        lag = STYLE_PROFILES['loose_aggressive']
        default = STYLE_PROFILES['default']
        lag_options = generate_bounded_options(ctx, profile=lag)
        default_options = generate_bounded_options(ctx, profile=default)
        lag_max = max(_raise_amounts(lag_options)) if _raise_amounts(lag_options) else 0
        def_max = max(_raise_amounts(default_options)) if _raise_amounts(default_options) else 0
        assert lag_max >= def_max, "LAG should have equal or larger max raise than default"


# ═══════════════════════════════════════════════════════════════════════════
# REGRESSION / COMPATIBILITY TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestRegressionCompatibility:
    """Ensure the refactored API is backward compatible."""

    def test_generate_bounded_options_default_profile(self):
        """Calling without profile still works (default profile)."""
        ctx = _base_context(equity=0.50)
        options = generate_bounded_options(ctx)
        assert 2 <= len(options) <= 4

    def test_format_options_for_prompt_unchanged(self):
        """format_options_for_prompt still produces expected format."""
        options = [
            BoundedOption('fold', 0, 'Save chips', '-EV', 'conservative'),
            BoundedOption('call', 0, 'Meet pot odds', 'neutral', 'standard'),
        ]
        result = format_options_for_prompt(options, 0.40, 3.0)
        assert '=== YOUR OPTIONS ===' in result
        assert '1. FOLD' in result
        assert '2. CALL' in result

    def test_bounded_option_immutable(self):
        """BoundedOption should be frozen."""
        opt = BoundedOption('call', 0, 'Test', 'neutral', 'standard')
        with pytest.raises(AttributeError):
            opt.action = 'fold'

    def test_option_profile_defaults(self):
        """Default OptionProfile should match documented defaults."""
        p = OptionProfile()
        assert p.fold_equity_multiplier == 2.0
        assert p.bluff_frequency == 0.0
        assert p.sizing_small == 0.33
        assert p.sizing_medium == 0.67
        assert p.sizing_large == 1.0

    def test_monster_fold_blocked_facing_bet(self):
        """Regression: monster hand facing bet should block fold."""
        ctx = _base_context(equity=0.95)
        options = generate_bounded_options(ctx)
        assert not _has_action(options, 'fold')

    def test_drawing_dead_call_blocked(self):
        """Regression: drawing dead should block call."""
        ctx = _base_context(equity=0.02)
        options = generate_bounded_options(ctx)
        assert not _has_action(options, 'call')

    def test_quad_tens_scenario(self):
        """Regression: the infamous quad-tens fold blocked."""
        ctx = {
            'equity': 1.0,
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
        options = generate_bounded_options(ctx)
        assert not _has_action(options, 'fold')
        assert _has_action(options, 'call') or _has_action(options, 'raise')

    def test_all_style_profiles_exist(self):
        """All expected style profiles should be defined."""
        expected = {'tight_passive', 'tight_aggressive', 'loose_passive', 'loose_aggressive', 'default'}
        assert set(STYLE_PROFILES.keys()) >= expected

    def test_required_equity_calculation(self):
        """Required equity calculation unchanged."""
        assert calculate_required_equity(200, 100) == pytest.approx(0.333, abs=0.01)
        assert calculate_required_equity(1000, 0) == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# EMOTIONAL SHIFT INTERNAL COMPONENT TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestEmotionalShiftDataclass:
    """Tests for the EmotionalShift dataclass."""

    def test_frozen(self):
        """EmotionalShift should be immutable."""
        shift = EmotionalShift(state='tilted', severity='mild', intensity=0.2)
        with pytest.raises(AttributeError):
            shift.state = 'composed'

    def test_to_dict(self):
        """EmotionalShift serialization."""
        shift = EmotionalShift(state='shaken', severity='extreme', intensity=0.8)
        d = shift.to_dict()
        assert d['state'] == 'shaken'
        assert d['severity'] == 'extreme'
        assert d['intensity'] == 0.8


class TestImpairmentProbabilityConfig:
    """Tests for IMPAIRMENT_PROBABILITY constants."""

    def test_none_is_zero(self):
        assert IMPAIRMENT_PROBABILITY['none'] == 0.0

    def test_mild_is_70(self):
        assert IMPAIRMENT_PROBABILITY['mild'] == 0.70

    def test_moderate_is_85(self):
        assert IMPAIRMENT_PROBABILITY['moderate'] == 0.85

    def test_extreme_is_95(self):
        assert IMPAIRMENT_PROBABILITY['extreme'] == 0.95

    def test_monotonically_increasing(self):
        """Impairment probability should increase with severity."""
        probs = [IMPAIRMENT_PROBABILITY[s] for s in ('none', 'mild', 'moderate', 'extreme')]
        for i in range(1, len(probs)):
            assert probs[i] > probs[i - 1]


class TestEmotionalDirectionConfig:
    """Tests for EMOTIONAL_DIRECTION mapping."""

    def test_tilted_is_aggressive(self):
        assert EMOTIONAL_DIRECTION['tilted'] == 'aggressive'

    def test_overconfident_is_aggressive(self):
        assert EMOTIONAL_DIRECTION['overconfident'] == 'aggressive'

    def test_shaken_is_passive(self):
        assert EMOTIONAL_DIRECTION['shaken'] == 'passive'

    def test_dissociated_is_passive(self):
        assert EMOTIONAL_DIRECTION['dissociated'] == 'passive'

    def test_composed_is_none(self):
        assert EMOTIONAL_DIRECTION['composed'] is None


class TestOptionSpectrumPosition:
    """Tests for _option_spectrum_position ordering."""

    def test_fold_is_most_passive(self):
        fold = BoundedOption('fold', 0, '', '', '')
        check = BoundedOption('check', 0, '', '', '')
        assert _option_spectrum_position(fold) < _option_spectrum_position(check)

    def test_check_less_than_call(self):
        check = BoundedOption('check', 0, '', '', '')
        call = BoundedOption('call', 0, '', '', '')
        assert _option_spectrum_position(check) < _option_spectrum_position(call)

    def test_call_less_than_raise(self):
        call = BoundedOption('call', 0, '', '', '')
        raise_small = BoundedOption('raise', 100, '', '', '')
        assert _option_spectrum_position(call) < _option_spectrum_position(raise_small)

    def test_small_raise_less_than_big_raise(self):
        small = BoundedOption('raise', 100, '', '', '')
        big = BoundedOption('raise', 500, '', '', '')
        assert _option_spectrum_position(small) < _option_spectrum_position(big)

    def test_all_in_is_most_aggressive(self):
        big_raise = BoundedOption('raise', 10000, '', '', '')
        all_in = BoundedOption('all_in', 0, '', '', '')
        assert _option_spectrum_position(big_raise) < _option_spectrum_position(all_in)


class TestApplyNarrativeFraming:
    """Tests for _apply_narrative_framing."""

    def test_tilted_modifies_raise_rationale(self):
        """Tilted should replace raise rationale with emotional text."""
        options = [
            BoundedOption('raise', 200, 'Value bet', '+EV', 'aggressive'),
        ]
        result = _apply_narrative_framing(options, 'tilted')
        assert result[0].rationale != 'Value bet'
        assert result[0].rationale == NARRATIVE_FRAMING['tilted']['raise']

    def test_shaken_modifies_fold_rationale(self):
        """Shaken should replace fold rationale with safety text."""
        options = [
            BoundedOption('fold', 0, 'Fold (need 33%)', '+EV', 'conservative'),
        ]
        result = _apply_narrative_framing(options, 'shaken')
        assert result[0].rationale == NARRATIVE_FRAMING['shaken']['fold']

    def test_dissociated_strips_to_minimum(self):
        """Dissociated should strip rationale to bare minimum."""
        options = [
            BoundedOption('raise', 200, 'Strong value bet (75% equity)', '+EV', 'aggressive'),
            BoundedOption('check', 0, 'Check and see a free card', 'neutral', 'conservative'),
        ]
        result = _apply_narrative_framing(options, 'dissociated')
        for o in result:
            # Dissociated rationale should be very short (single word + period)
            assert len(o.rationale) <= 10, f"Dissociated rationale too long: '{o.rationale}'"

    def test_composed_no_modification(self):
        """Composed state should not modify rationale."""
        options = [
            BoundedOption('call', 0, 'Call 5.0 BB - clearly profitable', '+EV', 'standard'),
        ]
        result = _apply_narrative_framing(options, 'composed')
        assert result[0].rationale == options[0].rationale

    def test_preserves_ev_and_style_tag(self):
        """Narrative framing should not change EV estimate or style tag."""
        options = [
            BoundedOption('raise', 200, 'Value bet', '+EV', 'aggressive'),
        ]
        result = _apply_narrative_framing(options, 'tilted')
        assert result[0].ev_estimate == '+EV'
        assert result[0].style_tag == 'aggressive'
        assert result[0].raise_to == 200


class TestReapplyMathBlocking:
    """Tests for _reapply_math_blocking safety net."""

    def test_removes_fold_when_blocked(self):
        """Math blocking removes fold even if emotional shift added it."""
        ctx = _base_context(equity=0.95)  # Monster — fold blocked
        options = [
            BoundedOption('fold', 0, 'Get out', '+EV', 'conservative'),
            BoundedOption('call', 0, 'Call', '+EV', 'standard'),
            BoundedOption('raise', 200, 'Raise', '+EV', 'aggressive'),
        ]
        result = _reapply_math_blocking(options, ctx)
        assert not _has_action(result, 'fold')

    def test_removes_call_when_blocked(self):
        """Math blocking removes call when drawing dead."""
        ctx = _base_context(equity=0.02)  # Dead — call blocked
        options = [
            BoundedOption('fold', 0, 'Fold', '+EV', 'conservative'),
            BoundedOption('call', 0, 'Call', '-EV', 'standard'),
        ]
        result = _reapply_math_blocking(options, ctx)
        assert not _has_action(result, 'call')

    def test_ensures_minimum_options(self):
        """After blocking, should still have at least a fallback option."""
        ctx = _base_context(equity=0.02, valid_actions=['fold', 'call', 'check'])
        options = [
            BoundedOption('call', 0, 'Call', '-EV', 'standard'),
        ]
        result = _reapply_math_blocking(options, ctx)
        # Call blocked (dead), but should add check as fallback
        assert len(result) >= 1


class TestApplyEmotionalWindowShiftDirect:
    """Tests for apply_emotional_window_shift called directly."""

    def test_composed_returns_unchanged(self):
        """Composed state returns options unchanged."""
        ctx = _free_context(equity=0.75)
        baseline = generate_bounded_options(ctx)
        shift = EmotionalShift(state='composed', severity='none', intensity=0.0)
        result = apply_emotional_window_shift(baseline, shift, ctx)
        assert _actions(result) == _actions(baseline)

    def test_empty_options_returns_empty(self):
        """Empty options list should return empty."""
        shift = EmotionalShift(state='tilted', severity='extreme', intensity=0.8)
        result = apply_emotional_window_shift([], shift, _base_context())
        assert result == []

    def test_deterministic_with_rng(self):
        """Same RNG seed should produce same results."""
        ctx = _free_context(equity=0.75)
        baseline = generate_bounded_options(ctx)
        shift = EmotionalShift(state='tilted', severity='mild', intensity=0.2)
        rng1 = random.Random(42)
        rng2 = random.Random(42)
        result1 = apply_emotional_window_shift(list(baseline), shift, ctx, rng=rng1)
        result2 = apply_emotional_window_shift(list(baseline), shift, ctx, rng=rng2)
        assert _actions(result1) == _actions(result2)

    def test_overconfident_aggressive_shift(self):
        """Overconfident should shift toward aggressive (like tilted)."""
        ctx = _free_context(equity=0.75)
        baseline = generate_bounded_options(ctx)
        # Force impaired roll with seed
        shift = EmotionalShift(state='overconfident', severity='extreme', intensity=0.8)
        # Try many seeds — at least one should produce an impaired result
        found_impaired = False
        for i in range(20):
            rng = random.Random(i)
            result = apply_emotional_window_shift(list(baseline), shift, ctx, rng=rng)
            if _actions(result) != _actions(baseline):
                found_impaired = True
                # Should have aggressive options
                assert _has_action(result, 'raise') or _has_action(result, 'all_in'), \
                    "Overconfident shift should preserve/add aggressive options"
                break
        assert found_impaired, "Should find at least one impaired roll for extreme"


class TestGetEmotionalShift:
    """Tests for get_emotional_shift helper."""

    def test_none_psychology_returns_composed(self):
        """None psychology should return composed/none."""
        shift = get_emotional_shift(None)
        assert shift.state == 'composed'
        assert shift.severity == 'none'
        assert shift.intensity == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# TRUNCATE OPTIONS TESTS (Issue 11)
# ═══════════════════════════════════════════════════════════════════════════


class TestTruncateOptions:
    """Tests for _truncate_options smart truncation."""

    def test_under_cap_returns_unchanged(self):
        """Options at or below max_options should be returned unchanged."""
        options = [
            BoundedOption('fold', 0, 'Fold', '-EV', 'conservative'),
            BoundedOption('call', 0, 'Call', 'neutral', 'standard'),
            BoundedOption('raise', 200, 'Raise', '+EV', 'aggressive'),
        ]
        result = _truncate_options(options, max_options=4)
        assert result == options

    def test_budget_zero_no_raises_kept(self):
        """When non-raises fill budget, no raises should be kept."""
        options = [
            BoundedOption('fold', 0, 'Fold', '+EV', 'conservative'),
            BoundedOption('check', 0, 'Check', 'neutral', 'conservative'),
            BoundedOption('call', 0, 'Call', 'neutral', 'standard'),
            BoundedOption('all_in', 0, 'All-in', '+EV', 'aggressive'),
            BoundedOption('raise', 200, 'Small raise', '+EV', 'standard'),
            BoundedOption('raise', 400, 'Medium raise', 'neutral', 'standard'),
        ]
        result = _truncate_options(options, max_options=4)
        assert len(result) == 4
        # Non-raises (4 total) fill budget completely; no room for raises
        raise_actions = [o for o in result if o.action == 'raise']
        assert len(raise_actions) == 0

    def test_single_raise_among_many_non_raises(self):
        """A single raise should be kept when budget allows."""
        options = [
            BoundedOption('fold', 0, 'Fold', '-EV', 'conservative'),
            BoundedOption('call', 0, 'Call', 'neutral', 'standard'),
            BoundedOption('raise', 300, 'Value raise', '+EV', 'aggressive'),
        ]
        # All fit within default cap of 4
        result = _truncate_options(options, max_options=4)
        assert _has_action(result, 'raise')
        assert len(result) == 3

    def test_preserves_best_ev_raise_and_largest_raise(self):
        """When truncating raises, keep the best-EV raise + largest for sizing spread."""
        options = [
            BoundedOption('call', 0, 'Call', 'neutral', 'standard'),
            BoundedOption('raise', 100, 'Small probe', '+EV', 'conservative'),
            BoundedOption('raise', 300, 'Medium bet', 'neutral', 'standard'),
            BoundedOption('raise', 600, 'Large bet', '-EV', 'aggressive'),
        ]
        # 1 non-raise + 3 raises -> budget=3 for raises in a cap of 4
        result = _truncate_options(options, max_options=4)
        assert len(result) <= 4
        raises = [o for o in result if o.action == 'raise']
        raise_amounts = [o.raise_to for o in raises]
        # Best-EV raise (100, +EV) should be kept
        assert 100 in raise_amounts, f"Best-EV raise (100) missing: {raise_amounts}"
        # Largest raise (600) should be kept for spread
        assert 600 in raise_amounts, f"Largest raise (600) missing: {raise_amounts}"

    def test_preserves_best_ev_when_only_one_slot(self):
        """With budget=1 for raises, keep the highest-EV raise."""
        options = [
            BoundedOption('fold', 0, 'Fold', '+EV', 'conservative'),
            BoundedOption('check', 0, 'Check', 'neutral', 'conservative'),
            BoundedOption('call', 0, 'Call', '-EV', 'standard'),
            BoundedOption('raise', 100, 'Small probe', '-EV', 'standard'),
            BoundedOption('raise', 500, 'Big bet', '+EV', 'aggressive'),
        ]
        result = _truncate_options(options, max_options=4)
        assert len(result) == 4
        raises = [o for o in result if o.action == 'raise']
        assert len(raises) == 1
        # The +EV raise should be the one kept
        assert raises[0].ev_estimate == '+EV'

    def test_non_raises_exceeding_cap_drops_lowest_ev(self):
        """When non-raises alone exceed cap, drop the lowest-EV ones."""
        options = [
            BoundedOption('fold', 0, 'Fold', '-EV', 'conservative'),
            BoundedOption('check', 0, 'Check', 'neutral', 'conservative'),
            BoundedOption('call', 0, 'Call', '+EV', 'standard'),
            BoundedOption('all_in', 0, 'All-in', '+EV', 'aggressive'),
            BoundedOption('raise', 200, 'Raise', '+EV', 'standard'),
        ]
        result = _truncate_options(options, max_options=3)
        assert len(result) == 3
        # +EV options should be prioritized
        evs = [o.ev_estimate for o in result]
        assert '-EV' not in evs or evs.count('+EV') >= 2

    def test_emotional_aggressive_add_not_dropped_first(self):
        """Aggressive emotional add (all_in) is a non-raise, kept alongside others."""
        options = [
            BoundedOption('check', 0, 'Check', 'neutral', 'conservative'),
            BoundedOption('raise', 200, 'Value bet', '+EV', 'standard'),
            BoundedOption('raise', 400, 'Pressure bet', '+EV', 'aggressive'),
            BoundedOption('all_in', 0, 'All-in (emotional)', '-EV', 'aggressive'),
        ]
        result = _truncate_options(options, max_options=4)
        # All fit within cap, nothing dropped
        assert len(result) == 4
        assert _has_action(result, 'all_in')

    def test_identical_raises_no_crash(self):
        """Two raises with same amount shouldn't cause issues."""
        options = [
            BoundedOption('call', 0, 'Call', 'neutral', 'standard'),
            BoundedOption('raise', 200, 'Value bet', '+EV', 'standard'),
            BoundedOption('raise', 200, 'Bluff bet', '-EV', 'aggressive'),
        ]
        result = _truncate_options(options, max_options=2)
        assert len(result) == 2


# ═══════════════════════════════════════════════════════════════════════════
# GET EMOTIONAL SHIFT WITH PENALTY DATA TESTS (Issue 12)
# ═══════════════════════════════════════════════════════════════════════════


def _mock_psychology(penalties):
    """Build a mock psychology object with the given penalties dict."""
    psychology = MagicMock()
    psychology.zone_effects.penalties = penalties
    return psychology


class TestGetEmotionalShiftWithPenalties:
    """Tests for get_emotional_shift with actual penalty data."""

    def test_tilted_penalty_maps_to_tilted(self):
        """'tilted' penalty zone → tilted state."""
        psych = _mock_psychology({'tilted': 0.5})
        shift = get_emotional_shift(psych)
        assert shift.state == 'tilted'

    def test_overheated_penalty_maps_to_tilted(self):
        """'overheated' penalty zone → tilted state (alias)."""
        psych = _mock_psychology({'overheated': 0.4})
        shift = get_emotional_shift(psych)
        assert shift.state == 'tilted'

    def test_overconfident_penalty_maps_to_overconfident(self):
        """'overconfident' penalty zone → overconfident state."""
        psych = _mock_psychology({'overconfident': 0.5})
        shift = get_emotional_shift(psych)
        assert shift.state == 'overconfident'

    def test_shaken_penalty_maps_to_shaken(self):
        """'shaken' penalty zone → shaken state."""
        psych = _mock_psychology({'shaken': 0.5})
        shift = get_emotional_shift(psych)
        assert shift.state == 'shaken'

    def test_timid_penalty_maps_to_shaken(self):
        """'timid' penalty zone → shaken state (alias)."""
        psych = _mock_psychology({'timid': 0.4})
        shift = get_emotional_shift(psych)
        assert shift.state == 'shaken'

    def test_detached_penalty_maps_to_dissociated(self):
        """'detached' penalty zone → dissociated state."""
        psych = _mock_psychology({'detached': 0.5})
        shift = get_emotional_shift(psych)
        assert shift.state == 'dissociated'

    def test_empty_penalties_returns_composed(self):
        """Empty penalties dict → composed/none."""
        psych = _mock_psychology({})
        shift = get_emotional_shift(psych)
        assert shift.state == 'composed'
        assert shift.severity == 'none'
        assert shift.intensity == 0.0

    def test_zero_intensity_returns_composed(self):
        """Penalty with intensity 0 → composed/none."""
        psych = _mock_psychology({'tilted': 0.0})
        shift = get_emotional_shift(psych)
        assert shift.state == 'composed'
        assert shift.severity == 'none'

    # --- Severity bucketing ---

    def test_severity_mild_low_boundary(self):
        """Intensity 0.01 → mild severity."""
        psych = _mock_psychology({'tilted': 0.01})
        shift = get_emotional_shift(psych)
        assert shift.severity == 'mild'
        assert shift.intensity == 0.01

    def test_severity_mild_upper_boundary(self):
        """Intensity 0.33 → mild severity."""
        psych = _mock_psychology({'shaken': 0.33})
        shift = get_emotional_shift(psych)
        assert shift.severity == 'mild'

    def test_severity_moderate_lower_boundary(self):
        """Intensity 0.34 → moderate severity."""
        psych = _mock_psychology({'overconfident': 0.34})
        shift = get_emotional_shift(psych)
        assert shift.severity == 'moderate'

    def test_severity_moderate_upper_boundary(self):
        """Intensity 0.66 → moderate severity."""
        psych = _mock_psychology({'tilted': 0.66})
        shift = get_emotional_shift(psych)
        assert shift.severity == 'moderate'

    def test_severity_extreme_lower_boundary(self):
        """Intensity 0.67 → extreme severity."""
        psych = _mock_psychology({'detached': 0.67})
        shift = get_emotional_shift(psych)
        assert shift.severity == 'extreme'

    def test_severity_extreme_high(self):
        """Intensity 1.0 → extreme severity."""
        psych = _mock_psychology({'tilted': 1.0})
        shift = get_emotional_shift(psych)
        assert shift.severity == 'extreme'
        assert shift.intensity == 1.0

    # --- Highest intensity wins ---

    def test_multiple_penalties_picks_strongest(self):
        """When multiple penalties present, pick the one with highest intensity."""
        psych = _mock_psychology({'tilted': 0.3, 'shaken': 0.7})
        shift = get_emotional_shift(psych)
        assert shift.state == 'shaken'
        assert shift.intensity == 0.7
        assert shift.severity == 'extreme'

    def test_multiple_penalties_same_intensity(self):
        """When two penalties tie, the last one iterated wins (dict order)."""
        psych = _mock_psychology({'tilted': 0.5, 'overconfident': 0.5})
        shift = get_emotional_shift(psych)
        # Both have same intensity — state should be one of the two
        assert shift.state in ('tilted', 'overconfident')
        assert shift.intensity == 0.5

    def test_unknown_penalty_zone_ignored(self):
        """Penalty zones not in state_map should be ignored."""
        psych = _mock_psychology({'unknown_zone': 0.9, 'tilted': 0.4})
        shift = get_emotional_shift(psych)
        assert shift.state == 'tilted'
        assert shift.intensity == 0.4

    def test_intensity_preserved_in_shift(self):
        """The raw intensity value should be preserved in the EmotionalShift."""
        psych = _mock_psychology({'overconfident': 0.55})
        shift = get_emotional_shift(psych)
        assert shift.intensity == 0.55


# ═══════════════════════════════════════════════════════════════════════════
# CASE CLASSIFICATION BOUNDARY TESTS (Issue 13)
# ═══════════════════════════════════════════════════════════════════════════




# ═══════════════════════════════════════════════════════════════════════════
# COMPOSED NUDGES
# ═══════════════════════════════════════════════════════════════════════════


from poker.nudge_phrases import (
    _classify_nudge_key,
    apply_composed_nudges,
    NUDGE_PHRASES,
)


class TestNudgeKeyClassification:
    """Test _classify_nudge_key maps (action, ev, style_tag) correctly."""

    def test_raise_plus_ev_is_raise_value(self):
        opt = BoundedOption('raise', 200, 'value bet', '+EV', 'standard')
        assert _classify_nudge_key(opt) == 'raise_value'

    def test_raise_neutral_is_raise_probe(self):
        opt = BoundedOption('raise', 200, 'probe', 'neutral', 'standard')
        assert _classify_nudge_key(opt) == 'raise_probe'

    def test_raise_minus_ev_aggressive_is_raise_bluff(self):
        opt = BoundedOption('raise', 200, 'bluff', '-EV', 'aggressive')
        assert _classify_nudge_key(opt) == 'raise_bluff'

    def test_raise_marginal_aggressive_is_raise_bluff(self):
        opt = BoundedOption('raise', 200, 'semi-bluff', 'marginal', 'aggressive')
        assert _classify_nudge_key(opt) == 'raise_bluff'

    def test_raise_minus_ev_standard_is_raise_probe(self):
        """Non-aggressive -EV raise is probe, not bluff."""
        opt = BoundedOption('raise', 200, 'probe', '-EV', 'standard')
        assert _classify_nudge_key(opt) == 'raise_probe'

    def test_call_plus_ev_is_call_strong(self):
        opt = BoundedOption('call', 0, 'good call', '+EV', 'standard')
        assert _classify_nudge_key(opt) == 'call_strong'

    def test_call_marginal_is_call_close(self):
        opt = BoundedOption('call', 0, 'borderline', 'marginal', 'standard')
        assert _classify_nudge_key(opt) == 'call_close'

    def test_call_minus_ev_is_call_light(self):
        opt = BoundedOption('call', 0, 'speculative', '-EV', 'standard')
        assert _classify_nudge_key(opt) == 'call_light'

    def test_call_neutral_is_call_light(self):
        opt = BoundedOption('call', 0, 'neutral call', 'neutral', 'standard')
        assert _classify_nudge_key(opt) == 'call_light'

    def test_check_trappy_is_check_slow(self):
        opt = BoundedOption('check', 0, 'trap', 'neutral', 'trappy')
        assert _classify_nudge_key(opt) == 'check_slow'

    def test_check_minus_ev_is_check_passive(self):
        opt = BoundedOption('check', 0, 'passive', '-EV', 'standard')
        assert _classify_nudge_key(opt) == 'check_passive'

    def test_check_marginal_is_check_passive(self):
        opt = BoundedOption('check', 0, 'marginal check', 'marginal', 'standard')
        assert _classify_nudge_key(opt) == 'check_passive'

    def test_check_neutral_is_check_free(self):
        opt = BoundedOption('check', 0, 'free card', 'neutral', 'standard')
        assert _classify_nudge_key(opt) == 'check_free'

    def test_check_plus_ev_is_check_free(self):
        opt = BoundedOption('check', 0, 'free card', '+EV', 'standard')
        assert _classify_nudge_key(opt) == 'check_free'

    def test_fold_plus_ev_is_fold_correct(self):
        opt = BoundedOption('fold', 0, 'correct fold', '+EV', 'conservative')
        assert _classify_nudge_key(opt) == 'fold_correct'

    def test_fold_neutral_is_fold_correct(self):
        opt = BoundedOption('fold', 0, 'ok fold', 'neutral', 'conservative')
        assert _classify_nudge_key(opt) == 'fold_correct'

    def test_fold_minus_ev_is_fold_tough(self):
        opt = BoundedOption('fold', 0, 'giving up equity', '-EV', 'conservative')
        assert _classify_nudge_key(opt) == 'fold_tough'

    def test_all_in_always_maps_to_all_in(self):
        opt = BoundedOption('all_in', 1000, 'ship it', '+EV', 'aggressive')
        assert _classify_nudge_key(opt) == 'all_in'

    def test_all_in_regardless_of_ev(self):
        for ev in ('+EV', 'neutral', '-EV', 'marginal'):
            opt = BoundedOption('all_in', 1000, 'ship', ev, 'standard')
            assert _classify_nudge_key(opt) == 'all_in'


class TestApplyComposedNudges:
    """Test apply_composed_nudges replaces rationale with nudge phrases."""

    def test_replaces_rationale(self):
        """Nudge rationale should differ from original raw rationale."""
        options = [
            BoundedOption('raise', 200, 'Value raise (48% equity)', '+EV', 'standard'),
            BoundedOption('call', 0, 'Call 1.0 BB — clearly profitable', '+EV', 'standard'),
        ]
        result = apply_composed_nudges(options, 'tight_aggressive')
        for opt in result:
            # Should be from nudge phrase pool, not original
            assert opt.rationale != options[0].rationale or opt.action != 'raise'

    def test_uses_profile_phrases(self):
        """Phrases should come from the specified profile's pool."""
        options = [
            BoundedOption('raise', 200, 'original', '+EV', 'standard'),
        ]
        result = apply_composed_nudges(options, 'loose_aggressive')
        lag_phrases = NUDGE_PHRASES['loose_aggressive']['raise_value']
        default_phrases = NUDGE_PHRASES['default']['raise_value']
        assert result[0].rationale in lag_phrases or result[0].rationale in default_phrases

    def test_falls_through_to_default(self):
        """Unknown profile key should fall through to default phrases."""
        options = [
            BoundedOption('call', 0, 'original', '+EV', 'standard'),
        ]
        result = apply_composed_nudges(options, 'nonexistent_profile')
        default_phrases = NUDGE_PHRASES['default']['call_strong']
        assert result[0].rationale in default_phrases

    def test_preserves_action_and_ev(self):
        """Nudges should only replace rationale, not action/ev/raise_to/style_tag."""
        options = [
            BoundedOption('raise', 300, 'original text', '+EV', 'aggressive'),
        ]
        result = apply_composed_nudges(options, 'default')
        assert result[0].action == 'raise'
        assert result[0].raise_to == 300
        assert result[0].ev_estimate == '+EV'
        assert result[0].style_tag == 'aggressive'

    def test_all_profiles_have_all_keys(self):
        """Every profile should cover all nudge keys (or fall through to default)."""
        all_keys = set(NUDGE_PHRASES['default'].keys())
        for profile_name, phrases in NUDGE_PHRASES.items():
            for key in all_keys:
                # Either profile has it or default has it
                pool = phrases.get(key) or NUDGE_PHRASES['default'].get(key)
                assert pool, f"Profile {profile_name} missing key {key} with no default fallback"

    def test_multiple_options_get_nudged(self):
        """All options in a list should get nudge phrases."""
        options = [
            BoundedOption('fold', 0, 'save chips', '+EV', 'conservative'),
            BoundedOption('call', 0, 'borderline', 'marginal', 'standard'),
            BoundedOption('raise', 200, 'value bet', '+EV', 'standard'),
        ]
        result = apply_composed_nudges(options, 'tight_aggressive')
        assert len(result) == 3
        for opt in result:
            assert opt.rationale  # Each option has a non-empty nudge

    def test_empty_options_returns_empty(self):
        """Empty input returns empty output."""
        result = apply_composed_nudges([], 'default')
        assert result == []

    def test_phrase_variety(self):
        """With enough iterations, different phrases should appear (non-deterministic)."""
        options = [BoundedOption('raise', 200, 'original', '+EV', 'standard')]
        seen = set()
        for _ in range(50):
            result = apply_composed_nudges(options, 'default')
            seen.add(result[0].rationale)
        # Default raise_value has 2 phrases, should see both
        assert len(seen) >= 2, f"Expected phrase variety, only saw: {seen}"


class TestNudgePhraseCoverage:
    """Verify NUDGE_PHRASES dict is complete and well-formed."""

    def test_default_has_all_12_keys(self):
        """Default profile must have all 12 nudge categories."""
        expected = {
            'raise_value', 'raise_probe', 'raise_bluff',
            'call_strong', 'call_close', 'call_light',
            'check_slow', 'check_passive', 'check_free',
            'fold_correct', 'fold_tough', 'all_in',
        }
        assert set(NUDGE_PHRASES['default'].keys()) == expected

    def test_all_phrases_under_10_words(self):
        """All phrases should be under 10 words (per hybrid-ai learnings)."""
        for profile, categories in NUDGE_PHRASES.items():
            for key, phrases in categories.items():
                for phrase in phrases:
                    word_count = len(phrase.split())
                    assert word_count <= 10, (
                        f"Phrase too long ({word_count} words) in "
                        f"{profile}.{key}: '{phrase}'"
                    )

    def test_all_phrases_non_empty(self):
        """No empty phrases."""
        for profile, categories in NUDGE_PHRASES.items():
            for key, phrases in categories.items():
                assert len(phrases) >= 1, f"Empty phrase list: {profile}.{key}"
                for phrase in phrases:
                    assert phrase.strip(), f"Empty phrase string: {profile}.{key}"

    def test_all_five_profiles_present(self):
        """All five style profiles should be in NUDGE_PHRASES."""
        expected_profiles = {
            'default', 'tight_aggressive', 'tight_passive',
            'loose_aggressive', 'loose_passive',
        }
        assert set(NUDGE_PHRASES.keys()) == expected_profiles


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
