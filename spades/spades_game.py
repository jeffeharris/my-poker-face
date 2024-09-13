# spades_game.py

from flask import Flask, render_template, request, redirect, url_for, session
import random

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Replace with a secure random key in production

# Define card ranks and suits
suits = ['Spades', 'Hearts', 'Diamonds', 'Clubs']
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
    for hand in hands.values():
        hand.sort(key=lambda x: (suits.index(x['suit']), ranks.index(x['rank'])))
    return hands

# Simple AI bidding logic
def get_cpu_bid(hand):
    high_cards = ['A', 'K', 'Q', 'J', '10']
    bid = sum(1 for card in hand if card['rank'] in high_cards or card['suit'] == 'Spades')
    return bid

# Initialize game state
def initialize_game():
    deck = create_deck()
    hands = deal_cards(deck)
    game_state = {
        'hands': hands,
        'bids': {},
        'tricks_won': {'Player': 0, 'CPU1': 0, 'CPU2': 0, 'CPU3': 0},
        'current_trick': [],
        'current_player': 'Player',
        'trick_number': 1,
        'start_player': 'Player',
        'spades_broken': False,
        'scores': {'Player': 0, 'CPU1': 0, 'CPU2': 0, 'CPU3': 0},
        'round_over': False
    }
    return game_state

# Determine the winner of a trick
def determine_winner(trick):
    leading_suit = trick[0]['card']['suit']
    valid_cards = []
    for play in trick:
        card = play['card']
        if card['suit'] == leading_suit or card['suit'] == 'Spades':
            valid_cards.append(play)
    winning_play = max(valid_cards, key=lambda x: (x['card']['suit'] == 'Spades', ranks.index(x['card']['rank'])))
    return winning_play['player']

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
            card_to_play = same_suit_cards[0]
        else:
            spades_cards = [card for card in hand if card['suit'] == 'Spades']
            if spades_cards and spades_broken:
                card_to_play = spades_cards[0]
            else:
                card_to_play = min(hand, key=lambda x: ranks.index(x['rank']))
    else:
        # First player of the trick
        if spades_broken:
            card_to_play = min(hand, key=lambda x: ranks.index(x['rank']))
        else:
            non_spades = [card for card in hand if card['suit'] != 'Spades']
            if non_spades:
                card_to_play = min(non_spades, key=lambda x: ranks.index(x['rank']))
            else:
                card_to_play = min(hand, key=lambda x: ranks.index(x['rank']))

    hand.remove(card_to_play)
    game_state['current_trick'].append({'player': player_name, 'card': card_to_play})
    if card_to_play['suit'] == 'Spades' and not spades_broken:
        game_state['spades_broken'] = True

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
                return render_template('bidding.html', error=error)
        except ValueError:
            error = "Please enter a valid integer."
            return render_template('bidding.html', error=error)
    return render_template('bidding.html')

@app.route('/play_hand', methods=['GET', 'POST'])
def play_hand():
    game_state = session['game_state']
    if game_state['round_over']:
        return redirect(url_for('game_over'))

    if request.method == 'POST':
        card_index = int(request.form['card_index'])
        player_hand = game_state['hands']['Player']
        selected_card = player_hand[card_index]

        # Validate play
        error = validate_play(selected_card, player_hand, game_state)
        if error:
            return render_template('play_hand.html', game_state=game_state, error=error)

        # Play the card
        player_hand.pop(card_index)
        game_state['current_trick'].append({'player': 'Player', 'card': selected_card})

        if selected_card['suit'] == 'Spades' and not game_state['spades_broken']:
            game_state['spades_broken'] = True

        # CPU players take their turns
        for cpu in ['CPU1', 'CPU2', 'CPU3']:
            cpu_play_card(cpu, game_state)

        # Determine winner of the trick
        winner = determine_winner(game_state['current_trick'])
        game_state['tricks_won'][winner] += 1
        game_state['current_player'] = winner
        game_state['current_trick'] = []
        game_state['trick_number'] += 1

        # Check if the round is over
        if game_state['trick_number'] > 13:
            game_state['round_over'] = True
            calculate_scores(game_state)
            session['game_state'] = game_state
            return redirect(url_for('game_over'))

        session['game_state'] = game_state
        return redirect(url_for('play_hand'))

    return render_template('play_hand.html', game_state=game_state)

def validate_play(selected_card, player_hand, game_state):
    current_trick = game_state['current_trick']
    spades_broken = game_state['spades_broken']

    if not current_trick:
        # First play of the trick
        if selected_card['suit'] == 'Spades' and not spades_broken:
            if all(card['suit'] == 'Spades' for card in player_hand):
                return None
            else:
                return "Cannot lead with Spades until they are broken."
    else:
        leading_suit = current_trick[0]['card']['suit']
        if selected_card['suit'] == leading_suit:
            return None
        elif any(card['suit'] == leading_suit for card in player_hand):
            return f"You must follow suit: {leading_suit}."
    return None

def calculate_scores(game_state):
    for player in game_state['scores']:
        bid = game_state['bids'][player]
        tricks = game_state['tricks_won'][player]
        if tricks >= bid:
            game_state['scores'][player] += 10 * bid + (tricks - bid)
        else:
            game_state['scores'][player] -= 10 * bid

@app.route('/game_over')
def game_over():
    game_state = session['game_state']
    return render_template('game_over.html', game_state=game_state)

if __name__ == '__main__':
    app.run(debug=True)
