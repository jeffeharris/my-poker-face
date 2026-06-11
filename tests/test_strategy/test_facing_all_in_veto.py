"""Facing-an-all-in preflop equity veto (TieredBotController).

Regression for the prod "Alexander jams 47o / Midas jams 89o into a 4-bet
all-in" bug: the chart's coarse vs_4bet stub assigned the same continue/jam
distribution to every non-premium hand, so trash could be sampled into a
shove. Facing a cold all-in the controller now decides call/fold purely on
pot odds and never voluntarily re-jams.
"""

import random
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.card import Card
from poker.strategy.nodes import PreflopNode
from poker.strategy.strategy_profile import StrategyProfile
from poker.strategy.strategy_table import StrategyTable
from poker.tiered_bot_controller import TieredBotController


def _table():
    # Minimal table — facing-all-in nodes miss and fall to the conservative
    # default; the veto overrides before that matters.
    return StrategyTable(
        {
            PreflopNode(
                hand='AA', position='UTG', scenario='rfi', opener_position=''
            ).key: StrategyProfile(action_probabilities={'raise_2.5bb': 1.0}),
        }
    )


def _state_machine(game_state, phase_name='PRE_FLOP'):
    phase = SimpleNamespace(name=phase_name)
    return SimpleNamespace(game_state=game_state, current_phase=phase, phase=phase)


def _facing_all_in_state(hero_hand):
    """6-max preflop state where hero faces a cold all-in.

    Mirrors prod hand 38: hero invested 7,000, a covered opponent shoved
    47,400 (all-in), one more live opponent, the rest folded. call_amount /
    pot give a ~0.44 required equity — trash folds, premiums call.
    """
    hero_bet = 7000
    allin_amount = 47400
    # Pot = the live bets (shover 47,400 + hero 7,000 + live opp 7,000); with a
    # 40,400 call that's a ~0.40 required equity — trash folds, premiums call.
    pot_total = 61400

    players = [
        SimpleNamespace(
            name='TestBot',
            stack=99990,
            bet=hero_bet,
            hand=hero_hand,
            is_human=False,
            is_folded=False,
            is_all_in=False,
            has_acted=True,
            last_action='raise',
        ),
        SimpleNamespace(
            name='Shover',
            stack=0,
            bet=allin_amount,
            hand=(Card('A', 'c'), Card('A', 'd')),
            is_human=False,
            is_folded=False,
            is_all_in=True,
            has_acted=True,
            last_action='all_in',
        ),
        SimpleNamespace(
            name='LiveOpp',
            stack=80000,
            bet=hero_bet,
            hand=(Card('Q', 'h'), Card('7', 'c')),
            is_human=False,
            is_folded=False,
            is_all_in=False,
            has_acted=True,
            last_action='call',
        ),
    ]
    for i in range(3):  # folded fillers → 6 seats
        players.append(
            SimpleNamespace(
                name=f'Folded{i}',
                stack=50000,
                bet=0,
                hand=(),
                is_human=False,
                is_folded=True,
                is_all_in=False,
                has_acted=True,
                last_action='fold',
            )
        )

    return SimpleNamespace(
        players=players,
        current_player_idx=0,
        current_player=players[0],
        current_ante=1000,
        highest_bet=allin_amount,
        last_raise_amount=1000,
        min_raise_amount=1000,
        raises_this_round=3,  # vs_4bet
        community_cards=(),
        pot={'total': pot_total},
        call_amount=allin_amount - hero_bet,
        table_positions={
            'button': 'LiveOpp',
            'small_blind_player': 'TestBot',
            'big_blind_player': 'Shover',
            'under_the_gun': 'Folded0',
            'middle_position_1': 'Folded1',
            'cutoff': 'Folded2',
        },
        current_player_options=['fold', 'call', 'all_in'],
    )


@patch('poker.tiered_bot_controller.AIPlayerController.__init__', return_value=None)
def _controller(mock_init, game_state):
    c = TieredBotController.__new__(TieredBotController)
    c.player_name = 'TestBot'
    c.state_machine = _state_machine(game_state)
    c.strategy_table = _table()
    c.hu_strategy_table = None
    c.debug_logging = False
    c.rng = random.Random(42)
    c._deviation_profile = None
    c.psychology = None
    c.prompt_config = SimpleNamespace(strategic_reflection=False)
    c._current_hand_plans = []
    c._hand_max_bluff_likelihood = 0
    return c


@pytest.mark.parametrize(
    'hand',
    [
        (Card('4', 'd'), Card('7', 's')),  # 47o — the Alexander shove
        (Card('9', 'c'), Card('8', 's')),  # 89o — the Midas shove
        (Card('7', 'h'), Card('2', 'c')),  # 72o — worst hand
    ],
)
def test_trash_folds_facing_all_in(hand):
    gs = _facing_all_in_state(hand)
    c = _controller(game_state=gs)
    result = c._get_ai_decision(
        message='',
        valid_actions=['fold', 'call', 'all_in'],
        call_amount=40400,
    )
    assert result['action'] == 'fold'
    assert c._last_pipeline_snapshot.get('facing_all_in_veto') is True
    # Never a voluntary jam with trash facing a shove.
    assert result['action'] != 'all_in'


def test_premium_calls_facing_all_in():
    gs = _facing_all_in_state((Card('A', 'h'), Card('A', 's')))
    c = _controller(game_state=gs)
    result = c._get_ai_decision(
        message='',
        valid_actions=['fold', 'call', 'all_in'],
        call_amount=40400,
    )
    # AA clears the pot-odds bar → continues (covered, so flat call — not a
    # voluntary over-jam).
    assert result['action'] == 'call'
    assert c._last_pipeline_snapshot.get('facing_all_in_veto') is True


def test_calling_the_shove_all_in_resolves_to_all_in():
    """When calling the shove commits hero's whole stack (call illegal, only
    all_in legal), the continue resolves to all_in — jam == call there."""
    gs = _facing_all_in_state((Card('A', 'h'), Card('A', 's')))
    # Hero is now the short stack: calling 40,400 puts the last chips in.
    gs.players[0].stack = 40400
    gs.current_player_options = ['fold', 'all_in']
    c = _controller(game_state=gs)
    result = c._get_ai_decision(
        message='',
        valid_actions=['fold', 'all_in'],
        call_amount=40400,
    )
    assert result['action'] == 'all_in'
    assert c._last_pipeline_snapshot.get('facing_all_in_veto') is True


def test_no_veto_when_not_facing_all_in():
    """An ordinary open (no all-in in front) keeps the normal chart path."""
    gs = _facing_all_in_state((Card('A', 'h'), Card('A', 's')))
    # Demote the shover to a live raiser — no one is all-in now.
    gs.players[1].stack = 50000
    gs.players[1].is_all_in = False
    c = _controller(game_state=gs)
    c._get_ai_decision(
        message='',
        valid_actions=['fold', 'call', 'raise', 'all_in'],
        call_amount=40400,
    )
    assert c._last_pipeline_snapshot.get('facing_all_in_veto') is not True
