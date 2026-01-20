"""Tests for run-it-out and all-in scenarios in betting round transitions."""

import unittest
from poker.poker_game import Player, PokerGameState
from poker.poker_state_machine import (
    run_betting_round_transition,
    ImmutableStateMachine,
    PokerPhase,
)


def create_test_state(players_data: list, phase: PokerPhase = PokerPhase.FLOP,
                      community_cards: tuple = ()) -> ImmutableStateMachine:
    """Helper to create a test state with given player configurations.

    Note: highest_bet is computed automatically from player bets.
    """
    players = tuple(
        Player(
            name=p['name'],
            stack=p.get('stack', 1000),
            bet=p.get('bet', 0),
            is_human=p.get('is_human', False),
            is_folded=p.get('is_folded', False),
            is_all_in=p.get('is_all_in', False),
            has_acted=p.get('has_acted', False),
            hand=(),
        )
        for p in players_data
    )

    game_state = PokerGameState(deck=(),
        players=players,
        community_cards=community_cards,
        current_player_idx=0,
    )

    return ImmutableStateMachine(
        game_state=game_state,
        phase=phase,
    )


class TestRunItOutScenarios(unittest.TestCase):
    """Test cases for run-it-out logic in betting round transitions."""

    def test_everyone_folded_except_one_goes_to_showdown(self):
        """When all but one player has folded, go straight to showdown."""
        state = create_test_state([
            {'name': 'Human', 'is_human': True, 'stack': 1000},
            {'name': 'AI1', 'is_folded': True},
            {'name': 'AI2', 'is_folded': True},
        ])

        result = run_betting_round_transition(state)

        self.assertEqual(result.phase, PokerPhase.SHOWDOWN)

    def test_user_all_in_opponent_needs_to_call(self):
        """When user goes all-in and opponent needs to call, wait for action."""
        # Human bet 1000 (all-in), AI1 bet 0 - AI1 needs to call
        state = create_test_state([
            {'name': 'Human', 'is_human': True, 'stack': 0, 'bet': 1000, 'is_all_in': True},
            {'name': 'AI1', 'stack': 500, 'bet': 0, 'is_all_in': False, 'has_acted': False},
            {'name': 'AI2', 'is_folded': True},
        ])

        result = run_betting_round_transition(state)

        # Should wait for AI1 to call/fold (AI1 bet 0 < highest_bet 1000)
        self.assertTrue(result.game_state.awaiting_action)
        self.assertFalse(result.game_state.run_it_out)

    def test_all_players_all_in_run_it_out(self):
        """When all players are all-in, run it out."""
        # Both have matched at 1000
        state = create_test_state([
            {'name': 'Human', 'is_human': True, 'stack': 0, 'bet': 1000, 'is_all_in': True},
            {'name': 'AI1', 'stack': 0, 'bet': 1000, 'is_all_in': True},
            {'name': 'AI2', 'is_folded': True},
        ])

        result = run_betting_round_transition(state)

        self.assertTrue(result.game_state.awaiting_action)
        self.assertTrue(result.game_state.run_it_out)

    def test_one_player_has_chips_others_all_in_run_it_out(self):
        """When one player has chips but others are all-in, run it out (no one to bet against)."""
        # New betting round - bets are 0, but AI1 is all-in from previous round
        state = create_test_state([
            {'name': 'Human', 'is_human': True, 'stack': 500, 'bet': 0, 'is_all_in': False, 'has_acted': False},
            {'name': 'AI1', 'stack': 0, 'bet': 0, 'is_all_in': True},
            {'name': 'AI2', 'is_folded': True},
        ])

        result = run_betting_round_transition(state)

        # Human has chips but no one to bet against - should run it out
        self.assertTrue(result.game_state.awaiting_action)
        self.assertTrue(result.game_state.run_it_out)

    def test_one_player_needs_to_call_all_in(self):
        """When one player needs to call an all-in bet, wait for their action."""
        # Human bet 2000, AI1 only bet 500 - AI1 needs to call more
        state = create_test_state([
            {'name': 'Human', 'is_human': True, 'stack': 0, 'bet': 2000, 'is_all_in': True},
            {'name': 'AI1', 'stack': 500, 'bet': 500, 'is_all_in': False, 'has_acted': False},
            {'name': 'AI2', 'is_folded': True},
        ])

        result = run_betting_round_transition(state)

        # AI1 needs to call (their bet 500 < highest_bet 2000)
        self.assertTrue(result.game_state.awaiting_action)
        self.assertFalse(result.game_state.run_it_out)

    def test_multiple_players_can_act_normal_betting(self):
        """When multiple players can act, normal betting continues."""
        state = create_test_state([
            {'name': 'Human', 'is_human': True, 'stack': 1000, 'bet': 100, 'has_acted': False},
            {'name': 'AI1', 'stack': 1000, 'bet': 100, 'has_acted': False},
            {'name': 'AI2', 'stack': 1000, 'bet': 100, 'has_acted': False},
        ])

        result = run_betting_round_transition(state)

        # Multiple players can act - should wait for action (normal betting)
        self.assertTrue(result.game_state.awaiting_action)
        self.assertFalse(result.game_state.run_it_out)

    def test_new_betting_round_after_all_in_bets_reset(self):
        """After dealing cards, bets reset. Player with chips shouldn't be asked to act."""
        # Simulating start of FLOP after PRE_FLOP where everyone went all-in
        # Bets are reset to 0 at start of new betting round
        state = create_test_state([
            {'name': 'Human', 'is_human': True, 'stack': 200, 'bet': 0, 'is_all_in': False, 'has_acted': False},
            {'name': 'AI1', 'stack': 0, 'bet': 0, 'is_all_in': True, 'has_acted': False},
            {'name': 'AI2', 'stack': 0, 'bet': 0, 'is_all_in': True, 'has_acted': False},
        ])

        result = run_betting_round_transition(state)

        # Human has chips but opponents are all-in - no one to bet against
        # Should run it out, not ask for action
        self.assertTrue(result.game_state.run_it_out)

    def test_heads_up_one_all_in_one_has_chips(self):
        """Heads up: one player all-in, one has chips remaining."""
        state = create_test_state([
            {'name': 'Human', 'is_human': True, 'stack': 500, 'bet': 0, 'is_all_in': False},
            {'name': 'AI1', 'stack': 0, 'bet': 0, 'is_all_in': True},
        ])

        result = run_betting_round_transition(state)

        # Only 1 player can act, no one owes money - run it out
        self.assertTrue(result.game_state.run_it_out)


class TestEdgeCases(unittest.TestCase):
    """Edge cases for run-it-out logic."""

    def test_player_with_zero_chips_not_all_in(self):
        """Player with 0 chips who isn't marked all-in (edge case).

        Note: This is a data inconsistency - a player with 0 chips should be
        marked as all-in. The current logic counts them as "can act" since
        we only check is_all_in and is_folded flags, not actual chip count.
        """
        state = create_test_state([
            {'name': 'Human', 'is_human': True, 'stack': 0, 'bet': 0, 'is_all_in': False},
            {'name': 'AI1', 'stack': 1000, 'bet': 0, 'is_all_in': False},
        ])

        result = run_betting_round_transition(state)

        # Both players counted as "can act" (is_all_in=False for both)
        # So num_can_act = 2, normal betting continues
        self.assertTrue(result.game_state.awaiting_action)
        self.assertFalse(result.game_state.run_it_out)  # Not run_it_out because 2 "can act"

    def test_partial_call_still_needs_action(self):
        """Player made partial call, still needs to complete or fold."""
        # Human bet 1000, AI1 only bet 300
        state = create_test_state([
            {'name': 'Human', 'is_human': True, 'stack': 0, 'bet': 1000, 'is_all_in': True},
            {'name': 'AI1', 'stack': 200, 'bet': 300, 'is_all_in': False},  # Partial call
        ])

        result = run_betting_round_transition(state)

        # AI1's bet (300) < highest_bet (1000), needs to call more or fold
        self.assertTrue(result.game_state.awaiting_action)
        self.assertFalse(result.game_state.run_it_out)


if __name__ == '__main__':
    unittest.main()
