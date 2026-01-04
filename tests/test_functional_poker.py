import unittest
from dataclasses import replace

from poker.poker_game import determine_winner, PokerGameState, Player


class PokerTestCase(unittest.TestCase):
    def setUp(self):
        self.game_state = PokerGameState(
            players=(
                Player(
                    name='john',
                    stack=10000,
                    is_human=False,
                    bet=100,
                    hand=({'rank': 'A', 'suit': 'spades'}, {'rank': 'K', 'suit': 'hearts'}),
                    is_folded=False,
                ),
                Player(
                    name='jane',
                    stack=10000,
                    is_human=False,
                    bet=100,
                    hand=({'rank': '2', 'suit': 'diamonds'}, {'rank': '3', 'suit': 'clubs'}),
                    is_folded=False,
                )
            ),
            community_cards=({'rank': 'J', 'suit': 'diamonds'}, {'rank': 'Q', 'suit': 'spades'},
                            {'rank': '10', 'suit': 'hearts'}, {'rank': '7', 'suit': 'spades'},
                            {'rank': '6', 'suit': 'clubs'}),
            pot={'total': 200}
        )

    def test_determine_winner(self):
        """Test that determine_winner correctly identifies the winner."""
        result = determine_winner(self.game_state)
        
        # Check that result is a dict with expected keys
        self.assertIn('winnings', result)
        self.assertIn('winning_hand', result)
        self.assertIn('hand_name', result)
        
        # John has A-K and can make a straight with community cards (A, K, Q, J, 10)
        # John should win
        self.assertIn('john', result['winnings'])
        self.assertEqual(result['winnings']['john'], 200)
        self.assertEqual(result['hand_name'], '14 high Straight')

    def test_determine_winner_folded(self):
        """Test that determine_winner handles folded players correctly."""
        # Update the first player to be folded
        folded_player = replace(self.game_state.players[0], is_folded=True)
        game_state_with_fold = replace(self.game_state, players=(folded_player, self.game_state.players[1]))
        
        result = determine_winner(game_state_with_fold)
        
        # Jane should win since John folded
        self.assertIn('jane', result['winnings'])
        self.assertEqual(result['winnings']['jane'], 200)
        self.assertNotIn('john', result['winnings'])

    def test_determine_winner_no_players(self):
        """Test that determine_winner handles empty player list."""
        # Create game state with no players having bets
        no_bet_player1 = replace(self.game_state.players[0], bet=0)
        no_bet_player2 = replace(self.game_state.players[1], bet=0)
        game_state_no_bets = replace(self.game_state, players=(no_bet_player1, no_bet_player2))
        
        result = determine_winner(game_state_no_bets)
        
        # With no active players (no bets), winnings should be empty
        self.assertEqual(result['winnings'], {})

    def test_determine_winner_no_community_cards(self):
        """Test that determine_winner works with no community cards."""
        game_state_no_community = replace(self.game_state, community_cards=())
        
        result = determine_winner(game_state_no_community)
        
        # John has A-K which is better than Jane's 2-3
        self.assertIn('john', result['winnings'])
        self.assertEqual(result['winnings']['john'], 200)
        self.assertEqual(result['hand_name'], 'High Card')


if __name__ == '__main__':
    unittest.main()
