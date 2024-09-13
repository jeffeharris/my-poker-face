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
        'nil_bids': {},  # Track Nil and Blind Nil bids
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
def get_cpu_bid(player_name, hand, game_state):
    high_cards = ['A', 'K', 'Q', 'J', '10']
    bid = sum(1 for card in hand if card['rank'] in high_cards or card['suit'] == 'Spades')

    # Implementing Nil and Blind Nil for CPUs
    if game_state['scores'][get_player_team(player_name, game_state)] <= -100:
        # Consider Blind Nil if behind by 100 points
        if random.random() < 0.1:  # 10% chance to bid Blind Nil
            game_state['nil_bids'][player_name] = 'Blind Nil'
            return 0
    elif random.random() < 0.1:  # 10% chance to bid Nil
        game_state['nil_bids'][player_name] = 'Nil'
        return 0

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
            # Cannot follow suit
            # Can play any card
            card_to_play = min(hand, key=lambda x: (suits.index(x['suit']), ranks.index(x['rank'])))
    else:
        # First player of the trick
        if spades_broken:
            card_to_play = min(hand, key=lambda x: (suits.index(x['suit']), ranks.index(x['rank'])))
        else:
            non_spades = [card for card in hand if card['suit'] != 'Spades']
            if non_spades:
                card_to_play = min(non_spades, key=lambda x: (suits.index(x['suit']), ranks.index(x['rank'])))
            else:
                # Only spades left
                card_to_play = min(hand, key=lambda x: (suits.index(x['suit']), ranks.index(x['rank'])))

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
        else:
            # Player cannot follow suit; they can play any card, including spades
            return None  # Valid play

    return None  # Valid play

# Calculate scores at the end of the round
def calculate_scores(game_state):
    # Check for Shooting the Moon (Boston)
    for team, players in game_state['teams'].items():
        if game_state['tricks_won'][team] == 13:
            # Team took all tricks
            game_state['scores'][team] += 250  # Boston bonus
            game_state['scores'][get_opponent_team(team, game_state)] -= 250
            return

    for team, players in game_state['teams'].items():
        bid = 0
        tricks = game_state['tricks_won'][team]

        # Calculate bids and penalties for Nil bids
        for player in players:
            player_bid = game_state['bids'][player]
            if player_bid == 0 and player in game_state['nil_bids']:
                # Handle Nil or Blind Nil
                if player in game_state.get('failed_nil', []):
                    # Nil bid failed
                    penalty = -100 if game_state['nil_bids'][player] == 'Nil' else -200
                    game_state['scores'][team] += penalty
                else:
                    # Nil bid succeeded
                    bonus = 100 if game_state['nil_bids'][player] == 'Nil' else 200
                    game_state['scores'][team] += bonus
            else:
                # Regular bid
                bid += player_bid

        # Calculate team score for regular bids
        if tricks >= bid:
            game_state['scores'][team] += 10 * bid + (tricks - bid)
        else:
            game_state['scores'][team] -= 10 * bid

def get_opponent_team(team, game_state):
    return 'Team1' if team == 'Team2' else 'Team2'

@app.route('/')
def index():
    session['game_state'] = initialize_game()
    return redirect(url_for('bidding'))

@app.route('/bidding', methods=['GET', 'POST'])
def bidding():
    game_state = session['game_state']
    if request.method == 'POST':
        # Handle player's bid
        bid_input = request.form['bid']
        if bid_input.lower() == 'nil':
            game_state['bids']['Player'] = 0
            game_state['nil_bids']['Player'] = 'Nil'
        elif bid_input.lower() == 'blind nil':
            if game_state['scores'][get_player_team('Player', game_state)] <= -100:
                game_state['bids']['Player'] = 0
                game_state['nil_bids']['Player'] = 'Blind Nil'
            else:
                error = "Blind Nil can only be bid when your team is behind by 100 points."
                return render_template('bidding.html', error=error, game_state=game_state)
        else:
            try:
                bid = int(bid_input)
                if 0 <= bid <= 13:
                    game_state['bids']['Player'] = bid
                else:
                    error = "Bid must be between 0 and 13."
                    return render_template('bidding.html', error=error, game_state=game_state)
            except ValueError:
                error = "Please enter a valid integer or 'Nil', 'Blind Nil'."
                return render_template('bidding.html', error=error, game_state=game_state)

        # CPU players make their bids
        for cpu in ['CPU1', 'CPU2', 'CPU3']:
            cpu_bid = get_cpu_bid(cpu, game_state['hands'][cpu], game_state)
            game_state['bids'][cpu] = cpu_bid

        session['game_state'] = game_state
        return redirect(url_for('play_hand'))
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

            # Check if player has a Nil bid and took a trick
            if 'Player' in game_state['nil_bids'] and game_state['nil_bids']['Player'] in ['Nil', 'Blind Nil']:
                # If player took a trick
                if 'Player' not in game_state.get('failed_nil', []) and len(game_state['current_trick']) == 1:
                    # Assume player hasn't failed yet; will check after trick completion
                    pass

            # Move to next player
            game_state['current_player'] = get_next_player(current_player)

        else:
            # CPU's turn
            cpu_play_card(current_player, game_state)
            # Move to next player
            game_state['current_player'] = get_next_player(current_player)

        # Check if trick is complete
        if len(game_state['current_trick']) == 4:
            # Determine winner of the trick
            winner = determine_winner(game_state['current_trick'])
            team = get_player_team(winner, game_state)
            game_state['tricks_won'][team] += 1
            game_state['previous_trick_winner'] = winner
            game_state['previous_trick'] = list(game_state['current_trick'])

            # Check for Nil bid failures
            for play in game_state['current_trick']:
                player = play['player']
                if player in game_state['nil_bids'] and game_state['nil_bids'][player] in ['Nil', 'Blind Nil']:
                    if player not in game_state.get('failed_nil', []):
                        game_state.setdefault('failed_nil', []).append(player)

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
        if game_state['current_player'] != 'Player' and not game_state['round_over']:
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
