# spades_game.py

from flask import Flask, render_template, request, redirect, url_for, session
import random

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Replace with a secure random key in production

# Define card ranks and suits
suits = ['Spades', 'Hearts', 'Diamonds', 'Clubs']
ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']

# [Rest of the functions remain the same]

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

        session['game_state'] = game_state  # Save after player's turn

        # Redirect to a new route to handle CPU plays
        return redirect(url_for('cpu_play'))

    return render_template('play_hand.html', game_state=game_state)

@app.route('/cpu_play')
def cpu_play():
    game_state = session['game_state']

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

# [Rest of the code remains the same]

if __name__ == '__main__':
    app.run(debug=True)
