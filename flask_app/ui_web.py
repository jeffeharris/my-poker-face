# Server-Side Python (ui_web.py) with Socket.IO integration and Flask routes for game management using a local dictionary for game states
from typing import Optional, Dict
from pathlib import Path

from flask import Flask, redirect, url_for, jsonify, Response, request, send_from_directory
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
from poker.ai_resilience import get_fallback_chat_response, FallbackActionSelector, AIFallbackStrategy
from poker.config import MIN_RAISE, AI_MESSAGE_CONTEXT_LIMIT
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

# Configure CORS from environment variable
# CORS_ORIGINS can be "*" for all origins, or comma-separated list of allowed origins
cors_origins_env = os.environ.get('CORS_ORIGINS', '*')
if cors_origins_env == '*':
    # Allow all origins with credentials by using regex pattern
    # This echoes back the requesting origin instead of literal "*"
    import re
    CORS(app, supports_credentials=True, origins=re.compile(r'.*'))
else:
    # Parse comma-separated origins and enable credentials
    cors_origins = [origin.strip() for origin in cors_origins_env.split(',') if origin.strip()]
    CORS(app, supports_credentials=True, origins=cors_origins)

# Custom key function that exempts Docker internal IPs
def get_rate_limit_key():
    """Get IP address for rate limiting, exempting Docker internal IPs."""
    remote_addr = get_remote_address()
    # Exempt Docker internal network IPs (172.x.x.x)
    if remote_addr and remote_addr.startswith('172.'):
        return None  # No rate limiting for internal Docker traffic
    return remote_addr

# Initialize rate limiter with graceful Redis fallback
redis_url = os.environ.get('REDIS_URL')
default_limits = ['10000 per day', '1000 per hour', '100 per minute']

if redis_url:
    try:
        # Test Redis connection first
        import redis
        r = redis.from_url(redis_url)
        r.ping()
        
        limiter = Limiter(
            app=app,
            key_func=get_rate_limit_key,
            default_limits=default_limits,
            storage_uri=redis_url
        )
        logger.info(f"Rate limiter initialized with Redis")
    except Exception as e:
        logger.warning(f"Redis not available, using in-memory rate limiting: {e}")
        limiter = Limiter(
            app=app,
            key_func=get_rate_limit_key,
            default_limits=default_limits
        )
else:
    # No Redis URL provided, use in-memory
    limiter = Limiter(
        app=app,
        key_func=get_rate_limit_key,
        default_limits=default_limits
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

# Initialize persistence layer
# Use /app/data in Docker, or local path otherwise
if os.path.exists('/app/data'):
    db_path = '/app/data/poker_games.db'
else:
    db_path = os.path.join(os.path.dirname(__file__), '..', 'poker_games.db')
persistence = GamePersistence(db_path)
event_repository = PressureEventRepository(db_path)

# Initialize authentication
auth_manager = AuthManager(app, persistence)


# Helper function to generate unique game ID
def generate_game_id():
    return str(int(time.time() * 1000))  # Use current time in milliseconds as a unique ID


def get_game_owner_info(game_id: str) -> tuple:
    """Get owner_id and owner_name for a game."""
    game_data = games.get(game_id, {})
    return game_data.get('owner_id'), game_data.get('owner_name')


def restore_ai_controllers(game_id: str, state_machine, persistence) -> Dict[str, AIPlayerController]:
    """Restore AI controllers with their saved state."""
    ai_controllers = {}
    ai_states = persistence.load_ai_player_states(game_id)
    
    for player in state_machine.game_state.players:
        if not player.is_human:
            controller = AIPlayerController(player.name, state_machine)
            
            # Restore AI state if available
            if player.name in ai_states:
                saved_state = ai_states[player.name]
                
                # Restore conversation history
                if hasattr(controller, 'assistant') and controller.assistant:
                    controller.assistant.messages = saved_state['messages']
                
                # Restore personality state
                if 'personality_state' in saved_state:
                    ps = saved_state['personality_state']
                    if 'traits' in ps:
                        controller.personality_traits = ps['traits']
                    if hasattr(controller, 'ai_player'):
                        controller.ai_player.confidence = ps.get('confidence', 'Normal')
                        controller.ai_player.attitude = ps.get('attitude', 'Neutral')
                
                print(f"Restored AI state for {player.name} with {len(saved_state.get('messages', []))} messages")
            
            ai_controllers[player.name] = controller
    
    return ai_controllers


def update_and_emit_game_state(game_id):
    current_game_data = games.get(game_id)
    if not current_game_data:
        return
        
    game_state = current_game_data['state_machine'].game_state
    game_state_dict = game_state.to_dict()
    
    # Include messages in the game state
    messages = []
    for msg in current_game_data.get('messages', []):
        messages.append({
            'id': str(msg.get('id', len(messages))),
            'sender': msg.get('sender', 'System'),
            'message': msg.get('content', msg.get('message', '')),
            'timestamp': msg.get('timestamp', datetime.now().isoformat()),
            'type': msg.get('message_type', msg.get('type', 'system'))
        })
    
    game_state_dict['messages'] = messages
    # Ensure the dealer and blind indices are included
    game_state_dict['current_dealer_idx'] = game_state.current_dealer_idx
    game_state_dict['small_blind_idx'] = game_state.small_blind_idx
    game_state_dict['big_blind_idx'] = game_state.big_blind_idx
    # Add missing top-level fields that the frontend expects
    game_state_dict['highest_bet'] = game_state.highest_bet
    game_state_dict['player_options'] = list(game_state.current_player_options) if game_state.current_player_options else []
    game_state_dict['min_raise'] = game_state.highest_bet * 2 if game_state.highest_bet > 0 else 20
    game_state_dict['big_blind'] = 20  # TODO: Get from game config
    game_state_dict['phase'] = str(current_game_data['state_machine'].current_phase).split('.')[-1]
    socketio.emit('update_game_state', {'game_state': game_state_dict}, to=game_id)


@socketio.on('join_game')
def on_join(game_id):
    join_room(game_id)
    print(f"User joined room: {game_id}")
    socketio.emit('player_joined', {'message': 'A new player has joined!'}, to=game_id)

    # Check if this is a new game that needs to be started
    game_id_str = str(game_id)
    if game_id_str in games:
        game_data = games[game_id_str]
        # Start the game if it hasn't been started yet
        if not game_data.get('game_started', False):
            game_data['game_started'] = True
            print(f"Starting game progression for: {game_id_str}")
            progress_game(game_id_str)


# Serve static files (React build)
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    static_path = Path(__file__).parent.parent / 'static'
    if path != "" and (static_path / path).exists():
        return send_from_directory(str(static_path), path)
    else:
        # Always return index.html for React Router to handle
        if (static_path / 'index.html').exists():
            return send_from_directory(str(static_path), 'index.html')
    
    # If no static files, return API info
    return jsonify({
        'message': 'My Poker Face API',
        'version': '1.0',
        'frontend': 'React app not built',
        'endpoints': {
            'games': '/api/pokergame',
            'new_game': '/api/pokergame/new/<num_players>',
            'game_state': '/api/pokergame/<game_id>',
            'health': '/health'
        }
    })


@app.route('/health')
@limiter.exempt
def health_check():
    """Health check endpoint for Docker and monitoring."""
    return jsonify({'status': 'healthy', 'service': 'poker-backend'}), 200


@app.route('/games')
def list_games():
    """List games for the current user."""
    current_user = auth_manager.get_current_user()
    
    if current_user:
        # Get only the user's games
        saved_games = persistence.list_games(owner_id=current_user.get('id'), limit=10)
    else:
        # No games for anonymous users
        saved_games = []
    
    games_data = []
    for game in saved_games:
        games_data.append({
            'game_id': game.game_id,
            'created_at': game.created_at.strftime("%Y-%m-%d %H:%M"),
            'updated_at': game.updated_at.strftime("%Y-%m-%d %H:%M"),
            'phase': game.phase,
            'num_players': game.num_players,
            'pot_size': game.pot_size,
            'is_owner': True  # Always true since we're filtering by owner
        })
    
    return jsonify({'games': games_data})

# Removed /api/my-games endpoint - consolidated with /games


@app.route('/api/game-state/<game_id>')
def api_game_state(game_id):
    """API endpoint to get current game state for React app."""
    current_game_data = games.get(game_id)
    
    if not current_game_data:
        # Try to load from database
        try:
            # First check if the game exists and belongs to the current user
            current_user = auth_manager.get_current_user()
            saved_games = persistence.list_games(owner_id=current_user.get('id') if current_user else None, limit=50)
            
            # Check if this game belongs to the current user
            game_found = False
            owner_id = None
            owner_name = None
            for saved_game in saved_games:
                if saved_game.game_id == game_id:
                    game_found = True
                    owner_id = saved_game.owner_id
                    owner_name = saved_game.owner_name
                    break
            
            if not game_found:
                return jsonify({'error': 'Game not found or access denied'}), 404
            
            base_state_machine = persistence.load_game(game_id)
            if base_state_machine:
                state_machine = StateMachineAdapter(base_state_machine)
                # Restore AI controllers with saved state
                ai_controllers = restore_ai_controllers(game_id, state_machine, persistence)
                
                # Load messages from database
                db_messages = persistence.load_messages(game_id)
                
                # Initialize elasticity tracking for loaded games
                elasticity_manager = ElasticityManager()
                for player in state_machine.game_state.players:
                    if not player.is_human and player.name in ai_controllers:
                        controller = ai_controllers[player.name]
                        elasticity_manager.add_player(
                            player.name,
                            controller.ai_player.personality_config
                        )
                
                pressure_detector = PressureEventDetector(elasticity_manager)
                pressure_stats = PressureStatsTracker()
                
                current_game_data = {
                    'state_machine': state_machine,
                    'ai_controllers': ai_controllers,
                    'elasticity_manager': elasticity_manager,
                    'pressure_detector': pressure_detector,
                    'pressure_stats': pressure_stats,
                    'owner_id': owner_id,
                    'owner_name': owner_name,
                    'messages': db_messages
                }
                games[game_id] = current_game_data
            else:
                return jsonify({'error': 'Game not found'}), 404
        except Exception as e:
            print(f"Error loading game {game_id}: {str(e)}")
            import traceback
            traceback.print_exc()
            # For now, return a user-friendly error
            return jsonify({
                'error': 'Game loading is currently unavailable',
                'message': 'This feature is under development. Please start a new game.',
                'players': []
            }), 200  # Return 200 so frontend can handle gracefully
    
    state_machine = current_game_data['state_machine']
    game_state = state_machine.game_state
    
    # Convert game state to API format
    players = []
    for player in game_state.players:
        players.append({
            'name': player.name,
            'stack': player.stack,
            'bet': player.bet,
            'is_folded': player.is_folded,
            'is_all_in': player.is_all_in,
            'is_human': player.is_human,
            'hand': player.hand if player.is_human and player.hand else None
        })
    
    # Convert community cards
    community_cards = []
    for card in game_state.community_cards:
        if hasattr(card, 'to_dict'):
            card_dict = card.to_dict()
            community_cards.append(f"{card_dict['rank']}{card_dict['suit']}")
        else:
            # Already a string
            community_cards.append(card)
    
    # Get messages
    messages = []
    for msg in current_game_data.get('messages', []):
        messages.append({
            'id': str(msg.get('id', len(messages))),
            'sender': msg.get('sender', 'System'),
            'message': msg.get('content', msg.get('message', '')),
            'timestamp': msg.get('timestamp', datetime.now().isoformat()),
            'type': msg.get('type', 'system')
        })
    
    response = {
        'players': players,
        'community_cards': community_cards,
        'pot': game_state.pot,
        'current_player_idx': game_state.current_player_idx,
        'current_dealer_idx': game_state.current_dealer_idx,
        'small_blind_idx': game_state.small_blind_idx,
        'big_blind_idx': game_state.big_blind_idx,
        'phase': str(state_machine.current_phase).split('.')[-1],
        'highest_bet': game_state.highest_bet,
        'player_options': list(game_state.current_player_options) if game_state.current_player_options else [],
        'min_raise': game_state.highest_bet * 2 if game_state.highest_bet > 0 else 20,
        'big_blind': 20,  # TODO: Get from game config
        'messages': messages,
        'game_id': game_id
    }
    
    return jsonify(response)


@app.route('/api/new-game', methods=['POST'])
@limiter.limit(os.environ.get('RATE_LIMIT_NEW_GAME', '10 per hour'))
def api_new_game():
    """Create a new game and return the game ID."""
    # Get player name from request, default to "Player" if not provided
    data = request.json or {}
    
    # Check if user is authenticated
    current_user = auth_manager.get_current_user()
    if current_user:
        # Use authenticated user's name by default
        player_name = data.get('playerName', current_user.get('name', 'Player'))
        owner_id = current_user.get('id')
        owner_name = current_user.get('name')
        
        # Check game limits
        game_count = persistence.count_user_games(owner_id)
        max_games = 1 if current_user.get('is_guest', True) else 10
        
        if game_count >= max_games:
            return jsonify({
                'error': f'Game limit reached. {"Guest users" if current_user.get("is_guest") else "You"} can have up to {max_games} saved game{"" if max_games == 1 else "s"}.'
            }), 400
    else:
        player_name = data.get('playerName', 'Player')
        owner_id = None
        owner_name = None
    
    # Check if specific personalities were requested
    requested_personalities = data.get('personalities', [])
    
    if requested_personalities:
        # Use the specific personalities requested
        ai_player_names = requested_personalities
    else:
        # Default to 3 random AI players
        ai_player_names = get_celebrities(shuffled=True)[:3]
    
    game_state = initialize_game_state(player_names=ai_player_names, human_name=player_name)
    base_state_machine = PokerStateMachine(game_state=game_state)
    state_machine = StateMachineAdapter(base_state_machine)
    
    # Create AI controllers and elasticity tracking
    ai_controllers = {}
    elasticity_manager = ElasticityManager()
    
    for player in state_machine.game_state.players:
        if not player.is_human:
            new_controller = AIPlayerController(player.name, state_machine)
            ai_controllers[player.name] = new_controller
            
            # Add to elasticity manager
            elasticity_manager.add_player(
                player.name,
                new_controller.ai_player.personality_config
            )
    
    pressure_detector = PressureEventDetector(elasticity_manager)
    game_id = generate_game_id()
    pressure_stats = PressureStatsTracker(game_id, event_repository)

    game_data = {
        'state_machine': state_machine,
        'ai_controllers': ai_controllers,
        'elasticity_manager': elasticity_manager,
        'pressure_detector': pressure_detector,
        'pressure_stats': pressure_stats,
        'owner_id': owner_id,
        'owner_name': owner_name,
        'messages': [{
            'id': '1',
            'sender': 'System',
            'content': 'New game started! Good luck!',
            'timestamp': datetime.now().isoformat(),
            'type': 'system'
        }]
    }
    games[game_id] = game_data
    
    # Save the new game to database
    persistence.save_game(game_id, state_machine._state_machine, owner_id, owner_name)

    # Game progression is now triggered by frontend via 'start_game' Socket.IO event
    # This allows the table UI to load immediately

    return jsonify({'game_id': game_id})


@app.route('/api/game/<game_id>/action', methods=['POST'])
@limiter.limit(os.environ.get('RATE_LIMIT_GAME_ACTION', '60 per minute'))
def api_player_action(game_id):
    """Handle player action via API."""
    data = request.json
    action = data.get('action')
    amount = data.get('amount', 0)
    
    current_game_data = games.get(game_id)
    if not current_game_data:
        return jsonify({'error': 'Game not found'}), 404
    
    state_machine = current_game_data['state_machine']
    
    # Play the current player's turn
    current_player = state_machine.game_state.current_player
    if not current_player.is_human:
        return jsonify({'error': 'Not human player turn'}), 400
    
    game_state = play_turn(state_machine.game_state, action, amount)
    
    # Generate a message to be added to the game table
    table_message_content = f"{current_player.name} chose to {action}{(' $' + str(amount)) if amount > 0 else ''}."
    send_message(game_id, "Table", table_message_content, "game")
    
    game_state = advance_to_next_active_player(game_state)
    state_machine.game_state = game_state
    
    # Update the game session states
    current_game_data['state_machine'] = state_machine
    games[game_id] = current_game_data
    
    # Save game after human action
    owner_id, owner_name = get_game_owner_info(game_id)
    persistence.save_game(game_id, state_machine._state_machine, owner_id, owner_name)
    
    # Progress the game to handle AI turns
    progress_game(game_id)
    
    return jsonify({'success': True})


@app.route('/api/game/<game_id>/message', methods=['POST'])
def api_send_message(game_id):
    """Send a chat message in the game."""
    data = request.json
    message = data.get('message', '')
    sender = data.get('sender', 'Player')  # Default to 'Player' instead of 'Jeff'
    
    if message.strip():
        send_message(game_id, sender, message.strip(), 'player')
        return jsonify({'success': True})
    
    return jsonify({'success': False, 'error': 'Empty message'})


@app.route('/new_game', methods=['GET'])
def new_game():
    # Deprecated: Use /api/new-game POST endpoint instead
    return redirect('/api/new-game')


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
        
        # Also save the updated state
        owner_id, owner_name = get_game_owner_info(game_id)
        persistence.save_game(game_id, state_machine._state_machine, owner_id, owner_name)

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
            print(f"AI turn: {game_state.current_player.name}")
            handle_ai_action(game_id)

        # Check for and handle the Evaluate Hand phase outside the state machine so we can update
        # the front end with the results.
        elif state_machine.current_phase == PokerPhase.EVALUATING_HAND:
            winner_info = determine_winner(game_state)
            winning_player_names = list(winner_info['winnings'].keys())
            
            # Get pot size BEFORE awarding winnings (important!)
            pot_size_before_award = game_state.pot.get('total', 0) if isinstance(game_state.pot, dict) else 0
            
            # Apply elasticity pressure events if elasticity is enabled
            if 'pressure_detector' in current_game_data:
                pressure_detector = current_game_data['pressure_detector']
                # Detect and apply pressure events from showdown
                events = pressure_detector.detect_showdown_events(game_state, winner_info)
                pressure_detector.apply_detected_events(events)
                
                # Log events for debugging and record stats
                if events:
                    event_names = [e[0] for e in events]
                    send_message(game_id, "System", f"[Debug] Pressure events: {', '.join(event_names)}", "system")
                    
                    # Record events in stats tracker
                    if 'pressure_stats' in current_game_data:
                        pressure_stats = current_game_data['pressure_stats']
                        pot_size = pot_size_before_award
                        
                        for event_name, affected_players in events:
                            details = {
                                'pot_size': pot_size,
                                'hand_rank': winner_info.get('hand_rank'),
                                'hand_name': winner_info.get('hand_name')
                            }
                            pressure_stats.record_event(event_name, affected_players, details)
                    
                    # Update AI controllers with new elasticity values
                    elasticity_manager = current_game_data['elasticity_manager']
                    ai_controllers = current_game_data.get('ai_controllers', {})
                    
                    for name, personality in elasticity_manager.personalities.items():
                        if name in ai_controllers:
                            controller = ai_controllers[name]
                            # Update the AI player's elastic personality
                            if hasattr(controller, 'ai_player') and hasattr(controller.ai_player, 'elastic_personality'):
                                controller.ai_player.elastic_personality = personality
                                # Update mood
                                controller.ai_player.update_mood_from_elasticity()
                    
                    # Emit elasticity update via WebSocket
                    elasticity_data = {}
                    for name, personality in elasticity_manager.personalities.items():
                        traits_data = {}
                        for trait_name, trait in personality.traits.items():
                            traits_data[trait_name] = {
                                'current': trait.value,
                                'anchor': trait.anchor,
                                'elasticity': trait.elasticity,
                                'pressure': trait.pressure,
                                'min': trait.min,
                                'max': trait.max
                            }
                        
                        elasticity_data[name] = {
                            'traits': traits_data,
                            'mood': personality.get_current_mood()
                        }
                    
                    socketio.emit('elasticity_update', elasticity_data, to=game_id)
            
            # Now award the pot winnings
            game_state = award_pot_winnings(game_state, winner_info['winnings'])

            winning_players_string = (', '.join(winning_player_names[:-1]) +
                                      f" and {winning_player_names[-1]}") \
                                      if len(winning_player_names) > 1 else winning_player_names[0]

            # Check if it was a showdown (more than one active player)
            active_players = [p for p in game_state.players if not p.is_folded]
            is_showdown = len(active_players) > 1
            
            # Prepare winner announcement data
            winner_data = {
                'winners': winning_player_names,
                'winnings': winner_info['winnings'],
                'showdown': is_showdown,
                'community_cards': []
            }
            
            # Only include hand name if it's a showdown
            if is_showdown:
                winner_data['hand_name'] = winner_info['hand_name']
            
            # Include community cards
            for card in game_state.community_cards:
                if isinstance(card, dict):
                    winner_data['community_cards'].append({
                        'rank': card['rank'],
                        'suit': card['suit']
                    })
                else:
                    winner_data['community_cards'].append(card)
            
            # If it's a showdown, include player cards
            if is_showdown:
                players_cards = {}
                for player in active_players:
                    if player.hand:
                        # Convert cards to backend format that Card component expects
                        formatted_cards = []
                        for card in player.hand:
                            if isinstance(card, dict):
                                formatted_cards.append({
                                    'rank': card['rank'],
                                    'suit': card['suit']
                                })
                            else:
                                # Already formatted
                                formatted_cards.append(card)
                        players_cards[player.name] = formatted_cards
                winner_data['players_cards'] = players_cards

            if is_showdown:
                message_content = (
                    f"{winning_players_string} won the pot of ${winner_info['winnings']} with {winner_info['hand_name']}. "
                    f"Winning hand: {winner_info['winning_hand']}"
                )
            else:
                message_content = f"{winning_players_string} won the pot of ${winner_info['winnings']}."
            
            send_message(game_id,"table", message_content, "table", 1)
            
            # Emit winner announcement event
            socketio.emit('winner_announcement', winner_data, to=game_id)
            
            # Delay before dealing new hand
            socketio.sleep(4 if is_showdown else 2)
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
            # Convert options to list if it's a set
            player_options = list(game_state.current_player_options) if game_state.current_player_options else []
            socketio.emit('player_turn_start', { 'current_player_options': player_options, 'cost_to_call': cost_to_call}, to=game_id)
            
            # Apply trait recovery while waiting for human action
            if 'elasticity_manager' in current_game_data:
                elasticity_manager = current_game_data['elasticity_manager']
                elasticity_manager.recover_all()
                
                # Emit updated elasticity data
                elasticity_data = {}
                for name, personality in elasticity_manager.personalities.items():
                    traits_data = {}
                    for trait_name, trait in personality.traits.items():
                        traits_data[trait_name] = {
                            'current': trait.value,
                            'anchor': trait.anchor,
                            'elasticity': trait.elasticity,
                            'pressure': trait.pressure,
                            'min': trait.min,
                            'max': trait.max
                        }
                    
                    elasticity_data[name] = {
                        'traits': traits_data,
                        'mood': personality.get_current_mood()
                    }
                
                socketio.emit('elasticity_update', elasticity_data, to=game_id)
            
            break


@app.route('/game/<game_id>', methods=['GET'])
def game(game_id) -> Response:
    # Deprecated: This route previously rendered a template
    # Now redirect to the API endpoint
    return redirect(f'/api/game-state/{game_id}')


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
    owner_id, owner_name = get_game_owner_info(game_id)
    persistence.save_game(game_id, state_machine._state_machine, owner_id, owner_name)
    
    update_and_emit_game_state(game_id)  # Emit updated game state
    progress_game(game_id)


def detect_and_apply_pressure(game_id: str, event_type: str, **kwargs) -> None:
    """Helper function to detect and apply pressure events."""
    current_game_data = games.get(game_id)
    if not current_game_data or 'pressure_detector' not in current_game_data:
        return
    
    pressure_detector = current_game_data['pressure_detector']
    elasticity_manager = current_game_data['elasticity_manager']
    game_state = current_game_data['state_machine'].game_state
    
    events = []
    
    if event_type == 'fold':
        # Detect fold pressure events
        folding_player = kwargs.get('player_name')
        pot_size = game_state.pot.get('total', 0) if isinstance(game_state.pot, dict) else 0
        
        # Folding to aggression when pot is large
        if pot_size > 100:  # Significant pot
            events.append(('fold_under_pressure', [folding_player]))
            
    elif event_type == 'big_bet':
        # Detect aggressive betting
        betting_player = kwargs.get('player_name')
        bet_size = kwargs.get('bet_size', 0)
        pot_size = game_state.pot.get('total', 0) if isinstance(game_state.pot, dict) else 0
        
        if bet_size > pot_size * 0.75:  # Large bet relative to pot
            events.append(('aggressive_bet', [betting_player]))
    
    # Apply detected events
    if events:
        pressure_detector.apply_detected_events(events)
        
        # Record events in stats
        if 'pressure_stats' in current_game_data:
            pressure_stats = current_game_data['pressure_stats']
            for event_name, affected_players in events:
                details = kwargs.copy()  # Include all passed details
                pressure_stats.record_event(event_name, affected_players, details)
        
        # Emit elasticity update via WebSocket
        elasticity_data = {}
        for name, personality in elasticity_manager.personalities.items():
            traits_data = {}
            for trait_name, trait in personality.traits.items():
                traits_data[trait_name] = {
                    'current': trait.value,
                    'anchor': trait.anchor,
                    'elasticity': trait.elasticity,
                    'pressure': trait.pressure,
                    'min': trait.min,
                    'max': trait.max
                }
            
            elasticity_data[name] = {
                'traits': traits_data,
                'mood': personality.get_current_mood()
            }
        
        socketio.emit('elasticity_update', elasticity_data, to=game_id)


def handle_ai_action(game_id: str) -> None:
    """
    Handle an AI player's action in the game.

    :param game_id: (int)
        The ID of the game for which the AI action is being handled.
    :return: (None)
    """
    print(f"[handle_ai_action] Starting AI action for game {game_id}")
    current_game_data = games.get(game_id)
    if not current_game_data:
        print(f"[handle_ai_action] No game data found for {game_id}")
        return

    state_machine = current_game_data['state_machine']
    game_messages = current_game_data['messages']
    ai_controllers = current_game_data['ai_controllers']

    current_player = state_machine.game_state.current_player
    print(f"[handle_ai_action] Current AI player: {current_player.name}")
    controller = ai_controllers[current_player.name]
    
    try:
        # The controller.decide_action already has resilience built in,
        # but we wrap in try/catch as a last resort
        player_response_dict = controller.decide_action(game_messages[-AI_MESSAGE_CONTEXT_LIMIT:])
        
        # Prepare variables needed for new messages
        action = player_response_dict['action']
        amount = player_response_dict.get('adding_to_pot', 0)
        player_message = player_response_dict.get('persona_response', '')
        player_physical_description = player_response_dict.get('physical', '')
        raise_corrected = player_response_dict.get('raise_amount_corrected', False)
        
    except Exception as e:
        # This should rarely happen since controller has built-in resilience
        print(f"[handle_ai_action] Critical error getting AI decision: {e}")

        # Use centralized FallbackActionSelector as last resort
        valid_actions = state_machine.game_state.current_player_options
        personality_traits = getattr(controller, 'personality_traits', {})
        call_amount = state_machine.game_state.highest_bet - current_player.bet
        max_raise = min(current_player.stack, state_machine.game_state.pot.get('total', 0) * 2)

        fallback_result = FallbackActionSelector.select_action(
            valid_actions=valid_actions,
            strategy=AIFallbackStrategy.MIMIC_PERSONALITY,
            personality_traits=personality_traits,
            call_amount=call_amount,
            min_raise=MIN_RAISE,
            max_raise=max_raise
        )

        action = fallback_result['action']
        amount = fallback_result['adding_to_pot']

        # Use personality-aware fallback messages
        player_message = get_fallback_chat_response(current_player.name)
        player_physical_description = "*pauses momentarily*"
        raise_corrected = False

        # Subtle notification that we're using fallback
        send_message(game_id, "table",
                    f"[{current_player.name} takes a moment to consider]",
                    "table")

    # Build table message with correction indicator
    table_message_content = f"{current_player.name} chose to {action}{(' by $' + str(amount)) if amount > 0 else ''}."
    if raise_corrected and action == 'raise':
        table_message_content += " ⚠️"  # Subtle warning emoji to indicate correction
    
    # Only send AI message if they actually spoke
    if player_message and player_message != '...':
        full_message = f"{player_message} {player_physical_description}".strip()
        send_message(game_id, current_player.name, full_message, "ai", 1)
    
    send_message(game_id, "table", table_message_content, "table")
    
    # Detect pressure events based on AI action
    if action == 'fold':
        detect_and_apply_pressure(game_id, 'fold', player_name=current_player.name)
    elif action in ['raise', 'all_in'] and amount > 0:
        detect_and_apply_pressure(game_id, 'big_bet', player_name=current_player.name, bet_size=amount)

    game_state = play_turn(state_machine.game_state, action, amount)
    game_state = advance_to_next_active_player(game_state)
    state_machine.game_state = game_state
    current_game_data['state_machine'] = state_machine
    games[game_id] = current_game_data
    
    # Save game after AI action
    owner_id, owner_name = get_game_owner_info(game_id)
    persistence.save_game(game_id, state_machine._state_machine, owner_id, owner_name)
    
    # Save AI state
    if hasattr(controller, 'assistant') and controller.assistant:
        personality_state = {
            'traits': getattr(controller, 'personality_traits', {}),
            'confidence': getattr(controller.ai_player, 'confidence', 'Normal'),
            'attitude': getattr(controller.ai_player, 'attitude', 'Neutral')
        }
        persistence.save_ai_player_state(
            game_id, 
            current_player.name,
            controller.assistant.messages,
            personality_state
        )
    
    update_and_emit_game_state(game_id)


@socketio.on('send_message')
def handle_send_message(data):
    # Get needed values from the data
    game_id = data.get('game_id')
    content = data.get('message')
    sender = data.get('sender', 'Player')  # Default to 'Player' instead of 'Jeff'
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


@app.route('/game/<game_id>', methods=['DELETE'])
def delete_game(game_id):
    """Delete a saved game."""
    try:
        # Remove from in-memory games if present
        if game_id in games:
            del games[game_id]
        
        # Delete from database
        persistence.delete_game(game_id)
        
        # Also need to delete AI states
        import sqlite3
        with sqlite3.connect(persistence.db_path) as conn:
            conn.execute("DELETE FROM ai_player_state WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM personality_snapshots WHERE game_id = ?", (game_id,))
        
        return jsonify({'message': 'Game deleted successfully'}), 200
    except Exception as e:
        logger.error(f"Error deleting game {game_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/end_game/<game_id>', methods=['GET'])
def end_game(game_id):
    if game_id not in games:
        return jsonify({'error': 'Game not found'}), 404
    games.pop(game_id, None)
    messages.pop(game_id, None)
    return jsonify({'message': 'Game ended successfully'})


@app.route('/settings/<game_id>')
def settings(game_id):
    # Deprecated: Settings are now handled in React
    game_state = games.get(game_id)
    if not game_state:
        return jsonify({'error': 'Game not found'}), 404
    return jsonify({'message': 'Settings should be accessed through the React app'})


@app.route('/messages/<game_id>', methods=['GET'])
def get_messages(game_id):
    game_data = games.get(game_id)
    if not game_data:
        game_messages = []
    else:
        game_messages = game_data['messages']
    return jsonify(game_messages)


@app.route('/api/game/<game_id>/elasticity', methods=['GET'])
def get_elasticity_data(game_id):
    """Get current elasticity data for all AI players."""
    game_data = games.get(game_id)
    if not game_data or 'elasticity_manager' not in game_data:
        return jsonify({'error': 'Game not found or elasticity not enabled'}), 404
    
    elasticity_manager = game_data['elasticity_manager']
    elasticity_data = {}
    
    for name, personality in elasticity_manager.personalities.items():
        traits_data = {}
        for trait_name, trait in personality.traits.items():
            traits_data[trait_name] = {
                'current': trait.value,
                'anchor': trait.anchor,
                'elasticity': trait.elasticity,
                'pressure': trait.pressure,
                'min': trait.min,
                'max': trait.max
            }
        
        elasticity_data[name] = {
            'traits': traits_data,
            'mood': personality.get_current_mood()
        }
    
    return jsonify(elasticity_data)


@app.route('/api/game/<game_id>/chat-suggestions', methods=['POST'])
@limiter.limit(os.environ.get('RATE_LIMIT_CHAT_SUGGESTIONS', '100 per hour'))
def get_chat_suggestions(game_id):
    """Generate smart chat suggestions based on game context."""
    if game_id not in games:
        return jsonify({"error": "Game not found"}), 404
    
    try:
        data = request.get_json()
        game_data = games[game_id]
        state_machine = game_data['state_machine']
        game_state = state_machine.game_state
        
        # Build context for the AI
        context_parts = []
        
        # Get player info
        player_name = data.get('playerName', 'Player')
        
        # Get recent action if provided
        last_action = data.get('lastAction')
        if last_action:
            action_text = f"{last_action['player']} just {last_action['type']}"
            if last_action.get('amount'):
                action_text += f" ${last_action['amount']}"
            context_parts.append(action_text)
        
        # Get game phase and pot
        context_parts.append(f"Game phase: {str(state_machine.current_phase).split('.')[-1]}")
        context_parts.append(f"Pot size: ${game_state.pot['total']}")
        
        # Get player's chip position if provided
        chip_position = data.get('chipPosition', '')
        if chip_position:
            context_parts.append(f"You are {chip_position}")
        
        # Build the prompt
        context_str = ". ".join(context_parts)
        
        prompt = f"""Generate exactly 3 short poker table chat messages for player "{player_name}".
Context: {context_str}

Requirements:
- Each message should be 2-4 words max
- Make them fun, casual, and appropriate for online poker
- Include one reaction, one strategic comment, and one social/fun message
- Keep them varied and natural
- No profanity or negativity

Return as JSON with this format:
{{
    "suggestions": [
        {{"text": "message here", "type": "reaction"}},
        {{"text": "message here", "type": "strategic"}},
        {{"text": "message here", "type": "social"}}
    ]
}}"""

        # Check if OpenAI API key is available
        if not os.environ.get("OPENAI_API_KEY"):
            print("Warning: No OpenAI API key found, returning fallback suggestions")
            raise ValueError("OpenAI API key not configured")
        
        # Use the OpenAI assistant
        assistant = OpenAILLMAssistant(
            ai_model="gpt-5-mini",  # Faster model for quick suggestions
            ai_temp=0.8,  # Slightly creative but not too random
            system_message="You are a friendly poker player giving brief chat suggestions."
        )
        
        messages = [
            {"role": "system", "content": assistant.system_message},
            {"role": "user", "content": prompt}
        ]
        
        response = assistant.get_json_response(messages)
        result = json.loads(response.choices[0].message.content)
        
        return jsonify(result)
        
    except Exception as e:
        print(f"Error generating chat suggestions: {str(e)}")
        # Return fallback suggestions if AI fails
        return jsonify({
            "suggestions": [
                {"text": "Nice play!", "type": "reaction"},
                {"text": "Interesting move", "type": "strategic"},
                {"text": "Let's go!", "type": "social"}
            ]
        })

@app.route('/api/game/<game_id>/pressure-stats', methods=['GET'])
def get_pressure_stats(game_id):
    """Get pressure event statistics for the game."""
    game_data = games.get(game_id)
    if not game_data or 'pressure_stats' not in game_data:
        return jsonify({'error': 'Game not found or stats not available'}), 404
    
    pressure_stats = game_data['pressure_stats']
    return jsonify(pressure_stats.get_session_summary())


# Personality management routes
@app.route('/personalities')
def personalities_page():
    """Deprecated: Personality manager page now in React."""
    return redirect('/api/personalities')

@app.route('/api/personalities', methods=['GET'])
def get_personalities():
    """Get all personalities."""
    try:
        # First, get personalities from database
        db_personalities = persistence.list_personalities(limit=200)
        
        # Convert to expected format
        personalities = {}
        for p in db_personalities:
            # Load the full config for each personality
            name = p['name']
            config = persistence.load_personality(name)
            if config:
                personalities[name] = config
        
        # Also load from personalities.json as fallback for any not in DB
        try:
            personalities_file = Path(__file__).parent.parent / 'poker' / 'personalities.json'
            with open(personalities_file, 'r') as f:
                data = json.load(f)
                # Add any from JSON that aren't in DB
                for name, config in data.get('personalities', {}).items():
                    if name not in personalities:
                        personalities[name] = config
        except:
            pass  # JSON file might not exist
        
        return jsonify({
            'success': True,
            'personalities': personalities
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/personality/<name>', methods=['GET'])
def get_personality(name):
    """Get a specific personality."""
    try:
        # First try database
        db_personality = persistence.load_personality(name)
        if db_personality:
            return jsonify({
                'success': True,
                'personality': db_personality,
                'name': name
            })
        
        # Fallback to personalities.json
        try:
            personalities_file = Path(__file__).parent.parent / 'poker' / 'personalities.json'
            with open(personalities_file, 'r') as f:
                data = json.load(f)
            
            if name in data['personalities']:
                return jsonify({
                    'success': True,
                    'personality': data['personalities'][name],
                    'name': name
                })
        except:
            pass
        
        return jsonify({'success': False, 'error': 'Personality not found'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/personality', methods=['POST'])
def create_personality():
    """Create a new personality."""
    try:
        data = request.json
        name = data.get('name')
        
        if not name:
            return jsonify({'success': False, 'error': 'Name is required'})
        
        # Remove name from config (it's the key, not part of the value)
        personality_config = {k: v for k, v in data.items() if k != 'name'}
        
        # Add default traits if missing
        if 'personality_traits' not in personality_config:
            personality_config['personality_traits'] = {
                "bluff_tendency": 0.5,
                "aggression": 0.5,
                "chattiness": 0.5,
                "emoji_usage": 0.3
            }
        
        # Save to database
        persistence.save_personality(name, personality_config, source='user_created')
        
        # Also save to personalities.json for backward compatibility
        try:
            personalities_file = Path(__file__).parent.parent / 'poker' / 'personalities.json'
            if personalities_file.exists():
                with open(personalities_file, 'r') as f:
                    data = json.load(f)
                data['personalities'][name] = personality_config
                with open(personalities_file, 'w') as f:
                    json.dump(data, f, indent=2)
        except:
            pass  # JSON update is optional
        
        return jsonify({
            'success': True,
            'message': f'Personality {name} created successfully'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/personality/<name>', methods=['PUT'])
def update_personality(name):
    """Update a personality."""
    try:
        personality_config = request.json
        
        # Save to database
        persistence.save_personality(name, personality_config, source='user_edited')
        
        # Also update personalities.json for backward compatibility
        try:
            personalities_file = Path(__file__).parent.parent / 'poker' / 'personalities.json'
            if personalities_file.exists():
                with open(personalities_file, 'r') as f:
                    data = json.load(f)
                data['personalities'][name] = personality_config
                with open(personalities_file, 'w') as f:
                    json.dump(data, f, indent=2)
        except:
            pass  # JSON update is optional
        
        return jsonify({
            'success': True,
            'message': f'Personality {name} updated successfully'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/personality/<name>', methods=['DELETE'])
def delete_personality(name):
    """Delete a personality."""
    try:
        # Delete from database
        deleted = persistence.delete_personality(name)
        
        if not deleted:
            return jsonify({
                'success': False,
                'error': f'Personality {name} not found'
            })
        
        # Also remove from personalities.json for backward compatibility
        try:
            personalities_file = Path(__file__).parent.parent / 'poker' / 'personalities.json'
            if personalities_file.exists():
                with open(personalities_file, 'r') as f:
                    data = json.load(f)
                
                if name in data['personalities']:
                    del data['personalities'][name]
                    with open(personalities_file, 'w') as f:
                        json.dump(data, f, indent=2)
        except:
            pass  # JSON update is optional
        
        return jsonify({
            'success': True,
            'message': f'Personality {name} deleted successfully'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/generate-theme', methods=['POST'])
def generate_theme():
    """Generate a themed game with appropriate personalities."""
    try:
        data = request.json
        theme = data.get('theme')
        theme_name = data.get('themeName')
        description = data.get('description')
        
        if not theme:
            return jsonify({'error': 'Theme is required'}), 400
        
        # Get a sample of personalities to send to OpenAI
        all_personalities = list(get_celebrities())
        sample_size = min(100, len(all_personalities))
        import random
        personality_sample = random.sample(all_personalities, sample_size)
        
        # Create prompt for OpenAI
        prompt = f"""Given these available personalities: {', '.join(personality_sample)}

Please select 3-5 personalities that would fit the theme: "{theme_name}" - {description}

Selection criteria:
- Choose personalities that match the theme
- Create an interesting mix of personalities that would have fun dynamics
- For "surprise" theme, pick an eclectic, unexpected mix
- Ensure good variety in play styles

Return ONLY a JSON array of personality names, like:
["Name1", "Name2", "Name3", "Name4"]

No other text or explanation."""

        # Call OpenAI
        from core.assistants import OpenAILLMAssistant
        assistant = OpenAILLMAssistant(
            system_prompt="You are a game designer selecting personalities for themed poker games.",
            ai_model="gpt-5-mini"
        )
        
        response = assistant.get_response(prompt)
        
        # Parse the response
        import json
        try:
            # Clean up the response in case it has extra text
            response_text = response.strip()
            if response_text.startswith('```'):
                # Remove code blocks if present
                response_text = response_text.split('```')[1]
                if response_text.startswith('json'):
                    response_text = response_text[4:]
            
            personalities = json.loads(response_text)
            
            # Validate that all personalities exist
            valid_personalities = []
            for name in personalities:
                if name in personality_sample:
                    valid_personalities.append(name)
            
            # Ensure we have at least 3
            if len(valid_personalities) < 3:
                # Fallback to random selection
                valid_personalities = random.sample(personality_sample, min(4, len(personality_sample)))
            
            return jsonify({
                'success': True,
                'personalities': valid_personalities[:5]  # Max 5 AI players
            })
            
        except json.JSONDecodeError:
            # Fallback to random selection
            personalities = random.sample(personality_sample, min(4, len(personality_sample)))
            return jsonify({
                'success': True,
                'personalities': personalities,
                'fallback': True
            })
            
    except Exception as e:
        logger.error(f"Error generating theme: {e}")
        # Fallback to random selection
        try:
            all_personalities = list(get_celebrities())
            personalities = random.sample(all_personalities, min(4, len(all_personalities)))
            return jsonify({
                'success': True,
                'personalities': personalities,
                'fallback': True
            })
        except:
            return jsonify({'error': 'Failed to generate theme'}), 500

@app.route('/api/generate_personality', methods=['POST'])
@limiter.limit(os.environ.get('RATE_LIMIT_GENERATE_PERSONALITY', '15 per hour'))
def generate_personality():
    """Generate a new personality using AI."""
    try:
        from poker.personality_generator import PersonalityGenerator
        
        data = request.json
        name = data.get('name', '').strip()
        
        if not name:
            return jsonify({'success': False, 'error': 'Name is required'})
        
        # Force generation even if exists
        force_generate = data.get('force', False)
        
        # Use the shared generator if available, or create a new one
        generator = PersonalityGenerator()
        
        # Generate the personality
        personality_config = generator.get_personality(
            name=name, 
            force_generate=force_generate
        )
        
        # Also save to personalities.json for consistency
        personalities_file = Path(__file__).parent.parent / 'poker' / 'personalities.json'
        with open(personalities_file, 'r') as f:
            personalities_data = json.load(f)
        
        personalities_data['personalities'][name] = personality_config
        
        with open(personalities_file, 'w') as f:
            json.dump(personalities_data, f, indent=2)
        
        return jsonify({
            'success': True,
            'personality': personality_config,
            'name': name,
            'message': f'Successfully generated personality for {name}'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'message': 'Failed to generate personality. Please check your OpenAI API key.'
        })

if __name__ == '__main__':
    import os
    port = int(os.environ.get('FLASK_RUN_PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True, allow_unsafe_werkzeug=True)
