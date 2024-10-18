from flask import Flask, render_template, request, redirect, url_for, session, jsonify, Response
from functional_poker import initialize_game_state, reset_game_state_for_new_hand, \
    play_turn, determine_winner, advance_to_next_active_player, \
    run_hand_until_player_turn, update_poker_game_state, PokerGameState
from utils import get_celebrities
import pickle

app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Replace with a secure secret key for sessions


# Helper function to save game state to session
def save_game_state(game_state):
    session['game_state'] = pickle.dumps(game_state)
    # display_game_state(game_state, include_deck=False)


# Helper function to load game state from session
def load_game_state():
    return pickle.loads(session['game_state']) if 'game_state' in session else None


@app.route('/')
def index():
    # Main page for initializing a new game
    return render_template('home.html')


@app.route('/new_game', methods=['GET'])
def new_game():
    # Initialize the game state
    ai_player_names = get_celebrities(shuffled=True)[:3]  # Using three AI players as default
    game_state = initialize_game_state(player_names=ai_player_names)
    save_game_state(game_state)
    return redirect(url_for('game'))
    # TODO: route to a new hand


@app.route('/game', methods=['GET'])
def game() -> str or Response:
    """
    Loads and renders the current game state. Identifies what phase of the game it's in and directs the user to the
    right next state.

    :return: the rendered game template
    """
    # Load the current game state
    game_state = load_game_state()
    if not game_state:
        return redirect(url_for('index'))  # Redirect to index if there's no game state

    num_players_remaining = len(game_state.players)

    if num_players_remaining == 1:
        return redirect(url_for('end_game_route'))
    else:
        game_state = run_hand_until_player_turn(game_state)
        save_game_state(game_state)
        if game_state.current_phase == 'determining-winner':
            # The hand will reset when it loops back
            # Determine the winner
            game_state, winner_info = determine_winner(game_state)
            print(winner_info)
            game_state = update_poker_game_state(game_state, current_phase='hand-over')
            print(10, game_state.current_phase, "hand has ended!")
            # Reset the game for a new hand
            game_state = reset_game_state_for_new_hand(game_state=game_state)
            save_game_state(game_state)
            return redirect(url_for('game'))
        # Get action from player and update the game state
        elif game_state.awaiting_action:
            return render_template(
                'poker_game.html', game_state=game_state, player_options=game_state.current_player_options)

    save_game_state(game_state)
    # Render the current game state
    return render_template('poker_game.html', game_state=game_state, player_options=game_state.current_player_options)


@app.route('/action', methods=['POST'])
def player_action() -> tuple[str, int] or Response:
    try:
        data = request.get_json()
        app.logger.debug(f"Received data: {data}")

        if not data or 'action' not in data:
            return jsonify({'error': 'Invalid request payload'}), 400

        action = data['action']
        amount = int(data.get('amount', 0))
        app.logger.debug(f"Action: {action}, Amount: {amount}")
    except (KeyError, TypeError, ValueError) as e:
        app.logger.error(f"Error parsing request: {e}")
        return jsonify({'error': str(e)}), 400

    game_state = load_game_state()
    if not game_state:
        return jsonify({'redirect': url_for('index')}), 400

    current_player = game_state.current_player
    if current_player['is_human']:
        app.logger.debug("Current player is human")
    else:
        app.logger.debug("Current player is AI")
    game_state = play_turn(game_state, action, amount)
    game_state = advance_to_next_active_player(game_state)
    save_game_state(game_state)
    app.logger.debug("Game state updated successfully")

    response = jsonify({'redirect': url_for('game')})
    app.logger.debug(f"Response: {response.get_data(as_text=True)}")
    return response



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
    session.clear()  # Clear the session after the game ends
    return render_template('winner.html', end_game_info=end_game_info)


if __name__ == '__main__':
    app.run(debug=True)