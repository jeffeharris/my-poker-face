import unittest

from poker.poker_game import determine_winner, PokerGameState, Player, Card


class PokerTestCase(unittest.TestCase):
    def setUp(self):
        # Create proper Player objects with required fields
        self.player1 = Player(
            name='john',
            stack=1000,
            is_human=False,
            bet=100,
            hand=(Card('A', 'spades'), Card('K', 'hearts')),
            is_folded=False,
        )
        self.player2 = Player(
            name='jane',
            stack=1000,
            is_human=False,
            bet=100,
            hand=(Card('2', 'diamonds'), Card('3', 'clubs')),
            is_folded=False,
        )
        # Create community cards
        self.community_cards = (
            Card('J', 'diamonds'),
            Card('Q', 'spades'),
            Card('10', 'hearts'),
            Card('7', 'spades'),
            Card('6', 'clubs'),
        )
        # Create proper PokerGameState
        self.game_state = PokerGameState(
            players=(self.player1, self.player2),
            community_cards=self.community_cards,
            pot={'total': 200},
        )

    def test_determine_winner(self):
        result = determine_winner(self.game_state)
        # John has A-K with community J-Q-10-7-6, making a straight (A-K-Q-J-10)
        # Jane has 2-3 with same community, no made hand
        # John should win
        self.assertIn('winnings', result)
        self.assertIn('john', result['winnings'])

    def test_determine_winner_folded(self):
        # Mark player1 as folded
        folded_player1 = self.player1.update(is_folded=True)
        game_state = PokerGameState(
            players=(folded_player1, self.player2),
            community_cards=self.community_cards,
            pot={'total': 200},
        )
        result = determine_winner(game_state)
        # Jane should win since John folded
        self.assertIn('winnings', result)

    def test_determine_winner_no_players(self):
        # Empty players tuple
        game_state = PokerGameState(
            players=(),
            community_cards=self.community_cards,
            pot={'total': 0},
        )
        result = determine_winner(game_state)
        # Should handle gracefully with empty winnings
        self.assertIn('winnings', result)
        self.assertEqual(result['winnings'], {})

    def test_determine_winner_no_community_cards(self):
        # Empty community cards - players use only their hole cards
        game_state = PokerGameState(
            players=(self.player1, self.player2),
            community_cards=(),
            pot={'total': 200},
        )
        result = determine_winner(game_state)
        # Should still determine winner based on hole cards alone
        self.assertIn('winnings', result)


if __name__ == '__main__':
    unittest.main()
