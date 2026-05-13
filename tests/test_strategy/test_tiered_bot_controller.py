"""Tests for TieredBotController."""

import json
import random
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from poker.strategy.nodes import PreflopNode
from poker.strategy.strategy_profile import StrategyProfile
from poker.strategy.strategy_table import StrategyTable
from poker.tiered_bot_controller import TieredBotController


# ── Fixtures ─────────────────────────────────────────────────────────────

def _make_strategy_table():
    """Create a minimal strategy table for testing."""
    data = {
        # UTG RFI: AA always raises, 72o always folds
        PreflopNode(hand='AA', position='UTG', scenario='rfi', opener_position='').key:
            StrategyProfile(action_probabilities={'raise_2.5bb': 1.0}),
        PreflopNode(hand='72o', position='UTG', scenario='rfi', opener_position='').key:
            StrategyProfile(action_probabilities={'fold': 1.0}),
        # BTN RFI: AKs mixed
        PreflopNode(hand='AKs', position='BTN', scenario='rfi', opener_position='').key:
            StrategyProfile(action_probabilities={'raise_2.5bb': 0.8, 'fold': 0.2}),
        # BB vs UTG: QQ 3-bets
        PreflopNode(hand='QQ', position='BB', scenario='vs_open', opener_position='UTG').key:
            StrategyProfile(action_probabilities={'raise_3x': 0.6, 'call': 0.4}),
    }
    return StrategyTable(data)


def _make_game_state(
    player_name='TestBot',
    hand=None,
    position_key='under_the_gun',
    raises=0,
    phase_name='PRE_FLOP',
    current_ante=100,
    highest_bet=100,
    num_players=6,
):
    """Create a mock game state for controller testing."""
    from core.card import Card

    # Build players
    players = []
    for i in range(num_players):
        name = player_name if i == 0 else f'Opponent{i}'
        p = SimpleNamespace(
            name=name,
            stack=10000,
            bet=0 if i != 1 else 50,  # SB
            hand=hand or (Card('A', 'h'), Card('A', 's')),
            is_human=False,
            is_folded=False,
            is_all_in=False,
            has_acted=False,
            last_action=None,
        )
        players.append(p)

    # Fix bets for blinds: SB=50, BB=100
    if num_players >= 3:
        players[1].bet = 50   # SB
        players[2].bet = 100  # BB

    # Build position mapping
    positions_6p = {
        'button': players[0].name if position_key == 'button' else players[3].name,
        'small_blind_player': players[1].name,
        'big_blind_player': players[2].name,
        'under_the_gun': players[0].name if position_key == 'under_the_gun' else players[3].name,
        'middle_position_1': players[4].name if num_players > 4 else players[0].name,
        'cutoff': players[5].name if num_players > 5 else players[0].name,
    }

    # Override so test player is in the right position
    if position_key in positions_6p:
        # Find who currently holds this position
        current_holder = positions_6p[position_key]
        # Swap
        for k, v in positions_6p.items():
            if v == player_name and k != position_key:
                positions_6p[k] = current_holder
                break
        positions_6p[position_key] = player_name

    game_state = SimpleNamespace(
        players=players,
        current_player_idx=0,
        current_player=players[0],
        current_ante=current_ante,
        highest_bet=highest_bet,
        last_raise_amount=current_ante,
        raises_this_round=raises,
        community_cards=(),
        pot={'total': 150},
        table_positions=positions_6p,
        current_player_options=['fold', 'call', 'raise', 'all_in'],
    )
    return game_state


def _make_state_machine(game_state, phase_name='PRE_FLOP'):
    """Wrap game_state in a mock state machine."""
    phase = SimpleNamespace(name=phase_name)
    sm = SimpleNamespace(
        game_state=game_state,
        current_phase=phase,
        phase=phase,
    )
    return sm


# ── Tests ────────────────────────────────────────────────────────────────

class TestTieredBotController:
    """Test the full TieredBotController decision pipeline."""

    @patch('poker.tiered_bot_controller.AIPlayerController.__init__', return_value=None)
    def _make_controller(self, mock_init, game_state=None, phase='PRE_FLOP'):
        """Helper to build a controller with mocked parent."""
        gs = game_state or _make_game_state()
        sm = _make_state_machine(gs, phase)
        table = _make_strategy_table()

        controller = TieredBotController.__new__(TieredBotController)
        # Manually init required attrs (parent __init__ is mocked)
        controller.player_name = 'TestBot'
        controller.state_machine = sm
        controller.strategy_table = table
        controller.hu_strategy_table = None
        controller.debug_logging = True
        controller.rng = random.Random(42)
        controller._deviation_profile = None
        controller.psychology = None
        controller.prompt_config = SimpleNamespace(strategic_reflection=False)
        controller._current_hand_plans = []
        controller._hand_max_bluff_likelihood = 0

        return controller

    def test_preflop_rfi_aa_raises(self):
        """AA from UTG in RFI should produce a raise."""
        from core.card import Card
        gs = _make_game_state(
            hand=(Card('A', 'h'), Card('A', 's')),
            position_key='under_the_gun',
            raises=0,
        )
        controller = self._make_controller(game_state=gs)
        result = controller._get_ai_decision(
            message='',
            valid_actions=['fold', 'call', 'raise', 'all_in'],
            call_amount=100,
        )
        # AA should raise
        assert result['action'] in ('raise', 'all_in')

    def test_preflop_rfi_72o_folds(self):
        """72o from UTG in RFI should fold."""
        from core.card import Card
        gs = _make_game_state(
            hand=(Card('7', 'h'), Card('2', 's')),
            position_key='under_the_gun',
            raises=0,
        )
        controller = self._make_controller(game_state=gs)
        result = controller._get_ai_decision(
            message='',
            valid_actions=['fold', 'call', 'raise', 'all_in'],
            call_amount=100,
        )
        assert result['action'] == 'fold'

    def test_postflop_fallback_checks(self):
        """Postflop should use check/fold fallback."""
        from core.card import Card
        gs = _make_game_state(
            hand=(Card('A', 'h'), Card('A', 's')),
        )
        controller = self._make_controller(game_state=gs, phase='FLOP')
        result = controller._get_ai_decision(
            message='',
            valid_actions=['check', 'raise', 'all_in'],
        )
        assert result['action'] == 'check'

    def test_postflop_fallback_folds_when_no_check(self):
        """Postflop should fold when check isn't available."""
        from core.card import Card
        gs = _make_game_state(
            hand=(Card('A', 'h'), Card('A', 's')),
        )
        controller = self._make_controller(game_state=gs, phase='TURN')
        result = controller._get_ai_decision(
            message='',
            valid_actions=['fold', 'call', 'raise'],
        )
        assert result['action'] == 'fold'

    def test_missing_hand_uses_fallback(self):
        """If no canonical hand can be derived, use fallback."""
        gs = _make_game_state(hand=())
        gs.current_player.hand = ()
        controller = self._make_controller(game_state=gs)
        result = controller._get_ai_decision(
            message='',
            valid_actions=['fold', 'call', 'raise', 'all_in'],
        )
        # Should get a valid action (fallback)
        assert result['action'] in ('fold', 'check', 'call', 'raise', 'all_in')

    def test_result_has_required_keys(self):
        """Decision result should have all required keys."""
        from core.card import Card
        gs = _make_game_state(
            hand=(Card('A', 'h'), Card('A', 's')),
            raises=0,
        )
        controller = self._make_controller(game_state=gs)
        result = controller._get_ai_decision(
            message='',
            valid_actions=['fold', 'call', 'raise', 'all_in'],
        )
        assert 'action' in result
        assert 'raise_to' in result
        assert 'dramatic_sequence' in result
        assert 'hand_strategy' in result

    def test_unknown_hand_falls_back_to_fold(self):
        """Hand not in strategy table should use conservative default (fold)."""
        from core.card import Card
        gs = _make_game_state(
            hand=(Card('3', 'h'), Card('2', 's')),
            position_key='under_the_gun',
            raises=0,
        )
        controller = self._make_controller(game_state=gs)
        result = controller._get_ai_decision(
            message='',
            valid_actions=['fold', 'call', 'raise', 'all_in'],
        )
        # 32o from UTG not in our mini table → conservative default → fold
        assert result['action'] == 'fold'

    def test_validate_action_fallback(self):
        """When resolved action isn't legal, validator should pick a fallback."""
        from core.card import Card
        gs = _make_game_state(
            hand=(Card('A', 'h'), Card('A', 's')),
        )
        controller = self._make_controller(game_state=gs)
        # Test the _validate_action method directly
        action, amount = controller._validate_action('raise', 250, ['fold', 'call'])
        assert action == 'call'
        assert amount == 0

    def test_deterministic_with_seed(self):
        """Same seed should produce same decision."""
        from core.card import Card
        gs1 = _make_game_state(
            hand=(Card('A', 'h'), Card('K', 's')),
            position_key='button',
            raises=0,
        )
        gs2 = _make_game_state(
            hand=(Card('A', 'h'), Card('K', 's')),
            position_key='button',
            raises=0,
        )
        # Create two controllers with same seed
        c1 = self._make_controller(game_state=gs1)
        c1.rng = random.Random(42)
        c2 = self._make_controller(game_state=gs2)
        c2.rng = random.Random(42)

        r1 = c1._get_ai_decision(
            message='', valid_actions=['fold', 'call', 'raise', 'all_in'],
        )
        r2 = c2._get_ai_decision(
            message='', valid_actions=['fold', 'call', 'raise', 'all_in'],
        )
        assert r1['action'] == r2['action']
        assert r1['raise_to'] == r2['raise_to']
