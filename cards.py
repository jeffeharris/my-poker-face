import random


class Card:
    rank_values = {'2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8,
                   '9': 9, '10': 10, 'J': 11, 'Q': 12, 'K': 13, 'A': 14}

    def __init__(self, rank, suit):
        self.rank = rank
        self.suit = suit
        self.suit_ascii = {'Hearts': '♥',
                           'Diamonds': '♦',
                           'Clubs': '♣',
                           'Spades': '♠'}
        self.value = Card.rank_values[rank]

    def __repr__(self):
        return f"{self.rank} of {self.suit}"

    def display_card(self):
        # Define the ASCII art templates for each rank and suit combination
        card_template = \
            '''
.---------.
|{}        |
| {}       |
|         |
|         |
|    {}    |
|        {}|
`---------'
'''
        # TODO: take care of extra character when the rank is '10'
        # Generate and print each card
        card = card_template.format(self.rank, self.suit_ascii[self.suit], self.suit_ascii[self.suit], self.rank)
        return card


class Deck:
    def __init__(self):
        ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
        suits = ['Hearts', 'Diamonds', 'Clubs', 'Spades']
        self.suits_ascii = {'Hearts': '♥',
                            'Diamonds': '♦',
                            'Clubs': '♣',
                            'Spades': '♠'}
        self.cards = [Card(rank, suit) for rank in ranks for suit in suits]
        self.shuffle()

    def shuffle(self):
        random.shuffle(self.cards)

    def deal(self, num=1):
        return [self.cards.pop() for _ in range(num)]


def render_cards(cards):
    """
    Function to display a set of cards
    :param cards: a list of Card objects
    :return: A string representation of the cards
    """
    card_lines = [card.display_card().strip().split('\n') for card in cards]

    if not card_lines:
        return None

    ascii_card_lines = []
    for lines in zip(*card_lines):
        ascii_card_lines.append('  '.join(lines))

    ascii_card_string = '\n'.join(ascii_card_lines)

    return ascii_card_string
