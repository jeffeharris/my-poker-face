"""
Comprehensive tests for pot distribution, split pots, and side pots.

Tests cover:
- Split pots when multiple players have identical hands
- Side pots for all-in situations with different bet amounts
- Multiple side pots with 3+ contribution tiers
- Odd chip distribution (remainder handling)
- Conservation of chips (no chips created or lost)
"""

import unittest

from poker.poker_game import determine_winner, award_pot_winnings, PokerGameState, Player, Card


class TestSplitPots(unittest.TestCase):
    """Tests for split pot scenarios where multiple players have identical hands."""

    def test_split_pot_two_winners_even_amount(self):
        """Two players with identical hands split pot evenly (no remainder)."""
        # Both players have A-K, making identical straights
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
        community_cards = (
            Card('Q', 'diamonds'),
            Card('J', 'clubs'),
            Card('10', 'spades'),
            Card('7', 'hearts'),
            Card('6', 'diamonds'),
        )
        game_state = PokerGameState(
            players=(player1, player2),
            community_cards=community_cards,
            pot={'total': 200},
            current_dealer_idx=0,
        )

        result = determine_winner(game_state)

        self.assertEqual(len(result['pot_breakdown']), 1)
        pot = result['pot_breakdown'][0]
        self.assertEqual(pot['pot_name'], 'Main Pot')
        self.assertEqual(pot['total_amount'], 200)
        self.assertEqual(len(pot['winners']), 2)

        # Both should get exactly 100
        winner_amounts = {w['name']: w['amount'] for w in pot['winners']}
        self.assertEqual(winner_amounts['Alice'], 100)
        self.assertEqual(winner_amounts['Bob'], 100)

    def test_split_pot_two_winners_odd_chip(self):
        """Two players split pot with odd chip - one player gets extra chip.

        Note: For odd chips to occur in a single pot, both players must have
        equal bets. The odd amount comes from antes/blinds included in pot.
        We simulate this by having equal bets but noting the pot includes antes.
        """
        # Both bet 100, but pot has 1 extra chip from antes
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
        # Third player folded but contributed to pot
        player3 = Player(
            name='Charlie',
            stack=1000,
            is_human=False,
            bet=1,  # Small blind that folded
            hand=(Card('2', 'clubs'), Card('3', 'clubs')),
            is_folded=True,
        )
        community_cards = (
            Card('Q', 'diamonds'),
            Card('J', 'clubs'),
            Card('10', 'spades'),
            Card('7', 'hearts'),
            Card('6', 'diamonds'),
        )
        # Alice is dealer (idx 0), Bob is to her left
        game_state = PokerGameState(
            players=(player1, player2, player3),
            community_cards=community_cards,
            pot={'total': 201},
            current_dealer_idx=0,
        )

        result = determine_winner(game_state)

        pot = result['pot_breakdown'][0]
        self.assertEqual(pot['total_amount'], 201)

        # Bob (seat 1) is closer to dealer's left, gets odd chip
        winner_amounts = {w['name']: w['amount'] for w in pot['winners']}
        self.assertEqual(winner_amounts['Bob'], 101)
        self.assertEqual(winner_amounts['Alice'], 100)

        # Verify conservation: total distributed equals pot
        total_distributed = sum(w['amount'] for w in pot['winners'])
        self.assertEqual(total_distributed, 201)

    def test_split_pot_three_winners(self):
        """Three players with identical hands split pot."""
        # All three have A-K making identical straights
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
        player3 = Player(
            name='Charlie',
            stack=1000,
            is_human=False,
            bet=100,
            hand=(Card('A', 'diamonds'), Card('K', 'clubs')),
            is_folded=False,
        )
        community_cards = (
            Card('Q', 'diamonds'),
            Card('J', 'clubs'),
            Card('10', 'spades'),
            Card('7', 'hearts'),
            Card('6', 'diamonds'),
        )
        game_state = PokerGameState(
            players=(player1, player2, player3),
            community_cards=community_cards,
            pot={'total': 300},
            current_dealer_idx=0,
        )

        result = determine_winner(game_state)

        pot = result['pot_breakdown'][0]
        self.assertEqual(len(pot['winners']), 3)

        # Each gets 100
        for winner in pot['winners']:
            self.assertEqual(winner['amount'], 100)

    def test_split_pot_three_winners_with_remainder(self):
        """Three-way split with 2 odd chips distributed by position.

        Pot of 302 split 3 ways: 100 each + 2 odd chips to first two players
        left of dealer.
        """
        # All bet 100, but pot has 2 extra chips from antes/blinds
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
        player3 = Player(
            name='Charlie',
            stack=1000,
            is_human=False,
            bet=100,
            hand=(Card('A', 'diamonds'), Card('K', 'clubs')),
            is_folded=False,
        )
        # Folded player contributed 2 to pot
        player4 = Player(
            name='Dave',
            stack=1000,
            is_human=False,
            bet=2,
            hand=(Card('2', 'clubs'), Card('3', 'clubs')),
            is_folded=True,
        )
        community_cards = (
            Card('Q', 'diamonds'),
            Card('J', 'clubs'),
            Card('10', 'spades'),
            Card('7', 'hearts'),
            Card('6', 'diamonds'),
        )
        # Alice is dealer (idx 0)
        game_state = PokerGameState(
            players=(player1, player2, player3, player4),
            community_cards=community_cards,
            pot={'total': 302},
            current_dealer_idx=0,
        )

        result = determine_winner(game_state)

        pot = result['pot_breakdown'][0]
        # 302 / 3 = 100 remainder 2
        # Bob (seat 1) and Charlie (seat 2) get extra chips as closest to dealer's left

        winner_amounts = {w['name']: w['amount'] for w in pot['winners']}
        self.assertEqual(winner_amounts['Bob'], 101)
        self.assertEqual(winner_amounts['Charlie'], 101)
        self.assertEqual(winner_amounts['Alice'], 100)

        # Verify conservation
        total_distributed = sum(w['amount'] for w in pot['winners'])
        self.assertEqual(total_distributed, 302)


class TestSidePots(unittest.TestCase):
    """Tests for side pot scenarios with all-in players."""

    def test_single_side_pot_short_stack_wins_main(self):
        """One all-in player wins main pot, side pot goes to remaining player."""
        # ShortStack: $50 all-in with AA (best hand)
        # BigStack1: $200 with KK
        # BigStack2: $200 with QQ
        short_stack = Player(
            name='ShortStack',
            stack=0,
            is_human=False,
            bet=50,
            hand=(Card('A', 'spades'), Card('A', 'hearts')),
            is_folded=False,
            is_all_in=True,
        )
        big_stack1 = Player(
            name='BigStack1',
            stack=800,
            is_human=False,
            bet=200,
            hand=(Card('K', 'spades'), Card('K', 'hearts')),
            is_folded=False,
        )
        big_stack2 = Player(
            name='BigStack2',
            stack=800,
            is_human=False,
            bet=200,
            hand=(Card('Q', 'spades'), Card('Q', 'hearts')),
            is_folded=False,
        )
        community_cards = (
            Card('7', 'diamonds'),
            Card('8', 'clubs'),
            Card('9', 'spades'),
            Card('2', 'hearts'),
            Card('3', 'diamonds'),
        )
        game_state = PokerGameState(
            players=(short_stack, big_stack1, big_stack2),
            community_cards=community_cards,
            pot={'total': 450},
            current_dealer_idx=0,
        )

        result = determine_winner(game_state)

        self.assertEqual(len(result['pot_breakdown']), 2)

        # Main pot: $150 (50 * 3) - ShortStack wins
        main_pot = result['pot_breakdown'][0]
        self.assertEqual(main_pot['pot_name'], 'Main Pot')
        self.assertEqual(main_pot['total_amount'], 150)
        self.assertEqual(len(main_pot['winners']), 1)
        self.assertEqual(main_pot['winners'][0]['name'], 'ShortStack')
        self.assertEqual(main_pot['winners'][0]['amount'], 150)

        # Side pot: $300 (150 * 2) - BigStack1 wins with KK
        side_pot = result['pot_breakdown'][1]
        self.assertEqual(side_pot['pot_name'], 'Side Pot 1')
        self.assertEqual(side_pot['total_amount'], 300)
        self.assertEqual(len(side_pot['winners']), 1)
        self.assertEqual(side_pot['winners'][0]['name'], 'BigStack1')
        self.assertEqual(side_pot['winners'][0]['amount'], 300)

    def test_single_side_pot_big_stack_wins_both(self):
        """Best hand wins both main pot and side pot."""
        # ShortStack: $50 all-in with weak hand
        # BigStack1: $200 with AA (best hand)
        # BigStack2: $200 with KK
        short_stack = Player(
            name='ShortStack',
            stack=0,
            is_human=False,
            bet=50,
            hand=(Card('2', 'spades'), Card('3', 'hearts')),
            is_folded=False,
            is_all_in=True,
        )
        big_stack1 = Player(
            name='BigStack1',
            stack=800,
            is_human=False,
            bet=200,
            hand=(Card('A', 'spades'), Card('A', 'hearts')),
            is_folded=False,
        )
        big_stack2 = Player(
            name='BigStack2',
            stack=800,
            is_human=False,
            bet=200,
            hand=(Card('K', 'spades'), Card('K', 'hearts')),
            is_folded=False,
        )
        community_cards = (
            Card('7', 'diamonds'),
            Card('8', 'clubs'),
            Card('9', 'spades'),
            Card('J', 'hearts'),
            Card('Q', 'diamonds'),
        )
        game_state = PokerGameState(
            players=(short_stack, big_stack1, big_stack2),
            community_cards=community_cards,
            pot={'total': 450},
            current_dealer_idx=0,
        )

        result = determine_winner(game_state)

        self.assertEqual(len(result['pot_breakdown']), 2)

        # BigStack1 wins both pots
        main_pot = result['pot_breakdown'][0]
        self.assertEqual(main_pot['winners'][0]['name'], 'BigStack1')
        self.assertEqual(main_pot['winners'][0]['amount'], 150)

        side_pot = result['pot_breakdown'][1]
        self.assertEqual(side_pot['winners'][0]['name'], 'BigStack1')
        self.assertEqual(side_pot['winners'][0]['amount'], 300)

    def test_multiple_side_pots_three_all_ins(self):
        """Three players all-in with different amounts - excess chips returned silently."""
        # Tiny: $30 all-in (weakest)
        # Small: $60 all-in (middle strength)
        # Medium: $100 all-in (best hand) - $40 excess returned
        tiny = Player(
            name='Tiny',
            stack=0,
            is_human=False,
            bet=30,
            hand=(Card('2', 'spades'), Card('3', 'hearts')),
            is_folded=False,
            is_all_in=True,
        )
        small = Player(
            name='Small',
            stack=0,
            is_human=False,
            bet=60,
            hand=(Card('K', 'spades'), Card('K', 'hearts')),
            is_folded=False,
            is_all_in=True,
        )
        medium = Player(
            name='Medium',
            stack=0,
            is_human=False,
            bet=100,
            hand=(Card('A', 'spades'), Card('A', 'hearts')),
            is_folded=False,
            is_all_in=True,
        )
        community_cards = (
            Card('7', 'diamonds'),
            Card('8', 'clubs'),
            Card('9', 'spades'),
            Card('J', 'hearts'),
            Card('Q', 'diamonds'),
        )
        game_state = PokerGameState(
            players=(tiny, small, medium),
            community_cards=community_cards,
            pot={'total': 190},
            current_dealer_idx=0,
        )

        result = determine_winner(game_state)

        # Only 2 pots now - excess chips returned silently instead of creating a 3rd pot
        self.assertEqual(len(result['pot_breakdown']), 2)

        # Main pot: $90 (30 * 3) - Medium wins with AA
        main_pot = result['pot_breakdown'][0]
        self.assertEqual(main_pot['pot_name'], 'Main Pot')
        self.assertEqual(main_pot['total_amount'], 90)
        self.assertEqual(main_pot['winners'][0]['name'], 'Medium')

        # Side pot 1: $60 (30 * 2 from Small and Medium) - Medium wins
        side_pot_1 = result['pot_breakdown'][1]
        self.assertEqual(side_pot_1['pot_name'], 'Side Pot 1')
        self.assertEqual(side_pot_1['total_amount'], 60)
        self.assertEqual(side_pot_1['winners'][0]['name'], 'Medium')

        # Medium's excess $40 is returned silently (no pot for single player)
        self.assertEqual(result['returned_chips'], {'Medium': 40})

    def test_side_pot_with_split(self):
        """Side pot is split between two players with identical hands."""
        # ShortStack: $50 all-in with weak hand
        # BigStack1 and BigStack2: $200 each with identical AA
        short_stack = Player(
            name='ShortStack',
            stack=0,
            is_human=False,
            bet=50,
            hand=(Card('2', 'spades'), Card('3', 'hearts')),
            is_folded=False,
            is_all_in=True,
        )
        big_stack1 = Player(
            name='BigStack1',
            stack=800,
            is_human=False,
            bet=200,
            hand=(Card('A', 'spades'), Card('K', 'hearts')),
            is_folded=False,
        )
        big_stack2 = Player(
            name='BigStack2',
            stack=800,
            is_human=False,
            bet=200,
            hand=(Card('A', 'hearts'), Card('K', 'spades')),
            is_folded=False,
        )
        # Both make the same straight
        community_cards = (
            Card('Q', 'diamonds'),
            Card('J', 'clubs'),
            Card('10', 'spades'),
            Card('7', 'hearts'),
            Card('6', 'diamonds'),
        )
        game_state = PokerGameState(
            players=(short_stack, big_stack1, big_stack2),
            community_cards=community_cards,
            pot={'total': 450},
            current_dealer_idx=0,
        )

        result = determine_winner(game_state)

        self.assertEqual(len(result['pot_breakdown']), 2)

        # Main pot: $150 - split between BigStack1 and BigStack2
        main_pot = result['pot_breakdown'][0]
        self.assertEqual(main_pot['total_amount'], 150)
        self.assertEqual(len(main_pot['winners']), 2)

        main_amounts = {w['name']: w['amount'] for w in main_pot['winners']}
        self.assertEqual(main_amounts['BigStack1'], 75)
        self.assertEqual(main_amounts['BigStack2'], 75)

        # Side pot: $300 - split between BigStack1 and BigStack2
        side_pot = result['pot_breakdown'][1]
        self.assertEqual(side_pot['total_amount'], 300)
        self.assertEqual(len(side_pot['winners']), 2)

        side_amounts = {w['name']: w['amount'] for w in side_pot['winners']}
        self.assertEqual(side_amounts['BigStack1'], 150)
        self.assertEqual(side_amounts['BigStack2'], 150)


class TestOddChipDistribution(unittest.TestCase):
    """Tests specifically for odd chip handling according to poker rules."""

    def test_odd_chip_goes_to_left_of_dealer(self):
        """Odd chip goes to player closest to dealer's left.

        With equal bets but odd pot from antes, odd chip goes to player
        closest to dealer's left.
        """
        # Both players bet 50, folded player adds 1 chip
        player1 = Player(
            name='Alice',
            stack=1000,
            is_human=False,
            bet=50,
            hand=(Card('A', 'spades'), Card('K', 'hearts')),
            is_folded=False,
        )
        player2 = Player(
            name='Bob',
            stack=1000,
            is_human=False,
            bet=50,
            hand=(Card('A', 'hearts'), Card('K', 'spades')),
            is_folded=False,
        )
        player3 = Player(
            name='Charlie',
            stack=1000,
            is_human=False,
            bet=1,  # Folded early
            hand=(Card('2', 'clubs'), Card('3', 'clubs')),
            is_folded=True,
        )
        community_cards = (
            Card('Q', 'diamonds'),
            Card('J', 'clubs'),
            Card('10', 'spades'),
            Card('7', 'hearts'),
            Card('6', 'diamonds'),
        )
        game_state = PokerGameState(
            players=(player1, player2, player3),
            community_cards=community_cards,
            pot={'total': 101},
            current_dealer_idx=0,  # Alice is dealer
        )

        result = determine_winner(game_state)
        winner_amounts = {w['name']: w['amount'] for w in result['pot_breakdown'][0]['winners']}

        # Bob is seat 1, left of dealer Alice (seat 0), gets odd chip
        self.assertEqual(winner_amounts['Bob'], 51)
        self.assertEqual(winner_amounts['Alice'], 50)

    def test_odd_chip_dealer_position_wraps(self):
        """Odd chip distribution wraps around the table correctly.

        Charlie is dealer (idx 2), so order left of dealer is:
        Alice (idx 0) -> Bob (idx 1) -> Charlie (idx 2)
        """
        # All bet 33, folded player adds 2 chips for odd pot
        player1 = Player(
            name='Alice',
            stack=1000,
            is_human=False,
            bet=33,
            hand=(Card('A', 'spades'), Card('K', 'hearts')),
            is_folded=False,
        )
        player2 = Player(
            name='Bob',
            stack=1000,
            is_human=False,
            bet=33,
            hand=(Card('A', 'hearts'), Card('K', 'spades')),
            is_folded=False,
        )
        player3 = Player(
            name='Charlie',
            stack=1000,
            is_human=False,
            bet=33,
            hand=(Card('A', 'diamonds'), Card('K', 'clubs')),
            is_folded=False,
        )
        player4 = Player(
            name='Dave',
            stack=1000,
            is_human=False,
            bet=2,  # Folded early
            hand=(Card('2', 'clubs'), Card('3', 'clubs')),
            is_folded=True,
        )
        community_cards = (
            Card('Q', 'diamonds'),
            Card('J', 'clubs'),
            Card('10', 'spades'),
            Card('7', 'hearts'),
            Card('6', 'diamonds'),
        )
        game_state = PokerGameState(
            players=(player1, player2, player3, player4),
            community_cards=community_cards,
            pot={'total': 101},
            current_dealer_idx=2,  # Charlie is dealer
        )

        result = determine_winner(game_state)
        winner_amounts = {w['name']: w['amount'] for w in result['pot_breakdown'][0]['winners']}

        # 101 / 3 = 33 remainder 2
        # Dealer is Charlie (idx 2). Distance from dealer:
        # - Alice (idx 0): (0-2)%4 = 2
        # - Bob (idx 1): (1-2)%4 = 3
        # - Charlie (idx 2): (2-2)%4 = 0 -> becomes 4 (dealer is last)
        # Sorted: Alice (2), Bob (3), Charlie (4)
        # First 2 players get odd chips: Alice and Bob
        self.assertEqual(winner_amounts['Alice'], 34)
        self.assertEqual(winner_amounts['Bob'], 34)
        self.assertEqual(winner_amounts['Charlie'], 33)


class TestChipConservation(unittest.TestCase):
    """Tests to verify no chips are created or lost in pot distribution."""

    def test_conservation_simple_pot(self):
        """Total distributed equals total pot in simple case."""
        player1 = Player(
            name='Alice',
            stack=1000,
            is_human=False,
            bet=100,
            hand=(Card('A', 'spades'), Card('A', 'hearts')),
            is_folded=False,
        )
        player2 = Player(
            name='Bob',
            stack=1000,
            is_human=False,
            bet=100,
            hand=(Card('K', 'spades'), Card('K', 'hearts')),
            is_folded=False,
        )
        community_cards = (
            Card('7', 'diamonds'),
            Card('8', 'clubs'),
            Card('9', 'spades'),
            Card('J', 'hearts'),
            Card('Q', 'diamonds'),
        )
        game_state = PokerGameState(
            players=(player1, player2),
            community_cards=community_cards,
            pot={'total': 200},
        )

        result = determine_winner(game_state)

        total_distributed = sum(
            w['amount']
            for pot in result['pot_breakdown']
            for w in pot['winners']
        )
        total_pot = sum(pot['total_amount'] for pot in result['pot_breakdown'])

        self.assertEqual(total_distributed, total_pot)
        self.assertEqual(total_distributed, 200)

    def test_conservation_with_side_pots(self):
        """Total distributed equals total pot with side pots (including returned chips)."""
        tiny = Player(
            name='Tiny',
            stack=0,
            is_human=False,
            bet=30,
            hand=(Card('2', 'spades'), Card('3', 'hearts')),
            is_folded=False,
            is_all_in=True,
        )
        small = Player(
            name='Small',
            stack=0,
            is_human=False,
            bet=60,
            hand=(Card('K', 'spades'), Card('K', 'hearts')),
            is_folded=False,
            is_all_in=True,
        )
        medium = Player(
            name='Medium',
            stack=0,
            is_human=False,
            bet=100,
            hand=(Card('A', 'spades'), Card('A', 'hearts')),
            is_folded=False,
            is_all_in=True,
        )
        community_cards = (
            Card('7', 'diamonds'),
            Card('8', 'clubs'),
            Card('9', 'spades'),
            Card('J', 'hearts'),
            Card('Q', 'diamonds'),
        )
        game_state = PokerGameState(
            players=(tiny, small, medium),
            community_cards=community_cards,
            pot={'total': 190},
        )

        result = determine_winner(game_state)

        total_from_pots = sum(
            w['amount']
            for pot in result['pot_breakdown']
            for w in pot['winners']
        )
        total_returned = sum(result.get('returned_chips', {}).values())
        total_distributed = total_from_pots + total_returned

        total_pot = sum(pot['total_amount'] for pot in result['pot_breakdown']) + total_returned

        self.assertEqual(total_distributed, total_pot)
        self.assertEqual(total_distributed, 190)

    def test_conservation_with_split_and_odd_chips(self):
        """Total distributed equals pot even with splits and odd chips."""
        player1 = Player(
            name='Alice',
            stack=1000,
            is_human=False,
            bet=67,
            hand=(Card('A', 'spades'), Card('K', 'hearts')),
            is_folded=False,
        )
        player2 = Player(
            name='Bob',
            stack=1000,
            is_human=False,
            bet=67,
            hand=(Card('A', 'hearts'), Card('K', 'spades')),
            is_folded=False,
        )
        player3 = Player(
            name='Charlie',
            stack=1000,
            is_human=False,
            bet=67,
            hand=(Card('A', 'diamonds'), Card('K', 'clubs')),
            is_folded=False,
        )
        community_cards = (
            Card('Q', 'diamonds'),
            Card('J', 'clubs'),
            Card('10', 'spades'),
            Card('7', 'hearts'),
            Card('6', 'diamonds'),
        )
        game_state = PokerGameState(
            players=(player1, player2, player3),
            community_cards=community_cards,
            pot={'total': 201},  # 201 / 3 = 67 remainder 0
        )

        result = determine_winner(game_state)

        total_distributed = sum(
            w['amount']
            for pot in result['pot_breakdown']
            for w in pot['winners']
        )

        self.assertEqual(total_distributed, 201)


class TestAwardPotWinnings(unittest.TestCase):
    """Tests for the award_pot_winnings function."""

    def test_award_single_winner(self):
        """Single winner gets pot added to stack."""
        player1 = Player(
            name='Alice',
            stack=900,
            is_human=False,
            bet=100,
            hand=(Card('A', 'spades'), Card('A', 'hearts')),
            is_folded=False,
        )
        player2 = Player(
            name='Bob',
            stack=900,
            is_human=False,
            bet=100,
            hand=(Card('K', 'spades'), Card('K', 'hearts')),
            is_folded=False,
        )
        community_cards = (
            Card('7', 'diamonds'),
            Card('8', 'clubs'),
            Card('9', 'spades'),
            Card('J', 'hearts'),
            Card('Q', 'diamonds'),
        )
        game_state = PokerGameState(
            players=(player1, player2),
            community_cards=community_cards,
            pot={'total': 200},
        )

        winner_info = determine_winner(game_state)
        new_state = award_pot_winnings(game_state, winner_info)

        # Alice should have 900 + 200 = 1100
        alice = next(p for p in new_state.players if p.name == 'Alice')
        self.assertEqual(alice.stack, 1100)

        # Bob's stack unchanged
        bob = next(p for p in new_state.players if p.name == 'Bob')
        self.assertEqual(bob.stack, 900)

    def test_award_split_pot(self):
        """Split pot winners both get their share added to stacks."""
        player1 = Player(
            name='Alice',
            stack=900,
            is_human=False,
            bet=100,
            hand=(Card('A', 'spades'), Card('K', 'hearts')),
            is_folded=False,
        )
        player2 = Player(
            name='Bob',
            stack=900,
            is_human=False,
            bet=100,
            hand=(Card('A', 'hearts'), Card('K', 'spades')),
            is_folded=False,
        )
        community_cards = (
            Card('Q', 'diamonds'),
            Card('J', 'clubs'),
            Card('10', 'spades'),
            Card('7', 'hearts'),
            Card('6', 'diamonds'),
        )
        game_state = PokerGameState(
            players=(player1, player2),
            community_cards=community_cards,
            pot={'total': 200},
        )

        winner_info = determine_winner(game_state)
        new_state = award_pot_winnings(game_state, winner_info)

        # Both should have 900 + 100 = 1000
        alice = next(p for p in new_state.players if p.name == 'Alice')
        bob = next(p for p in new_state.players if p.name == 'Bob')
        self.assertEqual(alice.stack, 1000)
        self.assertEqual(bob.stack, 1000)

    def test_award_multiple_pots(self):
        """Winner of multiple pots gets all winnings combined."""
        short_stack = Player(
            name='ShortStack',
            stack=0,
            is_human=False,
            bet=50,
            hand=(Card('2', 'spades'), Card('3', 'hearts')),
            is_folded=False,
            is_all_in=True,
        )
        big_stack = Player(
            name='BigStack',
            stack=800,
            is_human=False,
            bet=200,
            hand=(Card('A', 'spades'), Card('A', 'hearts')),
            is_folded=False,
        )
        other = Player(
            name='Other',
            stack=800,
            is_human=False,
            bet=200,
            hand=(Card('K', 'spades'), Card('K', 'hearts')),
            is_folded=False,
        )
        community_cards = (
            Card('7', 'diamonds'),
            Card('8', 'clubs'),
            Card('9', 'spades'),
            Card('J', 'hearts'),
            Card('Q', 'diamonds'),
        )
        game_state = PokerGameState(
            players=(short_stack, big_stack, other),
            community_cards=community_cards,
            pot={'total': 450},
        )

        winner_info = determine_winner(game_state)
        new_state = award_pot_winnings(game_state, winner_info)

        # BigStack wins both pots: 150 (main) + 300 (side) = 450
        big_stack_new = next(p for p in new_state.players if p.name == 'BigStack')
        self.assertEqual(big_stack_new.stack, 800 + 450)


class TestEdgeCases(unittest.TestCase):
    """Tests for edge cases and boundary conditions."""

    def test_single_player_wins_by_fold(self):
        """Single remaining player after all others fold wins entire pot."""
        player1 = Player(
            name='Alice',
            stack=1000,
            is_human=False,
            bet=100,
            hand=(Card('2', 'spades'), Card('3', 'hearts')),
            is_folded=False,
        )
        player2 = Player(
            name='Bob',
            stack=1000,
            is_human=False,
            bet=100,
            hand=(Card('A', 'spades'), Card('A', 'hearts')),
            is_folded=True,  # Folded despite having best hand
        )
        community_cards = (
            Card('7', 'diamonds'),
            Card('8', 'clubs'),
            Card('9', 'spades'),
            Card('J', 'hearts'),
            Card('Q', 'diamonds'),
        )
        game_state = PokerGameState(
            players=(player1, player2),
            community_cards=community_cards,
            pot={'total': 200},
        )

        result = determine_winner(game_state)

        self.assertEqual(len(result['pot_breakdown']), 1)
        self.assertEqual(result['pot_breakdown'][0]['winners'][0]['name'], 'Alice')
        self.assertEqual(result['pot_breakdown'][0]['winners'][0]['amount'], 200)

    def test_all_but_one_folded_no_side_pots(self):
        """When all but one fold, no side pots even with different bets."""
        player1 = Player(
            name='Alice',
            stack=900,
            is_human=False,
            bet=100,
            hand=(Card('2', 'spades'), Card('3', 'hearts')),
            is_folded=False,
        )
        player2 = Player(
            name='Bob',
            stack=950,
            is_human=False,
            bet=50,
            hand=(Card('A', 'spades'), Card('A', 'hearts')),
            is_folded=True,
        )
        player3 = Player(
            name='Charlie',
            stack=975,
            is_human=False,
            bet=25,
            hand=(Card('K', 'spades'), Card('K', 'hearts')),
            is_folded=True,
        )
        community_cards = (
            Card('7', 'diamonds'),
            Card('8', 'clubs'),
            Card('9', 'spades'),
            Card('J', 'hearts'),
            Card('Q', 'diamonds'),
        )
        game_state = PokerGameState(
            players=(player1, player2, player3),
            community_cards=community_cards,
            pot={'total': 175},
        )

        result = determine_winner(game_state)

        # Only one pot, Alice wins everything
        self.assertEqual(len(result['pot_breakdown']), 1)
        self.assertEqual(result['pot_breakdown'][0]['winners'][0]['name'], 'Alice')

    def test_zero_bet_players_excluded(self):
        """Players with zero bet are excluded from pot distribution."""
        player1 = Player(
            name='Alice',
            stack=1000,
            is_human=False,
            bet=100,
            hand=(Card('A', 'spades'), Card('A', 'hearts')),
            is_folded=False,
        )
        player2 = Player(
            name='Bob',
            stack=1000,
            is_human=False,
            bet=100,
            hand=(Card('K', 'spades'), Card('K', 'hearts')),
            is_folded=False,
        )
        player3 = Player(
            name='Charlie',
            stack=1000,
            is_human=False,
            bet=0,  # Didn't bet
            hand=(Card('Q', 'spades'), Card('Q', 'hearts')),
            is_folded=False,
        )
        community_cards = (
            Card('7', 'diamonds'),
            Card('8', 'clubs'),
            Card('9', 'spades'),
            Card('J', 'hearts'),
            Card('2', 'diamonds'),
        )
        game_state = PokerGameState(
            players=(player1, player2, player3),
            community_cards=community_cards,
            pot={'total': 200},
        )

        result = determine_winner(game_state)

        # Alice wins with AA
        self.assertEqual(len(result['pot_breakdown']), 1)
        self.assertEqual(result['pot_breakdown'][0]['winners'][0]['name'], 'Alice')


class TestGameHandlerIntegration(unittest.TestCase):
    """Integration tests for chip conservation through game handler flow."""

    def test_no_double_award_through_state_machine(self):
        """Verify chips aren't doubled when game_handler syncs state to state machine.

        Regression test for chip leak bug where:
        1. handle_evaluating_hand_phase calls award_pot_winnings
        2. State synced to state machine with pot/bets NOT cleared
        3. run_until_player_action() executes evaluating_hand_transition
        4. evaluating_hand_transition calls award_pot_winnings AGAIN
        """
        from poker.poker_state_machine import PokerStateMachine, PokerPhase

        player1 = Player(
            name='Alice',
            stack=970,
            is_human=True,
            bet=30,
            hand=(Card('A', 'spades'), Card('K', 'hearts')),
            is_folded=False,
        )
        player2 = Player(
            name='Bob',
            stack=970,
            is_human=False,
            bet=30,
            hand=(Card('2', 'clubs'), Card('3', 'diamonds')),
            is_folded=False,
        )
        community_cards = (
            Card('7', 'diamonds'),
            Card('8', 'clubs'),
            Card('9', 'spades'),
            Card('Q', 'hearts'),
            Card('2', 'spades'),
        )
        game_state = PokerGameState(
            players=(player1, player2),
            community_cards=community_cards,
            pot={'total': 60, 'Alice': 30, 'Bob': 30},
            current_ante=10,
        )

        initial_chips = sum(p.stack + p.bet for p in game_state.players)
        self.assertEqual(initial_chips, 2000)

        winner_info = determine_winner(game_state)
        game_state = award_pot_winnings(game_state, winner_info)

        state_machine = PokerStateMachine(game_state)
        state_machine.phase = PokerPhase.HAND_OVER
        state_machine.run_until_player_action()

        final_stacks = sum(p.stack for p in state_machine.game_state.players)
        final_pot = state_machine.game_state.pot.get('total', 0)
        final_chips = final_stacks + final_pot
        self.assertEqual(final_chips, 2000, f"Chip leak detected: {final_chips - 2000} chips")

    def test_double_award_without_phase_skip(self):
        """Demonstrate the bug: NOT skipping EVALUATING_HAND causes double-award."""
        from poker.poker_state_machine import PokerStateMachine, PokerPhase

        player1 = Player(
            name='Alice',
            stack=970,
            is_human=True,
            bet=30,
            hand=(Card('A', 'spades'), Card('K', 'hearts')),
            is_folded=False,
        )
        player2 = Player(
            name='Bob',
            stack=970,
            is_human=False,
            bet=30,
            hand=(Card('2', 'clubs'), Card('3', 'diamonds')),
            is_folded=False,
        )
        community_cards = (
            Card('7', 'diamonds'),
            Card('8', 'clubs'),
            Card('9', 'spades'),
            Card('Q', 'hearts'),
            Card('2', 'spades'),
        )
        game_state = PokerGameState(
            players=(player1, player2),
            community_cards=community_cards,
            pot={'total': 60, 'Alice': 30, 'Bob': 30},
            current_ante=10,
        )

        winner_info = determine_winner(game_state)
        game_state = award_pot_winnings(game_state, winner_info)

        state_machine = PokerStateMachine(game_state)
        state_machine.phase = PokerPhase.EVALUATING_HAND  # BUG: not skipping to HAND_OVER
        state_machine.run_until_player_action()

        final_stacks = sum(p.stack for p in state_machine.game_state.players)
        final_pot = state_machine.game_state.pot.get('total', 0)
        final_chips = final_stacks + final_pot

        self.assertNotEqual(final_chips, 2000,
            "This test should show chip leak when EVALUATING_HAND is not skipped")
        self.assertGreater(final_chips, 2000, f"Expected chip leak, got {final_chips}")


if __name__ == '__main__':
    unittest.main()
