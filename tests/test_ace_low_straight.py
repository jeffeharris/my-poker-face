import unittest
from core.card import Card
from poker.hand_evaluator import HandEvaluator


class TestAceLowStraight(unittest.TestCase):
    def test_ace_low_straight_beats_two_pair(self):
        """Test that A-2-3-4-5 straight (wheel) beats two pair."""
        # Community cards: 3♠, 5♠, 7♣, 9♣, A♥
        # Jeff's hand: 4♠, 2♦ -> makes A-2-3-4-5 straight
        # Terry's hand: 9♦, 3♦ -> makes two pair (9s and 3s)
        
        # Jeff's cards (A-2-3-4-5 straight)
        jeff_cards = [
            Card('4', 'Spades'),
            Card('2', 'Diamonds'),
            Card('3', 'Spades'),
            Card('5', 'Spades'),
            Card('7', 'Clubs'),
            Card('9', 'Clubs'),
            Card('A', 'Hearts')
        ]
        
        # Terry's cards (two pair - 9s and 3s)
        terry_cards = [
            Card('9', 'Diamonds'),
            Card('3', 'Diamonds'),
            Card('3', 'Spades'),
            Card('5', 'Spades'),
            Card('7', 'Clubs'),
            Card('9', 'Clubs'),
            Card('A', 'Hearts')
        ]
        
        # Evaluate hands
        jeff_hand = HandEvaluator(jeff_cards).evaluate_hand()
        terry_hand = HandEvaluator(terry_cards).evaluate_hand()
        
        # Debug output
        print(f"Jeff's hand: {jeff_hand}")
        print(f"Terry's hand: {terry_hand}")
        
        # Verify Jeff has a straight (rank 6)
        self.assertEqual(jeff_hand['hand_rank'], 6, "Jeff should have a straight")
        self.assertEqual(jeff_hand['hand_name'], "5 high Straight (Wheel)")
        
        # Verify Terry has two pair (rank 8)
        self.assertEqual(terry_hand['hand_rank'], 8, "Terry should have two pair")
        
        # Straight (rank 6) should beat two pair (rank 8)
        # Lower rank number = better hand
        self.assertLess(jeff_hand['hand_rank'], terry_hand['hand_rank'], 
                       "Straight should beat two pair")

    def test_ace_low_straight_recognition(self):
        """Test that the ace-low straight is properly recognized."""
        cards = [
            Card('A', 'Hearts'),
            Card('2', 'Diamonds'),
            Card('3', 'Clubs'),
            Card('4', 'Spades'),
            Card('5', 'Hearts'),
            Card('K', 'Clubs'),  # Extra high card
            Card('J', 'Diamonds')  # Extra high card
        ]
        
        result = HandEvaluator(cards).evaluate_hand()
        
        self.assertEqual(result['hand_rank'], 6, "Should be a straight")
        self.assertEqual(result['hand_name'], "5 high Straight (Wheel)")
        self.assertEqual(result['hand_values'], [5, 4, 3, 2, 1])


if __name__ == '__main__':
    unittest.main()