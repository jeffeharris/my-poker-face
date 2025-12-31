# Server-Side Python (ui_web.py) with Socket.IO integration and Flask routes for game management using a local dictionary for game states
from typing import Optional, Dict
from pathlib import Path
import uuid

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

# Configure logging to show INFO level for AI stats
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv(override=True)

# AI model configuration - use fast/lightweight models for quick operations
FAST_AI_MODEL = os.environ.get('OPENAI_FAST_MODEL', 'gpt-5-nano')

from poker.controllers import AIPlayerController
from poker.ai_resilience import get_fallback_chat_response, FallbackActionSelector, AIFallbackStrategy
from poker.config import MIN_RAISE, AI_MESSAGE_CONTEXT_LIMIT
from poker.elasticity_manager import ElasticityManager
from poker.pressure_detector import PressureEventDetector
from poker.pressure_stats import PressureStatsTracker
from poker.memory import AIMemoryManager
from poker.hand_evaluator import HandEvaluator
from core.card import Card
from poker.poker_game import PokerGameState, initialize_game_state, determine_winner, play_turn, \
    advance_to_next_active_player, award_pot_winnings
from poker.poker_state_machine import PokerStateMachine, PokerPhase
from poker.utils import get_celebrities
from poker.persistence import GamePersistence
from poker.repositories.sqlite_repositories import PressureEventRepository
from poker.auth import AuthManager
from .game_adapter import StateMachineAdapter, GameStateAdapter
from core.assistants import OpenAILLMAssistant
from openai import OpenAI

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(32).hex())

# Configure CORS securely based on environment
# In development: Allow all origins WITH credentials using regex (echoes back requesting origin)
# In production: Require explicit origins with credentials (secure)
# Check both FLASK_ENV (for backward compatibility) and FLASK_DEBUG
flask_env = os.environ.get('FLASK_ENV', 'production')
flask_debug = os.environ.get('FLASK_DEBUG', '0')
is_development = (flask_env == 'development' or flask_debug == '1')
cors_origins_env = os.environ.get('CORS_ORIGINS', '*')

if cors_origins_env == '*':
    # Wildcard origin mode
    if is_development:
        # Development: Allow all origins WITH credentials using regex
        # This echoes back the requesting origin instead of literal "*"
        # which allows credentials to work while maintaining flexibility
        import re
        CORS(app, supports_credentials=True, origins=re.compile(r'.*'))
    else:
        # Production: Wildcard is not allowed with credentials for security
        # Fall back to safe defaults or require explicit configuration
        raise ValueError(
            "CORS_ORIGINS='*' is not allowed in production. "
            "Please set CORS_ORIGINS to a comma-separated list of allowed origins. "
            "Example: CORS_ORIGINS=https://app.example.com,https://www.example.com"
        )
else:
    # Explicit origins - can safely use credentials
    cors_origins = [origin.strip() for origin in cors_origins_env.split(',') if origin.strip()]
    CORS(app, supports_credentials=True, origins=cors_origins)

# Custom key function for rate limiting
def get_rate_limit_key():
    """Get IP address for rate limiting."""
    return get_remote_address() or "127.0.0.1"

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


def format_action_message(player_name: str, action: str, amount: int = 0, highest_bet: int = 0) -> str:
    """
    Format a player action into a human-readable message.

    :param player_name: The name of the player taking the action
    :param action: The action type (raise, bet, call, check, fold, all_in)
    :param amount: The "raise BY" amount (increment over the call)
    :param highest_bet: The current highest bet before this action
    :return: Formatted message string
    """
    if action == 'raise':
        # amount is "raise BY", so total bet = highest_bet + amount
        raise_to_amount = highest_bet + amount
        return f"{player_name} raises to ${raise_to_amount}."
    elif action == 'bet':
        return f"{player_name} bets ${amount}."
    elif action == 'call':
        return f"{player_name} calls."
    elif action == 'check':
        return f"{player_name} checks."
    elif action == 'fold':
        return f"{player_name} folds."
    elif action == 'all_in':
        return f"{player_name} goes all-in!"
    else:
        return f"{player_name} chose to {action}."


def record_action_in_memory(game_data: dict, player_name: str, action: str,
                            amount: int, game_state, state_machine) -> None:
    """Record a player action in the memory manager if available.

    Args:
        game_data: The game data dictionary containing the memory_manager
        player_name: Name of the player who acted
        action: The action taken ('fold', 'check', 'call', 'raise', 'bet', 'all_in')
        amount: Amount added to pot
        game_state: Current game state (for pot total)
        state_machine: State machine (for current phase)
    """
    if 'memory_manager' not in game_data:
        return

    memory_manager = game_data['memory_manager']
    pot_total = game_state.pot.get('total', 0) if isinstance(game_state.pot, dict) else 0
    phase = state_machine.current_phase.name if hasattr(state_machine.current_phase, 'name') else str(state_machine.current_phase)

    memory_manager.on_action(
        player_name=player_name,
        action=action,
        amount=amount,
        phase=phase,
        pot_total=pot_total
    )


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
                
                # Restore conversation history (memory, excluding system message)
                if hasattr(controller, 'assistant') and controller.assistant:
                    saved_messages = saved_state.get('messages', [])
                    # Filter out system messages - only restore user/assistant exchanges
                    memory = [m for m in saved_messages if m.get('role') != 'system']
                    controller.assistant.memory = memory
                
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
    
    # Include messages in the game state (transform to frontend format)
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
    # min_raise is the minimum RAISE BY amount (not total bet)
    # Equals the last raise amount, or big blind if no raises yet
    game_state_dict['min_raise'] = game_state.min_raise_amount
    game_state_dict['big_blind'] = game_state.current_ante
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
        # Parse game state to get player data
        try:
            state = json.loads(game.game_state_json)
            players = state.get('players', [])
            player_names = [p['name'] for p in players]

            # Calculate player stats
            total_players = len(players)
            active_players = sum(1 for p in players if p.get('stack', 0) > 0)

            # Get human player's stack (first human player found)
            human_stack = None
            for p in players:
                if p.get('is_human', False):
                    human_stack = p.get('stack', 0)
                    break

            # Get big blind (current_ante)
            big_blind = state.get('current_ante', 20)

        except:
            player_names = []
            total_players = game.num_players
            active_players = game.num_players
            human_stack = None
            big_blind = 20

        # Convert numeric phase to readable string
        try:
            phase_num = int(game.phase) if isinstance(game.phase, str) else game.phase
            phase_name = PokerPhase(phase_num).name.replace('_', ' ').title()
        except:
            phase_name = game.phase

        games_data.append({
            'game_id': game.game_id,
            'created_at': game.created_at.strftime("%Y-%m-%d %H:%M"),
            'updated_at': game.updated_at.strftime("%Y-%m-%d %H:%M"),
            'phase': phase_name,
            'num_players': game.num_players,
            'pot_size': game.pot_size,
            'player_names': player_names,
            'is_owner': True,  # Always true since we're filtering by owner
            'active_players': active_players,
            'total_players': total_players,
            'human_stack': human_stack,
            'big_blind': big_blind
        })

    return jsonify({'games': games_data})

# Removed /api/my-games endpoint - consolidated with /games


@app.route('/api/game-state/<game_id>')
def api_game_state(game_id):
    """API endpoint to get current game state for React app."""
    current_game_data = games.get(game_id)

    # Auto-advance cached games that are stuck in non-action phases
    # Only advance if game hasn't been started yet (prevents duplicate progress_game calls)
    if current_game_data:
        state_machine = current_game_data['state_machine']
        if not state_machine.game_state.awaiting_action and not current_game_data.get('game_started', False):
            print(f"[CACHE] Auto-advancing cached game {game_id}, phase: {state_machine.current_phase}")
            current_game_data['game_started'] = True
            progress_game(game_id)

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

                # Initialize memory manager for AI learning (needed for commentary)
                memory_manager = AIMemoryManager(game_id, persistence.db_path)
                for player in state_machine.game_state.players:
                    if not player.is_human and player.name in ai_controllers:
                        memory_manager.initialize_for_player(player.name)
                        # Connect memory to AI controller
                        controller = ai_controllers[player.name]
                        controller.session_memory = memory_manager.get_session_memory(player.name)
                        controller.opponent_model_manager = memory_manager.get_opponent_model_manager()

                # Start recording the current hand
                memory_manager.on_hand_start(state_machine.game_state, hand_number=memory_manager.hand_count + 1)

                current_game_data = {
                    'state_machine': state_machine,
                    'ai_controllers': ai_controllers,
                    'elasticity_manager': elasticity_manager,
                    'pressure_detector': pressure_detector,
                    'pressure_stats': pressure_stats,
                    'memory_manager': memory_manager,
                    'owner_id': owner_id,
                    'owner_name': owner_name,
                    'messages': db_messages,
                    'game_started': True  # Mark loaded games as started to prevent duplicate progress_game calls
                }
                games[game_id] = current_game_data

                # Auto-advance if game is stuck in a non-action phase (e.g., HAND_OVER)
                print(f"[LOAD] Game {game_id} loaded. Phase: {state_machine.current_phase}, awaiting_action: {state_machine.game_state.awaiting_action}")
                if not state_machine.game_state.awaiting_action:
                    print(f"[LOAD] Auto-advancing game {game_id}")
                    progress_game(game_id)
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
    # Use dict format for cards to match WebSocket format (more robust than string parsing)
    players = []
    for player in game_state.players:
        if player.is_human and player.hand:
            hand = [card.to_dict() if hasattr(card, 'to_dict') else card for card in player.hand]
        else:
            hand = None
        players.append({
            'name': player.name,
            'stack': player.stack,
            'bet': player.bet,
            'is_folded': player.is_folded,
            'is_all_in': player.is_all_in,
            'is_human': player.is_human,
            'hand': hand
        })

    # Convert community cards (dict format to match WebSocket)
    community_cards = [card.to_dict() if hasattr(card, 'to_dict') else card for card in game_state.community_cards]
    
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
        'min_raise': game_state.min_raise_amount,  # minimum RAISE BY amount
        'big_blind': game_state.current_ante,
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
        max_games = 3 if current_user.get('is_guest', True) else 10
        
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

    # Get LLM configuration (model and reasoning_effort)
    llm_config = data.get('llm_config', {})

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
            new_controller = AIPlayerController(player.name, state_machine, llm_config=llm_config)
            ai_controllers[player.name] = new_controller
            
            # Add to elasticity manager
            elasticity_manager.add_player(
                player.name,
                new_controller.ai_player.personality_config
            )
    
    pressure_detector = PressureEventDetector(elasticity_manager)
    game_id = generate_game_id()
    pressure_stats = PressureStatsTracker(game_id, event_repository)

    # Initialize memory manager for AI learning
    memory_manager = AIMemoryManager(game_id, persistence.db_path)
    for player in state_machine.game_state.players:
        if not player.is_human:
            memory_manager.initialize_for_player(player.name)
            # Connect memory to AI controller
            controller = ai_controllers[player.name]
            controller.session_memory = memory_manager.get_session_memory(player.name)
            controller.opponent_model_manager = memory_manager.get_opponent_model_manager()

    # Start recording the first hand
    memory_manager.on_hand_start(state_machine.game_state, hand_number=1)

    game_data = {
        'state_machine': state_machine,
        'ai_controllers': ai_controllers,
        'elasticity_manager': elasticity_manager,
        'pressure_detector': pressure_detector,
        'pressure_stats': pressure_stats,
        'memory_manager': memory_manager,
        'owner_id': owner_id,
        'owner_name': owner_name,
        'llm_config': llm_config,
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

    # Game progression is triggered later by server-side events (e.g., player joins or actions).
    # This endpoint only initializes the game so the table UI can load immediately.

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
    
    # Capture highest_bet before play_turn modifies state
    highest_bet = state_machine.game_state.highest_bet
    game_state = play_turn(state_machine.game_state, action, amount)

    # Record human action in memory manager
    record_action_in_memory(current_game_data, current_player.name, action, amount, game_state, state_machine)

    # Generate a message to be added to the game table
    table_message_content = format_action_message(current_player.name, action, amount, highest_bet)
    send_message(game_id, "Table", table_message_content, "table")

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
            cards_str = [str(c) for c in game_state.community_cards[-num_cards_dealt:]]
            message_content = f"{state_machine.current_phase} cards dealt: {cards_str}"
            send_message(game_id, "Table", message_content, "table")

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

            # Process hand completion with memory manager (skip commentary for now - generate async)
            if 'memory_manager' in current_game_data:
                memory_manager = current_game_data['memory_manager']
                ai_controllers = current_game_data.get('ai_controllers', {})

                # Build AI players dict for commentary generation
                ai_players = {
                    name: controller.ai_player
                    for name, controller in ai_controllers.items()
                }

                # Complete hand recording (skip commentary - will generate async after showing winner)
                try:
                    memory_manager.on_hand_complete(
                        winner_info=winner_info,
                        game_state=game_state,
                        ai_players=ai_players,
                        skip_commentary=True
                    )
                except Exception as e:
                    logger.warning(f"Memory manager hand completion failed: {e}")

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
            
            # Include community cards (convert Card objects to dicts)
            for card in game_state.community_cards:
                if hasattr(card, 'to_dict'):
                    winner_data['community_cards'].append(card.to_dict())
                elif isinstance(card, dict):
                    winner_data['community_cards'].append(card)
                else:
                    winner_data['community_cards'].append({'rank': str(card), 'suit': ''})

            # If it's a showdown, include player cards and hand info in a combined structure
            if is_showdown:
                players_showdown = {}

                # Prepare community cards for hand evaluation
                community_cards_for_eval = []
                for card in game_state.community_cards:
                    if isinstance(card, Card):
                        community_cards_for_eval.append(card)
                    elif isinstance(card, dict):
                        community_cards_for_eval.append(Card(card['rank'], card['suit']))

                for player in active_players:
                    if player.hand:
                        formatted_cards = []
                        player_cards_for_eval = []
                        for card in player.hand:
                            if hasattr(card, 'to_dict'):
                                formatted_cards.append(card.to_dict())
                            elif isinstance(card, dict):
                                formatted_cards.append(card)
                            else:
                                formatted_cards.append({'rank': str(card), 'suit': ''})

                            # Prepare cards for hand evaluation
                            if isinstance(card, Card):
                                player_cards_for_eval.append(card)
                            elif isinstance(card, dict):
                                player_cards_for_eval.append(Card(card['rank'], card['suit']))

                        # Evaluate hand to get hand name, rank, and kickers
                        try:
                            full_hand = player_cards_for_eval + community_cards_for_eval
                            hand_result = HandEvaluator(full_hand).evaluate_hand()

                            # Convert kicker values to readable card names
                            kicker_values = hand_result.get('kicker_values', [])
                            # Flatten if nested (some hands return nested lists)
                            if kicker_values and isinstance(kicker_values[0], list):
                                kicker_values = kicker_values[0] if kicker_values[0] else []

                            value_names = {14: 'A', 13: 'K', 12: 'Q', 11: 'J', 10: '10',
                                           9: '9', 8: '8', 7: '7', 6: '6', 5: '5',
                                           4: '4', 3: '3', 2: '2'}
                            kicker_names = [value_names.get(v, str(v)) for v in kicker_values if isinstance(v, int)]

                            players_showdown[player.name] = {
                                'cards': formatted_cards,
                                'hand_name': hand_result.get('hand_name', 'Unknown'),
                                'hand_rank': hand_result.get('hand_rank', 10),  # Lower is better (1=Royal Flush)
                                'kickers': kicker_names  # Readable kicker card names
                            }
                        except Exception as e:
                            logger.warning(f"Failed to evaluate hand for {player.name}: {e}")
                            players_showdown[player.name] = {
                                'cards': formatted_cards,
                                'hand_name': None,
                                'hand_rank': 99,
                                'kickers': []
                            }

                winner_data['players_showdown'] = players_showdown

            if is_showdown:
                message_content = (
                    f"{winning_players_string} won the pot of ${winner_info['winnings']} with {winner_info['hand_name']}. "
                    f"Winning hand: {winner_info['winning_hand']}"
                )
            else:
                message_content = f"{winning_players_string} won the pot of ${winner_info['winnings']}."
            
            send_message(game_id, "Table", message_content, "table", 1)

            # Emit winner announcement event
            socketio.emit('winner_announcement', winner_data, to=game_id)

            # Generate AI commentary (parallel LLM calls for all AI players)
            # This runs after winner is displayed but before next hand starts
            if 'memory_manager' in current_game_data:
                memory_manager = current_game_data['memory_manager']
                ai_controllers = current_game_data.get('ai_controllers', {})
                ai_players = {
                    name: controller.ai_player
                    for name, controller in ai_controllers.items()
                }

                try:
                    logger.info(f"[Commentary] Starting generation for {len(ai_players)} AI players")
                    commentaries = memory_manager.generate_commentary_for_hand(ai_players)
                    logger.info(f"[Commentary] Generated {len(commentaries)} commentaries")

                    # Send AI commentary as chat messages
                    for player_name, commentary in commentaries.items():
                        if commentary and commentary.table_comment:
                            logger.info(f"[Commentary] {player_name}: {commentary.table_comment[:80]}...")
                            send_message(game_id, player_name, commentary.table_comment, "ai")

                    # Apply learned adjustments to AI personalities
                    for name, controller in ai_controllers.items():
                        if hasattr(controller, 'ai_player') and hasattr(controller.ai_player, 'elastic_personality'):
                            memory_manager.apply_learned_adjustments(
                                name,
                                controller.ai_player.elastic_personality
                            )
                except Exception as e:
                    logger.warning(f"Commentary generation failed: {e}")

            # Delay before dealing new hand
            socketio.sleep(4 if is_showdown else 2)
            send_message(game_id, "Table", "***   NEW HAND DEALT   ***", "table")

            # Start recording the new hand in memory manager
            if 'memory_manager' in current_game_data:
                memory_manager = current_game_data['memory_manager']
                new_hand_number = memory_manager.hand_count + 1
                memory_manager.on_hand_start(game_state, hand_number=new_hand_number)

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
    # Capture highest_bet before play_turn modifies state
    highest_bet = state_machine.game_state.highest_bet
    game_state = play_turn(state_machine.game_state, action, amount)

    # Generate a message to be added to the game table
    table_message_content = format_action_message(current_player.name, action, amount, highest_bet)
    send_message(game_id, "Table", table_message_content, "table")

    # Record action in memory manager
    record_action_in_memory(current_game_data, current_player.name, action, amount, game_state, state_machine)

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

        # Subtle notification that we're using fallback
        send_message(game_id, "Table",
                    f"[{current_player.name} takes a moment to consider]",
                    "table")

    # Build action text (capture highest_bet before play_turn modifies state)
    highest_bet = state_machine.game_state.highest_bet
    action_text = format_action_message(current_player.name, action, amount, highest_bet)

    # Send AI message with action included, or just table message if no chat
    if player_message and player_message != '...':
        full_message = f"{player_message} {player_physical_description}".strip()
        # Combined message: action + chat in one (action shown in floating bubble)
        send_message(game_id, current_player.name, full_message, "ai", sleep=1, action=action_text)
    else:
        # No chat, just send the action as a table message
        send_message(game_id, "Table", action_text, "table")

    # Detect pressure events based on AI action
    if action == 'fold':
        detect_and_apply_pressure(game_id, 'fold', player_name=current_player.name)
    elif action in ['raise', 'all_in'] and amount > 0:
        detect_and_apply_pressure(game_id, 'big_bet', player_name=current_player.name, bet_size=amount)

    game_state = play_turn(state_machine.game_state, action, amount)

    # Record action in memory manager
    record_action_in_memory(current_game_data, current_player.name, action, amount, game_state, state_machine)

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

def send_message(game_id: str, sender: str, content: str, message_type: str,
                 sleep: Optional[int] = None, action: Optional[str] = None) -> None:
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
    :param action: (Optional[str])
        Optional action text to include with AI messages (e.g., "raised to $50").
    :return: (None)
        None
    """
    game_data = games.get(game_id)
    if not game_data:
        return
    game_messages = game_data['messages']
    new_message = {
        "id": str(uuid.uuid4()),
        "sender": sender,
        "content": content,
        "timestamp": datetime.now().strftime("%H:%M %b %d %Y"),
        "message_type": message_type
    }
    # Include action for AI messages (shown in floating bubble)
    if action:
        new_message["action"] = action
    game_messages.append(new_message)

    # Update the messages session state
    game_data['messages'] = game_messages
    games[game_id] = game_data

    # Save message to database
    persistence.save_message(game_id, message_type, f"{sender}: {content}")
    # Emit only the new message to reduce payload size
    socketio.emit('new_message', {'message': new_message}, to=game_id)
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


@app.route('/api/game/<game_id>/memory-debug', methods=['GET'])
def get_memory_debug(game_id):
    """Get current memory state for debugging - shows if AI memory system is working."""
    game_data = games.get(game_id)
    if not game_data:
        return jsonify({'error': 'Game not found'}), 404

    if 'memory_manager' not in game_data:
        return jsonify({'error': 'Memory manager not initialized', 'working': False}), 200

    memory_manager = game_data['memory_manager']

    # Build debug info
    debug_info = {
        'working': True,
        'game_id': memory_manager.game_id,
        'hand_count': memory_manager.hand_count,
        'initialized_players': list(memory_manager.initialized_players),
        'session_memories': {},
        'opponent_models': {},
        'current_hand': None,
        'completed_hands_count': len(memory_manager.hand_recorder.completed_hands)
    }

    # Session memory info
    for player_name, session in memory_manager.session_memories.items():
        debug_info['session_memories'][player_name] = {
            'hands_played': session.context.hands_played,
            'hands_won': session.context.hands_won,
            'current_streak': session.context.current_streak,
            'streak_count': session.context.streak_count,
            'total_winnings': session.context.total_winnings,
            'hand_memories_count': len(session.hand_memories),
            'context_preview': session.get_context_for_prompt(100)[:200] if session.hand_memories else 'No hands yet'
        }

    # Opponent model info
    all_models = memory_manager.opponent_model_manager.models
    for observer, targets in all_models.items():
        debug_info['opponent_models'][observer] = {}
        for target, model in targets.items():
            debug_info['opponent_models'][observer][target] = {
                'hands_observed': model.tendencies.hands_observed,
                'vpip': round(model.tendencies.vpip, 2),
                'pfr': round(model.tendencies.pfr, 2),
                'aggression_factor': round(model.tendencies.aggression_factor, 2),
                'play_style': model.tendencies.get_play_style_label(),
                'summary': model.get_prompt_summary()
            }

    # Current hand in progress
    if memory_manager.hand_recorder.current_hand:
        current = memory_manager.hand_recorder.current_hand
        debug_info['current_hand'] = {
            'hand_number': current.hand_number,
            'actions_recorded': len(current.actions),
            'phase': current.actions[-1].phase if current.actions else 'PRE_FLOP'
        }

    return jsonify(debug_info)


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
            ai_model=FAST_AI_MODEL,
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


@app.route('/api/game/<game_id>/targeted-chat-suggestions', methods=['POST'])
@limiter.limit(os.environ.get('RATE_LIMIT_CHAT_SUGGESTIONS', '100 per hour'))
def get_targeted_chat_suggestions(game_id):
    """Generate targeted chat suggestions to engage specific AI players."""
    if game_id not in games:
        return jsonify({"error": "Game not found"}), 404

    try:
        data = request.get_json()
        game_data = games[game_id]
        state_machine = game_data['state_machine']
        game_state = state_machine.game_state

        # Get request parameters
        player_name = data.get('playerName', 'Player')
        target_player = data.get('targetPlayer')  # None = table talk
        tone = data.get('tone', 'encourage')

        # Define tone descriptions for the prompt
        tone_descriptions = {
            'encourage': 'supportive, friendly, complimentary about their play',
            'antagonize': 'playful trash talk, teasing, challenging their decisions (keep it fun, not mean)',
            'confuse': 'random non-sequiturs, weird observations, misdirection to throw them off',
            'flatter': 'over-the-top compliments, acknowledge their skill, be impressed',
            'challenge': 'direct dares, betting challenges, call them out to make a move'
        }

        tone_desc = tone_descriptions.get(tone, tone_descriptions['encourage'])

        # Build game context
        context_parts = []
        context_parts.append(f"Game phase: {str(state_machine.current_phase).split('.')[-1]}")
        context_parts.append(f"Pot size: ${game_state.pot['total']}")

        # Get last action if provided
        last_action = data.get('lastAction')
        if last_action:
            action_text = f"{last_action.get('player', 'Someone')} just {last_action.get('type', 'acted')}"
            if last_action.get('amount'):
                action_text += f" ${last_action['amount']}"
            context_parts.append(action_text)

        context_str = ". ".join(context_parts)

        # Build chat context from server-side messages (last 10)
        game_messages = game_data.get('messages', [])[-10:]
        chat_context = ""
        if game_messages:
            chat_lines = []
            for msg in game_messages:
                sender = msg.get('sender', 'Unknown')
                text = msg.get('content', msg.get('message', ''))[:100]
                if text:
                    chat_lines.append(f"- {sender}: {text}")
            if chat_lines:
                chat_context = "\nRecent table talk:\n" + "\n".join(chat_lines)

        # Build game situation using opponent_status (same as AI player decisions)
        game_situation = "\n".join(game_state.opponent_status)

        # Add board if available
        if game_state.community_cards:
            cards = [str(c) for c in game_state.community_cards]
            game_situation = f"Board: {', '.join(cards)}\n" + game_situation

        # Load target personality if targeting specific player
        target_context = ""
        if target_player:
            # Try to load personality from file
            try:
                personalities_file = Path(__file__).parent.parent / 'poker' / 'personalities.json'
                with open(personalities_file, 'r') as f:
                    personalities_data = json.load(f)

                if target_player in personalities_data.get('personalities', {}):
                    personality = personalities_data['personalities'][target_player]
                    play_style = personality.get('play_style', 'unknown')
                    verbal_tics = personality.get('verbal_tics', [])[:3]  # First 3 tics
                    attitude = personality.get('default_attitude', 'neutral')

                    target_context = f"""
Target player: {target_player}
Their personality: {play_style}
Their attitude: {attitude}
Their catchphrases: {', '.join(verbal_tics) if verbal_tics else 'none known'}"""
            except Exception as e:
                logger.warning(f"Could not load personality for {target_player}: {e}")
                target_context = f"\nTarget player: {target_player}"

        # Build the prompt
        if target_player:
            # Get first name for more natural addressing
            target_first_name = target_player.split()[0] if target_player else "them"
            prompt = f"""Generate exactly 2 short poker table chat messages for player "{player_name}" to say directly to {target_player}.
{target_context}

Tone: {tone_desc}
Game context: {context_str}
Table situation:
{game_situation}
{chat_context}

Requirements:
- Each message should be 5-15 words
- IMPORTANT: Include "{target_first_name}" or "{target_player}" in each message to make it clear who you're addressing
- Match the {tone} tone perfectly
- Reference the board, stacks, or recent conversation when relevant
- If you know their personality, play off their quirks
- Be playful but not offensive or mean-spirited
- Messages should feel natural for poker table banter

Example formats: "Hey {target_first_name}, ...", "{target_first_name}, you really think...", "What's the matter {target_first_name}..."

Return as JSON:
{{
    "suggestions": [
        {{"text": "message here", "tone": "{tone}"}},
        {{"text": "message here", "tone": "{tone}"}}
    ],
    "targetPlayer": "{target_player}"
}}"""
        else:
            # General table talk
            prompt = f"""Generate exactly 2 short poker table chat messages to announce to the whole table.

Tone: {tone_desc}
Game context: {context_str}
Table situation:
{game_situation}
{chat_context}

Requirements:
- Each message should be 5-15 words
- Write in FIRST PERSON - these are things the player will say directly
- Do NOT include the speaker's name - they are saying this themselves
- Match the {tone} tone perfectly
- Reference the board, stacks, or recent conversation when relevant
- General table talk, not directed at anyone specific
- Be playful and engaging
- Messages should feel natural for poker table banter

Good examples: "Anyone else feeling lucky tonight?", "This pot is getting interesting!", "That ace on the turn changes everything!"
Bad examples: "Jeff says he's feeling lucky" (don't use 3rd person), "Player announces confidence" (don't narrate)

Return as JSON:
{{
    "suggestions": [
        {{"text": "message here", "tone": "{tone}"}},
        {{"text": "message here", "tone": "{tone}"}}
    ],
    "targetPlayer": null
}}"""

        # Check if OpenAI API key is available
        if not os.environ.get("OPENAI_API_KEY"):
            logger.warning("No OpenAI API key found, returning fallback suggestions")
            raise ValueError("OpenAI API key not configured")

        # Debug logging
        logger.info(f"[QuickChat] Target: {target_player}, Tone: {tone}")
        logger.info(f"[QuickChat] Prompt: {prompt[:500]}...")

        # Use the OpenAI assistant - minimal reasoning for fast responses
        assistant = OpenAILLMAssistant(
            ai_model=FAST_AI_MODEL,
            reasoning_effort="minimal",
            system_message="You are a witty poker player helping generate fun table talk. Keep it light and entertaining."
        )

        messages = [
            {"role": "system", "content": assistant.system_message},
            {"role": "user", "content": prompt}
        ]

        response = assistant.get_json_response(messages)
        raw_content = response.choices[0].message.content
        logger.info(f"[QuickChat] Raw response: {raw_content}")
        result = json.loads(raw_content)

        return jsonify(result)

    except Exception as e:
        logger.error(f"[QuickChat] ERROR generating suggestions: {str(e)}")
        logger.exception("[QuickChat] Full traceback:")
        # Return fallback suggestions with error flag
        target = data.get('targetPlayer') if 'data' in dir() else None
        fallback_messages = {
            'encourage': ["Nice hand!", "Good play there!"],
            'antagonize': ["You sure about that?", "Interesting choice..."],
            'confuse': ["Did anyone else hear that?", "The cards speak to me."],
            'flatter': ["Impressive as always!", "You're too good!"],
            'challenge': ["Prove it!", "Show me what you got!"]
        }
        tone = data.get('tone', 'encourage') if 'data' in dir() else 'encourage'
        msgs = fallback_messages.get(tone, fallback_messages['encourage'])

        return jsonify({
            "suggestions": [
                {"text": msgs[0], "tone": tone},
                {"text": msgs[1], "tone": tone}
            ],
            "targetPlayer": target,
            "error": str(e),
            "fallback": True
        })


@app.route('/api/game/<game_id>/pressure-stats', methods=['GET'])
def get_pressure_stats(game_id):
    """Get pressure event statistics for the game."""
    game_data = games.get(game_id)
    if not game_data or 'pressure_stats' not in game_data:
        return jsonify({'error': 'Game not found or stats not available'}), 404
    
    pressure_stats = game_data['pressure_stats']
    return jsonify(pressure_stats.get_session_summary())


# Model configuration routes
@app.route('/api/models', methods=['GET'])
def get_available_models():
    """Get available OpenAI models for game configuration."""
    try:
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        models = client.models.list()
        # Filter to relevant models (GPT-5 and GPT-4o variants)
        available = [m.id for m in models.data if m.id.startswith(('gpt-5', 'gpt-4o'))]
        return jsonify({
            'success': True,
            'models': sorted(available),
            'default_model': 'gpt-5-nano',
            'reasoning_levels': ['minimal', 'low', 'medium', 'high'],
            'default_reasoning': 'low'
        })
    except Exception as e:
        logger.error(f"Error fetching models: {e}")
        # Return defaults on error
        return jsonify({
            'success': True,
            'models': ['gpt-5-nano', 'gpt-5-mini', 'gpt-5'],
            'default_model': 'gpt-5-nano',
            'reasoning_levels': ['minimal', 'low', 'medium', 'high'],
            'default_reasoning': 'low'
        })


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
            system_message="You are a game designer selecting personalities for themed poker games.",
            ai_model=FAST_AI_MODEL
        )

        messages = [
            {"role": "system", "content": assistant.system_message},
            {"role": "user", "content": prompt}
        ]
        response = assistant.get_response(messages)
        response_content = response.choices[0].message.content or ""

        # Parse the response
        import json
        try:
            # Clean up the response in case it has extra text
            response_text = response_content.strip()
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
                logger.warning(f"Theme generation returned insufficient valid personalities ({len(valid_personalities)}), using random fallback")
                valid_personalities = random.sample(personality_sample, min(4, len(personality_sample)))

            return jsonify({
                'success': True,
                'personalities': valid_personalities[:5]  # Max 5 AI players
            })

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse theme generation response: {e}. Response was: {response_content}")
            logger.warning("Theme generation using random fallback due to JSON parse error")
            personalities = random.sample(personality_sample, min(4, len(personality_sample)))
            return jsonify({
                'success': True,
                'personalities': personalities,
                'fallback': True
            })
            
    except Exception as e:
        logger.error(f"Error generating theme: {e}")
        logger.warning("Theme generation using random fallback due to exception")
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
