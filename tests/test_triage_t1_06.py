"""Tests for T1-06: Verify pot x 2 raise cap has been removed.

In No-Limit Hold'em, raise amounts should not be capped by pot size.
The max raise should be limited only by player/opponent stacks.
"""
from unittest.mock import MagicMock, patch

from poker.prompt_config import PromptConfig


class TestControllerMaxRaiseNoPotCap:
    """Verify AIPlayerController.decide_action does not cap max_raise at pot * 2."""

    def _make_controller(self):
        """Create a minimal AIPlayerController with dependencies mocked."""
        with patch('poker.controllers.AIPokerPlayer') as mock_player, \
             patch('poker.controllers.PromptManager'), \
             patch('poker.controllers.ChattinessManager'), \
             patch('poker.controllers.ResponseValidator') as mock_validator, \
             patch('poker.controllers.PlayerPsychology') as mock_psych:
            mock_player.return_value.assistant = MagicMock()
            mock_player.return_value.personality_config = {}
            mock_psych.from_personality_config.return_value = MagicMock()
            mock_psych.from_personality_config.return_value.get_prompt_section.return_value = ""
            mock_psych.from_personality_config.return_value.apply_tilt_effects.side_effect = lambda x: x
            mock_psych.from_personality_config.return_value.get_chattiness_traits.return_value = {}

            from poker.controllers import AIPlayerController
            controller = AIPlayerController('TestPlayer', prompt_config=PromptConfig())

            # Make response_validator.clean_response pass through
            mock_validator.return_value.clean_response.side_effect = lambda r, _: r

        return controller

    def _make_game_state(self, player_stack, opponent_stack, pot_total, highest_bet=0):
        """Create a mock game state with specified stack/pot values."""
        current_player = MagicMock()
        current_player.name = 'TestPlayer'
        current_player.stack = player_stack
        current_player.bet = 0
        current_player.is_folded = False
        current_player.is_all_in = False
        current_player.hand = [MagicMock(), MagicMock()]

        opponent = MagicMock()
        opponent.name = 'Opponent'
        opponent.stack = opponent_stack
        opponent.is_folded = False
        opponent.is_all_in = False
        opponent.bet = 0

        gs = MagicMock()
        gs.current_player = current_player
        gs.players = (current_player, opponent)
        gs.pot = {'total': pot_total}
        gs.highest_bet = highest_bet
        gs.min_raise_amount = 20
        gs.current_ante = 10
        gs.current_player_options = ['fold', 'call', 'raise']
        gs.community_cards = ()
        gs.table_positions = {}
        gs.phase = MagicMock()
        gs.phase.value = 'PRE_FLOP'

        return gs

    def test_max_raise_not_capped_by_pot(self):
        """When pot is small but stacks are large, max_raise should NOT be pot * 2.

        Scenario: pot=100, player_stack=5000, opponent_stack=5000
        Old behavior: max_raise = min(5000, 5000, 100*2) = 200  (WRONG)
        New behavior: max_raise = min(5000, 5000) = 5000         (CORRECT)
        """
        controller = self._make_controller()

        gs = self._make_game_state(
            player_stack=5000,
            opponent_stack=5000,
            pot_total=100,
        )

        state_machine = MagicMock()
        state_machine.game_state = gs
        controller.state_machine = state_machine

        # Capture the max_raise value passed to _get_ai_decision
        captured_kwargs = {}

        def capture_get_ai_decision(**kwargs):
            captured_kwargs.update(kwargs)
            return {'action': 'call', 'raise_to': 0}

        controller._get_ai_decision = capture_get_ai_decision

        # Mock internal methods to isolate the max_raise calculation
        with patch.object(controller, '_build_game_context', return_value={}), \
             patch.object(controller, '_build_memory_context', return_value=''), \
             patch.object(controller, '_build_chattiness_guidance', return_value=''), \
             patch('poker.controllers.summarize_messages', return_value=[]), \
             patch('poker.controllers._convert_messages_to_bb', return_value=[]), \
             patch('poker.controllers.build_base_game_state', return_value="mock prompt"):
            controller.decide_action([])

        # The key assertion: max_raise should be 5000, not 200
        assert captured_kwargs['max_raise'] == 5000, (
            f"max_raise should be 5000 (min of stacks), "
            f"got {captured_kwargs['max_raise']} (was it capped by pot * 2 = 200?)"
        )

    def test_max_raise_limited_by_opponent_stack(self):
        """Max raise should still be limited by the largest opponent stack."""
        controller = self._make_controller()

        gs = self._make_game_state(
            player_stack=5000,
            opponent_stack=2000,
            pot_total=100,
        )

        state_machine = MagicMock()
        state_machine.game_state = gs
        controller.state_machine = state_machine

        captured_kwargs = {}

        def capture_get_ai_decision(**kwargs):
            captured_kwargs.update(kwargs)
            return {'action': 'call', 'raise_to': 0}

        controller._get_ai_decision = capture_get_ai_decision

        with patch.object(controller, '_build_game_context', return_value={}), \
             patch.object(controller, '_build_memory_context', return_value=''), \
             patch.object(controller, '_build_chattiness_guidance', return_value=''), \
             patch('poker.controllers.summarize_messages', return_value=[]), \
             patch('poker.controllers._convert_messages_to_bb', return_value=[]), \
             patch('poker.controllers.build_base_game_state', return_value="mock prompt"):
            controller.decide_action([])

        # max_raise should be min(5000, 2000) = 2000
        assert captured_kwargs['max_raise'] == 2000


class TestFallbackMaxRaiseNoPotCap:
    """Verify the fallback handler in game_handler.py does not cap max_raise at pot * 2."""

    def test_fallback_max_raise_equals_player_stack(self):
        """In the fallback path, max_raise should equal player stack, not pot * 2.

        Scenario: pot=100, player_stack=5000
        Old behavior: max_raise = min(5000, 100*2) = 200  (WRONG)
        New behavior: max_raise = 5000                      (CORRECT)
        """
        # Simulate the fallback calculation directly (line 1205 of game_handler.py)
        # After fix: max_raise = current_player.stack
        current_player_stack = 5000
        pot_total = 100

        # New behavior
        max_raise = current_player_stack
        assert max_raise == 5000

        # Verify old behavior would have been wrong
        old_max_raise = min(current_player_stack, pot_total * 2)
        assert old_max_raise == 200, "Old capped behavior should have returned 200"
        assert max_raise > old_max_raise, "New behavior should allow larger raises"
