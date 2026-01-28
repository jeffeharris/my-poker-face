"""
Tests for T1-03: Two-pair kicker calculation verification.

Verifies that when two players have the same two pairs, the kicker
correctly determines the winner.
"""

import pytest
from poker.hand_evaluator import HandEvaluator
from poker.poker_game import determine_winner, PokerGameState, Player
from core.card import Card


def make_two_player_state(player1_hand, player2_hand, community_cards, bets=(100, 100)):
    """Helper to construct a simple two-player game state for showdown."""
    player1 = Player(
        name='Alice',
        stack=1000,
        is_human=False,
        bet=bets[0],
        hand=tuple(player1_hand),
        is_folded=False,
    )
    player2 = Player(
        name='Bob',
        stack=1000,
        is_human=False,
        bet=bets[1],
        hand=tuple(player2_hand),
        is_folded=False,
    )
    return PokerGameState(
        deck=(),
        players=(player1, player2),
        community_cards=tuple(community_cards),
        pot={'total': sum(bets)},
        current_dealer_idx=0,
    )


class TestTwoPairKicker:
    """Verify two-pair kicker tiebreak works correctly."""

    def test_same_two_pair_higher_kicker_wins(self):
        """Two players with Kings and Tens — higher kicker wins.

        Alice: K♠ Q♥ (kicker Q = 12)
        Bob:   K♣ 5♦ (kicker 5)
        Community: K♦ 10♠ 10♣ 3♥ 2♠

        Both have two pair (Kings and Tens).
        Alice's kicker is Q (12), Bob's kicker is 5.
        Alice should win.
        """
        game_state = make_two_player_state(
            player1_hand=[Card('K', 'Spades'), Card('Q', 'Hearts')],
            player2_hand=[Card('K', 'Clubs'), Card('5', 'Diamonds')],
            community_cards=[
                Card('K', 'Diamonds'),
                Card('10', 'Spades'),
                Card('10', 'Clubs'),
                Card('3', 'Hearts'),
                Card('2', 'Spades'),
            ],
        )

        result = determine_winner(game_state)

        pot = result['pot_breakdown'][0]
        assert len(pot['winners']) == 1, f"Expected 1 winner, got {len(pot['winners'])}"
        assert pot['winners'][0]['name'] == 'Alice', (
            f"Alice (kicker Q) should beat Bob (kicker 5), "
            f"but winner was {pot['winners'][0]['name']}"
        )

    def test_same_two_pair_same_kicker_splits(self):
        """Two players with identical two pairs and same kicker should split.

        Alice: K♠ 9♥
        Bob:   K♣ 9♦
        Community: K♦ 10♠ 10♣ 7♥ 3♠

        Both have two pair (Kings and Tens) with kicker 9
        (the 7 and 3 on the board are lower than 9, so best 5 cards
        for both are K K 10 10 9). Should be a split pot.
        """
        game_state = make_two_player_state(
            player1_hand=[Card('K', 'Spades'), Card('9', 'Hearts')],
            player2_hand=[Card('K', 'Clubs'), Card('9', 'Diamonds')],
            community_cards=[
                Card('K', 'Diamonds'),
                Card('10', 'Spades'),
                Card('10', 'Clubs'),
                Card('7', 'Hearts'),
                Card('3', 'Spades'),
            ],
        )

        result = determine_winner(game_state)

        pot = result['pot_breakdown'][0]
        assert len(pot['winners']) == 2, (
            f"Expected split pot (2 winners), got {len(pot['winners'])} winner(s): "
            f"{[w['name'] for w in pot['winners']]}"
        )

    def test_hand_evaluator_two_pair_kicker_value(self):
        """Verify HandEvaluator returns correct kicker for two-pair hands."""
        # Hand: K K 10 10 Q (two pair, Kings and Tens, kicker Queen)
        cards = [
            Card('K', 'Spades'),
            Card('K', 'Diamonds'),
            Card('10', 'Hearts'),
            Card('10', 'Clubs'),
            Card('Q', 'Spades'),
        ]
        result = HandEvaluator(cards).evaluate_hand()

        assert result['hand_rank'] == 8, f"Expected two-pair (rank 8), got rank {result['hand_rank']}"
        assert result['hand_name'].startswith('Two Pair')
        # Kicker should be Q (value 12)
        assert result['kicker_values'] == [12], (
            f"Expected kicker [12] (Queen), got {result['kicker_values']}"
        )

    def test_hand_evaluator_two_pair_selects_best_kicker_from_seven_cards(self):
        """With 7 cards (2 hole + 5 community), the best kicker is chosen.

        Hand: K♠ 5♦ + community K♦ 10♠ 10♣ Q♥ 3♠
        Best 5: K K 10 10 Q (kicker Q from community, not 5 from hole).
        """
        cards = [
            Card('K', 'Spades'),
            Card('5', 'Diamonds'),
            Card('K', 'Diamonds'),
            Card('10', 'Spades'),
            Card('10', 'Clubs'),
            Card('Q', 'Hearts'),
            Card('3', 'Spades'),
        ]
        result = HandEvaluator(cards).evaluate_hand()

        assert result['hand_rank'] == 8  # two-pair
        # The kicker should be Q (12), not 5 or 3
        assert result['kicker_values'] == [12], (
            f"Expected best kicker [12] (Queen), got {result['kicker_values']}"
        )
