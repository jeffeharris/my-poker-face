# spades_game.py

from flask import Flask, render_template, request, redirect, url_for, session
import random

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Replace with a secure random key in production

# Define card ranks and suits
suits = ['Clubs', 'Diamonds', 'Hearts', 'Spades']
ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']

# Create a deck of cards
def create_deck():
    deck = [{'rank': rank, 'suit': suit} for suit in suits for rank in ranks]
    return deck

# Deal cards to players
def deal_cards(deck):
    random.shuffle(deck)
    hands = {'Player': [], 'CPU1': [], 'CPU2': [], 'CPU3': []}
    for i, card in enumerate(deck):
        player = list(hands.keys())[i % 4]
        hands[player].append(card)
    # Sort each hand
    for hand in hands.values():
        hand.sort(key=lambda x: (suits.index(x['suit']), ranks.index(x['rank'])))
    return hands

# Initialize game state
def initialize_game():
    deck = create_deck()
    hands = deal_cards(deck)

    # Determine starting player (player with 2 of Clubs)
    starting_player = find_starting_player(hands)

    game_state = {
        'hands': hands,
        'bids': {},
        'tricks_won': {'Team1': 0, 'Team2': 0},
        'current_trick': [],
        'current_player': starting_player,
        'trick_number': 1,
        'spades_broken': False,
        'scores': {'Team1': 0, 'Team2': 0},
        'round_over': False,
        'previous_trick': [],
        'previous_trick_winner': None,
        'teams': {
            'Team1': ['Player', 'CPU2'],
            'Team2': ['CPU1', 'CPU3']
        }
    }
    return game_state

def find_starting_player(hands):
    for player, hand in hands.items():
        for card in hand:
            if card['suit'] == 'Clubs' and card['rank'] == '2':
                return player
    # If 2 of Clubs not found, find the lowest club
    lowest_club = None
    starting_player = None
    for player, hand in hands.items():
        for card in hand:
            if card['suit'] == 'Clubs':
                if (lowest_club is None) or (ranks.index(card['rank']) < ranks.index(lowest_club['rank'])):
                    lowest_club = card
                    starting_player = player
    # If no clubs are dealt, default to 'Player'
    return starting_player if starting_player else 'Player'

# Simple AI bidding logic
def get_cpu_bid(hand):
    high_cards = ['A', 'K', 'Q', 'J', '10']
    bid = sum(1 for card in hand if card['rank'] in high_cards or card['suit'] == 'Spades')
    if bid == 0:
        bid = 1  # Ensure CPU bids at least 1
    return bid

# CPU player logic for playing a card
def cpu_play_card(player_name, game_state):
    hand = game_state['hands'][player_name]
    current_trick = game_state['current_trick']
    spades_broken = game_state['spades_broken']

    # Implementing basic AI for card selection
    if current_trick:
        leading_suit = current_trick[0]['card']['suit']
        same_suit_cards = [card for card in hand if card['suit'] == leading_suit]
        if same_suit_cards:
            card_to_play = same_suit_cards[0]  # Play lowest card of leading suit
        else:
            spades_cards = [card for card in hand if card['suit'] == 'Spades']
            if spades_cards and (spades_broken or leading_suit == 'Spades'):
                card_to_play = spades_cards[0]  # Play lowest spade
            else:
                card_to_play = min(hand, key=lambda x: ranks.index(x['rank']))  # Play lowest card
    else:
        # First player of the trick
        non_spades = [card for card in hand if card['suit'] != 'Spades']
        if non_spades:
            card_to_play = min(non_spades, key=lambda x: (suits.index(x['suit']), ranks.index(x['rank'])))  # Play lowest non-spade card
        else:
            card_to_play = min(hand, key=lambda x: ranks.index(x['rank']))  # Only spades left, play lowest

    hand.remove(card_to_play)
    game_state['current_trick'].append({'player': player_name, 'card': card_to_play})

    if card_to_play['suit'] == 'Spades' and not spades_broken:
        game_state['spades_broken'] = True

# Determine the winner of a trick
def determine_winner(trick):
    leading_suit = trick[0]['card']['suit']
    winning_play = trick[0]
    for play in trick[1:]:
        card = play['card']
        if card['suit'] == winning_play['card']['suit']:
            if ranks.index(card['rank']) > ranks.index(winning_play['card']['rank']):
                winning_play = play
        elif card['suit'] == 'Spades':
            if winning_play['card']['suit'] != 'Spades':
                winning_play = play
            elif ranks.index(card['rank']) > ranks.index(winning_play['card']['rank']):
                winning_play = play
    return winning_play['player']

# Validate player's card selection
def validate_play(player_name, selected_card, game_state):
    player_hand = game_state['hands'][player_name]
    current_trick = game_state['current_trick']
    spades_broken = game_state['spades_broken']

    if player_name != game_state['current_player']:
        return "It's not your turn."

    if not current_trick:
        # First play of the trick
        if selected_card['suit'] == 'Spades' and not spades_broken:
            if all(card['suit'] == 'Spades' for card in player_hand):
                return None  # Only spades left, can play
            else:
                return "Cannot lead with Spades until they are broken."
    else:
        leading_suit = current_trick[0]['card']['suit']
        if selected_card['suit'] == leading_suit:
            return None  # Correct suit
        elif any(card['suit'] == leading_suit for card in player_hand):
            return f"You must follow suit: {leading_suit}."
    return None  # Valid play

# Calculate scores at the end of the round
def calculate_scores(game_state):
    for team, players in game_state['teams'].items():
        bid = sum(game_state['bids'][player] for player in players)
        tricks = sum(game_state['tricks_won'][player] for player in players)
        if tricks >= bid:
            game_state['scores'][team] += 10 * bid + (tricks - bid)
        else:
            game_state['scores'][team] -= 10 * bid

@app.route('/')
def index():
    session['game_state'] = initialize_game()
    return redirect(url_for('bidding'))

@app.route('/bidding', methods=['GET', 'POST'])
def bidding():
    game_state = session['game_state']
    if request.method == 'POST':
        try:
            bid = int(request.form['bid'])
            if 0 <= bid <= 13:
                game_state['bids']['Player'] = bid
                # CPU players make their bids
                for cpu in ['CPU1', 'CPU2', 'CPU3']:
                    cpu_bid = get_cpu_bid(game_state['hands'][cpu])
                    game_state['bids'][cpu] = cpu_bid
                session['game_state'] = game_state
                return redirect(url_for('play_hand'))
            else:
                error = "Bid must be between 0 and 13."
                return render_template('bidding.html', error=error, game_state=game_state)
        except ValueError:
            error = "Please enter a valid integer."
            return render_template('bidding.html', error=error, game_state=game_state)
    return render_template('bidding.html', game_state=game_state)

@app.route('/play_hand', methods=['GET', 'POST'])
def play_hand():
    game_state = session.get('game_state')
    if not game_state:
        return redirect(url_for('index'))

    if game_state['round_over']:
        return redirect(url_for('game_over'))

    error = None

    if request.method == 'POST':
        # Get the current player
        current_player = game_state['current_player']

        if current_player == 'Player':
            # Player's turn
            card_index = int(request.form['card_index'])
            player_hand = game_state['hands']['Player']
            selected_card = player_hand[card_index]

            # Validate play
            error = validate_play('Player', selected_card, game_state)
            if error:
                return render_template('play_hand.html', game_state=game_state, error=error)

            # Play the card
            player_hand.pop(card_index)
            game_state['current_trick'].append({'player': 'Player', 'card': selected_card})

            if selected_card['suit'] == 'Spades' and not game_state['spades_broken']:
                game_state['spades_broken'] = True

        else:
            # CPU's turn
            cpu_play_card(current_player, game_state)

        # Move to next player
        next_player = get_next_player(current_player)
        game_state['current_player'] = next_player

        # Check if trick is complete
        if len(game_state['current_trick']) == 4:
            # Determine winner of the trick
            winner = determine_winner(game_state['current_trick'])
            team = get_player_team(winner, game_state)
            game_state['tricks_won'][team] += 1
            game_state['previous_trick_winner'] = winner
            game_state['previous_trick'] = list(game_state['current_trick'])

            # Reset current trick
            game_state['current_trick'] = []
            game_state['current_player'] = winner  # Winner starts next trick
            game_state['trick_number'] += 1

            # Check if the round is over
            if game_state['trick_number'] > 13:
                game_state['round_over'] = True
                calculate_scores(game_state)
                session['game_state'] = game_state
                return redirect(url_for('game_over'))

        session['game_state'] = game_state

        # If next player is not 'Player', process AI moves
        while game_state['current_player'] != 'Player' and not game_state['round_over']:
            return redirect(url_for('play_hand'))

    # Render the play hand template
    return render_template('play_hand.html', game_state=game_state, error=error)

def get_next_player(current_player):
    player_order = ['Player', 'CPU1', 'CPU2', 'CPU3']
    index = player_order.index(current_player)
    next_index = (index + 1) % 4
    return player_order[next_index]

def get_player_team(player, game_state):
    for team, players in game_state['teams'].items():
        if player in players:
            return team
    return None

@app.route('/game_over')
def game_over():
    game_state = session['game_state']
    return render_template('game_over.html', game_state=game_state)

if __name__ == '__main__':
    app.run(debug=True)
