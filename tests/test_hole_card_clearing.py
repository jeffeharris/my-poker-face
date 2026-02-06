"""Tests for hole card clearing during hand transitions.

Verifies that hole cards are properly cleared between hands while
preserving other player attributes.
"""

import unittest

from poker.poker_game import Player, PokerGameState
from core.card import Card


class TestHoleCardClearing(unittest.TestCase):
    """Test cases for hole card clearing logic."""

    def _create_game_state_with_hands(self) -> PokerGameState:
        """Create a game state where players have hole cards."""
        players = (
            Player(
                name='Human',
                stack=1000,
                is_human=True,
                hand=(Card('A', 'spades'), Card('K', 'hearts')),
                bet=50,
                is_folded=False,
            ),
            Player(
                name='AI1',
                stack=800,
                is_human=False,
                hand=(Card('Q', 'diamonds'), Card('J', 'clubs')),
                bet=100,
                is_folded=False,
            ),
            Player(
                name='AI2',
                stack=500,
                is_human=False,
                hand=(Card('10', 'hearts'), Card('9', 'spades')),
                bet=0,
                is_folded=True,
            ),
        )
        return PokerGameState(
            deck=(),
            players=players,
            community_cards=(Card('2', 'clubs'), Card('3', 'diamonds'), Card('4', 'hearts')),
            current_player_idx=0,
        )

    def test_hole_cards_cleared_successfully(self):
        """Hole cards should be empty tuples after clearing."""
        game_state = self._create_game_state_with_hands()

        # Clear hole cards using the same pattern as handle_evaluating_hand_phase()
        cleared_players = tuple(p.update(hand=()) for p in game_state.players)
        cleared_game_state = game_state.update(players=cleared_players)

        for player in cleared_game_state.players:
            self.assertEqual(player.hand, (), f"{player.name} should have empty hand")

    def test_clearing_is_immutable(self):
        """Original game state should be unchanged after clearing."""
        game_state = self._create_game_state_with_hands()
        original_hands = [player.hand for player in game_state.players]

        # Clear hole cards
        cleared_players = tuple(p.update(hand=()) for p in game_state.players)
        cleared_game_state = game_state.update(players=cleared_players)

        # Original state should be unchanged
        for i, player in enumerate(game_state.players):
            self.assertEqual(player.hand, original_hands[i],
                           f"Original {player.name}'s hand should be unchanged")

        # Cleared state should have empty hands
        for player in cleared_game_state.players:
            self.assertEqual(player.hand, ())

    def test_other_player_attributes_preserved(self):
        """Non-hand attributes should be preserved after clearing."""
        game_state = self._create_game_state_with_hands()

        cleared_players = tuple(p.update(hand=()) for p in game_state.players)
        cleared_game_state = game_state.update(players=cleared_players)

        # Check each player's other attributes are preserved
        for orig, cleared in zip(game_state.players, cleared_game_state.players):
            self.assertEqual(orig.name, cleared.name)
            self.assertEqual(orig.stack, cleared.stack)
            self.assertEqual(orig.is_human, cleared.is_human)
            self.assertEqual(orig.bet, cleared.bet)
            self.assertEqual(orig.is_folded, cleared.is_folded)

    def test_clearing_empty_hands_is_idempotent(self):
        """Clearing already-empty hands should work without error."""
        players = (
            Player(name='Human', stack=1000, is_human=True, hand=()),
            Player(name='AI1', stack=800, is_human=False, hand=()),
        )
        game_state = PokerGameState(
            deck=(),
            players=players,
            community_cards=(),
            current_player_idx=0,
        )

        # Should not raise and should still have empty hands
        cleared_players = tuple(p.update(hand=()) for p in game_state.players)
        cleared_game_state = game_state.update(players=cleared_players)

        for player in cleared_game_state.players:
            self.assertEqual(player.hand, ())

    def test_community_cards_unaffected_by_hole_card_clearing(self):
        """Community cards should remain unchanged when hole cards are cleared."""
        game_state = self._create_game_state_with_hands()
        original_community = game_state.community_cards

        cleared_players = tuple(p.update(hand=()) for p in game_state.players)
        cleared_game_state = game_state.update(players=cleared_players)

        self.assertEqual(cleared_game_state.community_cards, original_community)


if __name__ == '__main__':
    unittest.main()
