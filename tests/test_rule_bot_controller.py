"""Tests for the RuleBotController - rule-based bot with psychology system."""

import pytest
from unittest.mock import MagicMock, patch

from poker.rule_bot_controller import RuleBotController
from poker.poker_state_machine import PokerStateMachine, PokerPhase


class TestRuleBotControllerBasics:
    """Tests for RuleBotController initialization and basic functionality."""

    @pytest.fixture
    def mock_state_machine(self):
        """Create a minimal mock state machine for testing."""
        mock_sm = MagicMock(spec=PokerStateMachine)
        mock_sm.current_phase = PokerPhase.PRE_FLOP

        # Create minimal game state
        mock_game_state = MagicMock()
        mock_game_state.current_ante = 100
        mock_game_state.pot = {'total': 300}
        mock_game_state.highest_bet = 200
        mock_game_state.min_raise_amount = 100
        mock_game_state.community_cards = []
        mock_game_state.table_positions = {'button': 'CaseBot'}
        mock_game_state.current_player_options = ['fold', 'call', 'raise']

        # Current player
        mock_player = MagicMock()
        mock_player.name = 'CaseBot'
        mock_player.stack = 5000
        mock_player.bet = 100
        mock_player.hand = []
        mock_player.is_folded = False
        mock_player.is_all_in = False
        mock_game_state.current_player = mock_player

        # Other players
        mock_opponent = MagicMock()
        mock_opponent.name = 'Human'
        mock_opponent.stack = 5000
        mock_opponent.bet = 200
        mock_opponent.is_folded = False
        mock_opponent.is_all_in = False
        mock_game_state.players = [mock_player, mock_opponent]

        mock_sm.game_state = mock_game_state
        mock_sm.phase = PokerPhase.PRE_FLOP

        return mock_sm

    def test_init_creates_psychology(self, mock_state_machine):
        """RuleBotController should have psychology from parent."""
        controller = RuleBotController(
            player_name='CaseBot',
            state_machine=mock_state_machine,
            strategy='case_based',
            game_id='test_game',
        )

        assert controller.psychology is not None
        assert hasattr(controller.psychology, 'tilt_level')
        assert hasattr(controller.psychology, 'apply_pressure_event')

    def test_init_creates_ai_player(self, mock_state_machine):
        """RuleBotController should have ai_player from parent."""
        controller = RuleBotController(
            player_name='CaseBot',
            state_machine=mock_state_machine,
            strategy='case_based',
            game_id='test_game',
        )

        assert controller.ai_player is not None
        assert hasattr(controller.ai_player, 'personality_config')

    def test_strategy_stored(self, mock_state_machine):
        """RuleBotController should store the strategy."""
        controller = RuleBotController(
            player_name='CaseBot',
            state_machine=mock_state_machine,
            strategy='abc',
            game_id='test_game',
        )

        assert controller.strategy == 'abc'
        assert controller.rule_config.strategy == 'abc'

    def test_has_prompt_config(self, mock_state_machine):
        """RuleBotController should have prompt_config from parent."""
        controller = RuleBotController(
            player_name='CaseBot',
            state_machine=mock_state_machine,
            strategy='case_based',
            game_id='test_game',
        )

        assert controller.prompt_config is not None

    def test_clear_decision_plans(self, mock_state_machine):
        """RuleBotController should support clear_decision_plans()."""
        controller = RuleBotController(
            player_name='CaseBot',
            state_machine=mock_state_machine,
            strategy='case_based',
        )

        plans = controller.clear_decision_plans()
        assert isinstance(plans, list)

    def test_clear_hand_bluff_likelihood(self, mock_state_machine):
        """RuleBotController should support clear_hand_bluff_likelihood()."""
        controller = RuleBotController(
            player_name='CaseBot',
            state_machine=mock_state_machine,
            strategy='case_based',
        )

        # Should not raise
        controller.clear_hand_bluff_likelihood()


class TestRuleBotControllerDecisions:
    """Tests for RuleBotController decision making."""

    @pytest.fixture
    def mock_state_machine(self):
        """Create a mock state machine with preflop game state."""
        mock_sm = MagicMock(spec=PokerStateMachine)
        mock_sm.current_phase = PokerPhase.PRE_FLOP

        mock_game_state = MagicMock()
        mock_game_state.current_ante = 100
        mock_game_state.pot = {'total': 300}
        mock_game_state.highest_bet = 200
        mock_game_state.min_raise_amount = 100
        mock_game_state.community_cards = []
        mock_game_state.table_positions = {'button': 'CaseBot'}
        mock_game_state.current_player_options = ['fold', 'call', 'raise']

        mock_player = MagicMock()
        mock_player.name = 'CaseBot'
        mock_player.stack = 5000
        mock_player.bet = 100
        mock_player.hand = []  # Will be set in tests
        mock_player.is_folded = False
        mock_player.is_all_in = False
        mock_game_state.current_player = mock_player

        mock_opponent = MagicMock()
        mock_opponent.name = 'Human'
        mock_opponent.stack = 5000
        mock_opponent.bet = 200
        mock_opponent.is_folded = False
        mock_opponent.is_all_in = False
        mock_game_state.players = [mock_player, mock_opponent]

        mock_sm.game_state = mock_game_state
        mock_sm.phase = PokerPhase.PRE_FLOP

        return mock_sm

    def test_get_ai_decision_returns_dict(self, mock_state_machine):
        """_get_ai_decision should return properly formatted dict."""
        controller = RuleBotController(
            player_name='CaseBot',
            state_machine=mock_state_machine,
            strategy='always_fold',
        )

        context = {
            'valid_actions': ['fold', 'call', 'raise'],
            'call_amount': 100,
            'min_raise': 300,
            'max_raise': 5000,
            'should_speak': False,
            'big_blind': 100,
        }

        result = controller._get_ai_decision("test message", **context)

        assert 'action' in result
        assert 'raise_to' in result
        assert 'dramatic_sequence' in result
        assert 'hand_strategy' in result

    def test_always_fold_strategy(self, mock_state_machine):
        """always_fold strategy should fold when there's a cost."""
        controller = RuleBotController(
            player_name='CaseBot',
            state_machine=mock_state_machine,
            strategy='always_fold',
        )

        context = {
            'valid_actions': ['fold', 'call', 'raise'],
            'call_amount': 100,  # There's a cost to call
            'min_raise': 300,
            'max_raise': 5000,
            'should_speak': False,
            'big_blind': 100,
        }

        result = controller._get_ai_decision("test message", **context)

        assert result['action'] == 'fold'

    def test_always_fold_checks_when_free(self, mock_state_machine):
        """always_fold strategy should check when free."""
        # Modify state for free check
        mock_state_machine.game_state.highest_bet = 100  # Same as player bet
        mock_state_machine.game_state.current_player_options = ['check', 'raise']

        controller = RuleBotController(
            player_name='CaseBot',
            state_machine=mock_state_machine,
            strategy='always_fold',
        )

        context = {
            'valid_actions': ['check', 'raise'],
            'call_amount': 0,  # Free
            'min_raise': 200,
            'max_raise': 5000,
            'should_speak': False,
            'big_blind': 100,
        }

        result = controller._get_ai_decision("test message", **context)

        assert result['action'] == 'check'

    def test_decision_history_tracked(self, mock_state_machine):
        """Decisions should be tracked in decision_history."""
        controller = RuleBotController(
            player_name='CaseBot',
            state_machine=mock_state_machine,
            strategy='always_fold',
        )

        context = {
            'valid_actions': ['fold', 'call'],
            'call_amount': 100,
            'min_raise': 300,
            'max_raise': 5000,
            'should_speak': False,
            'big_blind': 100,
        }

        controller._get_ai_decision("test message", **context)

        assert len(controller.decision_history) == 1
        assert controller.decision_history[0]['action'] == 'fold'
        assert controller.decision_history[0]['strategy'] == 'always_fold'

    def test_last_decision_context(self, mock_state_machine):
        """get_last_decision_context should return last decision details."""
        controller = RuleBotController(
            player_name='CaseBot',
            state_machine=mock_state_machine,
            strategy='always_fold',
        )

        context = {
            'valid_actions': ['fold', 'call'],
            'call_amount': 100,
            'min_raise': 300,
            'max_raise': 5000,
            'should_speak': False,
            'big_blind': 100,
        }

        controller._get_ai_decision("test message", **context)

        last_ctx = controller.get_last_decision_context()
        assert last_ctx is not None
        assert 'action' in last_ctx
        assert 'strategy' in last_ctx
        assert last_ctx['strategy'] == 'always_fold'


class TestRuleBotControllerPsychologyIntegration:
    """Tests for psychology integration in RuleBotController."""

    @pytest.fixture
    def mock_state_machine(self):
        """Create a mock state machine."""
        mock_sm = MagicMock(spec=PokerStateMachine)
        mock_sm.current_phase = PokerPhase.PRE_FLOP

        mock_game_state = MagicMock()
        mock_game_state.current_ante = 100
        mock_game_state.pot = {'total': 300}
        mock_game_state.highest_bet = 200
        mock_game_state.min_raise_amount = 100
        mock_game_state.community_cards = []
        mock_game_state.table_positions = {'button': 'CaseBot'}
        mock_game_state.current_player_options = ['fold', 'call', 'raise']

        mock_player = MagicMock()
        mock_player.name = 'CaseBot'
        mock_player.stack = 5000
        mock_player.bet = 100
        mock_player.hand = []
        mock_player.is_folded = False
        mock_player.is_all_in = False
        mock_game_state.current_player = mock_player

        mock_opponent = MagicMock()
        mock_opponent.name = 'Human'
        mock_opponent.stack = 5000
        mock_opponent.bet = 200
        mock_opponent.is_folded = False
        mock_opponent.is_all_in = False
        mock_game_state.players = [mock_player, mock_opponent]

        mock_sm.game_state = mock_game_state
        mock_sm.phase = PokerPhase.PRE_FLOP

        return mock_sm

    def test_psychology_tilt_level(self, mock_state_machine):
        """Psychology tilt level should be accessible."""
        controller = RuleBotController(
            player_name='CaseBot',
            state_machine=mock_state_machine,
            strategy='case_based',
        )

        # Initial tilt level should be 0 or low
        assert controller.psychology.tilt_level >= 0

    def test_psychology_apply_pressure_event(self, mock_state_machine):
        """Psychology should support pressure events."""
        controller = RuleBotController(
            player_name='CaseBot',
            state_machine=mock_state_machine,
            strategy='case_based',
        )

        initial_composure = controller.psychology.composure

        # Apply a pressure event
        controller.psychology.apply_pressure_event('bad_beat')

        # Composure should change (decrease)
        assert controller.psychology.composure <= initial_composure

    def test_psychology_get_display_emotion(self, mock_state_machine):
        """Psychology should provide display emotion."""
        controller = RuleBotController(
            player_name='CaseBot',
            state_machine=mock_state_machine,
            strategy='case_based',
        )

        emotion = controller.psychology.get_display_emotion()
        assert isinstance(emotion, str)
        assert len(emotion) > 0

    def test_psychology_to_dict(self, mock_state_machine):
        """Psychology should serialize to dict for persistence."""
        controller = RuleBotController(
            player_name='CaseBot',
            state_machine=mock_state_machine,
            strategy='case_based',
        )

        psych_dict = controller.psychology.to_dict()
        assert isinstance(psych_dict, dict)
        # Check for anchors or tilt_state (structure varies based on version)
        assert 'anchors' in psych_dict or 'tilt_state' in psych_dict
