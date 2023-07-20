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

    def draw_card(self):
        # Define the ASCII art templates for each rank and suit combination
        card_template = \
            '''
.---------.
|{}        |
| {}      |
|         |
|         |
|    {}   |
|        {}|
`---------'
'''

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
    
    def draw(self, num=1):
        drawn_cards = self.cards[:num]
        self.cards = self.cards[num:]
        return drawn_cards


def display_cards(cards: [Card]):
    # Split the ASCII art of each card into lines
    card_lines = [card.draw_card().strip().split('\n') for card in cards]

    # Iterate over the lines and print them side by side
    for i in range(len(card_lines[0])):
        line = ''
        for j in range(len(cards)):
            line += card_lines[j][i] + '  '
        print(line)


def display_hole_cards(cards: [Card, Card]):
    # Define the ASCII art templates for each rank and suit combination
    card_template = \
        '''
.---.---------.
|{}  |{}         |
| {}| {}       |
|   |          |
|   |          |
|   |       {} |
|   |         {}|
`---``---------'
'''

    sorted_cards = sorted(cards, key=lambda card: card.value)
    card_1 = sorted_cards[0]
    card_2 = sorted_cards[1]

    # Generate and print each card
    hole_card_art = card_template.format(card_1.rank, card_2.rank,
                                         card_1.suit_ascii[card_1.suit], card_2.suit_ascii[card_2.suit],
                                         card_2.suit_ascii[card_2.suit],
                                         card_2.rank)
    print(hole_card_art)
