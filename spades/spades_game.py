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
def deal_cards():
    deck = create_deck()
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
    game_state = {
        'hands': {},  # Will be populated after dealing
        'bids': {},
        'nil_bids': {},
        'failed_nil': [],
        'tricks_won': {'Team1': 0, 'Team2': 0},
        'current_trick': [],
        'current_player': None,
        'trick_number': 1,
        'spades_broken': False,
        'scores': {'Team1': 0, 'Team2': 0},
        'game_over': False,
        'winning_team': None,
        'previous_trick': [],
        'previous_trick_winner': None,
        'teams': {
            'Team1': ['Player', 'CPU2'],
            'Team2': ['CPU1', 'CPU3']
        },
        'bidding_order': ['Player', 'CPU1', 'CPU2', 'CPU3'],
        'current_bidder_index': 0,
        'current_bids': {},
        'round_number': 1
    }
    return game_state

def get_game_state():
    game_state = session.get('game_state')
    if not game_state:
        game_state = initialize_game()
        session['game_state'] = game_state
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

# CPU bidding logic (updated for softer bids)
def get_cpu_bid(player_name, hand, game_state):
    bid = 0

    # Evaluate the hand for high cards and Spades
    for card in hand:
        if card['rank'] == 'A':
            bid += 1  # Aces are likely to win
        elif card['rank'] == 'K':
            bid += 0.8
        elif card['rank'] == 'Q':
            bid += 0.5
        elif card['rank'] == 'J' or card['rank'] == '10':
            bid += 0.3
        if card['suit'] == 'Spades' and card['rank'] in ['A', 'K', 'Q']:
            bid += 0.5  # Count high Spades more aggressively

    # Round down bids to make CPU less aggressive
    bid = int(bid)

    # CPU logic for Nil or Blind Nil based on score
    team_score = game_state['scores'][get_player_team(player_name, game_state)]
    if bid == 0:
        bid = 1  # Ensure CPU bids at least 1
        if random.random() < 0.1 and team_score <= -100:  # 10% chance to bid Nil
            game_state['nil_bids'][player_name] = 'Nil'
            return 0

    if team_score <= -100:
        if random.random() < 0.02:  # 2% chance to bid Blind Nil
            game_state['nil_bids'][player_name] = 'Blind Nil'
            return 0

    return bid

# CPU player logic for playing a card (updated for spades breaking)
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
            spades_cards = [card for card in hand if card['suit'] == 'Spades']
            if spades_cards:
                # Play a Spade to break Spades if they haven't been broken
                card_to_play = min(spades_cards, key=lambda x: ranks.index(x['rank']))
                if not spades_broken:
                    game_state['spades_broken'] = True
            else:
                # No Spades; play lowest card
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
                # Only Spades left
                card_to_play = min(hand, key=lambda x: (suits.index(x['suit']), ranks.index(x['rank'])))

    hand.remove(card_to_play)
    game_state['current_trick'].append({'player': player_name, 'card': card_to_play})

    if card_to_play['suit'] == 'Spades' and not spades_broken:
        game_state['spades_broken'] = True

    # Check if CPU has a Nil bid and took a trick
    if player_name in game_state['nil_bids'] and game_state['nil_bids'][player_name] in ['Nil', 'Blind Nil']:
        # Mark that the Nil bid failed
        if player_name not in game_state.get('failed_nil', []):
            game_state.setdefault('failed_nil', []).append(player_name)


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
    # Initialize game state if not already done
    if 'game_state' not in session or session['game_state'].get('game_over', False):
        session['game_state'] = initialize_game()
    return redirect(url_for('start_game'))

@app.route('/start_game', methods=['GET', 'POST'])
def start_game():
    game_state = get_game_state()
    if request.method == 'POST':
        blind_nil_choice = request.form.get('blind_nil_choice')
        if blind_nil_choice == 'Yes':
            # Check if Blind Nil is allowed (team behind by 100 points)
            team_score = game_state['scores'][get_player_team('Player', game_state)]
            if team_score <= -100 or team_score == 0:
                game_state['bids']['Player'] = 0
                game_state['nil_bids']['Player'] = 'Blind Nil'
                # Deal cards without showing them
                game_state['hands'] = deal_cards()
                # CPUs make their bids in order
                process_bids(game_state)
                # Determine starting player
                game_state['current_player'] = find_starting_player(game_state['hands'])
                session['game_state'] = game_state
                return redirect(url_for('play_hand'))
            else:
                error = "Blind Nil can only be bid when your team is behind by 100 points."
                return render_template('start_game.html', game_state=game_state, error=error)
        else:
            # Proceed to regular bidding
            return redirect(url_for('bidding'))
    return render_template('start_game.html', game_state=game_state)

def process_bids(game_state):
    # Process bidding in order
    bidding_order = game_state['bidding_order']
    for bidder in bidding_order:
        if bidder == 'Player' and 'Player' in game_state['bids']:
            continue  # Player has already bid (in case of Blind Nil)
        elif bidder != 'Player':
            # CPU makes bid
            hand = game_state['hands'][bidder]
            bid = get_cpu_bid(bidder, hand, game_state)
            game_state['bids'][bidder] = bid
            game_state['current_bids'][bidder] = bid  # Record current bid for display

@app.route('/bidding', methods=['GET', 'POST'])
def bidding():
    game_state = get_game_state()
    if 'hands' not in game_state or not game_state['hands']:
        # Deal cards
        game_state['hands'] = deal_cards()
        # Determine starting player
        game_state['current_player'] = find_starting_player(game_state['hands'])
        session['game_state'] = game_state

    if request.method == 'POST':
        bid_input = request.form['bid']
        if bid_input.lower() == 'nil':
            game_state['bids']['Player'] = 0
            game_state['nil_bids']['Player'] = 'Nil'
        else:
            try:
                bid = int(bid_input)
                if 0 <= bid <= 13:
                    game_state['bids']['Player'] = bid
                else:
                    error = "Bid must be between 0 and 13."
                    return render_template('bidding.html', error=error, game_state=game_state)
            except ValueError:
                error = "Please enter a valid integer or 'Nil'."
                return render_template('bidding.html', error=error, game_state=game_state)

        # Process remaining bids
        process_bids(game_state)

        session['game_state'] = game_state
        return redirect(url_for('play_hand'))

    # Prepare data for bidding template
    bidder = game_state['bidding_order'][game_state['current_bidder_index']]
    bids_so_far = {player: game_state['bids'].get(player, None) for player in
                   game_state['bidding_order'][:game_state['current_bidder_index']]}
    game_state['current_bids'] = bids_so_far
    return render_template('bidding.html', game_state=game_state, bidder=bidder)

def reset_game_state_for_new_round(game_state):
    game_state.update({
        'hands': {},
        'bids': {},
        'nil_bids': {},
        'failed_nil': [],
        'tricks_won': {'Team1': 0, 'Team2': 0},
        'current_trick': [],
        'current_player': None,
        'trick_number': 1,
        'spades_broken': False,
        'round_over': False,
        'previous_trick': [],
        'previous_trick_winner': None,
        'current_bids': {},
        'current_bidder_index': 0,
        'bidding_order': ['Player', 'CPU1', 'CPU2', 'CPU3'],
        'round_number': game_state['round_number'] + 1,
    })


@app.route('/play_hand', methods=['GET', 'POST'])
def play_hand():
    game_state = get_game_state()
    if not game_state:
        return redirect(url_for('index'))

    if game_state.get('round_over', False):
        # Check for game over condition
        winning_score = 500
        for team, score in game_state['scores'].items():
            if score >= winning_score:
                game_state['game_over'] = True
                game_state['winning_team'] = team
                session['game_state'] = game_state
                return redirect(url_for('game_over'))
        # Reset for next round
        reset_game_state_for_new_round(game_state)
        session['game_state'] = game_state
        return redirect(url_for('start_game'))

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
                    if winner == player and player not in game_state.get('failed_nil', []):
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
                return redirect(url_for('play_hand'))

        session['game_state'] = game_state

        # If next player is not 'Player', process AI moves
        if game_state['current_player'] != 'Player' and not game_state.get('round_over', False):
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
    game_state = get_game_state()
    return render_template('game_over.html', game_state=game_state)

if __name__ == '__main__':
    app.run(debug=True)
