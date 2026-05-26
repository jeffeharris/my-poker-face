"""Tests for HU preflop chart routing in TieredBotController.

Phase 7: when len(game_state.players) == 2, preflop lookups go to
hu_strategy_table; otherwise they go to the 6-max strategy_table. Routing
must use SEATED count (len(players)) not active count, so 6-max spots
that have collapsed to 2 non-folded players still use the 6-max chart.
"""

import random
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from poker.strategy.nodes import PreflopNode
from poker.strategy.strategy_profile import StrategyProfile
from poker.strategy.strategy_table import StrategyTable
from poker.tiered_bot_controller import TieredBotController

# ── Fixtures ─────────────────────────────────────────────────────────────

# Sentinel actions let tests distinguish which table was hit.
_HU_OPEN = 'raise_3bb'
_SIXMAX_OPEN = 'raise_2.5bb'


def _make_hu_table():
    """HU table: SB opens AA with raise_3bb. Distinct action from 6-max."""
    data = {
        PreflopNode(
            hand='AA', position='SB', scenario='rfi', opener_position=''
        ).key: StrategyProfile(action_probabilities={_HU_OPEN: 1.0}),
        PreflopNode(
            hand='72o', position='SB', scenario='rfi', opener_position=''
        ).key: StrategyProfile(
            action_probabilities={_HU_OPEN: 1.0}
        ),  # HU SB still opens 72o sometimes; for routing test we don't care
    }
    return StrategyTable(data)


def _make_sixmax_table():
    """6-max table: AA opens with raise_2.5bb. Distinct action from HU."""
    data = {
        # SB rfi from 6-max (this is the entry HU would hit if routing is broken)
        PreflopNode(
            hand='AA', position='SB', scenario='rfi', opener_position=''
        ).key: StrategyProfile(action_probabilities={_SIXMAX_OPEN: 1.0}),
        # UTG rfi for 6-max-only test cases
        PreflopNode(
            hand='AA', position='UTG', scenario='rfi', opener_position=''
        ).key: StrategyProfile(action_probabilities={_SIXMAX_OPEN: 1.0}),
    }
    return StrategyTable(data)


def _make_game_state(
    num_players: int, num_folded: int = 0, position_key: str = 'small_blind_player'
):
    """Build a minimal game state with the test player at position_key.

    num_players: total seated (this controls the HU routing gate)
    num_folded: how many seated players are folded (irrelevant to routing)
    """
    from core.card import Card

    players = []
    for i in range(num_players):
        name = 'Hero' if i == 0 else f'Opp{i}'
        is_folded = i > 0 and i <= num_folded
        p = SimpleNamespace(
            name=name,
            stack=10000,
            bet=0,
            hand=(Card('A', 'h'), Card('A', 's')),
            is_human=False,
            is_folded=is_folded,
            is_all_in=False,
            has_acted=False,
            last_action='fold' if is_folded else None,
        )
        players.append(p)

    # Build position map sized to num_players. In HU, button == SB (same player).
    if num_players == 2:
        positions = {
            'button': players[0].name,
            'small_blind_player': players[0].name,
            'big_blind_player': players[1].name,
        }
    else:
        # Generic 6-max mapping; Hero takes the requested position_key
        slot_names = [
            'button',
            'small_blind_player',
            'big_blind_player',
            'under_the_gun',
            'middle_position_1',
            'cutoff',
        ][:num_players]
        positions = {}
        for i, slot in enumerate(slot_names):
            positions[slot] = players[i].name
        # Move Hero (players[0]) to requested slot
        if position_key in positions:
            current_holder = positions[position_key]
            for k, v in positions.items():
                if v == 'Hero' and k != position_key:
                    positions[k] = current_holder
                    break
            positions[position_key] = 'Hero'

    # Mark blinds (so raises_this_round=0 is unambiguous "rfi")
    sb_name = positions.get('small_blind_player')
    bb_name = positions.get('big_blind_player')
    for p in players:
        if p.name == sb_name:
            p.bet = 50
        elif p.name == bb_name:
            p.bet = 100

    return SimpleNamespace(
        players=players,
        current_player_idx=0,
        current_player=players[0],
        current_ante=100,
        highest_bet=100,
        last_raise_amount=100,
        # Engine property — kept consistent with last_raise_amount.
        min_raise_amount=100,
        raises_this_round=0,
        community_cards=(),
        pot={'total': 150},
        table_positions=positions,
        current_player_options=['fold', 'call', 'raise', 'all_in'],
    )


def _make_sm(game_state, phase_name='PRE_FLOP'):
    phase = SimpleNamespace(name=phase_name)
    return SimpleNamespace(
        game_state=game_state,
        current_phase=phase,
        phase=phase,
    )


def _make_controller(
    *,
    game_state,
    sixmax_table,
    hu_table,
    phase='PRE_FLOP',
    skip_distortion=True,
):
    """Build a TieredBotController bypassing parent __init__."""
    with patch('poker.tiered_bot_controller.AIPlayerController.__init__', return_value=None):
        controller = TieredBotController.__new__(TieredBotController)
    controller.player_name = 'Hero'
    controller.state_machine = _make_sm(game_state, phase)
    controller.strategy_table = sixmax_table
    controller.hu_strategy_table = hu_table
    controller.debug_logging = False
    controller.rng = random.Random(42)
    controller._deviation_profile = None
    controller.psychology = None
    controller.skip_personality_distortion = skip_distortion
    controller.opponent_model_manager = None
    controller.expression_generator = None
    controller.prompt_config = SimpleNamespace(strategic_reflection=False)
    controller._current_hand_plans = []
    controller._hand_max_bluff_likelihood = 0
    return controller


# ── Tests ────────────────────────────────────────────────────────────────


class TestHURouting:
    """Verify that the HU chart is used iff len(players) == 2."""

    def test_hu_table_used_when_two_seated(self):
        """2-player game with HU chart loaded → uses HU table."""
        gs = _make_game_state(num_players=2)
        controller = _make_controller(
            game_state=gs,
            sixmax_table=_make_sixmax_table(),
            hu_table=_make_hu_table(),
        )
        result = controller._get_ai_decision(
            message='',
            valid_actions=['fold', 'call', 'raise', 'all_in'],
            call_amount=100,
        )
        # HU AA -> raise_3bb. The exact concrete game action is 'raise',
        # but the hand_strategy string carries the abstract action chosen.
        assert (
            _HU_OPEN in result['hand_strategy']
        ), f"Expected HU open action ({_HU_OPEN}) in {result['hand_strategy']!r}"

    def test_sixmax_table_used_when_three_seated(self):
        """3-player game → uses 6-max table even if positions look HU-ish."""
        gs = _make_game_state(num_players=3, position_key='small_blind_player')
        controller = _make_controller(
            game_state=gs,
            sixmax_table=_make_sixmax_table(),
            hu_table=_make_hu_table(),
        )
        result = controller._get_ai_decision(
            message='',
            valid_actions=['fold', 'call', 'raise', 'all_in'],
            call_amount=100,
        )
        assert (
            _SIXMAX_OPEN in result['hand_strategy']
        ), f"Expected 6-max open ({_SIXMAX_OPEN}) in {result['hand_strategy']!r}"

    def test_sixmax_used_when_six_seated_with_four_folds(self):
        """6 seated but 4 folded → still 6-max (gate is seated count, not active).

        This is the case the Codex review flagged: a 6-max hand that has
        collapsed to 2 non-folded players is NOT heads-up — those folded
        players were dealt into the hand and put dead money in.
        """
        gs = _make_game_state(num_players=6, num_folded=4, position_key='small_blind_player')
        controller = _make_controller(
            game_state=gs,
            sixmax_table=_make_sixmax_table(),
            hu_table=_make_hu_table(),
        )
        # Sanity: 2 non-folded players, 6 seated.
        active = sum(1 for p in gs.players if not p.is_folded)
        assert active == 2
        assert len(gs.players) == 6

        result = controller._get_ai_decision(
            message='',
            valid_actions=['fold', 'call', 'raise', 'all_in'],
            call_amount=100,
        )
        assert _SIXMAX_OPEN in result['hand_strategy'], (
            f"6-max collapse-to-2 must use 6-max chart, got " f"{result['hand_strategy']!r}"
        )

    def test_sixmax_used_when_hu_table_missing(self):
        """HU chart not loaded → fall back to 6-max even at 2-handed."""
        gs = _make_game_state(num_players=2)
        controller = _make_controller(
            game_state=gs,
            sixmax_table=_make_sixmax_table(),
            hu_table=None,  # not loaded
        )
        result = controller._get_ai_decision(
            message='',
            valid_actions=['fold', 'call', 'raise', 'all_in'],
            call_amount=100,
        )
        # Falls back to 6-max SB rfi entry
        assert _SIXMAX_OPEN in result['hand_strategy']

    def test_hu_table_distortion_still_applies(self):
        """Personality distortion runs on top of HU base, not bypassed.

        AA is 100% raise in the HU stub. Distortion can't shift that to
        a different action — the gate here is that the pipeline runs to
        completion without crashing AND still emits an HU-flavored action.
        """
        gs = _make_game_state(num_players=2)
        controller = _make_controller(
            game_state=gs,
            sixmax_table=_make_sixmax_table(),
            hu_table=_make_hu_table(),
            skip_distortion=False,
        )
        anchors = SimpleNamespace(
            adaptation_bias=0.5,
            baseline_looseness=0.9,
            baseline_aggression=0.9,
            composure_baseline=0.5,
            risk_identity=0.5,
            expressiveness=0.5,
            baseline_energy=0.5,
            recovery_rate=0.5,
        )
        controller.psychology = SimpleNamespace(
            anchors=anchors,
            emotional_state='composed',
        )
        result = controller._get_ai_decision(
            message='',
            valid_actions=['fold', 'call', 'raise', 'all_in'],
            call_amount=100,
        )
        assert _HU_OPEN in result['hand_strategy']
