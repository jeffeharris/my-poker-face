"""
Tests for T1-02: Hand evaluator sort bug in determine_winner.

The bug: lines 876-877 in poker_game.py wrapped hand_values and kicker_values
in sorted(), which re-sorted already-descending values into ascending order.
This broke lexicographic comparison for hands like two-pair where value order
matters (e.g., [14,14,10,10] sorted ascending becomes [10,10,14,14]).

Impact was display-only (pot distribution used correct sort at lines 824-825),
but the reported "best overall hand" could be wrong.
"""

from poker.poker_game import determine_winner, PokerGameState, Player, Card


def make_game_state(player1_hand, player2_hand, community_cards, bets=(100, 100)):
    """Helper to construct a simple two-player game state."""
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


class TestBestOverallHandSort:
    """Verify determine_winner reports the correct best overall hand."""

    def test_two_pair_aces_tens_beats_kings_queens(self):
        """Aces and Tens should beat Kings and Queens in two-pair.

        This is the specific case that fails with sorted() wrapping:
        - Aces+Tens: hand_values=[14,14,10,10], sorted()=[10,10,14,14]
        - Kings+Queens: hand_values=[13,13,12,12], sorted()=[12,12,13,13]
        With reverse=True, [12,12,13,13] > [10,10,14,14] so Kings+Queens
        would incorrectly be selected as best hand.
        """
        # Alice: Aces and Tens (better two-pair)
        # Bob: Kings and Queens (worse two-pair)
        # Both players need two-pair from community + hand
        game_state = make_game_state(
            player1_hand=[Card('A', 'spades'), Card('10', 'hearts')],
            player2_hand=[Card('K', 'hearts'), Card('Q', 'clubs')],
            community_cards=[
                Card('A', 'diamonds'),  # Gives Alice pair of Aces
                Card('10', 'clubs'),    # Gives Alice pair of Tens
                Card('K', 'spades'),    # Gives Bob pair of Kings
                Card('Q', 'diamonds'),  # Gives Bob pair of Queens
                Card('3', 'hearts'),    # Neutral
            ],
        )

        result = determine_winner(game_state)

        # Alice (Aces and Tens) should win
        pot = result['pot_breakdown'][0]
        assert len(pot['winners']) == 1
        assert pot['winners'][0]['name'] == 'Alice'

        # The best overall hand reported should also be Alice's
        assert 'Two Pair' in result['hand_name']
        assert "A" in result['hand_name'], (
            f"Best hand should be Aces, got: {result['hand_name']}"
        )

    def test_best_overall_hand_matches_tier_winner(self):
        """The best overall hand should match the actual pot winner.

        With the sorted() bug, the display hand could disagree with
        who actually won the pot.
        """
        # Alice has a flush, Bob has two-pair
        game_state = make_game_state(
            player1_hand=[Card('A', 'hearts'), Card('K', 'hearts')],
            player2_hand=[Card('J', 'spades'), Card('10', 'clubs')],
            community_cards=[
                Card('Q', 'hearts'),
                Card('9', 'hearts'),
                Card('2', 'hearts'),
                Card('J', 'diamonds'),
                Card('10', 'diamonds'),
            ],
        )

        result = determine_winner(game_state)

        # Alice should win with flush
        pot = result['pot_breakdown'][0]
        assert pot['winners'][0]['name'] == 'Alice'
        # The reported best hand should be Alice's flush, not Bob's two-pair
        assert 'Flush' in result['hand_name']
