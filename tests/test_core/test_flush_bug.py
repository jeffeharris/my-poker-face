import unittest
from poker.hand_evaluator import HandEvaluator
from core.card import Card


class TestFlushBugFix(unittest.TestCase):
    """Test cases for the flush evaluation bug fix"""
    
    def test_flush_with_more_than_five_cards(self):
        """Test that flush evaluation only returns the best 5 cards"""
        # Create 7 cards of the same suit (not a straight/royal flush)
        cards = [
            Card('A', 'clubs'),
            Card('K', 'clubs'), 
            Card('Q', 'clubs'),
            Card('J', 'clubs'),
            Card('9', 'clubs'),  # Skip 10 to avoid straight
            Card('7', 'clubs'),
            Card('5', 'clubs')
        ]
        
        evaluator = HandEvaluator(cards)
        result = evaluator.evaluate_hand()
        
        # Should have a flush (rank 5)
        self.assertEqual(result['hand_rank'], 5)
        self.assertEqual(result['hand_name'], 'Flush with clubs')
        
        # Should only have 5 cards in hand_values
        self.assertEqual(len(result['hand_values']), 5)
        
        # Should be the best 5 cards (A, K, Q, J, 9)
        expected_values = [14, 13, 12, 11, 9]  # Card values for A, K, Q, J, 9
        self.assertEqual(result['hand_values'], expected_values)
    
    def test_flush_bug_scenario(self):
        """Test the specific scenario described in the bug:
        - Community cards are A high flush of clubs
        - Player A: has 2 hearts, one an Ace
        - Player B: has KC and AD
        - Player B should win with K♣
        """
        # Community cards: A♣, Q♣, 9♣, 6♣, 3♣
        community = [
            Card('A', 'clubs'),
            Card('Q', 'clubs'),
            Card('9', 'clubs'),
            Card('6', 'clubs'),
            Card('3', 'clubs')
        ]
        
        # Player A: A♥, 7♥
        player_a_cards = [Card('A', 'hearts'), Card('7', 'hearts')] + community
        
        # Player B: K♣, A♦
        player_b_cards = [Card('K', 'clubs'), Card('A', 'diamonds')] + community
        
        # Evaluate both hands
        eval_a = HandEvaluator(player_a_cards)
        eval_b = HandEvaluator(player_b_cards)
        
        hand_a = eval_a.evaluate_hand()
        hand_b = eval_b.evaluate_hand()
        
        # Both should have flushes (rank 5)
        self.assertEqual(hand_a['hand_rank'], 5)
        self.assertEqual(hand_b['hand_rank'], 5)
        
        # Player A's flush: A♣, Q♣, 9♣, 6♣, 3♣ (all community)
        # Player B's flush: A♣, K♣, Q♣, 9♣, 6♣ (includes their K♣)
        
        # Player B should have a better flush
        self.assertNotEqual(hand_a['hand_values'], hand_b['hand_values'])
        
        # Check that Player B has K♣ in their flush
        self.assertIn(13, hand_b['hand_values'])  # 13 is the value for King
        
        # Player B's second card should be K (13), Player A's should be Q (12)
        self.assertEqual(hand_b['hand_values'][1], 13)
        self.assertEqual(hand_a['hand_values'][1], 12)
        
        # Therefore Player B wins
        self.assertGreater(hand_b['hand_values'][1], hand_a['hand_values'][1])
    
    def test_flush_exactly_five_cards(self):
        """Test that flush with exactly 5 cards works correctly"""
        cards = [
            Card('A', 'clubs'),
            Card('K', 'clubs'),
            Card('Q', 'clubs'),
            Card('J', 'clubs'),
            Card('9', 'clubs'),  # Skip 10 to avoid straight flush
            Card('8', 'hearts'),
            Card('7', 'hearts')
        ]
        
        evaluator = HandEvaluator(cards)
        result = evaluator.evaluate_hand()
        
        # Should have a flush (rank 5)
        self.assertEqual(result['hand_rank'], 5)
        self.assertEqual(len(result['hand_values']), 5)
        self.assertEqual(result['hand_values'], [14, 13, 12, 11, 9])


if __name__ == '__main__':
    unittest.main()