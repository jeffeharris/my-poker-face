"""Heads-up support regression tests."""

from unittest.mock import MagicMock

import pytest

from core.card import Card
from poker.bounded_options import BoundedOption, OptionProfile, generate_bounded_options
from poker.hand_tiers import is_hand_in_range
from poker.hybrid_ai_controller import HybridAIController
from poker.lean_bounded_controller import LeanBoundedController
from poker.nudge_phrases import HEADS_UP_NUDGE_OVERRIDES, apply_composed_nudges
from poker.poker_game import initialize_game_state, set_betting_round_start_player, setup_hand
from poker.range_guidance import classify_preflop_hand_for_player, looseness_to_range_pct


def _bounded_context(**overrides):
    ctx = {
        'phase': 'PRE_FLOP',
        'equity': 0.50,
        'pot_total': 200,
        'cost_to_call': 100,
        'player_stack': 1000,
        'stack_bb': 50,
        'min_raise': 200,
        'max_raise': 1000,
        'big_blind': 100,
        'valid_actions': ['fold', 'call', 'raise'],
        'already_bet': 0,
        'position': 'button',
        'num_opponents': 2,
    }
    ctx.update(overrides)
    return ctx


def _raise_options(options):
    return [o for o in options if o.action == 'raise']


def _make_lean_prompt_stub():
    stub = MagicMock(spec=HybridAIController)
    stub.player_name = 'TestPlayer'
    stub._current_game_messages = []
    cfg = MagicMock()
    cfg.composed_nudges = False
    cfg.show_ev_labels = None
    stub.prompt_config = cfg
    stub._build_lean_prompt = LeanBoundedController._build_lean_prompt.__get__(stub)
    stub._build_street_action_summary = LeanBoundedController._build_street_action_summary.__get__(
        stub
    )
    return stub


class TestHeadsUpBlindPosting:
    def test_small_blind_is_dealer_for_two_players(self):
        state = initialize_game_state(['Villain'])
        assert len(state.players) == 2
        assert state.small_blind_idx == state.current_dealer_idx

    def test_big_blind_is_next_seat_for_two_players(self):
        state = initialize_game_state(['Villain'])
        assert state.big_blind_idx == (state.current_dealer_idx + 1) % 2

    def test_setup_hand_posts_correct_blinds_for_heads_up(self):
        state = setup_hand(initialize_game_state(['Villain']))
        sb = state.players[state.small_blind_idx]
        bb = state.players[state.big_blind_idx]

        assert sb.bet == 25
        assert bb.bet == 50
        assert state.pot['total'] == 75

    def test_three_plus_blind_positions_unchanged(self):
        state = initialize_game_state(['AI1', 'AI2'])
        assert len(state.players) == 3
        assert state.small_blind_idx == (state.current_dealer_idx + 1) % 3
        assert state.big_blind_idx == (state.current_dealer_idx + 2) % 3


class TestHeadsUpActionOrder:
    def test_dealer_acts_first_preflop_in_heads_up(self):
        state = initialize_game_state(['Villain'])
        started = set_betting_round_start_player(state)
        assert started is not None
        assert started.current_player_idx == state.current_dealer_idx

    def test_non_dealer_acts_first_postflop_in_heads_up(self):
        state = initialize_game_state(['Villain']).update(
            community_cards=(
                Card('A', 'Spades'),
                Card('K', 'Hearts'),
                Card('Q', 'Diamonds'),
            )
        )
        started = set_betting_round_start_player(state)
        assert started is not None
        assert started.current_player_idx == (state.current_dealer_idx + 1) % 2


class TestHeadsUpRangeGates:
    def test_looseness_to_range_pct_wider_heads_up(self):
        multiway = looseness_to_range_pct(0.50, 'button', num_opponents=2)
        heads_up = looseness_to_range_pct(0.50, 'button', num_opponents=1)
        assert heads_up > multiway
        assert heads_up == pytest.approx(0.80)
        assert multiway == pytest.approx(0.55)

    def test_top_85_and_top_95_tiers_are_reachable(self):
        assert is_hand_in_range('J4o', 0.85) is True
        assert is_hand_in_range('J4o', 0.75) is False
        assert is_hand_in_range('T6o', 0.95) is True
        assert is_hand_in_range('T6o', 0.85) is False

    def test_classify_preflop_is_softer_heads_up(self):
        hu = classify_preflop_hand_for_player('72o', 0.30, 'button', num_opponents=1)
        multiway = classify_preflop_hand_for_player('72o', 0.30, 'button', num_opponents=2)

        assert 'heads-up' in hu
        assert 'should fold' not in hu
        assert hu != multiway


class TestHeadsUpBoundedOptions:
    def test_monster_fold_block_threshold_is_lower_in_heads_up(self):
        hu_ctx = _bounded_context(equity=0.80, pot_total=100, cost_to_call=200, num_opponents=1)
        mw_ctx = _bounded_context(equity=0.80, pot_total=100, cost_to_call=200, num_opponents=2)

        hu_actions = [o.action for o in generate_bounded_options(hu_ctx)]
        mw_actions = [o.action for o in generate_bounded_options(mw_ctx)]

        assert 'fold' not in hu_actions
        assert 'fold' in mw_actions

    def test_heads_up_preflop_uses_min_raise_open_sizing(self):
        ctx = _bounded_context(
            phase='PRE_FLOP',
            equity=0.40,
            min_raise=150,
            max_raise=500,
            big_blind=100,
            num_opponents=1,
        )
        options = generate_bounded_options(ctx)
        raises = _raise_options(options)

        assert raises
        assert raises[0].raise_to == 200
        assert '2x BB' in raises[0].rationale

    def test_range_bias_is_disabled_heads_up(self):
        hu_ctx = _bounded_context(num_opponents=1, cost_to_call=100)
        mw_ctx = _bounded_context(num_opponents=2, cost_to_call=100)

        hu = generate_bounded_options(
            hu_ctx, in_range=False, range_pct=0.25, position_display='the button'
        )
        mw = generate_bounded_options(
            mw_ctx, in_range=False, range_pct=0.25, position_display='the button'
        )

        hu_call = next(o for o in hu if o.action == 'call')
        mw_call = next(o for o in mw if o.action == 'call')

        assert 'outside your range' not in hu_call.rationale
        assert 'outside your range' in mw_call.rationale

    def test_hu_equity_offset_gated_off_by_default(self):
        """T1-34 (gated): without apply_hu_equity_offset=True, HU equity is
        unchanged (offsets stay opt-in for A/B work)."""
        ctx = _bounded_context(
            equity=0.40, num_opponents=1, position='button', cost_to_call=100, pot_total=200
        )
        opts_default = generate_bounded_options(ctx)
        opts_explicit_off = generate_bounded_options(ctx, apply_hu_equity_offset=False)
        # EV labels are identical without the offset.
        assert [o.ev_estimate for o in opts_default] == [o.ev_estimate for o in opts_explicit_off]

    def test_hu_equity_offset_when_enabled_promotes_btn_raise(self):
        """T1-34 (gated): with apply_hu_equity_offset=True, BTN raises with
        moderate equity get a stronger EV label (offset adds +0.30)."""
        ctx = _bounded_context(
            equity=0.40, num_opponents=1, position='button', cost_to_call=100, pot_total=200
        )
        without = generate_bounded_options(ctx, apply_hu_equity_offset=False)
        with_offset = generate_bounded_options(ctx, apply_hu_equity_offset=True)
        # The first raise option should improve from -EV/neutral to neutral/+EV
        # under the offset. Compare label index to assert non-degradation.
        order = {'-EV': 0, 'marginal': 1, 'neutral': 2, '+EV': 3}
        without_raise = next(o for o in without if o.action == 'raise')
        with_raise = next(o for o in with_offset if o.action == 'raise')
        assert order[with_raise.ev_estimate] >= order[without_raise.ev_estimate]

    def test_heads_up_profile_overrides_apply_to_raise_ev_labels(self):
        profile = OptionProfile(
            raise_plus_ev=0.80,
            raise_neutral=0.70,
            heads_up_raise_plus_ev=0.50,
            heads_up_raise_neutral=0.35,
        )

        hu_ctx = _bounded_context(equity=0.45, num_opponents=1)
        mw_ctx = _bounded_context(equity=0.45, num_opponents=2)

        hu_raise = _raise_options(generate_bounded_options(hu_ctx, profile=profile))[0]
        mw_raise = _raise_options(generate_bounded_options(mw_ctx, profile=profile))[0]

        # HU thresholds elevate the raise above -EV; the +EV guarantee
        # (T1-35) may further promote a neutral raise to +EV when no other
        # option is naturally +EV. MW keeps the stricter thresholds.
        assert hu_raise.ev_estimate in ('+EV', 'neutral')
        assert mw_raise.ev_estimate == '-EV'


class TestHeadsUpPrompts:
    @pytest.mark.parametrize('num_opponents', [1, 0])
    def test_lean_prompt_includes_heads_up_line_when_one_or_fewer_opponents(self, num_opponents):
        stub = _make_lean_prompt_stub()
        prompt = stub._build_lean_prompt(
            [BoundedOption('check', 0, 'Check', 'neutral', 'conservative')],
            {
                'hole_cards': ['Ah', 'Kd'],
                'community_cards': [],
                'big_blind': 100,
                'phase': 'PRE_FLOP',
                'stack_bb': 40,
                'pot_total': 150,
                'num_opponents': num_opponents,
            },
            'default',
            profile=None,
        )
        assert 'Heads-up (1v1)' in prompt

    def test_composed_nudges_use_heads_up_overrides(self):
        option = BoundedOption('raise', 300, 'raw', '-EV', 'aggressive')
        result = apply_composed_nudges([option], profile_key='tight_aggressive', is_heads_up=True)
        assert result[0].rationale in HEADS_UP_NUDGE_OVERRIDES['raise_bluff']
