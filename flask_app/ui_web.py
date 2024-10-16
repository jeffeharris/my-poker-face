from flask import Flask, render_template, request, redirect, url_for, session, jsonify

from old_files.poker_player import AIPokerPlayer

from functional_poker import *
from utils import get_celebrities
import pickle

app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Replace with a secure secret key for sessions


# Helper function to save game state to session
def save_game_state(game_state):
    session['game_state'] = pickle.dumps(game_state)
    app.logger.debug("Game state updated successfully")
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
    game_state = setup_hand(game_state)
    game_state = set_betting_round_starting_player(game_state)
    save_game_state(game_state)
    return redirect(url_for('game'))
    # TODO: route to a new hand


# TODO: the game state is looping instead of pausing when it's the player's turn
@app.route('/game', methods=['GET'])
def game():
    """
    Loads and renders the current game state. Identifies what phase of the game it is in and directs the user to the
    right next state.
    :return: the rendered game template
    """
    game_state = load_game_state()
    if not game_state:
        return redirect(url_for('index'))  # Redirect to index if there's no game state

    # Validate the current state of the pot and active players
    if not are_pot_contributions_valid(game_state) and len(
            [p for p in game_state.players if not p['is_folded'] and not p['is_all_in']]) > 1:
        print("Pot is not ready")
        # game_state = set_betting_round_starting_player(game_state)
        save_game_state(game_state)

        # Check whose turn it is and route accordingly
        if game_state.current_player['is_human']:
            print("Human player's turn")
            return render_template('poker_game.html', game_state=game_state,
                                   player_options=game_state.current_player_options)
        elif not game_state.current_player['is_human']:
            print("AI player's turn")
            return redirect(url_for('ai_player_action'))
    else:
        print("Pot is ready")
        game_state = deal_community_cards(game_state)
        return render_template('poker_game.html', game_state=game_state, player_options=game_state.current_player_options)

@app.route('/action', methods=['POST'])
def player_action():
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
        game_state = play_turn(game_state, action, amount)
        save_game_state(game_state)
        game_state = advance_to_next_active_player(game_state)

    save_game_state(game_state)
    response = jsonify({'redirect': url_for('game')})
    app.logger.debug(f"Response: {response.get_data(as_text=True)}")
    return response

@app.route('/ai_action', methods=['GET'])
def ai_player_action():
    game_state = load_game_state()
    if not game_state:
        return jsonify({'redirect': url_for('index')}), 400

    current_player = game_state.current_player
    poker_player = AIPokerPlayer(current_player['name'],starting_money=current_player['stack'],ai_temp=0.9)
    ai = poker_player.assistant
    # for message in player_messages:
    #     ai_assistant.assistant.add_to_memory(message)
    message = json.dumps(prepare_ui_data(game_state))
    # print(message)
    response_dict = ai.chat(message + "\nPlease only respond with the JSON, not the text with back quotes.")
    try:
        response_dict = json.loads(response_dict)
    except json.JSONDecodeError as e:
        raise ValueError(f"Error decoding JSON response: {e}")

    action = response_dict['action']
    amount = response_dict['adding_to_pot']
    player_message = response_dict['persona_response']
    player_physical_description = response_dict['physical']

    print(player_message)
    print(player_physical_description)

    app.logger.debug("Current player is AI")
    game_state = play_turn(game_state, action, amount)
    save_game_state(game_state)
    game_state = advance_to_next_active_player(game_state)
    save_game_state(game_state)
    #
    # response = jsonify({'redirect': url_for('game')})
    # app.logger.debug(f"Response: {response.get_data(as_text=True)}")
    return render_template('poker_game.html', game_state=game_state, player_options=game_state.current_player_options)



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


@app.route('/settings')
def settings():
    game_state = load_game_state()
    if not game_state:
        return redirect(url_for('index'))
    return render_template('settings.html')


# TODO: <FEATURE> update these messages interactions to use socketio for real time back/forth
@app.route('/messages', methods=['GET'])
def get_messages():
    return jsonify(
        [
            {
                "sender": "Jeff",
                "content": "hello!",
                "timestamp": "11:23 Aug 25 2024",
                "message_type": "user"
            },
            {
                "sender": "Kanye West",
                "content": "the way to the truth is through my hands",
                "timestamp": "11:25 Aug 25 2024",
                "message_type": "ai"
            },
            {
                "sender": "table",
                "content": "The flop has been dealt",
                "timestamp": "11:26 Aug 25 2024",
                "message_type": "table"
            },
            {
                "sender": "Jeff",
                "content": "I'm not sure how to respond to that Kanye, but can you share your dealers number with me?",
                "timestamp": "11:27 Aug 25 2024",
                "message_type": "user"
            }
        ]
    )
    # return jsonify(game['messages'])


@app.route('/messages', methods=['POST'])
def add_message():
    new_message = request.json.get('message')
    if new_message:
        game['messages'].append(new_message)
        return jsonify({"status": "success"}), 201
    return jsonify({"status": "error"}), 400


if __name__ == '__main__':
    app.run(debug=True)