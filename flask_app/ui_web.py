# Server-Side Python (ui_web.py) with Socket.IO integration and Flask routes for game management using a local dictionary for game states
from typing import Optional

from flask import Flask, render_template, request, redirect, url_for, jsonify, Response
from flask_socketio import SocketIO, emit
from datetime import datetime
import time
import pickle

from old_files.poker_player import AIPokerPlayer
from functional_poker import *
from ui_console import prepare_ui_data
from utils import get_celebrities

app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Replace with a secure secret key for sessions
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Dictionary to hold game states and messages for each game ID
games = {}
messages = {}

# Helper function to generate unique game ID
def generate_game_id():
    return str(int(time.time() * 1000))  # Use current time in milliseconds as a unique ID

@app.route('/')
def index():
    return render_template('home.html')

@app.route('/new_game', methods=['GET'])
def new_game():
    ai_player_names = get_celebrities(shuffled=True)[:2]
    game_state = initialize_game_state(player_names=ai_player_names)
    game_id = generate_game_id()
    games[game_id] = game_state
    messages[game_id] = []
    return redirect(url_for('game', game_id=game_id))

@app.route('/game/<game_id>', methods=['GET'])
def game(game_id) -> str or Response:
    game_state = games.get(game_id)
    if not game_state:
        return redirect(url_for('index'))

    num_players_remaining = len(game_state.players)
    if num_players_remaining == 1:
        return redirect(url_for('end_game', game_id=game_id))
    else:
        game_state = run_hand_until_player_turn(game_state)
        games[game_id] = game_state
        if game_state.awaiting_action:
            if game_state.current_phase in ['Flop', 'Turn', 'River'] and game_state.no_action_taken:
                # Send a table messages with the cards that were dealt
                num_cards_dealt = 3 if game_state.current_phase == 'Flop' else 1
                message_content = (f"{game_state.current_phase} cards dealt: "
                                   f"{[''.join([c['rank'], c['suit'][:1]]) for c in game_state.community_cards[-num_cards_dealt:]]}")
                send_message(game_id, "table", message_content, "table")

            if not game_state.current_player['is_human']:
                socketio.start_background_task(ai_player_action, game_id)
                return render_template('poker_game.html',
                                       game_state=game_state,
                                       player_options=game_state.current_player_options,
                                       game_id=game_id)
            else:
                return render_template('poker_game.html',
                                       game_state=game_state,
                                       player_options=game_state.current_player_options,
                                       game_id=game_id)

        elif game_state.current_phase == 'determining-winner':
            game_state, winner_info = determine_winner(game_state)

            message_content = (f"{' and'.join([name for name in winner_info['winning_player_names']])} won the pot of "
                               f"${winner_info['pot_total']}.\nwinning hand: {winner_info['winning_hand']}")
            send_message(game_id,"table", message_content, "table", 1)

            game_state = update_poker_game_state(game_state, current_phase='hand-over')
            game_state = reset_game_state_for_new_hand(game_state=game_state)
            games[game_id] = game_state
            return redirect(url_for('game', game_id=game_id))

    return render_template('poker_game.html', game_state=game_state, player_options=game_state.current_player_options, game_id=game_id)

@app.route('/action/<game_id>', methods=['POST'])
def player_action(game_id) -> tuple[str, int] or Response:
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

    game_state = games.get(game_id)
    if not game_state:
        return jsonify({'redirect': url_for('index')}), 400

    # Play the current player's turn
    current_player = game_state.current_player
    game_state = play_turn(game_state, action, amount)

    # Generate a message to be added to the game table
    message_content = f"{current_player['name']} chose to {action}{(' by ' + str(amount)) if amount > 0 else ''}."
    send_message(game_id,"table", message_content, "table")
    game_state = advance_to_next_active_player(game_state)

    # Update the game session states (global variables right now)
    games[game_id] = game_state
    return jsonify({'redirect': url_for('game', game_id=game_id)})


def send_message(game_id: str, sender: str, content: str, message_type: str, sleep: Optional[int] = None) -> None:
    """
    Send a message to the specified game chat.

    :param game_id: (str)
        The unique identifier for the game.
    :param sender: (str)
        The sender's username or identifier.
    :param content: (str)
        The message content.
    :param message_type: (str)
        The type of the message ['ai', 'table', 'user'].
    :param sleep: (Optional[int])
        Optional time to sleep after sending the message, in seconds.
    :return: (None)
        None
    """
    # Load the messages from the session and append the new message then emit the full list of messages.
    # Not the most efficient but it works for now.
    game_messages = messages.get(game_id, [])
    new_message = {
        "sender": sender,
        "content": content,
        "timestamp": datetime.now().strftime("%H:%M %b %d %Y"),
        "message_type": message_type
    }
    game_messages.append(new_message)

    # Update the messages session state
    messages[game_id] = game_messages
    socketio.emit('new_messages', {'game_messages': game_messages})
    socketio.sleep(sleep) if sleep else None


def ai_player_action(game_id):
    game_state = games.get(game_id)
    game_messages = messages.get(game_id, [])
    if not game_state:
        return

    current_player = game_state.current_player
    poker_player = AIPokerPlayer(current_player['name'], starting_money=current_player['stack'], ai_temp=0.9)
    ai = poker_player.assistant
    message = json.dumps(prepare_ui_data(game_state))
    response_dict = ai.chat(message + "\nPlease only respond with the JSON, not the text with back quotes.")
    try:
        response_dict = json.loads(response_dict)
    except json.JSONDecodeError as e:
        raise ValueError(f"Error decoding JSON response: {e}")

    # Prepare variables needed for new messages
    action = response_dict['action']
    amount = response_dict['adding_to_pot']
    player_message = response_dict['persona_response']
    player_physical_description = response_dict['physical']

    send_message(game_id, "table", f"{current_player['name']} chose to {action} by {amount}.", "table", 1)
    send_message(game_id, current_player['name'], f"{player_message} {player_physical_description}", "ai")

    game_state = play_turn(game_state, action, amount)
    game_state = advance_to_next_active_player(game_state)
    games[game_id] = game_state
    messages[game_id] = game_messages
    socketio.emit('ai_action_complete')

@app.route('/next_round/<game_id>', methods=['POST'])
def next_round(game_id):
    game_state = games.get(game_id)
    game_messages = messages.get(game_id, [])
    if not game_state:
        return redirect(url_for('index'))
    games[game_id] = game_state
    messages[game_id] = game_messages
    return redirect(url_for('game', game_id=game_id))

@app.route('/end_game/<game_id>', methods=['GET'])
def end_game(game_id):
    if game_id not in games:
        return redirect(url_for('index'))
    games.pop(game_id, None)
    messages.pop(game_id, None)
    return render_template('winner.html')

@app.route('/settings/<game_id>')
def settings(game_id):
    game_state = games.get(game_id)
    if not game_state:
        return redirect(url_for('index'))
    return render_template('settings.html')

@app.route('/messages/<game_id>', methods=['GET'])
def get_messages(game_id):
    game_messages = messages.get(game_id, [])
    return jsonify(game_messages)

@socketio.on('send_message')
def handle_send_message(data):
    game_id = data.get('game_id')
    content = data.get('message')
    sender = data.get('sender', 'User')
    message_type = data.get('message_type', 'user')
    send_message(game_id, sender, content, message_type)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
