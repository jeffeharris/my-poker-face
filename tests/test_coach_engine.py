"""Tests for coach_engine helper functions.

Tests _get_available_actions, _get_position_context, _get_opponent_stats,
and _get_style_label to ensure the coach provides valid action recommendations
and position guidance.
"""

import unittest
from unittest.mock import MagicMock, patch

import pytest

from flask_app.services.coach_engine import _get_style_label


class TestGetStyleLabel:
    """Test _get_style_label() style classification.

    Thresholds from poker.config:
    - VPIP_TIGHT_THRESHOLD = 0.3 (< 0.3 is tight)
    - AGGRESSION_FACTOR_HIGH = 1.5 (> 1.5 is aggressive)
    """

    def test_tight_aggressive(self):
        """Low VPIP + high aggression = tight-aggressive."""
        assert _get_style_label(vpip=0.2, aggression=2.0) == 'tight-aggressive'

    def test_loose_aggressive(self):
        """High VPIP + high aggression = loose-aggressive."""
        assert _get_style_label(vpip=0.5, aggression=2.0) == 'loose-aggressive'

    def test_tight_passive(self):
        """Low VPIP + low aggression = tight-passive."""
        assert _get_style_label(vpip=0.2, aggression=1.0) == 'tight-passive'

    def test_loose_passive(self):
        """High VPIP + low aggression = loose-passive."""
        assert _get_style_label(vpip=0.5, aggression=1.0) == 'loose-passive'

    def test_boundary_tight_threshold(self):
        """VPIP at exactly 0.3 (threshold) is not tight."""
        # VPIP_TIGHT_THRESHOLD = 0.3, so 0.3 is NOT < 0.3 → loose
        assert _get_style_label(vpip=0.3, aggression=2.0) == 'loose-aggressive'

    def test_boundary_aggression_threshold(self):
        """Aggression at exactly 1.5 (threshold) is not aggressive."""
        # AGGRESSION_FACTOR_HIGH = 1.5, so 1.5 is NOT > 1.5 → passive
        assert _get_style_label(vpip=0.2, aggression=1.5) == 'tight-passive'


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


class TestExtractPreflopAction(unittest.TestCase):
    """Tests for _extract_preflop_action parsing game messages."""

    def _make_game_state(self, bb_player='BigBlind'):
        """Create a mock game state with table positions."""
        game_state = MagicMock()
        game_state.table_positions = {'big_blind_player': bb_player}
        return game_state

    def test_returns_none_for_empty_messages(self):
        """Empty game_messages list returns None."""
        from flask_app.services.coach_engine import _extract_preflop_action

        result = _extract_preflop_action(
            'Villain', [], self._make_game_state()
        )
        self.assertIsNone(result)

    def test_returns_none_for_none_messages(self):
        """None game_messages returns None (via _parse_game_messages)."""
        from flask_app.services.coach_engine import _extract_preflop_action

        result = _extract_preflop_action(
            'Villain', None, self._make_game_state()
        )
        self.assertIsNone(result)

    def test_detects_open_raise(self):
        """First raise is classified as open_raise."""
        from flask_app.services.coach_engine import _extract_preflop_action

        messages = ['Villain raises $50']
        result = _extract_preflop_action(
            'Villain', messages, self._make_game_state()
        )
        self.assertEqual(result, 'open_raise')

    def test_detects_3bet(self):
        """Second raise is classified as 3bet."""
        from flask_app.services.coach_engine import _extract_preflop_action

        messages = [
            'Hero raises $20',
            'Villain raises $60',  # 3-bet
        ]
        result = _extract_preflop_action(
            'Villain', messages, self._make_game_state()
        )
        self.assertEqual(result, '3bet')

    def test_detects_4bet_plus(self):
        """Third+ raise is classified as 4bet+."""
        from flask_app.services.coach_engine import _extract_preflop_action

        messages = [
            'Player1 raises $20',
            'Player2 raises $60',
            'Villain raises $180',  # 4-bet
        ]
        result = _extract_preflop_action(
            'Villain', messages, self._make_game_state()
        )
        self.assertEqual(result, '4bet+')

    def test_detects_call(self):
        """Call action is detected."""
        from flask_app.services.coach_engine import _extract_preflop_action

        messages = [
            'Hero raises $20',
            'Villain calls $20',
        ]
        result = _extract_preflop_action(
            'Villain', messages, self._make_game_state()
        )
        self.assertEqual(result, 'call')

    def test_detects_limp(self):
        """Limp (first in call) is detected."""
        from flask_app.services.coach_engine import _extract_preflop_action

        messages = ['Villain calls $10']
        result = _extract_preflop_action(
            'Villain', messages, self._make_game_state()
        )
        self.assertEqual(result, 'limp')

    def test_case_insensitive_opponent_name(self):
        """Opponent name matching is case insensitive."""
        from flask_app.services.coach_engine import _extract_preflop_action

        messages = ['VILLAIN raises $50']
        result = _extract_preflop_action(
            'villain', messages, self._make_game_state()
        )
        self.assertEqual(result, 'open_raise')

    def test_ignores_big_blind_forced_post(self):
        """BB's forced post is ignored (not a voluntary action)."""
        from flask_app.services.coach_engine import _extract_preflop_action

        messages = [
            'BigBlind posts big blind $10',
            'Hero raises $40',  # Someone raised
            'BigBlind calls $40',  # BB calls the raise
        ]
        # When opponent is BB, ignore their forced post
        result = _extract_preflop_action(
            'BigBlind', messages, self._make_game_state(bb_player='BigBlind')
        )
        # Should return call (calling a raise)
        self.assertEqual(result, 'call')

    def test_returns_most_aggressive_action(self):
        """When opponent takes multiple actions, most aggressive wins."""
        from flask_app.services.coach_engine import _extract_preflop_action

        messages = [
            'Hero raises $20',
            'Batman calls $20',
            'Hero raises $60',
            'Batman raises $180',  # Re-raise after 2 prior raises = 4bet+
        ]
        result = _extract_preflop_action(
            'Batman', messages, self._make_game_state()
        )
        # Should be 4bet+ (raise after 2 prior raises)
        self.assertEqual(result, '4bet+')

    def test_opponent_not_found_returns_none(self):
        """Opponent not in messages returns None (no action detected)."""
        from flask_app.services.coach_engine import _extract_preflop_action

        messages = ['Player1 raises $50', 'Player2 calls']
        result = _extract_preflop_action(
            'Villain', messages, self._make_game_state()
        )
        self.assertIsNone(result)

    def test_opponent_folded_returns_none(self):
        """Opponent who folded returns None."""
        from flask_app.services.coach_engine import _extract_preflop_action

        messages = ['Hero raises $50', 'Villain folds']
        result = _extract_preflop_action(
            'Villain', messages, self._make_game_state()
        )
        self.assertIsNone(result)


class TestExtractPostflopAggression(unittest.TestCase):
    """Tests for _extract_postflop_aggression parsing game messages."""

    def test_returns_none_for_preflop_phase(self):
        """PRE_FLOP phase always returns None."""
        from flask_app.services.coach_engine import _extract_postflop_aggression

        messages = ['Villain bets $50']  # Even with a bet message
        result = _extract_postflop_aggression('Villain', messages, 'PRE_FLOP')
        self.assertIsNone(result)

    def test_returns_none_for_empty_messages(self):
        """Empty messages returns None."""
        from flask_app.services.coach_engine import _extract_postflop_aggression

        result = _extract_postflop_aggression('Villain', [], 'FLOP')
        self.assertIsNone(result)

    def test_returns_none_for_none_messages(self):
        """None messages returns None."""
        from flask_app.services.coach_engine import _extract_postflop_aggression

        result = _extract_postflop_aggression('Villain', None, 'FLOP')
        self.assertIsNone(result)

    def test_detects_bet(self):
        """Bet action is detected."""
        from flask_app.services.coach_engine import _extract_postflop_aggression

        messages = ['--- FLOP ---', 'Villain bets $50']
        result = _extract_postflop_aggression('Villain', messages, 'FLOP')
        self.assertEqual(result, 'bet')

    def test_detects_raise(self):
        """Raise action is detected."""
        from flask_app.services.coach_engine import _extract_postflop_aggression

        messages = ['--- FLOP ---', 'Hero bets $30', 'Villain raises to $90']
        result = _extract_postflop_aggression('Villain', messages, 'FLOP')
        self.assertEqual(result, 'raise')

    def test_detects_check_call(self):
        """Call action (after check is not possible) is detected.

        Note: Using 'Batman' instead of 'Villain' because the all-in detection
        checks for 'all' in line and 'in' in line, which falsely triggers when
        'calls' (contains 'all') is used with names containing 'in' like 'Villain'.
        """
        from flask_app.services.coach_engine import _extract_postflop_aggression

        messages = ['--- FLOP ---', 'Hero bets $50', 'Batman calls $50']
        result = _extract_postflop_aggression('Batman', messages, 'FLOP')
        self.assertEqual(result, 'check_call')

    def test_detects_check(self):
        """Check action is detected."""
        from flask_app.services.coach_engine import _extract_postflop_aggression

        messages = ['--- FLOP ---', 'Villain checks']
        result = _extract_postflop_aggression('Villain', messages, 'FLOP')
        self.assertEqual(result, 'check')

    def test_detects_all_in_hyphen(self):
        """All-in with hyphen is detected as raise."""
        from flask_app.services.coach_engine import _extract_postflop_aggression

        messages = ['--- FLOP ---', 'Villain goes all-in for $500']
        result = _extract_postflop_aggression('Villain', messages, 'FLOP')
        self.assertEqual(result, 'raise')

    def test_detects_all_in_space(self):
        """All-in with space is detected as raise."""
        from flask_app.services.coach_engine import _extract_postflop_aggression

        messages = ['--- FLOP ---', 'Villain goes all in for $500']
        result = _extract_postflop_aggression('Villain', messages, 'FLOP')
        self.assertEqual(result, 'raise')

    def test_case_insensitive_matching(self):
        """Opponent name matching is case insensitive."""
        from flask_app.services.coach_engine import _extract_postflop_aggression

        messages = ['--- FLOP ---', 'VILLAIN bets $50']
        result = _extract_postflop_aggression('villain', messages, 'FLOP')
        self.assertEqual(result, 'bet')

    def test_returns_most_aggressive_action(self):
        """When multiple actions, most aggressive wins."""
        from flask_app.services.coach_engine import _extract_postflop_aggression

        messages = [
            '--- FLOP ---',
            'Villain checks',
            'Hero bets $30',
            'Villain raises to $90',  # More aggressive than check
        ]
        result = _extract_postflop_aggression('Villain', messages, 'FLOP')
        self.assertEqual(result, 'raise')

    def test_filters_to_current_street_flop(self):
        """Only FLOP actions counted when phase is FLOP."""
        from flask_app.services.coach_engine import _extract_postflop_aggression

        messages = [
            '--- FLOP ---',
            'Villain checks',
            '--- TURN ---',
            'Villain bets $100',  # Should be ignored for FLOP phase
        ]
        result = _extract_postflop_aggression('Villain', messages, 'FLOP')
        self.assertEqual(result, 'check')

    def test_filters_to_current_street_turn(self):
        """Only TURN actions counted when phase is TURN."""
        from flask_app.services.coach_engine import _extract_postflop_aggression

        messages = [
            '--- FLOP ---',
            'Villain checks',
            '--- TURN ---',
            'Villain bets $100',
        ]
        result = _extract_postflop_aggression('Villain', messages, 'TURN')
        self.assertEqual(result, 'bet')

    def test_filters_to_current_street_river(self):
        """Only RIVER actions counted when phase is RIVER."""
        from flask_app.services.coach_engine import _extract_postflop_aggression

        messages = [
            '--- FLOP ---',
            'Villain bets $50',
            '--- TURN ---',
            'Villain bets $100',
            '--- RIVER ---',
            'Villain checks',
        ]
        result = _extract_postflop_aggression('Villain', messages, 'RIVER')
        self.assertEqual(result, 'check')

    def test_opponent_not_on_street_returns_none(self):
        """Opponent not acting on current street returns None."""
        from flask_app.services.coach_engine import _extract_postflop_aggression

        messages = [
            '--- FLOP ---',
            'Hero bets $50',
            'OtherPlayer calls',
            # Villain didn't act on flop
        ]
        result = _extract_postflop_aggression('Villain', messages, 'FLOP')
        self.assertIsNone(result)


class TestGetPlayerSelfStats(unittest.TestCase):
    """Tests for _get_player_self_stats observer selection."""

    def _make_tendencies(self, vpip=0.25, pfr=0.15, aggression=1.5, hands_observed=20):
        """Create mock tendencies object."""
        t = MagicMock()
        t.vpip = vpip
        t.pfr = pfr
        t.aggression_factor = aggression
        t.hands_observed = hands_observed
        t.get_play_style_label.return_value = 'tight-aggressive'
        return t

    def _make_opponent_model(self, tendencies):
        """Create mock opponent model."""
        model = MagicMock()
        model.tendencies = tendencies
        return model

    def _make_game_data(self, omm_models=None):
        """Create game data dict with optional opponent model manager."""
        memory_manager = MagicMock()
        if omm_models is not None:
            omm = MagicMock()
            omm.models = omm_models
            memory_manager.opponent_model_manager = omm
        else:
            memory_manager.opponent_model_manager = None
        return {'memory_manager': memory_manager}

    def test_returns_none_without_memory_manager(self):
        """Returns None when no memory_manager in game_data."""
        from flask_app.services.coach_engine import _get_player_self_stats

        result = _get_player_self_stats({'memory_manager': None}, 'Hero')
        self.assertIsNone(result)

    def test_returns_none_without_omm(self):
        """Returns None when memory_manager has no opponent_model_manager."""
        from flask_app.services.coach_engine import _get_player_self_stats

        game_data = {'memory_manager': MagicMock(opponent_model_manager=None)}
        result = _get_player_self_stats(game_data, 'Hero')
        self.assertIsNone(result)

    def test_returns_none_when_no_observers(self):
        """Returns None when no AI has observed the human player."""
        from flask_app.services.coach_engine import _get_player_self_stats

        game_data = self._make_game_data(omm_models={
            'Batman': {},  # Batman has models but none for Hero
        })
        result = _get_player_self_stats(game_data, 'Hero')
        self.assertIsNone(result)

    def test_skips_self_as_observer(self):
        """Skips the human player as an observer of themselves."""
        from flask_app.services.coach_engine import _get_player_self_stats

        tendencies = self._make_tendencies(hands_observed=10)
        game_data = self._make_game_data(omm_models={
            'Hero': {'Hero': self._make_opponent_model(tendencies)},  # Self-observation
            'Batman': {'Hero': self._make_opponent_model(self._make_tendencies(hands_observed=15))},
        })

        result = _get_player_self_stats(game_data, 'Hero')
        # Should use Batman's observation (15 hands), not self
        self.assertIsNotNone(result)
        self.assertEqual(result['hands_observed'], 15)

    def test_selects_observer_with_most_hands(self):
        """Selects the observer with the most hands observed."""
        from flask_app.services.coach_engine import _get_player_self_stats

        game_data = self._make_game_data(omm_models={
            'Batman': {'Hero': self._make_opponent_model(self._make_tendencies(hands_observed=10))},
            'Superman': {'Hero': self._make_opponent_model(self._make_tendencies(hands_observed=25))},
            'Joker': {'Hero': self._make_opponent_model(self._make_tendencies(hands_observed=5))},
        })

        result = _get_player_self_stats(game_data, 'Hero')
        # Should use Superman's observation (most hands)
        self.assertEqual(result['hands_observed'], 25)

    def test_returns_none_when_hands_below_threshold(self):
        """Returns None when all observers have < 1 hand observed."""
        from flask_app.services.coach_engine import _get_player_self_stats

        game_data = self._make_game_data(omm_models={
            'Batman': {'Hero': self._make_opponent_model(self._make_tendencies(hands_observed=0))},
        })

        result = _get_player_self_stats(game_data, 'Hero')
        self.assertIsNone(result)

    def test_formats_stats_correctly(self):
        """Returns properly formatted stats dict."""
        from flask_app.services.coach_engine import _get_player_self_stats

        tendencies = self._make_tendencies(vpip=0.256, pfr=0.178, aggression=1.83, hands_observed=30)
        game_data = self._make_game_data(omm_models={
            'Batman': {'Hero': self._make_opponent_model(tendencies)},
        })

        result = _get_player_self_stats(game_data, 'Hero')

        self.assertIn('vpip', result)
        self.assertIn('pfr', result)
        self.assertIn('aggression', result)
        self.assertIn('style', result)
        self.assertIn('hands_observed', result)

    def test_rounds_values_correctly(self):
        """Stats values are rounded appropriately."""
        from flask_app.services.coach_engine import _get_player_self_stats

        tendencies = self._make_tendencies(vpip=0.256789, pfr=0.178123, aggression=1.8345)
        game_data = self._make_game_data(omm_models={
            'Batman': {'Hero': self._make_opponent_model(tendencies)},
        })

        result = _get_player_self_stats(game_data, 'Hero')

        # VPIP and PFR rounded to 2 decimal places
        self.assertEqual(result['vpip'], 0.26)
        self.assertEqual(result['pfr'], 0.18)
        # Aggression rounded to 1 decimal place
        self.assertEqual(result['aggression'], 1.8)


class TestBuildOpponentInfosHistoricFallback(unittest.TestCase):
    """Tests for _build_opponent_infos historic data fallback."""

    def _make_player(self, name: str, stack: int = 500, is_folded: bool = False):
        """Create a mock player."""
        player = MagicMock()
        player.name = name
        player.stack = stack
        player.is_folded = is_folded
        return player

    def _make_game_state(self, players, positions=None):
        """Create a mock game state."""
        game_state = MagicMock()
        game_state.players = players
        game_state.table_positions = positions or {
            'button': players[0].name if players else 'Hero',
            'big_blind_player': players[1].name if len(players) > 1 else 'Villain',
        }
        return game_state

    def _make_game_data(self, game_state, state_machine_phase='PRE_FLOP',
                        memory_manager=None, messages=None):
        """Create game data dict."""
        state_machine = MagicMock()
        state_machine.game_state = game_state
        state_machine.phase.name = state_machine_phase
        return {
            'state_machine': state_machine,
            'memory_manager': memory_manager,
            'messages': messages or [],
        }

    def test_uses_current_session_when_sufficient_hands(self):
        """Uses current session stats when hands_observed >= min_hands."""
        from flask_app.services.coach_engine import _build_opponent_infos

        hero = self._make_player("Hero")
        villain = self._make_player("Villain")
        game_state = self._make_game_state([hero, villain])

        # Setup memory manager with sufficient hands
        tendencies = MagicMock()
        tendencies.hands_observed = 20  # >= min_hands (15)
        tendencies.vpip = 0.35
        tendencies.pfr = 0.25
        tendencies.aggression_factor = 2.0

        omm = MagicMock()
        omm.models = {
            'Hero': {'Villain': MagicMock(tendencies=tendencies)},
        }
        memory_manager = MagicMock()
        memory_manager.opponent_model_manager = omm

        game_data = self._make_game_data(game_state, memory_manager=memory_manager)

        result = _build_opponent_infos(game_data, game_state, 'Hero')

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, 'Villain')
        self.assertEqual(result[0].hands_observed, 20)
        self.assertEqual(result[0].vpip, 0.35)

    def test_falls_back_to_historic_when_insufficient_hands(self):
        """Falls back to historic stats when current session has < min_hands."""
        from flask_app.services.coach_engine import _build_opponent_infos

        hero = self._make_player("Hero")
        villain = self._make_player("Villain")
        game_state = self._make_game_state([hero, villain])

        # Setup memory manager with insufficient hands
        tendencies = MagicMock()
        tendencies.hands_observed = 5  # < min_hands (15)
        tendencies.vpip = 0.30
        tendencies.pfr = 0.20
        tendencies.aggression_factor = 1.5

        omm = MagicMock()
        omm.models = {
            'Hero': {'Villain': MagicMock(tendencies=tendencies)},
        }
        memory_manager = MagicMock()
        memory_manager.opponent_model_manager = omm

        game_data = self._make_game_data(game_state, memory_manager=memory_manager)

        # Mock historic data
        with patch('flask_app.services.coach_engine.game_repo') as mock_repo:
            mock_repo.load_cross_session_opponent_models.return_value = {
                'Villain': {
                    'total_hands': 50,
                    'vpip': 0.40,
                    'pfr': 0.30,
                    'aggression_factor': 2.5,
                    'session_count': 5,
                }
            }

            result = _build_opponent_infos(game_data, game_state, 'Hero', user_id='user123')

        self.assertEqual(len(result), 1)
        # Should use historic stats
        self.assertEqual(result[0].hands_observed, 50)
        self.assertEqual(result[0].vpip, 0.40)

    def test_skips_historic_when_no_user_id(self):
        """Does not load historic data when user_id is not provided."""
        from flask_app.services.coach_engine import _build_opponent_infos

        hero = self._make_player("Hero")
        villain = self._make_player("Villain")
        game_state = self._make_game_state([hero, villain])

        game_data = self._make_game_data(game_state)

        with patch('flask_app.services.coach_engine.game_repo') as mock_repo:
            result = _build_opponent_infos(game_data, game_state, 'Hero', user_id=None)
            # Should not call load_cross_session_opponent_models
            mock_repo.load_cross_session_opponent_models.assert_not_called()

    def test_handles_historic_load_failure(self):
        """Gracefully handles failure to load historic data."""
        from flask_app.services.coach_engine import _build_opponent_infos

        hero = self._make_player("Hero")
        villain = self._make_player("Villain")
        game_state = self._make_game_state([hero, villain])

        game_data = self._make_game_data(game_state)

        with patch('flask_app.services.coach_engine.game_repo') as mock_repo:
            mock_repo.load_cross_session_opponent_models.side_effect = Exception("DB error")

            # Should not raise, just log warning
            result = _build_opponent_infos(game_data, game_state, 'Hero', user_id='user123')

        # Should still return opponent info (without historic stats)
        self.assertEqual(len(result), 1)

    def test_prefers_current_at_exact_threshold(self):
        """Uses current session stats when exactly at min_hands threshold."""
        from flask_app.services.coach_engine import _build_opponent_infos
        from poker.hand_ranges import EquityConfig

        hero = self._make_player("Hero")
        villain = self._make_player("Villain")
        game_state = self._make_game_state([hero, villain])

        min_hands = EquityConfig().min_hands_for_stats

        tendencies = MagicMock()
        tendencies.hands_observed = min_hands  # Exactly at threshold
        tendencies.vpip = 0.35
        tendencies.pfr = 0.25
        tendencies.aggression_factor = 2.0

        omm = MagicMock()
        omm.models = {'Hero': {'Villain': MagicMock(tendencies=tendencies)}}
        memory_manager = MagicMock()
        memory_manager.opponent_model_manager = omm

        game_data = self._make_game_data(game_state, memory_manager=memory_manager)

        with patch('flask_app.services.coach_engine.game_repo') as mock_repo:
            mock_repo.load_cross_session_opponent_models.return_value = {}

            result = _build_opponent_infos(game_data, game_state, 'Hero', user_id='user123')

        # Should use current session stats
        self.assertEqual(result[0].hands_observed, min_hands)
        self.assertEqual(result[0].vpip, 0.35)

    def test_excludes_folded_opponents(self):
        """Excludes folded opponents from the result."""
        from flask_app.services.coach_engine import _build_opponent_infos

        hero = self._make_player("Hero")
        active = self._make_player("Active")
        folded = self._make_player("Folded", is_folded=True)
        game_state = self._make_game_state([hero, active, folded])

        game_data = self._make_game_data(game_state)

        result = _build_opponent_infos(game_data, game_state, 'Hero')

        names = [opp.name for opp in result]
        self.assertIn('Active', names)
        self.assertNotIn('Folded', names)

    def test_extracts_preflop_action(self):
        """Extracts preflop_action for each opponent."""
        from flask_app.services.coach_engine import _build_opponent_infos

        hero = self._make_player("Hero")
        villain = self._make_player("Batman")  # Using name without 'in'
        game_state = self._make_game_state([hero, villain])

        messages = ['Batman raises $50']
        game_data = self._make_game_data(game_state, messages=messages)

        result = _build_opponent_infos(game_data, game_state, 'Hero')

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].preflop_action, 'open_raise')

    def test_extracts_postflop_aggression(self):
        """Extracts postflop_aggression_this_hand for each opponent."""
        from flask_app.services.coach_engine import _build_opponent_infos

        hero = self._make_player("Hero")
        villain = self._make_player("Batman")
        game_state = self._make_game_state([hero, villain])

        messages = ['--- FLOP ---', 'Batman bets $50']
        game_data = self._make_game_data(game_state, state_machine_phase='FLOP', messages=messages)

        result = _build_opponent_infos(game_data, game_state, 'Hero')

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].postflop_aggression_this_hand, 'bet')


if __name__ == '__main__':
    unittest.main()
