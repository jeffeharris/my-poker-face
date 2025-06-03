# Server-Side Python (ui_web.py) with Socket.IO integration and Flask routes for game management using a local dictionary for game states
from typing import Optional

from flask import Flask, render_template, redirect, url_for, jsonify, Response
from flask_socketio import SocketIO, join_room
from datetime import datetime
import time
import os

from poker.controllers import AIPlayerController
from poker.poker_game import PokerGameState, initialize_game_state, determine_winner, play_turn, \
    advance_to_next_active_player, award_pot_winnings
from poker.poker_state_machine import PokerStateMachine, PokerPhase
from poker.utils import get_celebrities
from poker.persistence import GamePersistence
from .game_adapter import StateMachineAdapter, GameStateAdapter

app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Replace with a secure secret key for sessions
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Dictionary to hold game states and messages for each game ID
games = {}
messages = {}

# Initialize persistence layer
# Use /app/data in Docker, or local path otherwise
if os.path.exists('/app/data'):
    db_path = '/app/data/poker_games.db'
else:
    db_path = os.path.join(os.path.dirname(__file__), '..', 'poker_games.db')
persistence = GamePersistence(db_path)


# Helper function to generate unique game ID
def generate_game_id():
    return str(int(time.time() * 1000))  # Use current time in milliseconds as a unique ID


def update_and_emit_game_state(game_id):
    game_state = games[game_id]['state_machine'].game_state  # Obtain current game state
    socketio.emit('update_game_state', {'game_state': game_state.to_dict()}, to=game_id)


@socketio.on('join_game')
def on_join(game_id):
    join_room(game_id)
    print(f"User joined room: {game_id}")
    socketio.emit('player_joined', {'message': 'A new player has joined!'}, to=game_id)


@app.route('/')
def index():
    return render_template('home.html')


@app.route('/games')
def list_games():
    """List all saved games."""
    saved_games = persistence.list_games(limit=50)
    games_data = []
    
    for game in saved_games:
        games_data.append({
            'game_id': game.game_id,
            'created_at': game.created_at.strftime("%Y-%m-%d %H:%M"),
            'updated_at': game.updated_at.strftime("%Y-%m-%d %H:%M"),
            'phase': game.phase,
            'num_players': game.num_players,
            'pot_size': game.pot_size
        })
    
    return jsonify({'games': games_data})


@app.route('/new_game', methods=['GET'])
def new_game():
    ai_player_names = get_celebrities(shuffled=True)[:4]
    game_state = initialize_game_state(player_names=ai_player_names)
    base_state_machine = PokerStateMachine(game_state=game_state)
    state_machine = StateMachineAdapter(base_state_machine)
    # Create a controller for each player in the game and add to a map of name -> controller
    ai_controllers = {}
    for player in state_machine.game_state.players:
        if not player.is_human:
            new_controller = AIPlayerController(player.name, state_machine)
            ai_controllers[player.name] = new_controller

    game_data = {
        'state_machine': state_machine,
        'ai_controllers': ai_controllers,
        'messages': []
    }
    game_id = generate_game_id()
    games[game_id] = game_data
    
    # Save the new game to database  
    persistence.save_game(game_id, state_machine._state_machine)
    
    return redirect(url_for('game', game_id=game_id))


@socketio.on('progress_game')
def progress_game(game_id):
    current_game_data = games.get(game_id)
    if not current_game_data:
        return
    state_machine = current_game_data['state_machine']

    while True:
        # Run until a player action is needed or the hand has ended
        state_machine.run_until([PokerPhase.EVALUATING_HAND])
        current_game_data['state_machine'] = state_machine
        games[game_id] = current_game_data
        game_state = state_machine.game_state

        # Emit the latest game state to the client
        update_and_emit_game_state(game_id)

        if len([p.name for p in game_state.players if p.is_human]) < 1 or len(game_state.players) == 1:
            return redirect(url_for('end_game', game_id=game_id))

        if state_machine.current_phase in [PokerPhase.FLOP, PokerPhase.TURN, PokerPhase.RIVER] and game_state.no_action_taken:
            # Send a table messages with the cards that were dealt
            num_cards_dealt = 3 if state_machine.current_phase == PokerPhase.FLOP else 1
            message_content = (f"{state_machine.current_phase} cards dealt: "
                               f"{[''.join([c['rank'], c['suit'][:1]]) for c in game_state.community_cards[-num_cards_dealt:]]}")
            send_message(game_id, "table", message_content, "table")

        # Check if it's an AI's turn to play, then handle AI actions
        if not game_state.current_player.is_human and game_state.awaiting_action:
            handle_ai_action(game_id)

        # Check for and handle the Evaluate Hand phase outside the state machine so we can update
        # the front end with the results.
        elif state_machine.current_phase == PokerPhase.EVALUATING_HAND:
            winner_info = determine_winner(game_state)
            winning_player_names = list(winner_info['winnings'].keys())
            game_state = award_pot_winnings(game_state, winner_info['winnings'])

            winning_players_string = (', '.join(winning_player_names[:-1]) +
                                      f" and {winning_player_names[-1]}") \
                                      if len(winning_player_names) > 1 else winning_player_names[0]

            message_content = (
                f"{winning_players_string} won the pot of ${winner_info['winnings']} with {winner_info['hand_name']}. "
                f"Winning hand: {winner_info['winning_hand']}"
            )
            send_message(game_id,"table", message_content, "table", 1)
            send_message(game_id, "table", "***   NEW HAND DEALT   ***", "table")

            # Update the state_machine to be ready for it's next run through the game progression
            state_machine.update_phase()
            state_machine.game_state = game_state
            current_game_data['state_machine'] = state_machine
            games[game_id] = current_game_data
            state_machine.advance_state()
            update_and_emit_game_state(game_id)

        else:
            # If a human action is required, exit the loop
            cost_to_call = game_state.highest_bet - game_state.current_player.bet
            socketio.emit('player_turn_start', { 'current_player_options': game_state.current_player_options, 'cost_to_call': cost_to_call}, to=game_id)
            break


@app.route('/game/<game_id>', methods=['GET'])
def game(game_id) -> str or Response:
    current_game_data = games.get(game_id)
    
    # Try to load from database if not in memory
    if not current_game_data:
        base_state_machine = persistence.load_game(game_id)
        if base_state_machine:
            state_machine = StateMachineAdapter(base_state_machine)
            # Recreate AI controllers for loaded game
            ai_controllers = {}
            for player in state_machine.game_state.players:
                if not player.is_human:
                    ai_controllers[player.name] = AIPlayerController(player.name, state_machine)
            
            # Load messages from database
            db_messages = persistence.load_messages(game_id)
            
            current_game_data = {
                'state_machine': state_machine,
                'ai_controllers': ai_controllers,
                'messages': db_messages
            }
            games[game_id] = current_game_data
        else:
            return redirect(url_for('index'))
    
    state_machine = current_game_data['state_machine']

    # progress_game(game_id)

    return render_template('poker_game.html',
                           game_state=state_machine.game_state,
                           player_options=state_machine.game_state.current_player_options,
                           game_id=game_id,
                           current_phase=str(state_machine.current_phase))


@socketio.on('player_action')
def handle_player_action(data):
    try:
        game_id = data['game_id']
        action = data['action']
        amount = int(data.get('amount', 0))
    except KeyError:
        return

    current_game_data = games.get(game_id)
    if not current_game_data:
        return
    state_machine = current_game_data['state_machine']

    # Play the current player's turn
    current_player = state_machine.game_state.current_player
    game_state = play_turn(state_machine.game_state, action, amount)

    # Generate a message to be added to the game table
    table_message_content = f"{current_player.name} chose to {action}{(' by $' + str(amount)) if amount > 0 else ''}."
    send_message(game_id,"table", table_message_content, "table")
    game_state = advance_to_next_active_player(game_state)
    state_machine.game_state = game_state

    # Update the game session states (global variables right now)
    current_game_data['state_machine'] = state_machine
    games[game_id] = current_game_data
    
    # Save game after human action
    persistence.save_game(game_id, state_machine._state_machine)
    
    update_and_emit_game_state(game_id)  # Emit updated game state
    progress_game(game_id)


def handle_ai_action(game_id: str) -> None:
    """
    Handle an AI player's action in the game.

    :param game_id: (int)
        The ID of the game for which the AI action is being handled.
    :return: (None)
    """
    current_game_data = games.get(game_id)
    if not current_game_data:
        return

    state_machine = current_game_data['state_machine']
    game_messages = current_game_data['messages']
    ai_controllers = current_game_data['ai_controllers']

    current_player = state_machine.game_state.current_player
    controller = ai_controllers[current_player.name]
    player_response_dict = controller.decide_action(game_messages[-8:])

    # Prepare variables needed for new messages
    action = player_response_dict['action']
    amount = player_response_dict['adding_to_pot']
    player_message = player_response_dict['persona_response']
    player_physical_description = player_response_dict['physical']

    table_message_content = f"{current_player.name} chose to {action}{(' by $' + str(amount)) if amount > 0 else ''}."
    send_message(game_id, current_player.name, f"{player_message} {player_physical_description}", "ai", 1)
    send_message(game_id, "table", table_message_content, "table")

    game_state = play_turn(state_machine.game_state, action, amount)
    game_state = advance_to_next_active_player(game_state)
    state_machine.game_state = game_state
    current_game_data['state_machine'] = state_machine
    games[game_id] = current_game_data
    
    # Save game after AI action
    persistence.save_game(game_id, state_machine._state_machine)
    
    update_and_emit_game_state(game_id)


@socketio.on('send_message')
def handle_send_message(data):
    # Get needed values from the data
    game_id = data.get('game_id')
    content = data.get('message')
    sender = data.get('sender', 'Jeff')
    message_type = data.get('message_type', 'user')

    send_message(game_id, sender, content, message_type)

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
    game_data = games.get(game_id)
    if not game_data:
        return
    game_messages = game_data['messages']
    new_message = {
        "sender": sender,
        "content": content,
        "timestamp": datetime.now().strftime("%H:%M %b %d %Y"),
        "message_type": message_type
    }
    game_messages.append(new_message)

    # Update the messages session state
    game_data['messages'] = game_messages
    games[game_id] = game_data
    
    # Save message to database
    persistence.save_message(game_id, message_type, f"{sender}: {content}")
    socketio.emit('new_messages', {'game_messages': game_messages}, to=game_id)
    socketio.sleep(sleep) if sleep else None


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
    game_data = games.get(game_id)
    if not game_data:
        game_messages = []
    else:
        game_messages = game_data['messages']
    return jsonify(game_messages)


if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5001, debug=True, allow_unsafe_werkzeug=True)
