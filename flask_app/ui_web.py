from flask import Flask, render_template, request, redirect, url_for, session
from functional_poker import initialize_game_state, reset_game_state_for_new_hand, end_game, deal_hole_cards, \
    play_turn, play_betting_round, deal_community_cards, determine_winner, advance_to_next_active_player
from utils import get_celebrities
import pickle

app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Replace with a secure secret key for sessions


# Helper function to save game state to session
def save_game_state(game_state):
    session['game_state'] = pickle.dumps(game_state)


# Helper function to load game state from session
def load_game_state():
    return pickle.loads(session['game_state']) if 'game_state' in session else None


@app.route('/')
def index():
    # Main page for initializing a new game
    return render_template('home.html')


@app.route('/new_game', methods=['POST'])
def new_game():
    # Initialize the game state
    ai_player_names = get_celebrities(shuffled=True)[:3]  # Using three AI players as default
    game_state = initialize_game_state(player_names=ai_player_names)
    save_game_state(game_state)
    return redirect(url_for('game'))


@app.route('/game')
def game():
    # Load the current game state
    game_state = load_game_state()
    if not game_state:
        return redirect(url_for('index'))  # Redirect to index if there's no game state

    # Render the current game state
    return render_template('poker_game.html', game_state=game_state)


@app.route('/action', methods=['POST'])
def player_action():
    # Load the current game state
    game_state = load_game_state()
    if not game_state:
        return redirect(url_for('index'))

    current_player = game_state.current_player
    if current_player['is_human']:
        # Get action from form
        action = request.form['action']
        amount = int(request.form.get('amount', 0))

        # Play turn based on user action
        game_state = play_turn(game_state, action, amount)
        save_game_state(game_state)

        # Advance to next player
        game_state = advance_to_next_active_player(game_state)
        save_game_state(game_state)

    return redirect(url_for('game'))


@app.route('/next_round', methods=['POST'])
def next_round():
    # Load the current game state
    game_state = load_game_state()
    if not game_state:
        return redirect(url_for('index'))

    # Handle end of a betting round and proceed to the next phase
    # (e.g., deal community cards if applicable, then start next betting round)
    # Play betting round, deal community cards etc.
    # ...

    save_game_state(game_state)
    return redirect(url_for('game'))


@app.route('/end_game')
def end_game_route():
    # Load the current game state
    game_state = load_game_state()
    if not game_state:
        return redirect(url_for('index'))

    # Determine the winner of the game
    end_game_info = end_game(game_state)
    session.clear()  # Clear the session after the game ends
    return render_template('winner.html', end_game_info=end_game_info)


if __name__ == '__main__':
    app.run(debug=True)