import random


class Card:
    SUIT_TO_ASCII = {'Hearts': '♥', 'Diamonds': '♦', 'Clubs': '♣', 'Spades': '♠'}
    RANK_VALUES = {'2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9, '10': 10, 'J': 11, 'Q': 12, 'K': 13,
                   'A': 14}

    def __init__(self, rank, suit):
        self.rank = rank
        self.suit = suit
        self.value = Card.RANK_VALUES[rank]

    def __repr__(self):
        return f"Card('{self.rank.ljust(2)}', '{self.suit}')"

    def render_card(self):
        card_template = \
            '''
.---------.
|{}       |
| {}       |
|         |
|         |
|    {}    |
|       {}|
`---------'
'''
        rank_left = self.rank.ljust(2)
        rank_right = self.rank.rjust(2)
        card = card_template.format(rank_left, Card.SUIT_TO_ASCII[self.suit], Card.SUIT_TO_ASCII[self.suit], rank_right)
        return card


class Deck:
    def __init__(self):
        ranks = list(Card.RANK_VALUES.keys())
        suits = list(Card.SUIT_TO_ASCII.keys())
        self.cards = [Card(rank, suit) for rank in ranks for suit in suits]
        self.shuffle()

    def shuffle(self):
        random.shuffle(self.cards)

    def deal(self, num=1):
        return [self.cards.pop() for _ in range(num)]


def render_cards(cards):
    card_lines = [card.render_card().strip().split('\n') for card in cards]
    if not card_lines:
        return None
    ascii_card_lines = []
    for lines in zip(*card_lines):
        ascii_card_lines.append('  '.join(lines))
    ascii_card_string = '\n'.join(ascii_card_lines)
    return ascii_card_string


def render_two_cards(card_1, card_2):
    # Define the ASCII art templates for each rank and suit combination
    card_template = '''
.---.---------.
|{}  |{}        |
|  {}|  {}      |
|   |         |
|   |         |
|   |       {} |
|   |        {}|
`---`---------'
'''
    # Generate and print each card
    two_card_art = card_template.format(card_1.rank, card_2.rank,
                                        Card.SUIT_TO_ASCII[card_1.suit], Card.SUIT_TO_ASCII[card_2.suit],
                                        Card.SUIT_TO_ASCII[card_2.suit],
                                        card_2.rank)
    return two_card_art
