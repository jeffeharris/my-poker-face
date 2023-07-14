import unittest
from cards import *
from poker import HandEvaluator, Game, Player, AIPlayer
from unittest.mock import MagicMock
import json
import pickle


class TestDetermineStartPlayer(unittest.TestCase):
    def test_2_remaining_1_folded(self):
        player1 = Player(name="Player1")
        player2 = Player(name="Player2")
        player3 = Player(name="Player3")
        game = Game(player1, player2, player3)

        game.set_dealer(player1)
        game.set_current_round("turn")
        player1.folded = True
        player2.folded = False
        player3.folded = False

        self.assertEqual(game.determine_start_player(), player2, msg="Working as expected!")
        
    def test_1_remaining_2_folded(self):
        player1 = Player(name="Player1")
        player2 = Player(name="Player2")
        player3 = Player(name="Player3")
        game = Game(player1, player2, player3)

        game.set_dealer(player1)
        game.set_current_round("flop")
        player1.folded = True
        player2.folded = True
        player3.folded = False

        self.assertEqual(game.determine_start_player(), player3, msg="Working as expected!")
        
    def test_dealer_remaining_1_folded(self):
        dealer = Player(name="Player1")
        player2 = Player(name="Player2")
        player3 = Player(name="Player3")
        game = Game(dealer, player2, player3)

        game.set_dealer(dealer)
        game.set_current_round("flop")
        dealer.folded = False
        player2.folded = True
        player3.folded = False

        self.assertEqual(game.determine_start_player(), player3, msg="Working as expected!")
        
    def test_dealer_remaining_2_folded(self):
        dealer = Player(name="Player1")
        player2 = Player(name="Player2")
        player3 = Player(name="Player3")
        game = Game(dealer, player2, player3)

        game.set_dealer(dealer)
        game.set_current_round("flop")
        dealer.folded = False
        player2.folded = True
        player3.folded = True

        self.assertEqual(game.determine_start_player(), dealer, msg="Working as expected!")


class TestHandEvaluator(unittest.TestCase):
    def test_high_card(self):
        cards = [
            Card(rank='2', suit='hearts'),
            Card(rank='3', suit='diamonds'),
            Card(rank='4', suit='clubs'),
            Card(rank='5', suit='hearts'),
            Card(rank='7', suit='spades'),
        ]
        evaluator = HandEvaluator(cards)
        result = evaluator.evaluate_hand()
        self.assertEqual(result, {"hand_rank": 10, "hand_values": [], "kicker_values": [7, 5, 4, 3, 2]})

    def test_one_pair(self):
        cards = [
            Card(rank='2', suit='hearts'),
            Card(rank='2', suit='diamonds'),
            Card(rank='4', suit='clubs'),
            Card(rank='5', suit='hearts'),
            Card(rank='7', suit='spades'),
        ]
        evaluator = HandEvaluator(cards)
        result = evaluator.evaluate_hand()
        self.assertEqual(result, {"hand_rank": 9, "hand_values": [2, 2], "kicker_values": [7, 5, 4]})
        
    def test_hand_one(self):
        cards = [
            Card(rank='A', suit='hearts'),
            Card(rank='K', suit='hearts'),
            Card(rank='Q', suit='hearts'),
            Card(rank='J', suit='hearts'),
            Card(rank='10', suit='hearts'),
        ]
        evaluator = HandEvaluator(cards)
        result = evaluator.evaluate_hand()
        self.assertEqual(result, {"hand_rank": 1, "hand_values": [14, 13, 12, 11, 10], "kicker_values": []})
        
        
class TestGame(unittest.TestCase):
    def test_determine_winner(self):
        game = Game(Player("Winner"), Player("Loser"))
        game.community_cards = [
            Card(rank='2', suit='hearts'),
            Card(rank='3', suit='diamonds'),
            Card(rank='4', suit='clubs'),
            Card(rank='5', suit='hearts'),
            Card(rank='6', suit='spades'),
        ]
        game.players[0].cards = [
            Card(rank='7', suit='hearts'),
            Card(rank='8', suit='diamonds'),
        ]
        game.players[1].cards = [
            Card(rank='9', suit='hearts'),
            Card(rank='10', suit='diamonds'),
        ]
        winner = game.determine_winner()
        self.assertEqual(winner, game.players[0])
        
    def test_three_player_game_1(self):
        player1 = Player("Winner")
        player2 = Player("Player2")
        player3 = Player("Player3")
        game = Game(player1, player2, player3)

        # Scenario 1: Player 1 wins with a straight flush
        game.community_cards = [
            Card(rank='6', suit='hearts'),
            Card(rank='7', suit='hearts'),
            Card(rank='8', suit='hearts'),
            Card(rank='10', suit='diamonds'),
            Card(rank='2', suit='spades'),
        ]
        player1.cards = [
            Card(rank='9', suit='hearts'),
            Card(rank='10', suit='hearts'),
        ]
        player2.cards = [
            Card(rank='4', suit='hearts'),
            Card(rank='3', suit='hearts'),
        ]
        player3.cards = [
            Card(rank='A', suit='hearts'),
            Card(rank='K', suit='hearts'),
        ]
        self.assertEqual(game.determine_winner(), player1)

    def test_three_player_game_2(self):
        player1 = Player("Player1")
        player2 = Player("Winner")
        player3 = Player("Player3")
        game = Game(player1, player2, player3)
        
        # Scenario 2: Player 2 wins with a four of a kind
        game.community_cards = [
            Card(rank='6', suit='hearts'),
            Card(rank='6', suit='diamonds'),
            Card(rank='8', suit='hearts'),
            Card(rank='10', suit='hearts'),
            Card(rank='2', suit='spades'),
        ]
        player1.cards = [
            Card(rank='9', suit='hearts'),
            Card(rank='5', suit='hearts'),
        ]
        player2.cards = [
            Card(rank='6', suit='clubs'),
            Card(rank='6', suit='spades'),
        ]
        player3.cards = [
            Card(rank='A', suit='hearts'),
            Card(rank='K', suit='hearts'),
        ]
        self.assertEqual(game.determine_winner(), player2)


class TestBettingRound(unittest.TestCase):
    def test_first_player_folds(self):
        # Create a game with 3 players
        player1 = Player("Player 1")
        player2 = Player("Player 2")
        player3 = Player("Player 3")
        game = Game(player1, player2, player3)
        game.dealer = player1

        # Set up the game state
        game.players = [player1, player2, player3]
        game.current_bet = 10
        game.pot = 30
        player1.money = 50
        player2.money = 50
        player3.money = 50

        # Mock the Player action method
        # Player 1 will fold, Player 2 and 3 will call
        player1.action = MagicMock(return_value=("fold", 0))
        player2.action = MagicMock(return_value=("call", game.current_bet))
        player3.action = MagicMock(return_value=("call", game.current_bet))

        # Run the betting round
        game.betting_round()

        # Check that the game state is as expected
        self.assertEqual(player1.money, 50)  # Player 1 didn't bet anything
        self.assertEqual(player2.money, 40)  # Player 2 bet 10
        self.assertEqual(player3.money, 40)  # Player 3 bet 10
        self.assertEqual(game.pot, 50)  # The pot increased by 20
        self.assertTrue(player1.folded)  # Player 1 folded
        self.assertFalse(player2.folded)  # Player 2 didn't fold
        self.assertFalse(player3.folded)  # Player 3 didn't fold

    def test_three_player_game_3(self):
        player1 = Player("Palyer1")
        player2 = Player("Player2")
        player3 = Player("Winner")
        game = Game(player1, player2, player3)
        
        # Scenario 3: Player 3 wins with a full house
        game.community_cards = [
            Card(rank='6', suit='hearts'),
            Card(rank='6', suit='diamonds'),
            Card(rank='8', suit='hearts'),
            Card(rank='8', suit='diamonds'),
            Card(rank='2', suit='spades'),
        ]
        player1.cards = [
            Card(rank='9', suit='hearts'),
            Card(rank='5', suit='hearts'),
        ]
        player2.cards = [
            Card(rank='4', suit='hearts'),
            Card(rank='3', suit='hearts'),
        ]
        player3.cards = [
            Card(rank='8', suit='clubs'),
            Card(rank='6', suit='spades'),
        ]
        self.assertEqual(game.determine_winner(), player3)
        
    def test_simulate_game(self):
        player1 = AIPlayer("Player1")
        player2 = AIPlayer("Player2")
        player3 = AIPlayer("Player3")
        game = Game(player1, player2, player3)
        
        game.play_hand()
        
        self.assertTrue(game.determine_winner() in game.players)


if __name__ == '__main__':
    unittest.main()
    