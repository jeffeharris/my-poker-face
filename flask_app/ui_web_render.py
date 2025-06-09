# Server-Side Python (ui_web.py) with Socket.IO integration and Flask routes for game management using a local dictionary for game states
from typing import Optional, Dict
from pathlib import Path

from flask import Flask, redirect, url_for, jsonify, Response, request
from flask_socketio import SocketIO, join_room
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from datetime import datetime
import time
import os
import json
import logging
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv(override=True)

from poker.controllers import AIPlayerController
from poker.ai_resilience import get_fallback_chat_response
from poker.elasticity_manager import ElasticityManager
from poker.pressure_detector import PressureEventDetector
from poker.pressure_stats import PressureStatsTracker
from poker.poker_game import PokerGameState, initialize_game_state, determine_winner, play_turn, \
    advance_to_next_active_player, award_pot_winnings
from poker.poker_state_machine import PokerStateMachine, PokerPhase
from poker.utils import get_celebrities
from poker.persistence import GamePersistence
from poker.repositories.sqlite_repositories import PressureEventRepository
from poker.auth import AuthManager
from .game_adapter import StateMachineAdapter, GameStateAdapter
from core.assistants import OpenAILLMAssistant

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(32).hex())
CORS(app, supports_credentials=True, origins=["http://localhost:3173", "http://localhost:5173", "*"])  # Enable CORS with credentials

# Custom key function that exempts Docker internal IPs
def get_rate_limit_key():
    """Get IP address for rate limiting, exempting Docker internal IPs."""
    remote_addr = get_remote_address()
    # Exempt Docker internal network IPs (172.x.x.x)
    if remote_addr and remote_addr.startswith('172.'):
        return None  # No rate limiting for internal Docker traffic
    return remote_addr

# Initialize rate limiter with fallback for Render deployment
redis_url = os.environ.get('REDIS_URL')
if redis_url:
    # Production with Redis
    try:
        limiter = Limiter(
            app=app,
            key_func=get_rate_limit_key,
            default_limits=['200 per day', '50 per hour'],
            storage_uri=redis_url
        )
        logger.info(f"Rate limiter initialized with Redis at {redis_url}")
    except Exception as e:
        logger.warning(f"Failed to connect to Redis, using in-memory rate limiting: {e}")
        limiter = Limiter(
            app=app,
            key_func=get_rate_limit_key,
            default_limits=['200 per day', '50 per hour']
        )
else:
    # Development or no Redis available - use in-memory rate limiting
    limiter = Limiter(
        app=app,
        key_func=get_rate_limit_key,
        default_limits=['200 per day', '50 per hour']
    )
    logger.info("Rate limiter initialized with in-memory storage")

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Dictionary to hold game states and messages for each game ID
games = {}
messages = {}

# Custom error handler for rate limit exceeded
@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({
        'error': 'Rate limit exceeded',
        'message': str(e.description),
        'retry_after': e.retry_after if hasattr(e, 'retry_after') else None
    }), 429

# Function to serialize game state
def serialize_game_state(game_state, game_id):
    '''Serialize the game state object to a dictionary'''
    state_dict = game_state.__dict__.copy()
    
    # Serialize players
    state_dict['players'] = [player.__dict__.copy() for player in game_state.players]
    
    return state_dict

# Function to broadcast game state to all connected clients in a game room
def broadcast_game_state(game_id):
    if game_id in games:
        game_state = games[game_id]
        socketio.emit('game_state_update', serialize_game_state(game_state, game_id), room=game_id)

@app.route('/health')
@limiter.exempt
def health_check():
    """Health check endpoint that's exempt from rate limiting."""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'service': 'poker-backend'
    })

@app.route('/')
def index():
    '''redirect to the poker game'''
    return redirect(url_for('api_poker_game'))

@app.route('/api/pokergame')
def api_poker_game():
    '''list of poker games'''
    return {'games': list(games.keys())}

###### GAME MANAGEMENT API ENDPOINTS ######
ASSISTANT_THRESHOLD = 6

@app.route('/api/pokergame/new/<int:num_players>', methods=['POST'])
def new_game(num_players):
    """Create a new poker game with AI players"""
    
    # Initialize auth manager
    auth_manager = AuthManager()
    
    # Get username from request (from login form or default)
    data = request.get_json() or {}
    username = data.get('username', 'Player')
    player_name = data.get('player_name', username)
    
    # Create or get user
    user = auth_manager.create_guest_user(username)
    
    # Validate number of players
    if num_players < 2 or num_players > 8:
        return jsonify({'error': 'Number of players must be between 2 and 8'}), 400
    
    # Get AI personalities
    celebrities = get_celebrities()
    if num_players - 1 > len(celebrities):
        return jsonify({'error': f'Not enough AI personalities available. Maximum {len(celebrities) + 1} players.'}), 400
    
    # Create new game with AI players
    player_names = [player_name]  # Human player
    
    # Add AI players
    selected_celebrities = celebrities[:num_players - 1]
    player_names.extend([celeb['name'] for celeb in selected_celebrities])
    
    # Initialize game
    game = StateMachineAdapter.create_game(
        player_names=player_names,
        starting_chips=1000,
        small_blind=5,
        big_blind=10,
        ai_personalities={celeb['name']: celeb for celeb in selected_celebrities}
    )
    
    # Get the game state and ID
    game_state = game.get_state()
    game_id = game_state.id
    
    # Create assistant if we have enough players
    if num_players >= ASSISTANT_THRESHOLD:
        personality = {
            "name": "Game Master Sam",
            "play_style": "Game Master",
            "personality_description": "Your name is Sam and you are a seasoned game master who provides insights about the game and players' tendencies.",
            "persona": "Knowledgeable commentator who observes player patterns and provides strategic insights",
            "mood": "analytical"
        }
        assistant = OpenAILLMAssistant(personality=personality)
        game.assistant = assistant
        games[game_id] = {
            'game': game,
            'assistant': assistant,
            'ai_controllers': game.ai_controllers
        }
    else:
        games[game_id] = {
            'game': game,
            'assistant': None,
            'ai_controllers': game.ai_controllers
        }
    
    messages[game_id] = []
    
    # Get pressure detector
    detector = game.get_pressure_detector()
    
    # Persist the game
    persistence = GamePersistence()
    persistence.save_game(game_id, game.get_state())
    
    response = {
        'game_id': game_id,
        'player_names': player_names,
        'your_player_index': 0,  # Human player is always at index 0
        'starting_chips': 1000,
        'small_blind': 5,
        'big_blind': 10,
        'user_id': user.id,
        'username': user.username
    }
    
    return jsonify(response)

@app.route('/api/pokergame/<game_id>')
def get_game_state(game_id):
    '''get the state of a specific poker game'''
    if game_id not in games:
        # Try to load from persistence
        persistence = GamePersistence()
        game_state = persistence.load_game(game_id)
        if game_state:
            # Reconstruct the game from persisted state
            game = StateMachineAdapter(game_state)
            games[game_id] = {
                'game': game,
                'assistant': None,  # TODO: Reconstruct assistant if needed
                'ai_controllers': {}  # TODO: Reconstruct AI controllers
            }
            messages[game_id] = []
        else:
            return jsonify({'error': 'Game not found'}), 404
    
    try:
        game_entry = games[game_id]
        game = game_entry['game']
        game_state = game.get_state()
        stats_tracker = game.get_stats_tracker()
        pressure_detector = game.get_pressure_detector()
        
        # Get current hand stats
        current_hand_stats = None
        if stats_tracker:
            current_hand_stats = stats_tracker.get_current_hand_stats()
        
        # Add assistant info if available
        serialized_state = serialize_game_state(game_state, game_id)
        
        # Ensure all required fields are present
        if 'max_hand_contribution' not in serialized_state:
            serialized_state['max_hand_contribution'] = game_state.big_blind if hasattr(game_state, 'big_blind') else 10
        
        if game_entry.get('assistant'):
            serialized_state['has_assistant'] = True
            # Get assistant's analysis of current state
            if game_state.current_phase not in ['SHOWDOWN', 'HAND_COMPLETE']:
                assistant_insight = game_entry['assistant'].get_game_insight(game_state)
                serialized_state['assistant_insight'] = assistant_insight
        else:
            serialized_state['has_assistant'] = False
        
        # Add pressure stats if available
        if current_hand_stats:
            serialized_state['pressure_stats'] = current_hand_stats
            
        # Add any detected pressure events
        if pressure_detector and hasattr(pressure_detector, 'recent_events'):
            serialized_state['pressure_events'] = [
                event.__dict__ for event in pressure_detector.recent_events[-5:]  # Last 5 events
            ]
        
        return jsonify(serialized_state)
    except Exception as e:
        logger.error(f"Error getting game state: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Failed to get game state: {str(e)}'}), 500

@app.route('/api/pokergame/<game_id>/action', methods=['POST'])
def player_action(game_id):
    '''process a player action'''
    if game_id not in games:
        return jsonify({'error': 'Game not found'}), 404
    
    # Check if the request specifies it's an AI action
    data = request.get_json()
    is_ai_action = data.get('is_ai', False)
    
    # Apply rate limiting only for non-AI actions
    if not is_ai_action:
        # Check rate limit manually
        try:
            with limiter.limit("30 per minute"):
                pass
        except:
            return jsonify({'error': 'Rate limit exceeded for player actions'}), 429
    
    try:
        action = data.get('action')
        bet_amount = data.get('bet_amount', 0)
        
        game_entry = games[game_id]
        game = game_entry['game']
        game_state = game.get_state()
        state_machine = game.state_machine
        detector = game.get_pressure_detector()
        stats_tracker = game.get_stats_tracker()
        
        # Update state machine with current game state
        state_machine.state = game_state
        
        # Get current player
        current_player = game_state.players[game_state.current_player_index]
        
        # Calculate pressure before action
        if detector and stats_tracker:
            pre_pressure = detector.calculate_player_pressure(
                game_state, 
                game_state.current_player_index
            )
        
        # Process the action
        if action == 'call':
            result = state_machine.process_action('call')
        elif action == 'fold':
            result = state_machine.process_action('fold')
        elif action in ['bet', 'raise']:
            result = state_machine.process_action(action, amount=bet_amount)
        elif action == 'check':
            result = state_machine.process_action('check')
        elif action == 'allin':
            # All-in is a bet/raise for all remaining chips
            all_in_amount = current_player.chips
            result = state_machine.process_action('raise', amount=all_in_amount)
        else:
            return jsonify({'error': f'Invalid action: {action}'}), 400
        
        if not result['success']:
            return jsonify({'error': result.get('error', 'Action failed')}), 400
        
        # Update game state
        game.state = state_machine.state
        game_state = game.get_state()
        
        # Calculate pressure after action and track stats
        if detector and stats_tracker:
            post_pressure = detector.calculate_player_pressure(
                game_state, 
                game_state.current_player_index
            )
            
            # Track the action
            stats_tracker.track_action(
                game_state,
                player_index=game_state.current_player_index,
                action=action,
                amount=bet_amount if action in ['bet', 'raise'] else None,
                pre_pressure=pre_pressure,
                post_pressure=post_pressure
            )
        
        # Persist the updated game state
        persistence = GamePersistence()
        persistence.save_game(game_id, game_state)
        
        # Check if hand is complete
        if game_state.current_phase == PokerPhase.HAND_COMPLETE:
            # Get final hand stats before starting new hand
            if stats_tracker:
                final_stats = stats_tracker.finalize_hand(game_state)
                
                # Store stats in repository
                try:
                    repo = PressureEventRepository()
                    for event in stats_tracker.current_hand_events:
                        repo.create(
                            game_id=game_id,
                            hand_number=final_stats['hand_number'],
                            event_type=event['event_type'],
                            player_index=event['player_index'],
                            player_name=event['player_name'],
                            pressure_value=event['pressure_value'],
                            details=event
                        )
                except Exception as e:
                    logger.error(f"Failed to save pressure events: {e}")
            
            # Start new hand
            result = state_machine.start_new_hand()
            if result['success']:
                game.state = state_machine.state
                game_state = game.get_state()
                persistence.save_game(game_id, game_state)
        
        # Check for eliminated players
        eliminated = [i for i, p in enumerate(game_state.players) if p.chips == 0 and p.status == 'active']
        for player_index in eliminated:
            game_state.players[player_index].status = 'eliminated'
        
        # Check if game is over (only one player with chips)
        active_players = [p for p in game_state.players if p.chips > 0]
        if len(active_players) == 1:
            winner = active_players[0]
            return jsonify({
                'game_over': True,
                'winner': winner.name,
                'final_chips': winner.chips
            })
        
        # Process AI turns if needed
        if game.should_process_ai_turn():
            game.process_ai_turns()
        
        return jsonify({
            'success': True,
            'game_state': serialize_game_state(game.get_state(), game_id),
            'stats': stats_tracker.get_current_hand_stats() if stats_tracker else None
        })
        
    except Exception as e:
        logger.error(f"Error processing action: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Failed to process action: {str(e)}'}), 500

@app.route('/api/pokergame/<game_id>/assistant', methods=['POST'])
def get_assistant_response(game_id):
    '''Get assistant response for a user query'''
    if game_id not in games:
        return jsonify({'error': 'Game not found'}), 404
    
    game_entry = games[game_id]
    if not game_entry.get('assistant'):
        return jsonify({'error': 'No assistant available for this game'}), 400
    
    data = request.get_json()
    query = data.get('query', '')
    
    if not query:
        return jsonify({'error': 'Query is required'}), 400
    
    try:
        assistant = game_entry['assistant']
        game_state = game_entry['game'].get_state()
        
        # Get response from assistant
        response = assistant.get_response(query, game_state)
        
        return jsonify({
            'response': response,
            'assistant_name': assistant.personality['name']
        })
    except Exception as e:
        logger.error(f"Error getting assistant response: {str(e)}")
        return jsonify({'error': 'Failed to get assistant response'}), 500

@app.route('/api/pokergame/<game_id>/deal', methods=['POST'])
def deal_hand(game_id):
    '''deal a new hand'''
    if game_id not in games:
        return jsonify({'error': 'Game not found'}), 404
    
    game = games[game_id]['game']
    state_machine = game.state_machine
    
    # Start a new hand
    result = state_machine.start_new_hand()
    
    if result['success']:
        game.state = state_machine.state
        
        # Persist the game
        persistence = GamePersistence()
        persistence.save_game(game_id, game.get_state())
        
        return jsonify({'success': True})
    else:
        return jsonify({'error': result.get('error', 'Failed to deal new hand')}), 400

@app.route('/api/pokergame/<game_id>/messages')
def get_messages(game_id):
    '''get chat messages for a game'''
    if game_id not in messages:
        messages[game_id] = []
    
    # Include pressure events as system messages
    game_messages = messages[game_id].copy()
    
    # Add recent pressure events if available
    if game_id in games:
        game_entry = games[game_id]
        detector = game_entry['game'].get_pressure_detector()
        if detector and hasattr(detector, 'recent_events'):
            for event in detector.recent_events[-3:]:  # Last 3 pressure events
                game_messages.append({
                    'type': 'pressure_event',
                    'player': event.player_name,
                    'event_type': event.event_type,
                    'severity': event.severity,
                    'description': event.description,
                    'timestamp': event.timestamp
                })
    
    return jsonify(game_messages)

@app.route('/api/pokergame/<game_id>/messages', methods=['POST'])
def post_message(game_id):
    '''post a chat message to a game'''
    data = request.get_json()
    player_name = data.get('player_name', 'Anonymous')
    message_text = data.get('message', '')
    
    if not message_text:
        return jsonify({'error': 'Message cannot be empty'}), 400
    
    if game_id not in messages:
        messages[game_id] = []
    
    # Check if this is an AI player
    is_ai = False
    ai_personality = None
    if game_id in games:
        game_entry = games[game_id]
        game_state = game_entry['game'].get_state()
        
        # Find the player
        for player in game_state.players:
            if player.name == player_name and player.is_ai:
                is_ai = True
                # Get personality from ai_controllers
                if player_name in game_entry['ai_controllers']:
                    ai_personality = game_entry['ai_controllers'][player_name].personality
                break
    
    message = {
        'player': player_name,
        'message': message_text,
        'timestamp': time.time(),
        'is_ai': is_ai
    }
    
    # Add personality info if it's an AI message
    if is_ai and ai_personality:
        message['ai_personality'] = ai_personality.get('persona', '')
    
    messages[game_id].append(message)
    
    # Emit to all clients in the game room
    socketio.emit('new_message', message, room=game_id)
    
    return jsonify({'success': True})

##### AI AND CHAT ENDPOINTS #####

@app.route('/api/ai/elasticity/<game_id>/<player_name>')
def get_ai_elasticity(game_id, player_name):
    """Get current elasticity parameters for an AI player"""
    if game_id not in games:
        return jsonify({'error': 'Game not found'}), 404
    
    game_entry = games[game_id]
    game_state = game_entry['game'].get_state()
    
    # Find the player
    player = None
    for p in game_state.players:
        if p.name == player_name:
            player = p
            break
    
    if not player or not player.is_ai:
        return jsonify({'error': 'Player not found or not an AI'}), 404
    
    # Get elasticity manager
    if player_name in game_entry.get('ai_controllers', {}):
        controller = game_entry['ai_controllers'][player_name]
        elasticity_manager = ElasticityManager(controller.personality)
        
        # Calculate current parameters
        params = elasticity_manager.get_elasticity_parameters(
            game_state=game_state,
            player_index=game_state.players.index(player)
        )
        
        return jsonify({
            'player_name': player_name,
            'elasticity': params,
            'base_personality': controller.personality
        })
    
    return jsonify({'error': 'AI controller not found'}), 404

@app.route('/api/ai/chat/<game_id>', methods=['POST'])
def ai_chat_response(game_id):
    """Generate an AI chat response based on game context"""
    if game_id not in games:
        return jsonify({'error': 'Game not found'}), 404
    
    data = request.get_json()
    player_name = data.get('player_name')
    trigger = data.get('trigger', 'general')
    context = data.get('context', {})
    
    game_entry = games[game_id]
    game_state = game_entry['game'].get_state()
    
    # Find the AI controller
    if player_name not in game_entry.get('ai_controllers', {}):
        return jsonify({'error': 'AI player not found'}), 404
    
    controller = game_entry['ai_controllers'][player_name]
    
    try:
        # Generate contextual response
        if hasattr(controller, 'get_chat_response'):
            response = controller.get_chat_response(game_state, trigger, context)
        else:
            # Fallback response
            response = get_fallback_chat_response(trigger)
        
        # Post the message
        if response:
            message_data = {
                'player_name': player_name,
                'message': response
            }
            # Use the post_message endpoint logic
            post_message(game_id)
        
        return jsonify({
            'success': True,
            'message': response
        })
        
    except Exception as e:
        logger.error(f"Error generating AI chat: {str(e)}")
        # Return a fallback response
        fallback = get_fallback_chat_response(trigger)
        return jsonify({
            'success': True,
            'message': fallback,
            'fallback': True
        })

##### PRESSURE AND STATS ENDPOINTS #####

@app.route('/api/pokergame/<game_id>/pressure/stats')
def get_pressure_stats(game_id):
    """Get pressure statistics for the current hand"""
    if game_id not in games:
        return jsonify({'error': 'Game not found'}), 404
    
    try:
        game_entry = games[game_id]
        stats_tracker = game_entry['game'].get_stats_tracker()
        
        if not stats_tracker:
            return jsonify({'error': 'Stats tracking not available'}), 404
        
        stats = stats_tracker.get_current_hand_stats()
        return jsonify(stats)
        
    except Exception as e:
        logger.error(f"Error getting pressure stats: {str(e)}")
        return jsonify({'error': 'Failed to get pressure stats'}), 500

@app.route('/api/pokergame/<game_id>/pressure/history')
def get_pressure_history(game_id):
    """Get historical pressure events for a game"""
    try:
        repo = PressureEventRepository()
        
        # Get optional query parameters
        hand_number = request.args.get('hand_number', type=int)
        player_name = request.args.get('player_name')
        event_type = request.args.get('event_type')
        limit = request.args.get('limit', default=50, type=int)
        
        events = repo.get_events(
            game_id=game_id,
            hand_number=hand_number,
            player_name=player_name,
            event_type=event_type,
            limit=limit
        )
        
        return jsonify({
            'events': [event.to_dict() for event in events],
            'count': len(events)
        })
        
    except Exception as e:
        logger.error(f"Error getting pressure history: {str(e)}")
        return jsonify({'error': 'Failed to get pressure history'}), 500

@app.route('/api/pokergame/<game_id>/pressure/summary')
def get_pressure_summary(game_id):
    """Get pressure summary statistics for a game"""
    try:
        repo = PressureEventRepository()
        events = repo.get_events(game_id=game_id)
        
        if not events:
            return jsonify({'error': 'No pressure data available'}), 404
        
        # Calculate summary statistics
        summary = {
            'total_events': len(events),
            'hands_played': len(set(e.hand_number for e in events)),
            'players': {},
            'event_types': {}
        }
        
        # Per-player statistics
        for event in events:
            player = event.player_name
            if player not in summary['players']:
                summary['players'][player] = {
                    'total_events': 0,
                    'avg_pressure': 0,
                    'max_pressure': 0,
                    'high_pressure_events': 0
                }
            
            player_stats = summary['players'][player]
            player_stats['total_events'] += 1
            player_stats['max_pressure'] = max(player_stats['max_pressure'], event.pressure_value)
            
            if event.details.get('severity') == 'high':
                player_stats['high_pressure_events'] += 1
        
        # Calculate averages
        for player, stats in summary['players'].items():
            player_events = [e for e in events if e.player_name == player]
            if player_events:
                stats['avg_pressure'] = sum(e.pressure_value for e in player_events) / len(player_events)
        
        # Event type distribution
        for event in events:
            event_type = event.event_type
            summary['event_types'][event_type] = summary['event_types'].get(event_type, 0) + 1
        
        return jsonify(summary)
        
    except Exception as e:
        logger.error(f"Error getting pressure summary: {str(e)}")
        return jsonify({'error': 'Failed to get pressure summary'}), 500

##### AUTHENTICATION ENDPOINTS #####

@app.route('/api/auth/guest', methods=['POST'])
def create_guest():
    """Create a guest user account"""
    auth_manager = AuthManager()
    data = request.get_json() or {}
    username = data.get('username', 'Guest')
    
    try:
        user = auth_manager.create_guest_user(username)
        return jsonify({
            'user_id': user.id,
            'username': user.username,
            'display_name': user.display_name,
            'is_guest': user.is_guest
        })
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/auth/user/<user_id>')
def get_user(user_id):
    """Get user information"""
    auth_manager = AuthManager()
    user = auth_manager.get_user(user_id)
    
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    return jsonify({
        'user_id': user.id,
        'username': user.username,
        'display_name': user.display_name,
        'is_guest': user.is_guest,
        'created_at': user.created_at.isoformat()
    })

##### SOCKET.IO EVENTS #####

@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    print(f"Client disconnected: {request.sid}")

@socketio.on('join_game')
def handle_join_game(data):
    game_id = data.get('game_id')
    if game_id:
        join_room(game_id)
        print(f"Client {request.sid} joined game {game_id}")
        # Send current game state to the newly joined client
        if game_id in games:
            emit('game_state_update', serialize_game_state(games[game_id]['game'].get_state(), game_id))

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)