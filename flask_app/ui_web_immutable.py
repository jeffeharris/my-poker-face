"""
Minimal Flask API endpoints using immutable state machine.
This replaces ui_web.py for the React app.
"""
from flask import Flask, jsonify, request
from flask_cors import CORS
from poker.poker_game import initialize_game_state, play_turn, advance_to_next_active_player
from poker.poker_state_machine import PokerStateMachine, PokerPhase
from poker.controllers import AIPlayerController
from poker.persistence import GamePersistence
from datetime import datetime
import uuid
import os


app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# In-memory game storage (temporary until we update persistence)
games = {}

# Initialize persistence
if os.environ.get('DOCKER_ENV'):
    db_path = '/app/data/poker_games.db'
else:
    db_path = './poker_games.db'
persistence = GamePersistence(db_path)


def generate_game_id():
    """Generate a unique game ID."""
    return str(uuid.uuid4())[:8]


@app.route('/api/new-game', methods=['POST'])
def api_new_game():
    """Create a new game and return the game ID."""
    # Initialize game with AI players
    player_names = ['Jeff', 'Feidman', 'Ivey', 'Negreanu']
    game_state = initialize_game_state(player_names)
    
    # Create immutable state machine
    state_machine = PokerStateMachine(game_state)

    # Generate game_id first for tracking
    game_id = generate_game_id()

    # Initialize AI controllers
    ai_controllers = {}
    for i, player in enumerate(game_state.players):
        if not player.is_human:
            ai_controllers[player.name] = AIPlayerController(
                player_name=player.name,
                state_machine=state_machine,
                game_id=game_id
            )

    # Store game data
    games[game_id] = {
        'state_machine': state_machine,
        'ai_controllers': ai_controllers,
        'messages': [{
            'id': str(uuid.uuid4()),
            'sender': 'System',
            'message': 'New game started! Good luck!',
            'timestamp': datetime.now().isoformat(),
            'type': 'system'
        }]
    }
    
    # Progress to first action
    games[game_id]['state_machine'] = progress_game_to_action(games[game_id])
    
    return jsonify({'game_id': game_id})


@app.route('/api/game-state/<game_id>')
def api_game_state(game_id):
    """Get current game state for React app."""
    if game_id not in games:
        return jsonify({'error': 'Game not found'}), 404
    
    game_data = games[game_id]
    state_machine = game_data['state_machine']
    game_state = state_machine.game_state
    
    # Convert to API format
    return jsonify({
        'players': [player.to_dict() for player in game_state.players],
        'community_cards': [card.to_dict() if hasattr(card, 'to_dict') else card 
                           for card in game_state.community_cards],
        'pot': game_state.pot,
        'current_player_idx': game_state.current_player_idx,
        'current_dealer_idx': game_state.current_dealer_idx,
        'small_blind_idx': game_state.small_blind_idx,
        'big_blind_idx': game_state.big_blind_idx,
        'phase': state_machine.phase.name,
        'highest_bet': game_state.highest_bet,
        'player_options': list(game_state.current_player_options) if game_state.current_player_options else [],
        'min_raise': game_state.min_raise,
        'big_blind': game_state.big_blind,
        'messages': game_data.get('messages', [])
    })


@app.route('/api/game/<game_id>/action', methods=['POST'])
def api_player_action(game_id):
    """Handle player action."""
    if game_id not in games:
        return jsonify({'error': 'Game not found'}), 404
    
    data = request.json
    action = data.get('action')
    amount = data.get('amount', 0)
    
    game_data = games[game_id]
    state_machine = game_data['state_machine']
    
    # Validate it's human's turn
    current_player = state_machine.game_state.current_player
    if not current_player.is_human:
        return jsonify({'error': 'Not human player turn'}), 400
    
    # Apply action
    new_game_state = play_turn(state_machine.game_state, action, amount)
    new_game_state = advance_to_next_active_player(new_game_state)
    
    # Create new state machine with updated game state
    state_machine = state_machine.with_game_state(new_game_state)
    
    # Add message
    game_data['messages'].append({
        'id': str(uuid.uuid4()),
        'sender': current_player.name,
        'message': f"{action}{(' $' + str(amount)) if amount > 0 else ''}",
        'timestamp': datetime.now().isoformat(),
        'type': 'player'
    })
    
    # Progress game and handle AI turns
    games[game_id]['state_machine'] = state_machine
    games[game_id] = progress_game_with_ai(game_data)
    
    return jsonify({'success': True})


def progress_game_to_action(game_data):
    """Progress game until player action needed."""
    state_machine = game_data['state_machine']
    
    # Use immutable run_until_player_action
    return state_machine.run_until_player_action()


def progress_game_with_ai(game_data):
    """Progress game handling AI turns."""
    state_machine = game_data['state_machine']
    ai_controllers = game_data['ai_controllers']
    
    while True:
        # Check if we need player action
        if state_machine.awaiting_action:
            current_player = state_machine.game_state.current_player
            
            if current_player.is_human:
                # Stop for human input
                break
            else:
                # AI turn
                controller = ai_controllers[current_player.name]
                # Update controller's state machine reference
                controller.state_machine = state_machine
                action_type, amount = controller.decide_action()
                
                # Apply AI action
                new_game_state = play_turn(state_machine.game_state, action_type, amount)
                new_game_state = advance_to_next_active_player(new_game_state)
                state_machine = state_machine.with_game_state(new_game_state)
                
                # Add AI message
                game_data['messages'].append({
                    'id': str(uuid.uuid4()),
                    'sender': current_player.name,
                    'message': f"{action_type}{(' $' + str(amount)) if amount > 0 else ''}",
                    'timestamp': datetime.now().isoformat(),
                    'type': 'player'
                })
        
        # Advance state
        state_machine = state_machine.advance()
        
        # Check for hand completion
        if state_machine.phase == PokerPhase.HAND_OVER:
            # Start new hand
            state_machine = state_machine.advance()
    
    game_data['state_machine'] = state_machine
    return game_data


@app.route('/game/<game_id>', methods=['DELETE'])
def delete_game(game_id):
    """Delete a game."""
    if game_id in games:
        del games[game_id]
    
    # Also delete from persistence
    persistence.delete_game(game_id)
    
    return '', 204


@app.route('/games')
def list_games():
    """List saved games for React app."""
    saved_games = persistence.list_games()
    return jsonify({'games': saved_games})


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)