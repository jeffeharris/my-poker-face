"""Tests for coach_engine helper functions.

Tests _get_available_actions, _get_position_context, and _get_opponent_stats
to ensure the coach provides valid action recommendations and position guidance.
"""

import unittest
from unittest.mock import MagicMock, patch


class TestGetAvailableActions(unittest.TestCase):
    """Test _get_available_actions edge cases."""

    def _make_player(self, name: str, stack: int, is_folded: bool = False):
        """Create a mock player."""
        player = MagicMock()
        player.name = name
        player.stack = stack
        player.is_folded = is_folded
        return player

    def _make_game_state(self, players):
        """Create a mock game state."""
        game_state = MagicMock()
        game_state.players = players
        return game_state

    def test_check_and_bet_available_when_no_cost_to_call(self):
        """When cost_to_call is 0 and opponents have chips, check and bet available."""
        from flask_app.services.coach_engine import _get_available_actions

        hero = self._make_player("Hero", stack=1000)
        opponent = self._make_player("Villain", stack=500)
        game_state = self._make_game_state([hero, opponent])

        actions = _get_available_actions(game_state, hero, cost_to_call=0)

        self.assertIn('check', actions)
        self.assertIn('bet', actions)
        self.assertNotIn('fold', actions)
        self.assertNotIn('call', actions)

    def test_only_check_when_all_opponents_all_in(self):
        """Cannot bet when all opponents are all-in (stack=0)."""
        from flask_app.services.coach_engine import _get_available_actions

        hero = self._make_player("Hero", stack=1000)
        opponent1 = self._make_player("Villain1", stack=0)  # all-in
        opponent2 = self._make_player("Villain2", stack=0)  # all-in
        game_state = self._make_game_state([hero, opponent1, opponent2])

        actions = _get_available_actions(game_state, hero, cost_to_call=0)

        self.assertIn('check', actions)
        self.assertNotIn('bet', actions)

    def test_fold_call_raise_when_facing_bet(self):
        """When cost_to_call > 0, player can fold, call, and raise."""
        from flask_app.services.coach_engine import _get_available_actions

        hero = self._make_player("Hero", stack=1000)
        opponent = self._make_player("Villain", stack=500)
        game_state = self._make_game_state([hero, opponent])

        actions = _get_available_actions(game_state, hero, cost_to_call=100)

        self.assertIn('fold', actions)
        self.assertIn('call', actions)
        self.assertIn('raise', actions)
        self.assertNotIn('check', actions)

    def test_no_raise_when_all_opponents_all_in(self):
        """Cannot raise when all opponents are all-in."""
        from flask_app.services.coach_engine import _get_available_actions

        hero = self._make_player("Hero", stack=1000)
        opponent = self._make_player("Villain", stack=0)  # all-in
        game_state = self._make_game_state([hero, opponent])

        actions = _get_available_actions(game_state, hero, cost_to_call=100)

        self.assertIn('fold', actions)
        self.assertIn('call', actions)
        self.assertNotIn('raise', actions)

    def test_all_in_instead_of_call_when_stack_insufficient(self):
        """When player can't afford to call, only all-in is available."""
        from flask_app.services.coach_engine import _get_available_actions

        hero = self._make_player("Hero", stack=50)  # less than cost_to_call
        opponent = self._make_player("Villain", stack=500)
        game_state = self._make_game_state([hero, opponent])

        actions = _get_available_actions(game_state, hero, cost_to_call=100)

        self.assertIn('fold', actions)
        self.assertIn('all-in', actions)
        self.assertNotIn('call', actions)
        self.assertNotIn('raise', actions)

    def test_folded_opponents_ignored(self):
        """Folded opponents don't count for all-in detection."""
        from flask_app.services.coach_engine import _get_available_actions

        hero = self._make_player("Hero", stack=1000)
        folded_opp = self._make_player("Folder", stack=0, is_folded=True)
        active_opp = self._make_player("Active", stack=500)
        game_state = self._make_game_state([hero, folded_opp, active_opp])

        actions = _get_available_actions(game_state, hero, cost_to_call=0)

        # Active opponent has chips, so bet should be available
        self.assertIn('bet', actions)

    def test_no_actions_when_hero_has_no_stack(self):
        """When player has no stack, limited actions available."""
        from flask_app.services.coach_engine import _get_available_actions

        hero = self._make_player("Hero", stack=0)
        opponent = self._make_player("Villain", stack=500)
        game_state = self._make_game_state([hero, opponent])

        actions = _get_available_actions(game_state, hero, cost_to_call=0)

        self.assertIn('check', actions)
        self.assertNotIn('bet', actions)  # Can't bet with no chips


class TestGetPositionContext(unittest.TestCase):
    """Test _get_position_context for all positions and phases."""

    def test_button_preflop_wide_opening(self):
        """Button pre-flop should suggest wide opening."""
        from flask_app.services.coach_engine import _get_position_context

        result = _get_position_context("Button", "PRE_FLOP")

        self.assertIn("wide", result.lower())
        self.assertIn("best", result.lower())

    def test_button_postflop_position_advantage(self):
        """Button post-flop should mention positional advantage."""
        from flask_app.services.coach_engine import _get_position_context

        result = _get_position_context("Button", "FLOP")

        self.assertIn("position", result.lower())
        self.assertIn("last", result.lower())

    def test_under_the_gun_preflop_tight(self):
        """UTG pre-flop should suggest tight play."""
        from flask_app.services.coach_engine import _get_position_context

        result = _get_position_context("Under The Gun", "PRE_FLOP")

        self.assertIn("tight", result.lower())
        self.assertIn("premium", result.lower())

    def test_small_blind_preflop_worst_position(self):
        """Small blind should warn about worst position."""
        from flask_app.services.coach_engine import _get_position_context

        result = _get_position_context("Small Blind Player", "PRE_FLOP")

        self.assertIn("worst", result.lower())

    def test_big_blind_preflop_defend(self):
        """Big blind should suggest defending wider."""
        from flask_app.services.coach_engine import _get_position_context

        result = _get_position_context("Big Blind Player", "PRE_FLOP")

        self.assertIn("defend", result.lower())

    def test_cutoff_preflop_fairly_wide(self):
        """Cutoff pre-flop should allow fairly wide opening."""
        from flask_app.services.coach_engine import _get_position_context

        result = _get_position_context("Cutoff", "PRE_FLOP")

        self.assertIn("wide", result.lower())

    def test_middle_position_preflop_moderate(self):
        """Middle position should suggest moderate range."""
        from flask_app.services.coach_engine import _get_position_context

        result = _get_position_context("Middle Position 2", "PRE_FLOP")

        self.assertIn("moderate", result.lower())

    def test_blinds_postflop_out_of_position(self):
        """Blinds post-flop should warn about being out of position."""
        from flask_app.services.coach_engine import _get_position_context

        result = _get_position_context("Small Blind Player", "FLOP")

        self.assertIn("out of position", result.lower())

    def test_unknown_position_returns_empty(self):
        """Unknown position should return empty string."""
        from flask_app.services.coach_engine import _get_position_context

        result = _get_position_context("Unknown", "PRE_FLOP")

        self.assertEqual(result, "")

    def test_case_insensitive_matching(self):
        """Position matching should be case insensitive."""
        from flask_app.services.coach_engine import _get_position_context

        result1 = _get_position_context("BUTTON", "PRE_FLOP")
        result2 = _get_position_context("button", "PRE_FLOP")
        result3 = _get_position_context("Button", "PRE_FLOP")

        self.assertEqual(result1, result2)
        self.assertEqual(result2, result3)


class TestGetOpponentStats(unittest.TestCase):
    """Test _get_opponent_stats with stack and all-in info."""

    def _make_player(self, name: str, stack: int, bet: int = 0, is_folded: bool = False):
        """Create a mock player."""
        player = MagicMock()
        player.name = name
        player.stack = stack
        player.bet = bet
        player.is_folded = is_folded
        return player

    def _make_game_data(self, players, memory_manager=None):
        """Create mock game data dict."""
        game_state = MagicMock()
        game_state.players = players
        state_machine = MagicMock()
        state_machine.game_state = game_state
        return {
            'state_machine': state_machine,
            'memory_manager': memory_manager,
        }

    def test_opponent_with_zero_stack_is_all_in(self):
        """Opponent with stack=0 should have is_all_in=True."""
        from flask_app.services.coach_engine import _get_opponent_stats

        hero = self._make_player("Hero", stack=1000)
        opponent = self._make_player("Villain", stack=0, bet=500)
        game_data = self._make_game_data([hero, opponent])

        stats = _get_opponent_stats(game_data, "Hero")

        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]['name'], "Villain")
        self.assertTrue(stats[0]['is_all_in'])
        self.assertEqual(stats[0]['stack'], 0)
        self.assertEqual(stats[0]['bet'], 500)

    def test_opponent_with_stack_not_all_in(self):
        """Opponent with stack > 0 should have is_all_in=False."""
        from flask_app.services.coach_engine import _get_opponent_stats

        hero = self._make_player("Hero", stack=1000)
        opponent = self._make_player("Villain", stack=500, bet=100)
        game_data = self._make_game_data([hero, opponent])

        stats = _get_opponent_stats(game_data, "Hero")

        self.assertEqual(len(stats), 1)
        self.assertFalse(stats[0]['is_all_in'])
        self.assertEqual(stats[0]['stack'], 500)

    def test_folded_opponents_excluded(self):
        """Folded opponents should not appear in stats."""
        from flask_app.services.coach_engine import _get_opponent_stats

        hero = self._make_player("Hero", stack=1000)
        active = self._make_player("Active", stack=500)
        folded = self._make_player("Folded", stack=300, is_folded=True)
        game_data = self._make_game_data([hero, active, folded])

        stats = _get_opponent_stats(game_data, "Hero")

        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]['name'], "Active")

    def test_hero_excluded_from_stats(self):
        """The human player should not appear in opponent stats."""
        from flask_app.services.coach_engine import _get_opponent_stats

        hero = self._make_player("Hero", stack=1000)
        opponent = self._make_player("Villain", stack=500)
        game_data = self._make_game_data([hero, opponent])

        stats = _get_opponent_stats(game_data, "Hero")

        names = [s['name'] for s in stats]
        self.assertNotIn("Hero", names)
        self.assertIn("Villain", names)

    def test_returns_empty_list_when_no_state_machine(self):
        """Should return empty list when state_machine is missing."""
        from flask_app.services.coach_engine import _get_opponent_stats

        game_data = {'memory_manager': None}  # No state_machine

        stats = _get_opponent_stats(game_data, "Hero")

        self.assertEqual(stats, [])

    def test_default_stats_without_memory_manager(self):
        """Without memory manager, should return basic stats with defaults."""
        from flask_app.services.coach_engine import _get_opponent_stats

        hero = self._make_player("Hero", stack=1000)
        opponent = self._make_player("Villain", stack=500)
        game_data = self._make_game_data([hero, opponent], memory_manager=None)

        stats = _get_opponent_stats(game_data, "Hero")

        self.assertEqual(len(stats), 1)
        self.assertIsNone(stats[0]['vpip'])
        self.assertIsNone(stats[0]['pfr'])
        self.assertEqual(stats[0]['style'], 'unknown')
        self.assertEqual(stats[0]['hands_observed'], 0)

    def test_multiple_opponents(self):
        """Should return stats for all active opponents."""
        from flask_app.services.coach_engine import _get_opponent_stats

        hero = self._make_player("Hero", stack=1000)
        opp1 = self._make_player("Villain1", stack=500)
        opp2 = self._make_player("Villain2", stack=300)
        opp3 = self._make_player("Villain3", stack=0)  # all-in
        game_data = self._make_game_data([hero, opp1, opp2, opp3])

        stats = _get_opponent_stats(game_data, "Hero")

        self.assertEqual(len(stats), 3)
        names = [s['name'] for s in stats]
        self.assertIn("Villain1", names)
        self.assertIn("Villain2", names)
        self.assertIn("Villain3", names)

        # Check all-in detection
        all_in_stats = [s for s in stats if s['is_all_in']]
        self.assertEqual(len(all_in_stats), 1)
        self.assertEqual(all_in_stats[0]['name'], "Villain3")


if __name__ == '__main__':
    unittest.main()
