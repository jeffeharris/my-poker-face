"""
Tests for high card hand comparison.

These tests verify that high card hands are compared correctly:
- Higher card wins (Ace beats King)
- Kickers are compared in order (2nd card, then 3rd, etc.)
- Ties split the pot

This addresses a bug where sorted() was incorrectly applied to already-sorted
kicker values, causing Ace-high to lose to King-high in certain scenarios.
"""

import unittest

from poker.poker_game import determine_winner, PokerGameState, Player, Card


class TestHighCardComparison(unittest.TestCase):
    """Tests for high card vs high card hand comparisons."""

    def test_ace_high_beats_king_high(self):
        """Ace high should beat King high - the original bug scenario."""
        # Player 1 has Ace high
        player1 = Player(
            name='Alice',
            stack=1000,
            is_human=False,
            bet=100,
            hand=(Card('A', 'spades'), Card('3', 'hearts')),
            is_folded=False,
        )
        # Player 2 has King high
        player2 = Player(
            name='Bob',
            stack=1000,
            is_human=False,
            bet=100,
            hand=(Card('K', 'hearts'), Card('Q', 'spades')),
            is_folded=False,
        )
        # Community cards that don't make any pairs or better hands
        community_cards = (
            Card('7', 'diamonds'),
            Card('5', 'clubs'),
            Card('4', 'spades'),
            Card('9', 'hearts'),
            Card('2', 'diamonds'),
        )
        game_state = PokerGameState(
            deck=(),
            players=(player1, player2),
            community_cards=community_cards,
            pot={'total': 200},
            current_dealer_idx=0,
        )

        result = determine_winner(game_state)

        # Alice with Ace high should win
        self.assertEqual(len(result['pot_breakdown']), 1)
        pot = result['pot_breakdown'][0]
        self.assertEqual(len(pot['winners']), 1)
        self.assertEqual(pot['winners'][0]['name'], 'Alice')
        self.assertEqual(pot['winners'][0]['amount'], 200)

    def test_ace_high_with_low_kickers_beats_king_high_with_high_kickers(self):
        """Ace-3-2 should beat K-Q-J because Ace is higher than King."""
        # Player 1 has A-3 (Ace high with very low kicker)
        player1 = Player(
            name='Alice',
            stack=1000,
            is_human=False,
            bet=100,
            hand=(Card('A', 'spades'), Card('3', 'hearts')),
            is_folded=False,
        )
        # Player 2 has K-Q (King high with high kicker)
        player2 = Player(
            name='Bob',
            stack=1000,
            is_human=False,
            bet=100,
            hand=(Card('K', 'hearts'), Card('Q', 'spades')),
            is_folded=False,
        )
        # Community cards: J-10-6-5-2 (no pairs, straights, or flushes)
        community_cards = (
            Card('J', 'diamonds'),
            Card('10', 'clubs'),
            Card('6', 'diamonds'),
            Card('5', 'clubs'),
            Card('2', 'diamonds'),
        )
        game_state = PokerGameState(
            deck=(),
            players=(player1, player2),
            community_cards=community_cards,
            pot={'total': 200},
            current_dealer_idx=0,
        )

        result = determine_winner(game_state)

        # Alice's hand: A-J-10-6-5 (Ace high)
        # Bob's hand: K-Q-J-10-6 (King high)
        # Alice should win because Ace > King
        pot = result['pot_breakdown'][0]
        self.assertEqual(len(pot['winners']), 1)
        self.assertEqual(pot['winners'][0]['name'], 'Alice')

    def test_same_high_card_second_kicker_wins(self):
        """When high cards are equal, second highest card wins."""
        # Both players have Ace, but Alice has King kicker
        player1 = Player(
            name='Alice',
            stack=1000,
            is_human=False,
            bet=100,
            hand=(Card('A', 'spades'), Card('K', 'hearts')),
            is_folded=False,
        )
        # Bob has Ace with Queen kicker
        player2 = Player(
            name='Bob',
            stack=1000,
            is_human=False,
            bet=100,
            hand=(Card('A', 'hearts'), Card('Q', 'spades')),
            is_folded=False,
        )
        # Cards spread far enough apart to avoid straights
        community_cards = (
            Card('9', 'diamonds'),
            Card('7', 'clubs'),
            Card('5', 'spades'),
            Card('3', 'hearts'),
            Card('2', 'diamonds'),
        )
        game_state = PokerGameState(
            deck=(),
            players=(player1, player2),
            community_cards=community_cards,
            pot={'total': 200},
            current_dealer_idx=0,
        )

        result = determine_winner(game_state)

        # Alice's hand: A-K-9-7-5 beats Bob's A-Q-9-7-5
        pot = result['pot_breakdown'][0]
        self.assertEqual(len(pot['winners']), 1)
        self.assertEqual(pot['winners'][0]['name'], 'Alice')

    def test_same_high_card_third_kicker_wins(self):
        """When first two cards are equal, third highest card wins."""
        # Both have A-K, Alice has Jack kicker
        player1 = Player(
            name='Alice',
            stack=1000,
            is_human=False,
            bet=100,
            hand=(Card('A', 'spades'), Card('J', 'hearts')),
            is_folded=False,
        )
        # Bob has A-K with 10 kicker (from hand)
        player2 = Player(
            name='Bob',
            stack=1000,
            is_human=False,
            bet=100,
            hand=(Card('A', 'hearts'), Card('10', 'spades')),
            is_folded=False,
        )
        # Community provides King for both, spread cards to avoid straights
        community_cards = (
            Card('K', 'diamonds'),
            Card('7', 'clubs'),
            Card('5', 'spades'),
            Card('3', 'hearts'),
            Card('2', 'diamonds'),
        )
        game_state = PokerGameState(
            deck=(),
            players=(player1, player2),
            community_cards=community_cards,
            pot={'total': 200},
            current_dealer_idx=0,
        )

        result = determine_winner(game_state)

        # Alice's hand: A-K-J-7-5 beats Bob's A-K-10-7-5
        pot = result['pot_breakdown'][0]
        self.assertEqual(len(pot['winners']), 1)
        self.assertEqual(pot['winners'][0]['name'], 'Alice')

    def test_identical_high_cards_split_pot(self):
        """Identical high card hands should split the pot."""
        # Both players have same hole cards (different suits)
        player1 = Player(
            name='Alice',
            stack=1000,
            is_human=False,
            bet=100,
            hand=(Card('A', 'spades'), Card('K', 'hearts')),
            is_folded=False,
        )
        player2 = Player(
            name='Bob',
            stack=1000,
            is_human=False,
            bet=100,
            hand=(Card('A', 'hearts'), Card('K', 'spades')),
            is_folded=False,
        )
        # Spread cards to avoid straights
        community_cards = (
            Card('9', 'diamonds'),
            Card('7', 'clubs'),
            Card('5', 'spades'),
            Card('3', 'hearts'),
            Card('2', 'diamonds'),
        )
        game_state = PokerGameState(
            deck=(),
            players=(player1, player2),
            community_cards=community_cards,
            pot={'total': 200},
            current_dealer_idx=0,
        )

        result = determine_winner(game_state)

        # Both have A-K-9-7-5, should split
        pot = result['pot_breakdown'][0]
        self.assertEqual(len(pot['winners']), 2)
        winner_amounts = {w['name']: w['amount'] for w in pot['winners']}
        self.assertEqual(winner_amounts['Alice'], 100)
        self.assertEqual(winner_amounts['Bob'], 100)

    def test_fifth_kicker_wins(self):
        """When first four cards are equal, fifth kicker decides."""
        # Both have A-K from community, Alice has 8, Bob has 7
        player1 = Player(
            name='Alice',
            stack=1000,
            is_human=False,
            bet=100,
            hand=(Card('8', 'spades'), Card('2', 'hearts')),
            is_folded=False,
        )
        player2 = Player(
            name='Bob',
            stack=1000,
            is_human=False,
            bet=100,
            hand=(Card('7', 'hearts'), Card('2', 'spades')),
            is_folded=False,
        )
        # Community provides A-K-Q-J (first four cards for both)
        community_cards = (
            Card('A', 'diamonds'),
            Card('K', 'clubs'),
            Card('Q', 'spades'),
            Card('J', 'hearts'),
            Card('3', 'diamonds'),
        )
        game_state = PokerGameState(
            deck=(),
            players=(player1, player2),
            community_cards=community_cards,
            pot={'total': 200},
            current_dealer_idx=0,
        )

        result = determine_winner(game_state)

        # Alice's hand: A-K-Q-J-8 beats Bob's A-K-Q-J-7
        pot = result['pot_breakdown'][0]
        self.assertEqual(len(pot['winners']), 1)
        self.assertEqual(pot['winners'][0]['name'], 'Alice')


if __name__ == '__main__':
    unittest.main()
