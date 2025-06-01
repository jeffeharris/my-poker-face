from collections import Counter

class HandEvaluator:
    """
        Class HandEvaluator:
            This class is responsible for evaluating a hand of cards in poker.

        Attributes:
            cards (list): A list of Card objects representing the current hand.
            ranks (list): A list of integer values representing the ranks of the cards in the hand.
            suits (list): A list of suits for the cards in the hand.
            rank_counts (Counter): A counter object to keep track of the frequency of each card rank in the hand.
            suit_counts (Counter): A counter object to keep track of the frequency of each card suit in the hand.

        Methods:
            evaluate_hand:
                Evaluates the hand and returns a dictionary containing the rank of the hand, the values contributing to
                the hand rank, and kicker values if applicable. Returns the evaluated hands.

            _check_royal_flush:
                Checks if the hand is a royal flush.

            _check_straight_flush:
                Checks if the hand is a straight flush.

            _check_four_of_a_kind:
                Checks if the hand contains four cards of the same rank (four of a kind).

            _check_full_house:
                Checks if the hand contains a three of a kind and a pair (full house).

            _check_flush:
                Checks if the hand contains five cards of the same suit (flush).

            _check_straight:
                Checks if the hand contains five consecutive cards (straight).

            _check_three_of_a_kind:
                Checks if the hand contains three cards of the same rank (three of a kind).

            _check_two_pair:
                Checks if the hand contains two pairs of different ranks (two pair).

            _check_one_pair:
                Checks if the hand contains one pair of cards with the same rank (one pair).
    """
    # Bug fixed: flush evaluation now correctly returns only the best 5 cards
    # Previously, when more than 5 cards of the same suit were available,
    # all flush cards were returned instead of just the best 5, causing
    # incorrect hand comparisons
    def __init__(self, cards):
        self.cards = cards
        self.ranks = [card.value for card in cards]
        self.suits = [card.suit for card in cards]
        self.rank_counts = Counter(self.ranks)
        self.suit_counts = Counter(self.suits)

    def evaluate_hand(self):
        checks = [
            self._check_royal_flush,
            self._check_straight_flush,
            self._check_four_of_a_kind,
            self._check_full_house,
            self._check_flush,
            self._check_straight,
            self._check_three_of_a_kind,
            self._check_two_pair,
            self._check_one_pair,
        ]
        for i, check in enumerate(checks, start=1):
            result = check()
            if result[0]:
                return {"hand_rank": i, "hand_values": result[1], "kicker_values": result[2], "suit": result[3], "hand_name": result[4]}
        return {"hand_rank": 10, "hand_values": [], "kicker_values": sorted(self.ranks, reverse=True)[:5], "hand_name": "High Card"}

    def _check_royal_flush(self):
        has_straight_flush, straight_flush_values, _, straight_flush_suit, _ = self._check_straight_flush()
        if has_straight_flush:
            # straight_flush_ranks = [card.value for card in self.cards if card.suit == straight_flush_suit]
            comparison_set = list(set(range(10, 15)))
            comparison_set.reverse()
            if straight_flush_values == comparison_set:
                return True, comparison_set, [], straight_flush_suit, f"Royal Flush with {straight_flush_suit}"
        return False, [], [], None, None

    def _check_straight_flush(self):
        has_flush, flush_values, _, flush_suit, _ = self._check_flush()
        if has_flush:
            flush_cards = [card for card in self.cards if card.suit == flush_suit]
            has_straight, straight_values, _, _, _ = HandEvaluator(flush_cards)._check_straight()
            if has_straight:
                return True, straight_values, [], flush_suit, f"{straight_values[0]} high Straight Flush with {flush_suit}"
        return False, [], [], None, None

    def _check_four_of_a_kind(self):
        for rank, count in self.rank_counts.items():
            if count == 4:
                kicker = sorted([card for card in self.ranks if card != rank], reverse=True)
                return True, [rank]*4, [kicker], None, "Four of a kind"
        return False, [], [], None, None

    def _check_full_house(self):
        three = None
        two = None
        for rank, count in sorted(self.rank_counts.items(), reverse=True):
            if count >= 3 and three is None:
                three = rank
            elif count >= 2 and two is None:
                two = rank
        if three is not None and two is not None:
            return True, [three]*3 + [two]*2, [], None, f"Full House {three}'s over {two}'s"
        return False, [], [], None, None

    def _check_flush(self):
        for suit, count in self.suit_counts.items():
            if count >= 5:
                flush_cards = sorted([card.value for card in self.cards if card.suit == suit], reverse=True)
                # Only return the best 5 cards for the flush
                best_five = flush_cards[:5]
                return True, best_five, [], suit, f"Flush with {suit}"
        return False, [], [], None, None

    def _check_straight(self):
        sorted_values = sorted(self.ranks, reverse=True)
        if not sorted_values:
            return False, [], [], None, None
        for top in range(sorted_values[0], 4, -1):
            if set(range(top-4, top+1)).issubset(set(sorted_values)):
                straight_values = list(range(top, top-5, -1))
                return True, straight_values, [], None, f"{top} high Straight"
        return False, [], [], None, None

    def _check_three_of_a_kind(self):
        for rank, count in self.rank_counts.items():
            if count == 3:
                kickers = sorted([card for card in self.ranks if card != rank], reverse=True)[:2]
                return True, [rank]*3, kickers, None, f"Three of a kind with {rank}'s"
        return False, [], [], None, None

    def _check_two_pair(self):
        pairs = [rank for rank, count in self.rank_counts.items() if count >= 2]
        if len(pairs) >= 2:
            pairs = sorted(pairs, reverse=True)[:2]
            kicker = sorted([card for card in self.ranks if card not in pairs], reverse=True)[0]
            kickers = [kicker]
            return True, pairs*2, kickers, None, f"Two Pair, {pairs[0]}'s and {pairs[1]}'s"
        return False, [], [], None, None

    def _check_one_pair(self):
        pairs = [rank for rank, count in self.rank_counts.items() if count >= 2]
        if pairs:
            pair = max(pairs)
            kickers = sorted([card for card in self.ranks if card != pair], reverse=True)[:3]
            return True, [pair]*2, kickers, None, f"One Pair, {pair}'s"
        return False, [], [], None, None
