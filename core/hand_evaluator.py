from collections import Counter

class HandEvaluator:
    def __init__(self, cards):
        self.cards = cards
        self.ranks = [card.value for card in cards]
        self.suits = [card.suit for card in cards]
        self.rank_counts = Counter(self.ranks)
        self.suit_counts = Counter(self.suits)

    def evaluate_hand(self):
        checks = [
            self.check_royal_flush,
            self.check_straight_flush,
            self.check_four_of_a_kind,
            self.check_full_house,
            self.check_flush,
            self.check_straight,
            self.check_three_of_a_kind,
            self.check_two_pair,
            self.check_one_pair,
        ]
        for i, check in enumerate(checks, start=1):
            result = check()
            if result[0]:
                return {"hand_rank": i, "hand_values": result[1], "kicker_values": result[2]}
        return {"hand_rank": 10, "hand_values": [], "kicker_values": sorted(self.ranks, reverse=True)[:5]}

    def check_royal_flush(self):
        has_straight_flush, straight_flush_values, _, straight_flush_suit = self.check_straight_flush()
        if has_straight_flush:
            # straight_flush_ranks = [card.value for card in self.cards if card.suit == straight_flush_suit]
            comparison_set = list(set(range(10, 15)))
            comparison_set.reverse()
            if straight_flush_values == comparison_set:
                return True, comparison_set, []
        return False, [], []

    def check_straight_flush(self):
        has_flush, flush_values, _, flush_suit = self.check_flush()
        if has_flush:
            flush_cards = [card for card in self.cards if card.suit == flush_suit]
            has_straight, straight_values, _ = HandEvaluator(flush_cards).check_straight()
            if has_straight:
                return True, straight_values, [], flush_suit
        return False, [], [], []        # TODO: should we handle the 4th return value for suit?

    def check_four_of_a_kind(self):
        for rank, count in self.rank_counts.items():
            if count == 4:
                kicker = sorted([card for card in self.ranks if card != rank], reverse=True)
                return True, [rank]*4, [kicker]
        return False, [], []

    def check_full_house(self):
        three = None
        two = None
        for rank, count in sorted(self.rank_counts.items(), reverse=True):
            if count >= 3 and three is None:
                three = rank
            elif count >= 2 and two is None:
                two = rank
        if three is not None and two is not None:
            return True, [three]*3 + [two]*2, []
        return False, [], []

    def check_flush(self):
        for suit, count in self.suit_counts.items():
            if count >= 5:
                flush_cards = sorted([card.value for card in self.cards if card.suit == suit], reverse=True)
                return True, flush_cards, [], suit
        return False, [], [], None      # TODO: should we handle the 4th return value for suit?

    def check_straight(self):
        sorted_values = sorted(self.ranks, reverse=True)
        if not sorted_values:
            return False, [], []
        for top in range(sorted_values[0], 4, -1):
            if set(range(top-4, top+1)).issubset(set(sorted_values)):
                straight_values = list(range(top, top-5, -1))
                return True, straight_values, []
        return False, [], []

    def check_three_of_a_kind(self):
        for rank, count in self.rank_counts.items():
            if count == 3:
                kickers = sorted([card for card in self.ranks if card != rank], reverse=True)[:2]
                return True, [rank]*3, kickers
        return False, [], []

    def check_two_pair(self):
        pairs = [rank for rank, count in self.rank_counts.items() if count >= 2]
        if len(pairs) >= 2:
            pairs = sorted(pairs, reverse=True)[:2]
            kicker = sorted([card for card in self.ranks if card not in pairs], reverse=True)[0]
            kickers = [kicker]
            return True, pairs*2, kickers
        return False, [], []

    def check_one_pair(self):
        pairs = [rank for rank, count in self.rank_counts.items() if count >= 2]
        if pairs:
            pair = max(pairs)
            kickers = sorted([card for card in self.ranks if card != pair], reverse=True)[:3]
            return True, [pair]*2, kickers
        return False, [], []
