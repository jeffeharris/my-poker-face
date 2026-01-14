"""Game-related routes and socket events."""

import time
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict

from flask import Blueprint, jsonify, request, redirect, send_from_directory
from flask_socketio import join_room

from poker.controllers import AIPlayerController
from poker.poker_game import initialize_game_state, play_turn, advance_to_next_active_player
from poker.poker_state_machine import PokerStateMachine, PokerPhase
from poker.utils import get_celebrities
from poker.elasticity_manager import ElasticityManager
from poker.tilt_modifier import TiltState
from poker.emotional_state import EmotionalState
from poker.pressure_detector import PressureEventDetector
from poker.pressure_stats import PressureStatsTracker
from poker.memory import AIMemoryManager
from poker.memory.opponent_model import OpponentModelManager
from poker.tournament_tracker import TournamentTracker
from poker.character_images import get_avatar_url

from ..game_adapter import StateMachineAdapter
from ..extensions import socketio, persistence, auth_manager, limiter
from ..services import game_state_service
from ..services.elasticity_service import format_elasticity_data
from ..handlers.game_handler import (
    progress_game, update_and_emit_game_state, restore_ai_controllers
)
from ..handlers.message_handler import (
    send_message, format_action_message, record_action_in_memory, format_messages_for_api
)
from ..handlers.avatar_handler import start_background_avatar_generation
from .. import config
from core.llm import AVAILABLE_PROVIDERS, PROVIDER_MODELS

logger = logging.getLogger(__name__)

game_bp = Blueprint('game', __name__)


def analyze_player_decision(
    game_id: str,
    player_name: str,
    action: str,
    amount: int,
    state_machine,
    game_state,
    hand_number: int = None,
    memory_manager=None
) -> None:
    """Analyze a player decision (human or AI) and save to database.

    This tracks decision quality for ALL players, not just AI.
    """
    try:
        from poker.decision_analyzer import get_analyzer

        player = game_state.current_player
        if player.name != player_name:
            # Find the player who acted (may have moved to next player already)
            player = next((p for p in game_state.players if p.name == player_name), None)
            if not player:
                return

        # Get cards in format equity calculator understands
        def card_to_string(c):
            """Convert card (dict or Card object) to short string like '8h'."""
            if isinstance(c, dict):
                rank = c.get('rank', '')
                suit = c.get('suit', '')[0].lower() if c.get('suit') else ''
                if rank == '10':
                    rank = 'T'
                return f"{rank}{suit}"
            else:
                s = str(c)
                suit_map = {'♠': 's', '♥': 'h', '♦': 'd', '♣': 'c'}
                for symbol, letter in suit_map.items():
                    s = s.replace(symbol, letter)
                s = s.replace('10', 'T')
                return s

        community_cards = [card_to_string(c) for c in game_state.community_cards] if game_state.community_cards else []
        player_hand = [card_to_string(c) for c in player.hand] if player.hand else []

        # Count opponents still in hand
        opponents_in_hand = [
            p for p in game_state.players
            if not p.is_folded and p.name != player_name
        ]
        num_opponents = len(opponents_in_hand)

        # Get positions for range-based equity calculation
        table_positions = game_state.table_positions
        position_by_name = {name: pos for pos, name in table_positions.items()}
        player_position = position_by_name.get(player_name)
        opponent_positions = [
            position_by_name.get(p.name, "button")  # Default to button (widest range) if unknown
            for p in opponents_in_hand
        ]

        # Build OpponentInfo objects with observed stats and personality data
        from poker.hand_ranges import build_opponent_info
        opponent_infos = []
        opponent_model_manager = memory_manager.get_opponent_model_manager() if memory_manager else None

        for opp in opponents_in_hand:
            opp_position = position_by_name.get(opp.name, "button")

            # Get observed stats from opponent model manager
            opp_model_data = None
            if opponent_model_manager:
                opp_model = opponent_model_manager.get_model(player_name, opp.name)
                if opp_model and opp_model.tendencies:
                    opp_model_data = opp_model.tendencies.to_dict()

            opponent_infos.append(build_opponent_info(
                name=opp.name,
                position=opp_position,
                opponent_model=opp_model_data,
            ))

        # Calculate effective cost to call (capped at player's stack)
        raw_cost_to_call = max(0, game_state.highest_bet - player.bet)
        cost_to_call = min(raw_cost_to_call, player.stack)

        analyzer = get_analyzer()
        analysis = analyzer.analyze(
            game_id=game_id,
            player_name=player_name,
            hand_number=hand_number,
            phase=state_machine.current_phase.name if state_machine.current_phase else None,
            player_hand=player_hand,
            community_cards=community_cards,
            pot_total=game_state.pot.get('total', 0),
            cost_to_call=cost_to_call,
            player_stack=player.stack,
            num_opponents=num_opponents,
            action_taken=action,
            raise_amount=amount if action == 'raise' else None,
            player_position=player_position,
            opponent_positions=opponent_positions,
            opponent_infos=opponent_infos,
        )

        persistence.save_decision_analysis(analysis)
        equity_str = f"{analysis.equity:.2f}" if analysis.equity is not None else "N/A"
        logger.debug(
            f"[DECISION_ANALYSIS] {player_name}: {analysis.decision_quality} "
            f"(equity={equity_str}, ev_lost={analysis.ev_lost:.0f})"
        )
    except Exception as e:
        logger.warning(f"[DECISION_ANALYSIS] Failed to analyze decision for {player_name}: {e}")


def generate_game_id() -> str:
    """Generate a unique game ID based on current timestamp."""
    return str(int(time.time() * 1000))


@game_bp.route('/api/games')
def list_games():
    """List games for the current user."""
    current_user = auth_manager.get_current_user()

    if current_user:
        saved_games = persistence.list_games(owner_id=current_user.get('id'), limit=10)
    else:
        saved_games = []

    games_data = []
    for game in saved_games:
        try:
            state = json.loads(game.game_state_json)
            players = state.get('players', [])
            player_names = [p['name'] for p in players]
            total_players = len(players)
            active_players = sum(1 for p in players if p.get('stack', 0) > 0)

            human_stack = None
            for p in players:
                if p.get('is_human', False):
                    human_stack = p.get('stack', 0)
                    break

            big_blind = state.get('current_ante', 20)
        except:
            player_names = []
            total_players = game.num_players
            active_players = game.num_players
            human_stack = None
            big_blind = 20

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
            'is_owner': True,
            'active_players': active_players,
            'total_players': total_players,
            'human_stack': human_stack,
            'big_blind': big_blind
        })

    return jsonify({'games': games_data})


@game_bp.route('/api/game-state/<game_id>')
def api_game_state(game_id):
    """API endpoint to get current game state for React app."""
    current_game_data = game_state_service.get_game(game_id)

    # Auto-advance cached games that are stuck in non-action phases
    if current_game_data:
        state_machine = current_game_data['state_machine']
        if not state_machine.game_state.awaiting_action and not current_game_data.get('game_started', False):
            print(f"[CACHE] Auto-advancing cached game {game_id}, phase: {state_machine.current_phase}")
            current_game_data['game_started'] = True
            progress_game(game_id)

    if not current_game_data:
        # Try to load from database
        try:
            current_user = auth_manager.get_current_user()
            saved_games = persistence.list_games(owner_id=current_user.get('id') if current_user else None, limit=50)

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
                # Load per-player LLM configs for proper provider restoration
                llm_configs = persistence.load_llm_configs(game_id) or {}
                ai_controllers = restore_ai_controllers(
                    game_id, state_machine, persistence,
                    owner_id=owner_id,
                    player_llm_configs=llm_configs.get('player_llm_configs'),
                    default_llm_config=llm_configs.get('default_llm_config')
                )
                db_messages = persistence.load_messages(game_id)

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

                memory_manager = AIMemoryManager(game_id, persistence.db_path, owner_id=owner_id)
                memory_manager.set_persistence(persistence)  # Enable hand history saving

                # Restore hand count from database
                restored_hand_count = persistence.get_hand_count(game_id)
                if restored_hand_count > 0:
                    memory_manager.hand_count = restored_hand_count
                    logger.info(f"[LOAD] Restored hand count: {restored_hand_count} for game {game_id}")

                # Restore opponent models from database
                saved_opponent_models = persistence.load_opponent_models(game_id)
                if saved_opponent_models:
                    memory_manager.opponent_model_manager = OpponentModelManager.from_dict(saved_opponent_models)
                    logger.info(f"[LOAD] Restored opponent models for game {game_id}")

                for player in state_machine.game_state.players:
                    if not player.is_human and player.name in ai_controllers:
                        memory_manager.initialize_for_player(player.name)
                        controller = ai_controllers[player.name]
                        controller.session_memory = memory_manager.get_session_memory(player.name)
                        controller.opponent_model_manager = memory_manager.get_opponent_model_manager()
                    elif player.is_human:
                        # Initialize human player for opponent observation tracking
                        memory_manager.initialize_human_observer(player.name)

                # Restore debug capture state from database
                debug_capture_enabled = persistence.get_debug_capture_enabled(game_id)
                if debug_capture_enabled:
                    for controller in ai_controllers.values():
                        if hasattr(controller, 'debug_capture'):
                            controller.debug_capture = True
                            controller._persistence = persistence
                    logger.info(f"[LOAD] Restored debug capture mode (enabled) for game {game_id}")

                memory_manager.on_hand_start(state_machine.game_state, hand_number=memory_manager.hand_count + 1)

                # Try to load tournament tracker from database, or create new one
                tracker_data = persistence.load_tournament_tracker(game_id)
                if tracker_data:
                    tournament_tracker = TournamentTracker.from_dict(tracker_data)
                    logger.info(f"[LOAD] Restored tournament tracker with {len(tournament_tracker.eliminations)} eliminations")
                else:
                    # Fallback: create new tracker with current players
                    starting_players = [
                        {'name': p.name, 'is_human': p.is_human}
                        for p in state_machine.game_state.players
                    ]
                    tournament_tracker = TournamentTracker(
                        game_id=game_id,
                        starting_players=starting_players
                    )
                    tournament_tracker.hand_count = memory_manager.hand_count

                current_game_data = {
                    'state_machine': state_machine,
                    'ai_controllers': ai_controllers,
                    'elasticity_manager': elasticity_manager,
                    'pressure_detector': pressure_detector,
                    'pressure_stats': pressure_stats,
                    'memory_manager': memory_manager,
                    'tournament_tracker': tournament_tracker,
                    'owner_id': owner_id,
                    'owner_name': owner_name,
                    'messages': db_messages,
                    'game_started': True
                }
                game_state_service.set_game(game_id, current_game_data)

                game_state = state_machine.game_state
                current_player = game_state.current_player
                print(f"[LOAD] Game {game_id} loaded. Phase: {state_machine.current_phase}, "
                      f"awaiting_action: {game_state.awaiting_action}, "
                      f"current_player: {current_player.name} (human: {current_player.is_human})")

                if not game_state.awaiting_action:
                    print(f"[LOAD] Auto-advancing game {game_id} (not awaiting action)")
                    progress_game(game_id)
                elif game_state.awaiting_action and not current_player.is_human:
                    print(f"[LOAD] Resuming AI turn for {current_player.name} in game {game_id}")
                    progress_game(game_id)
            else:
                return jsonify({'error': 'Game not found'}), 404
        except Exception as e:
            print(f"Error loading game {game_id}: {str(e)}")
            import traceback
            traceback.print_exc()
            return jsonify({
                'error': 'Game loading is currently unavailable',
                'message': 'This feature is under development. Please start a new game.',
                'players': []
            }), 200

    state_machine = current_game_data['state_machine']
    game_state = state_machine.game_state

    ai_controllers = current_game_data.get('ai_controllers', {})
    players = []
    for player in game_state.players:
        if player.is_human and player.hand:
            hand = [card.to_dict() if hasattr(card, 'to_dict') else card for card in player.hand]
        else:
            hand = None

        avatar_url = None
        avatar_emotion = None
        if not player.is_human and player.name in ai_controllers:
            controller = ai_controllers[player.name]
            emotional_state = getattr(controller, 'emotional_state', None)
            if emotional_state:
                avatar_emotion = emotional_state.get_display_emotion()
            else:
                avatar_emotion = 'confident'
            avatar_url = get_avatar_url(player.name, avatar_emotion)

        players.append({
            'name': player.name,
            'stack': player.stack,
            'bet': player.bet,
            'is_folded': player.is_folded,
            'is_all_in': player.is_all_in,
            'is_human': player.is_human,
            'hand': hand,
            'avatar_url': avatar_url,
            'avatar_emotion': avatar_emotion
        })

    community_cards = [card.to_dict() if hasattr(card, 'to_dict') else card for card in game_state.community_cards]
    messages = format_messages_for_api(current_game_data.get('messages', []))

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
        'min_raise': game_state.min_raise_amount,
        'big_blind': game_state.current_ante,
        'messages': messages,
        'game_id': game_id
    }

    return jsonify(response)


def _get_db_path() -> str:
    """Get the database path based on environment."""
    if Path('/app/data').exists():
        return '/app/data/poker_games.db'
    return str(Path(__file__).parent.parent.parent / 'poker_games.db')


def get_model_cost_tiers() -> Dict[str, Dict[str, str]]:
    """Calculate cost tiers for all models from pricing database.

    Tiers are based on output_tokens_1m cost:
    - free: <= $0.10
    - $: < $1.00
    - $$: $1.00 - $5.00
    - $$$: $5.00 - $20.00
    - $$$$: > $20.00

    Returns:
        Dict mapping provider -> model -> tier string
    """
    tiers: Dict[str, Dict[str, str]] = {}

    # Model aliases: UI name -> pricing table name(s)
    # Used when UI model names differ from actual API model names
    model_aliases = {
        'xai': {
            'grok-4-fast': 'grok-4-fast-reasoning',  # Maps to same price as non-reasoning
        }
    }

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            cursor = conn.execute("""
                SELECT provider, model, cost FROM model_pricing
                WHERE unit = 'output_tokens_1m'
                  AND (valid_from IS NULL OR valid_from <= datetime('now'))
                  AND (valid_until IS NULL OR valid_until > datetime('now'))
            """)

            for provider, model, cost in cursor:
                if provider not in tiers:
                    tiers[provider] = {}

                # Calculate tier based on output cost thresholds
                if cost <= 0.10:
                    tier = "free"
                elif cost < 1.00:
                    tier = "$"
                elif cost <= 5.00:
                    tier = "$$"
                elif cost <= 20.00:
                    tier = "$$$"
                else:
                    tier = "$$$$"

                tiers[provider][model] = tier

            # Apply model aliases: copy tier from pricing model to UI model name
            for provider, aliases in model_aliases.items():
                if provider in tiers:
                    for ui_model, pricing_model in aliases.items():
                        if pricing_model in tiers[provider]:
                            tiers[provider][ui_model] = tiers[provider][pricing_model]

    except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
        logger.warning(f"Failed to load model pricing for tiers: {e}")

    return tiers


@game_bp.route('/api/llm-providers', methods=['GET'])
def api_llm_providers():
    """Get available LLM providers and their models for game configuration."""
    from core.llm import (
        AVAILABLE_PROVIDERS,
        PROVIDER_MODELS,
        PROVIDER_DEFAULT_MODELS,
        PROVIDER_CAPABILITIES,
    )

    # Get cost tiers from pricing database
    model_tiers = get_model_cost_tiers()

    # Get enabled models from database (if table exists)
    enabled_models = _get_enabled_models_map()

    providers = []
    for provider in AVAILABLE_PROVIDERS:
        all_models = PROVIDER_MODELS.get(provider, [])

        # Filter by enabled models if we have the table
        if enabled_models:
            models = [m for m in all_models if enabled_models.get((provider, m), True)]
        else:
            models = all_models

        # Skip providers with no enabled models
        if not models:
            continue

        # Adjust default model if it's been disabled
        default_model = PROVIDER_DEFAULT_MODELS.get(provider)
        if default_model not in models and models:
            default_model = models[0]

        providers.append({
            'id': provider,
            'name': provider.title(),
            'models': models,
            'default_model': default_model,
            'capabilities': PROVIDER_CAPABILITIES.get(provider, {}),
            'model_tiers': model_tiers.get(provider, {}),
        })

    return jsonify({
        'providers': providers,
        'default_provider': 'openai',
    })


def _get_enabled_models_map():
    """Get a map of (provider, model) -> enabled status.

    Returns empty dict if enabled_models table doesn't exist yet.
    """
    import sqlite3
    from pathlib import Path

    db_path = '/app/data/poker_games.db' if Path('/app/data').exists() else str(Path(__file__).parent.parent.parent / 'poker_games.db')

    try:
        with sqlite3.connect(db_path) as conn:
            # Check if table exists
            cursor = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='enabled_models'
            """)
            if not cursor.fetchone():
                return {}

            cursor = conn.execute("""
                SELECT provider, model, enabled FROM enabled_models
            """)
            return {(row[0], row[1]): bool(row[2]) for row in cursor.fetchall()}
    except Exception:
        return {}


@game_bp.route('/api/new-game', methods=['POST'])
@limiter.limit(config.RATE_LIMIT_NEW_GAME)
def api_new_game():
    """Create a new game and return the game ID."""
    data = request.json or {}

    current_user = auth_manager.get_current_user()
    if current_user:
        player_name = data.get('playerName', current_user.get('name', 'Player'))
        owner_id = current_user.get('id')
        owner_name = current_user.get('name')

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

    requested_personalities = data.get('personalities', [])
    default_llm_config = data.get('llm_config', {})
    starting_stack = data.get('starting_stack', 10000)
    big_blind = data.get('big_blind', 50)
    blind_growth = data.get('blind_growth', 1.5)
    blinds_increase = data.get('blinds_increase', 6)
    max_blind = data.get('max_blind', 0)  # 0 = no limit

    # Validate default LLM config if provided
    if default_llm_config:
        default_provider = default_llm_config.get('provider', 'openai').lower()
        if default_provider not in AVAILABLE_PROVIDERS:
            return jsonify({'error': f'Invalid default provider: {default_provider}'}), 400
        default_model = default_llm_config.get('model')
        if default_model and default_model not in PROVIDER_MODELS.get(default_provider, []):
            return jsonify({'error': f'Invalid default model {default_model} for provider {default_provider}'}), 400

    # Validate: ensure starting stack is at least 10x big blind
    if starting_stack < big_blind * 10:
        starting_stack = big_blind * 10

    # Parse personalities - supports both string names and objects with llm_config
    # Format: ["Batman", {"name": "Sherlock", "llm_config": {"provider": "groq"}}]
    ai_player_names = []
    player_llm_configs = {}  # Map of player_name -> llm_config

    if requested_personalities:
        for p in requested_personalities:
            if isinstance(p, str):
                # Simple string name - uses default llm_config
                ai_player_names.append(p)
            elif isinstance(p, dict):
                # Object with name and optional llm_config
                name = p.get('name')
                if name:
                    ai_player_names.append(name)
                    if 'llm_config' in p:
                        # Validate per-player LLM config before merging
                        p_llm_config = p['llm_config']
                        provider = p_llm_config.get('provider', 'openai').lower()
                        if provider not in AVAILABLE_PROVIDERS:
                            return jsonify({'error': f'Invalid provider: {provider}'}), 400
                        model = p_llm_config.get('model')
                        if model and model not in PROVIDER_MODELS.get(provider, []):
                            return jsonify({'error': f'Invalid model {model} for provider {provider}'}), 400
                        # Merge with default config (per-player overrides default)
                        player_llm_configs[name] = {**default_llm_config, **p_llm_config}
    else:
        ai_player_names = get_celebrities(shuffled=True)[:3]

    game_state = initialize_game_state(
        player_names=ai_player_names,
        human_name=player_name,
        starting_stack=starting_stack,
        big_blind=big_blind
    )

    # Blind escalation config
    blind_config = {
        'growth': blind_growth,
        'hands_per_level': blinds_increase,
        'max_blind': max_blind
    }
    base_state_machine = PokerStateMachine(game_state=game_state, blind_config=blind_config)
    state_machine = StateMachineAdapter(base_state_machine)

    # Generate game_id first so it can be passed to controllers for tracking
    game_id = generate_game_id()

    ai_controllers = {}
    elasticity_manager = ElasticityManager()

    for player in state_machine.game_state.players:
        if not player.is_human:
            # Use per-player config if set, otherwise use default
            player_config = player_llm_configs.get(player.name, default_llm_config)
            new_controller = AIPlayerController(
                player.name,
                state_machine,
                llm_config=player_config,
                game_id=game_id,
                owner_id=owner_id,
                persistence=persistence
            )
            ai_controllers[player.name] = new_controller
            elasticity_manager.add_player(
                player.name,
                new_controller.ai_player.personality_config
            )
    from poker.repositories.sqlite_repositories import PressureEventRepository
    event_repository = PressureEventRepository(config.DB_PATH)
    pressure_detector = PressureEventDetector(elasticity_manager)
    pressure_stats = PressureStatsTracker(game_id, event_repository)

    memory_manager = AIMemoryManager(game_id, persistence.db_path, owner_id=owner_id)
    memory_manager.set_persistence(persistence)  # Enable hand history saving
    for player in state_machine.game_state.players:
        if not player.is_human:
            memory_manager.initialize_for_player(player.name)
            controller = ai_controllers[player.name]
            controller.session_memory = memory_manager.get_session_memory(player.name)
            controller.opponent_model_manager = memory_manager.get_opponent_model_manager()
        else:
            # Initialize human player for opponent observation tracking
            memory_manager.initialize_human_observer(player.name)

    memory_manager.on_hand_start(state_machine.game_state, hand_number=1)

    starting_players = [
        {'name': p.name, 'is_human': p.is_human}
        for p in state_machine.game_state.players
    ]
    tournament_tracker = TournamentTracker(
        game_id=game_id,
        starting_players=starting_players
    )

    game_data = {
        'state_machine': state_machine,
        'ai_controllers': ai_controllers,
        'elasticity_manager': elasticity_manager,
        'pressure_detector': pressure_detector,
        'pressure_stats': pressure_stats,
        'memory_manager': memory_manager,
        'tournament_tracker': tournament_tracker,
        'owner_id': owner_id,
        'owner_name': owner_name,
        'llm_config': default_llm_config,  # Default config for new players
        'player_llm_configs': player_llm_configs,  # Per-player overrides
        'messages': [{
            'id': '1',
            'sender': 'System',
            'content': 'New game started! Good luck!',
            'timestamp': datetime.now().isoformat(),
            'type': 'system'
        }]
    }
    game_state_service.set_game(game_id, game_data)

    persistence.save_game(
        game_id, state_machine._state_machine, owner_id, owner_name,
        llm_configs={'player_llm_configs': player_llm_configs, 'default_llm_config': default_llm_config}
    )
    persistence.save_tournament_tracker(game_id, tournament_tracker)
    persistence.save_opponent_models(game_id, memory_manager.get_opponent_model_manager())
    start_background_avatar_generation(game_id, ai_player_names)

    return jsonify({'game_id': game_id})


@game_bp.route('/api/game/<game_id>/action', methods=['POST'])
@limiter.limit(config.RATE_LIMIT_GAME_ACTION)
def api_player_action(game_id):
    """Handle player action via API."""
    data = request.json
    action = data.get('action')
    amount = data.get('amount', 0)

    current_game_data = game_state_service.get_game(game_id)
    if not current_game_data:
        return jsonify({'error': 'Game not found'}), 404

    state_machine = current_game_data['state_machine']

    current_player = state_machine.game_state.current_player
    if not current_player.is_human:
        return jsonify({'error': 'Not human player turn'}), 400

    highest_bet = state_machine.game_state.highest_bet
    pre_action_state = state_machine.game_state  # Save state before action for analysis
    game_state = play_turn(state_machine.game_state, action, amount)

    # Analyze decision quality (works for both human and AI)
    memory_manager = current_game_data.get('memory_manager')
    hand_number = memory_manager.hand_count if memory_manager else None
    analyze_player_decision(game_id, current_player.name, action, amount, state_machine, pre_action_state, hand_number, memory_manager)

    record_action_in_memory(current_game_data, current_player.name, action, amount, game_state, state_machine)

    table_message_content = format_action_message(current_player.name, action, amount, highest_bet)
    send_message(game_id, "Table", table_message_content, "table")

    game_state = advance_to_next_active_player(game_state)
    state_machine.game_state = game_state

    current_game_data['state_machine'] = state_machine
    game_state_service.set_game(game_id, current_game_data)

    owner_id, owner_name = game_state_service.get_game_owner_info(game_id)
    persistence.save_game(game_id, state_machine._state_machine, owner_id, owner_name)
    if 'memory_manager' in current_game_data:
        persistence.save_opponent_models(game_id, current_game_data['memory_manager'].get_opponent_model_manager())

    progress_game(game_id)

    return jsonify({'success': True})


@game_bp.route('/api/game/<game_id>/message', methods=['POST'])
def api_send_message(game_id):
    """Send a chat message in the game."""
    data = request.json
    message = data.get('message', '')
    sender = data.get('sender', 'Player')

    if message.strip():
        send_message(game_id, sender, message.strip(), 'player')
        return jsonify({'success': True})

    return jsonify({'success': False, 'error': 'Empty message'})


@game_bp.route('/api/game/<game_id>/retry', methods=['POST'])
def api_retry_game(game_id):
    """Force-retry a hung game by re-triggering AI turns."""
    current_game_data = game_state_service.get_game(game_id)

    if not current_game_data:
        return jsonify({'error': 'Game not found in memory. Try refreshing the page first.'}), 404

    state_machine = current_game_data['state_machine']
    game_state = state_machine.game_state
    current_player = game_state.current_player

    diagnostic = {
        'game_id': game_id,
        'phase': str(state_machine.current_phase).split('.')[-1],
        'awaiting_action': game_state.awaiting_action,
        'current_player': current_player.name,
        'current_player_is_human': current_player.is_human,
        'current_player_is_folded': current_player.is_folded,
    }

    if current_player.is_human:
        return jsonify({
            'status': 'not_stuck',
            'message': 'Game is waiting for human player action',
            'diagnostic': diagnostic
        }), 200

    if not game_state.awaiting_action:
        return jsonify({
            'status': 'not_stuck',
            'message': 'Game is not awaiting action',
            'diagnostic': diagnostic
        }), 200

    current_game_data['game_started'] = False

    lock = game_state_service.game_locks.get(game_id)
    if lock and lock.locked():
        try:
            lock.release()
            logger.info(f"[RETRY] Released stuck lock for game {game_id}")
        except RuntimeError:
            pass

    logger.info(f"[RETRY] Force-retrying AI turn for game {game_id}, player: {current_player.name}")
    progress_game(game_id)

    return jsonify({
        'status': 'retried',
        'message': f'Retried AI turn for {current_player.name}',
        'diagnostic': diagnostic
    }), 200


@game_bp.route('/api/game/<game_id>', methods=['DELETE'])
def delete_game(game_id):
    """Delete a saved game."""
    try:
        game_state_service.delete_game(game_id)
        persistence.delete_game(game_id)

        import sqlite3
        with sqlite3.connect(persistence.db_path) as conn:
            conn.execute("DELETE FROM ai_player_state WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM personality_snapshots WHERE game_id = ?", (game_id,))

        return jsonify({'message': 'Game deleted successfully'}), 200
    except Exception as e:
        logger.error(f"Error deleting game {game_id}: {e}")
        return jsonify({'error': str(e)}), 500


@game_bp.route('/api/end_game/<game_id>', methods=['GET', 'POST'])
def end_game(game_id):
    """Clean up game after tournament completes or user exits."""
    game_state_service.delete_game(game_id)

    try:
        persistence.delete_game(game_id)
    except Exception as e:
        print(f"Error deleting game {game_id} from database: {e}")

    return jsonify({'message': 'Game ended successfully'})


@game_bp.route('/game/<game_id>', methods=['GET'])
def game(game_id):
    """Deprecated: Redirect to API endpoint."""
    return redirect(f'/api/game-state/{game_id}')


@game_bp.route('/new_game', methods=['GET'])
def new_game():
    """Deprecated: Use /api/new-game POST endpoint instead."""
    return redirect('/api/new-game')


@game_bp.route('/messages/<game_id>', methods=['GET'])
def get_messages(game_id):
    """Get messages for a game."""
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return jsonify([])
    return jsonify(game_data.get('messages', []))


# SocketIO event handlers
def register_socket_events(sio):
    """Register SocketIO event handlers for game events."""

    @sio.on('join_game')
    def on_join(game_id):
        join_room(game_id)
        print(f"User joined room: {game_id}")
        socketio.emit('player_joined', {'message': 'A new player has joined!'}, to=game_id)

        game_id_str = str(game_id)
        game_data = game_state_service.get_game(game_id_str)
        if game_data:
            if not game_data.get('game_started', False):
                game_data['game_started'] = True
                print(f"Starting game progression for: {game_id_str}")
                progress_game(game_id_str)

    @sio.on('player_action')
    def handle_player_action(data):
        try:
            game_id = data['game_id']
            action = data['action']
            amount = int(data.get('amount', 0))
        except KeyError:
            return

        current_game_data = game_state_service.get_game(game_id)
        if not current_game_data:
            return

        state_machine = current_game_data['state_machine']
        current_player = state_machine.game_state.current_player
        highest_bet = state_machine.game_state.highest_bet
        pre_action_state = state_machine.game_state  # Save state before action for analysis
        game_state = play_turn(state_machine.game_state, action, amount)

        # Analyze decision quality (works for both human and AI)
        memory_manager = current_game_data.get('memory_manager')
        hand_number = memory_manager.hand_count if memory_manager else None
        analyze_player_decision(game_id, current_player.name, action, amount, state_machine, pre_action_state, hand_number, memory_manager)

        table_message_content = format_action_message(current_player.name, action, amount, highest_bet)
        send_message(game_id, "Table", table_message_content, "table")

        record_action_in_memory(current_game_data, current_player.name, action, amount, game_state, state_machine)

        game_state = advance_to_next_active_player(game_state)
        state_machine.game_state = game_state

        current_game_data['state_machine'] = state_machine
        game_state_service.set_game(game_id, current_game_data)

        owner_id, owner_name = game_state_service.get_game_owner_info(game_id)
        persistence.save_game(game_id, state_machine._state_machine, owner_id, owner_name)
        if 'memory_manager' in current_game_data:
            persistence.save_opponent_models(game_id, current_game_data['memory_manager'].get_opponent_model_manager())

        update_and_emit_game_state(game_id)
        progress_game(game_id)

    @sio.on('send_message')
    def handle_send_message(data):
        game_id = data.get('game_id')
        content = data.get('message')
        sender = data.get('sender', 'Player')
        message_type = data.get('message_type', 'user')

        send_message(game_id, sender, content, message_type)

        game_data = game_state_service.get_game(game_id)
        if game_data and content:
            if 'pressure_detector' in game_data and 'ai_controllers' in game_data:
                pressure_detector = game_data['pressure_detector']
                ai_controllers = game_data['ai_controllers']
                ai_player_names = list(ai_controllers.keys())

                chat_events = pressure_detector.detect_chat_events(sender, content, ai_player_names)

                for event_name, affected_players in chat_events:
                    for player_name in affected_players:
                        if player_name in ai_controllers:
                            controller = ai_controllers[player_name]
                            if hasattr(controller, 'tilt_state'):
                                controller.tilt_state.apply_pressure_event(event_name, sender)

    @sio.on('progress_game')
    def on_progress_game(game_id):
        progress_game(game_id)
