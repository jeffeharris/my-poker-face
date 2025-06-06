import unittest

from poker.poker_game import determine_winner


class PokerTestCase(unittest.TestCase):
    def setUp(self):
        self.game_state = {
            'players': [
                {
                    'name': 'john',
                    'hand': [{'rank': 'A', 'suit': 'spades'}, {'rank': 'K', 'suit': 'hearts'}],
                    'is_folded': False,
                },
                {
                    'name': 'jane',
                    'hand': [{'rank': '2', 'suit': 'diamonds'}, {'rank': '3', 'suit': 'clubs'}],
                    'is_folded': False,
                }
            ],
            'community_cards': [{'rank': 'J', 'suit': 'diamonds'}, {'rank': 'Q', 'suit': 'spades'},
                                {'rank': '10', 'suit': 'hearts'}, {'rank': '7', 'suit': 'spades'},
                                {'rank': '6', 'suit': 'clubs'}],
            'pot': 'total'
        }

    def test_determine_winner(self):
        updated_game_state = determine_winner(self.game_state)
        self.assertEqual(updated_game_state, self.game_state)

    def test_determine_winner_folded(self):
        self.game_state['players'][0]['is_folded'] = True
        updated_game_state = determine_winner(self.game_state)
        self.assertEqual(updated_game_state, self.game_state)

    def test_determine_winner_no_players(self):
        self.game_state['players'] = []
        updated_game_state = determine_winner(self.game_state)
        self.assertEqual(updated_game_state, self.game_state)

    def test_determine_winner_no_community_cards(self):
        self.game_state['community_cards'] = []
        updated_game_state = determine_winner(self.game_state)
        self.assertEqual(updated_game_state, self.game_state)


if __name__ == '__main__':
    unittest.main()
