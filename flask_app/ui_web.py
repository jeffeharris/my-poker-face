# Server-Side Python (ui_web.py) with Socket.IO integration and Flask routes for game management using a local dictionary for game states

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
    ai_player_names = get_celebrities(shuffled=True)[:3]
    game_state = initialize_game_state(player_names=ai_player_names)
    game_id = generate_game_id()
    games[game_id] = game_state
    messages[game_id] = []
    return redirect(url_for('game', game_id=game_id))

@app.route('/game/<game_id>', methods=['GET'])
def game(game_id) -> str or Response:
    game_state = games.get(game_id)
    game_messages = messages.get(game_id, [])
    if not game_state:
        return redirect(url_for('index'))

    num_players_remaining = len(game_state.players)
    if num_players_remaining == 1:
        return redirect(url_for('end_game', game_id=game_id))
    else:
        game_state = run_hand_until_player_turn(game_state)
        games[game_id] = game_state
        if game_state.current_phase == 'determining-winner':
            game_state, winner_info = determine_winner(game_state)
            new_message = {
                "sender": "table",
                "content": f"{' and'.join([name for name in winner_info['winning_player_names']])} won the pot of ${winner_info['pot_total']}.\n"
                           f"winning hand: {winner_info['winning_hand']}",
                "timestamp": datetime.now().strftime("%H:%M %b %d %Y"),
                "message_type": "table"
            }
            game_messages.append(new_message)
            socketio.emit('new_messages', {'game_messages': game_messages, 'game_id': game_id})
            socketio.sleep(1)
            game_state = update_poker_game_state(game_state, current_phase='hand-over')
            game_state = reset_game_state_for_new_hand(game_state=game_state)
            games[game_id] = game_state
            messages[game_id] = game_messages
            return redirect(url_for('game', game_id=game_id))
        elif game_state.awaiting_action:
            if not game_state.current_player['is_human']:
                socketio.emit('ai_action_in_progress', {'game_id': game_id})
                socketio.start_background_task(ai_player_action, game_id)
                messages[game_id] = game_messages
                return render_template('poker_game.html', game_state=game_state, player_options=game_state.current_player_options, game_id=game_id)
            else:
                messages[game_id] = game_messages
                return render_template('poker_game.html', game_state=game_state, player_options=game_state.current_player_options, game_id=game_id)
    messages[game_id] = game_messages
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
    game_messages = messages.get(game_id, [])
    if not game_state:
        return jsonify({'redirect': url_for('index')}), 400

    current_player = game_state.current_player
    game_state = play_turn(game_state, action, amount)
    new_message = {
        "sender": "table",
        "content": f"{current_player['name']} chose to {action}{(' by ' + str(amount)) if amount > 0 else ''}.",
        "timestamp": datetime.now().strftime("%H:%M %b %d %Y"),
        "message_type": "table"
    }
    game_messages.append(new_message)
    socketio.emit('new_messages', {'game_messages': game_messages})
    game_state = advance_to_next_active_player(game_state)
    games[game_id] = game_state
    messages[game_id] = game_messages
    return jsonify({'redirect': url_for('game', game_id=game_id)})

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

    action = response_dict['action']
    amount = response_dict['adding_to_pot']
    player_message = response_dict['persona_response']
    player_physical_description = response_dict['physical']

    new_table_message = {
        "sender": "table",
        "content": f"{current_player['name']} chose to {action} by {amount}.",
        "timestamp": datetime.now().strftime("%H:%M %b %d %Y"),
        "message_type": "table"
    }
    game_messages.append(new_table_message)
    socketio.emit('new_messages', {'game_messages': game_messages, 'game_id': game_id})
    socketio.sleep(1)

    new_ai_message = {
        "sender": current_player['name'],
        "content": f"{player_message} {player_physical_description}",
        "timestamp": datetime.now().strftime("%H:%M %b %d %Y"),
        "message_type": "ai"
    }
    game_messages.append(new_ai_message)
    socketio.emit('new_messages', {'game_messages': game_messages, 'game_id': game_id})
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
    game_state = games.get(game_id)
    game_messages = messages.get(game_id, [])
    if game_state is None:
        return
    message = {
        'sender': sender,
        'content': content,
        'timestamp': datetime.now().strftime("%H:%M %b %d %Y"),
        'message_type': message_type
    }
    game_messages.append(message)
    messages[game_id] = game_messages
    socketio.emit('new_messages', {'game_messages': game_messages, 'game_id': game_id})

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
