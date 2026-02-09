"""Game-related routes and socket events."""

import time
import json
import logging
import secrets
from datetime import datetime
from typing import Dict

from flask import Blueprint, jsonify, request, redirect, send_from_directory
from flask_socketio import join_room

from poker.controllers import AIPlayerController
from poker.poker_game import initialize_game_state, play_turn, advance_to_next_active_player
from poker.prompt_config import PromptConfig
from poker.betting_context import BettingContext
from poker.poker_state_machine import PokerStateMachine, PokerPhase
from poker.utils import get_celebrities
# TiltState removed - now using ComposureState from player_psychology
from poker.emotional_state import EmotionalState
from poker.pressure_detector import PressureEventDetector
from poker.pressure_stats import PressureStatsTracker
from poker.memory import AIMemoryManager
from poker.memory.opponent_model import OpponentModelManager
from poker.tournament_tracker import TournamentTracker
from flask_app.handlers.avatar_handler import get_avatar_url_with_fallback

from ..game_adapter import StateMachineAdapter
from ..extensions import socketio, auth_manager, limiter, game_repo, user_repo, guest_tracking_repo, llm_repo, tournament_repo, hand_history_repo, prompt_preset_repo, decision_analysis_repo, capture_label_repo, coach_repo, persistence_db_path
from ..socket_rate_limit import socket_rate_limit
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
from ..validation import validate_player_action
from core.llm import AVAILABLE_PROVIDERS, PROVIDER_MODELS
from poker.guest_limits import (
    is_guest, check_guest_game_limit, validate_guest_opponent_count,
    check_guest_message_limit, check_guest_hands_limit
)
from poker.guest_limits import GUEST_MAX_HANDS, GUEST_MAX_ACTIVE_GAMES, GUEST_MAX_OPPONENTS, GUEST_LIMITS_ENABLED

logger = logging.getLogger(__name__)

game_bp = Blueprint('game', __name__)


def load_game_mode_preset(game_mode: str) -> PromptConfig:
    """Load a game mode as a preset from the database.

    Game modes (casual, standard, pro, competitive) are stored as system presets
    in the prompt_presets table, unifying them with user-defined presets.

    Args:
        game_mode: The game mode name ('casual', 'standard', 'pro', 'competitive')

    Returns:
        PromptConfig with the preset's settings applied
    """
    preset = prompt_preset_repo.get_prompt_preset_by_name(game_mode)
    if preset:
        prompt_config = preset.get('prompt_config')
        if prompt_config:
            return PromptConfig.from_dict(prompt_config)
        else:
            # Preset exists but has empty/null config - use defaults
            return PromptConfig()
    else:
        # Fallback to hardcoded mode if preset not found (e.g., migration not run)
        logger.warning(f"Preset '{game_mode}' not found in database, using fallback")
        return PromptConfig.from_mode_name(game_mode)


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
        from poker.card_utils import card_to_string

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

        decision_analysis_repo.save_decision_analysis(analysis)
        equity_str = f"{analysis.equity:.2f}" if analysis.equity is not None else "N/A"
        logger.debug(
            f"[DECISION_ANALYSIS] {player_name}: {analysis.decision_quality} "
            f"(equity={equity_str}, ev_lost={analysis.ev_lost:.0f})"
        )
    except Exception as e:
        logger.warning(f"[DECISION_ANALYSIS] Failed to analyze decision for {player_name}: {e}")


def _evaluate_coach_progression(game_id: str, player_name: str, action: str,
                                 amount: int, game_data: dict,
                                 pre_action_state) -> None:
    """Post-action hook: evaluate the human player's action against skill targets.

    Uses a broad try/except intentionally: this entire function is a non-critical
    post-action hook. Any failure must not disrupt the game flow. The phases
    (data loading, classification/evaluation, feedback prompt generation) are kept
    in one block to avoid partial state from early failures.
    """
    try:
        from flask_app.services.coach_engine import compute_coaching_data
        from flask_app.services.coach_progression import CoachProgressionService, SessionMemory
        from flask_app.services.situation_classifier import SituationClassifier

        user_id = game_data.get('owner_id', '')
        if not user_id:
            logger.debug("[COACH_PROGRESSION] Skipped: no owner_id for game=%s", game_id)
            return

        # Compute coaching data from the pre-action state for accurate evaluation
        coaching_data = compute_coaching_data(
            game_id, player_name, game_data=game_data,
            game_state_override=pre_action_state,
        )
        if not coaching_data:
            logger.debug("[COACH_PROGRESSION] Skipped: no coaching_data for game=%s player=%s",
                         game_id, player_name)
            return

        # Inject current action's bet sizing (not available from hand_actions
        # because the current action hasn't been recorded yet)
        if action in ('raise', 'bet', 'all_in') and amount > 0:
            pot_total = coaching_data.get('pot_total', 0)
            ratio = amount / pot_total if pot_total > 0 else 0
            coaching_data = {**coaching_data, 'bet_to_pot_ratio': ratio}

        service = CoachProgressionService(coach_repo)
        player_state = service.get_or_initialize_player(user_id)

        # Get range targets from player profile
        profile = player_state.get('profile', {})
        range_targets = profile.get('range_targets') if profile else None

        classifier = SituationClassifier()
        unlocked = [g for g, gp in player_state['gate_progress'].items() if gp.unlocked]
        classification = classifier.classify(
            coaching_data, unlocked, player_state['skill_states'],
            range_targets=range_targets
        )

        if classification.relevant_skills:
            evaluations = service.evaluate_and_update(
                user_id, action, coaching_data, classification,
                range_targets=range_targets
            )
            if evaluations:
                logger.debug(
                    f"[COACH_PROGRESSION] {player_name}: evaluated {len(evaluations)} skills, "
                    f"primary={classification.primary_skill}"
                )

                # Record evaluations in session memory for hand review
                session_memory = game_data.get('coach_session_memory')
                if session_memory is None:
                    session_memory = SessionMemory()
                    game_data['coach_session_memory'] = session_memory

                memory_manager = game_data.get('memory_manager')
                if memory_manager and hasattr(memory_manager, 'hand_recorder'):
                    hand_number = getattr(memory_manager.hand_recorder, 'hand_count', 0)
                else:
                    hand_number = 0
                    logger.debug("[COACH_PROGRESSION] No memory_manager; recording under hand_number=0")

                for ev in evaluations:
                    session_memory.record_hand_evaluation(hand_number, ev)
    except Exception as e:
        logger.error(
            f"[COACH_PROGRESSION] Failed for game={game_id} player={player_name}: {e}",
            exc_info=True,
        )


def generate_game_id() -> str:
    """Generate a unique, unpredictable game ID."""
    return secrets.token_urlsafe(16)


@game_bp.route('/api/usage-stats')
def get_usage_stats():
    """Get guest usage stats (hands played, limits)."""
    current_user = auth_manager.get_current_user()
    guest = is_guest(current_user) if current_user else True

    hands_played = 0
    if guest:
        tracking_id = request.cookies.get('guest_tracking_id')
        if tracking_id:
            hands_played = guest_tracking_repo.get_hands_played(tracking_id)

    hands_limit_reached = (
        guest and GUEST_LIMITS_ENABLED and hands_played >= GUEST_MAX_HANDS
    )

    return jsonify({
        'hands_played': hands_played,
        'hands_limit': GUEST_MAX_HANDS,
        'hands_limit_reached': hands_limit_reached,
        'max_opponents': GUEST_MAX_OPPONENTS if guest else 9,
        'max_active_games': GUEST_MAX_ACTIVE_GAMES if guest else 10,
        'is_guest': guest,
    })


@game_bp.route('/api/games')
def list_games():
    """List games for the current user."""
    current_user = auth_manager.get_current_user()

    try:
        limit = int(request.args.get('limit', 20))
        offset = int(request.args.get('offset', 0))
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid pagination parameters'}), 400
    limit = max(0, min(limit, config.GAME_LIST_MAX_LIMIT))
    offset = max(0, offset)

    if current_user:
        saved_games = game_repo.list_games(owner_id=current_user.get('id'), limit=limit, offset=offset)
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
        except Exception:
            logger.warning(f"Failed to parse game state for game {game.game_id}")
            player_names = []
            total_players = game.num_players
            active_players = game.num_players
            human_stack = None
            big_blind = 20

        try:
            phase_num = int(game.phase) if isinstance(game.phase, str) else game.phase
            phase_name = PokerPhase(phase_num).name.replace('_', ' ').title()
        except (ValueError, TypeError):
            logger.warning(f"Failed to parse phase for game {game.game_id}")
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
            logger.debug(f"[CACHE] Auto-advancing cached game {game_id}, phase: {state_machine.current_phase}")
            current_game_data['game_started'] = True
            progress_game(game_id)

    if not current_game_data:
        # Try to load from database
        try:
            current_user = auth_manager.get_current_user()
            saved_games = game_repo.list_games(owner_id=current_user.get('id') if current_user else None, limit=50)

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

            base_state_machine = game_repo.load_game(game_id)
            if base_state_machine:
                state_machine = StateMachineAdapter(base_state_machine)
                # Load per-player LLM configs for proper provider restoration
                llm_configs = game_repo.load_llm_configs(game_id) or {}
                ai_controllers = restore_ai_controllers(
                    game_id, state_machine, game_repo,
                    owner_id=owner_id,
                    player_llm_configs=llm_configs.get('player_llm_configs'),
                    default_llm_config=llm_configs.get('default_llm_config'),
                    capture_label_repo=capture_label_repo, decision_analysis_repo=decision_analysis_repo,
                    bot_types=llm_configs.get('bot_types')
                )
                db_messages = game_repo.load_messages(game_id)

                pressure_detector = PressureEventDetector()
                pressure_stats = PressureStatsTracker()

                memory_manager = AIMemoryManager(game_id, persistence_db_path, owner_id=owner_id)
                memory_manager.set_hand_history_repo(hand_history_repo)  # Enable hand history saving

                # Restore hand count from database
                restored_hand_count = hand_history_repo.get_hand_count(game_id)
                if restored_hand_count > 0:
                    memory_manager.hand_count = restored_hand_count
                    logger.info(f"[LOAD] Restored hand count: {restored_hand_count} for game {game_id}")

                # Restore opponent models from database
                saved_opponent_models = game_repo.load_opponent_models(game_id)
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

                memory_manager.on_hand_start(state_machine.game_state, hand_number=memory_manager.hand_count + 1)

                # Try to load tournament tracker from database, or create new one
                tracker_data = game_repo.load_tournament_tracker(game_id)
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
                    'pressure_detector': pressure_detector,
                    'pressure_stats': pressure_stats,
                    'memory_manager': memory_manager,
                    'tournament_tracker': tournament_tracker,
                    'owner_id': owner_id,
                    'owner_name': owner_name,
                    'messages': db_messages,
                    'last_announced_phase': None,  # Reset on game load
                    'game_started': True,
                    'guest_tracking_id': current_user.get('tracking_id') if current_user else None,
                }
                game_state_service.set_game(game_id, current_game_data)

                game_state = state_machine.game_state
                current_player = game_state.current_player
                logger.debug(f"[LOAD] Game {game_id} loaded. Phase: {state_machine.current_phase}, "
                      f"awaiting_action: {game_state.awaiting_action}, "
                      f"current_player: {current_player.name} (human: {current_player.is_human})")

                if not game_state.awaiting_action:
                    logger.debug(f"[LOAD] Auto-advancing game {game_id} (not awaiting action)")
                    progress_game(game_id)
                elif game_state.awaiting_action and not current_player.is_human:
                    logger.debug(f"[LOAD] Resuming AI turn for {current_player.name} in game {game_id}")
                    progress_game(game_id)
            else:
                return jsonify({'error': 'Game not found'}), 404
        except Exception as e:
            logger.warning(f"[LOAD] Error loading game {game_id}: {str(e)}")
            import traceback
            traceback.print_exc()
            logger.debug(f"[LOAD] Error loading game {game_id}: {str(e)}")
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
            avatar_url = get_avatar_url_with_fallback(game_id, player.name, avatar_emotion)

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

    # Build betting context for current player
    betting_context = BettingContext.from_game_state(game_state).to_dict()
    opponent_covers = BettingContext.get_opponent_covers(game_state)
    for cover in opponent_covers:
        controller = ai_controllers.get(cover['name'])
        if controller:
            cover['nickname'] = controller.ai_player.personality_config.get('nickname', cover['name'].split()[0])
        else:
            cover['nickname'] = cover['name'].split()[0]
    betting_context['opponent_covers'] = opponent_covers

    response = {
        'players': players,
        'community_cards': community_cards,
        'pot': game_state.pot,
        'current_player_idx': game_state.current_player_idx,
        'current_dealer_idx': game_state.current_dealer_idx,
        'small_blind_idx': game_state.small_blind_idx,
        'big_blind_idx': game_state.big_blind_idx,
        'phase': state_machine.current_phase.name,
        'highest_bet': game_state.highest_bet,
        'player_options': list(game_state.current_player_options) if game_state.current_player_options else [],
        'min_raise': game_state.min_raise_amount,
        'big_blind': game_state.current_ante,
        'messages': messages,
        'game_id': game_id,
        'betting_context': betting_context,
    }

    return jsonify(response)


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
    return llm_repo.get_model_cost_tiers()


@game_bp.route('/api/user-models', methods=['GET'])
def api_user_models():
    """Get LLM providers and models available for user-facing game configuration.

    Returns models where BOTH enabled=1 AND user_enabled=1.
    Use /api/system-models for admin tools that need system-only models.
    """
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

    # Get model-level capabilities (supplements provider-level)
    model_capabilities = _get_model_capabilities_map()

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

        # Build model-specific capabilities for this provider
        provider_model_caps = {
            m: model_capabilities.get((provider, m), {})
            for m in models
            if (provider, m) in model_capabilities
        }

        providers.append({
            'id': provider,
            'name': provider.title(),
            'models': models,
            'default_model': default_model,
            'capabilities': PROVIDER_CAPABILITIES.get(provider, {}),
            'model_capabilities': provider_model_caps,
            'model_tiers': model_tiers.get(provider, {}),
        })

    return jsonify({
        'providers': providers,
        'default_provider': 'openai',
    })


@game_bp.route('/api/system-models', methods=['GET'])
def api_system_models():
    """Get LLM providers and models available for system/admin features.

    Returns models where enabled=1 (ignores user_enabled).
    This includes "System-only" models that admins can use but users cannot see.

    Use this endpoint for:
    - Experiment Designer
    - Prompt Debugger
    - Decision Analyzer
    - Prompt Playground
    """
    from core.llm import (
        AVAILABLE_PROVIDERS,
        PROVIDER_MODELS,
        PROVIDER_DEFAULT_MODELS,
        PROVIDER_CAPABILITIES,
    )

    # Get cost tiers from pricing database
    model_tiers = get_model_cost_tiers()

    # Get system-enabled models (only checks enabled, ignores user_enabled)
    enabled_models = _get_system_enabled_models_map()

    # Get model-level capabilities (supplements provider-level)
    model_capabilities = _get_model_capabilities_map()

    providers = []
    for provider in AVAILABLE_PROVIDERS:
        all_models = PROVIDER_MODELS.get(provider, [])

        # Filter by system-enabled models if we have the table
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

        # Build model-specific capabilities for this provider
        provider_model_caps = {
            m: model_capabilities.get((provider, m), {})
            for m in models
            if (provider, m) in model_capabilities
        }

        providers.append({
            'id': provider,
            'name': provider.title(),
            'models': models,
            'default_model': default_model,
            'capabilities': PROVIDER_CAPABILITIES.get(provider, {}),
            'model_capabilities': provider_model_caps,
            'model_tiers': model_tiers.get(provider, {}),
        })

    return jsonify({
        'providers': providers,
        'default_provider': 'openai',
    })


def _get_enabled_models_map():
    """Get a map of (provider, model) -> enabled status for user-facing features.

    For game setup and user-facing features, models must have BOTH:
    - enabled = 1 (system enabled)
    - user_enabled = 1 (user enabled)

    Returns empty dict if enabled_models table doesn't exist yet.
    """
    return llm_repo.get_enabled_models_map()


def _get_system_enabled_models_map():
    """Get a map of (provider, model) -> enabled status for system/admin features.

    For admin tools (experiments, playground, decision analyzer), models only need:
    - enabled = 1 (system enabled)

    This includes "System-only" models (enabled=1, user_enabled=0) that admins
    can use but regular users cannot see in game setup.

    Returns empty dict if enabled_models table doesn't exist yet.
    """
    return llm_repo.get_system_enabled_models_map()


def _get_model_capabilities_map():
    """Get a map of (provider, model) -> capability flags.

    Returns model-level capabilities (supports_img2img, etc.) from enabled_models table.
    This supplements provider-level capabilities with model-specific flags.

    Returns:
        Dict mapping (provider, model) to dict of capability flags
    """
    return llm_repo.get_model_capabilities_map()


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

        game_count = user_repo.count_user_games(owner_id)

        # Use guest-specific limits if applicable
        if is_guest(current_user):
            allowed, error_msg = check_guest_game_limit(current_user, game_count)
            if not allowed:
                return jsonify({
                    'error': error_msg,
                    'code': 'GUEST_LIMIT_GAMES'
                }), 403
        else:
            max_games = 10
            if game_count >= max_games:
                return jsonify({
                    'error': f'Game limit reached. You can have up to {max_games} saved games.'
                }), 400

        # Prevent duplicate game creation from rapid clicks
        last_created = user_repo.get_last_game_creation_time(owner_id)
        if last_created is not None and (time.time() - last_created) < 3:
            return jsonify({
                'error': 'Please wait a moment before creating another game.'
            }), 429
    else:
        player_name = data.get('playerName', 'Player')
        owner_id = None
        owner_name = None

    requested_personalities = data.get('personalities', [])
    default_llm_config = data.get('llm_config', {})
    starting_stack = data.get('starting_stack', 5000)
    big_blind = data.get('big_blind', 100)
    blind_growth = data.get('blind_growth', 1.5)
    blinds_increase = data.get('blinds_increase', 6)
    max_blind = data.get('max_blind', 1000)  # 0 = no limit

    # Validate game mode (if provided)
    game_mode = data.get('game_mode', 'standard').lower()
    VALID_GAME_MODES = {'casual', 'standard', 'pro', 'competitive'}
    if game_mode not in VALID_GAME_MODES:
        return jsonify({
            'error': f'Invalid game_mode: {game_mode}',
            'valid_modes': list(VALID_GAME_MODES)
        }), 400

    # Validate default LLM config if provided
    if default_llm_config:
        default_provider = default_llm_config.get('provider', 'openai').lower()
        if default_provider not in AVAILABLE_PROVIDERS:
            return jsonify({'error': f'Invalid default provider: {default_provider}'}), 400
        default_model = default_llm_config.get('model')
        if default_model and default_model not in PROVIDER_MODELS.get(default_provider, []):
            return jsonify({'error': f'Invalid default model {default_model} for provider {default_provider}'}), 400

    # Note: UI warns if starting stack < 10x big blind, but we allow it

    # Parse personalities - supports both string names and objects with llm_config/game_mode
    # Format: ["Batman", {"name": "Sherlock", "llm_config": {"provider": "groq"}, "game_mode": "pro"}]
    ai_player_names = []
    player_llm_configs = {}  # Map of player_name -> llm_config
    player_prompt_configs = {}  # Map of player_name -> prompt_config

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
                    # Handle per-player game_mode override
                    if 'game_mode' in p:
                        p_mode = p['game_mode'].lower()
                        if p_mode not in VALID_GAME_MODES:
                            return jsonify({
                                'error': f'Invalid game_mode for {name}: {p_mode}',
                                'valid_modes': list(VALID_GAME_MODES)
                            }), 400
                        player_prompt_configs[name] = load_game_mode_preset(p_mode)
    else:
        opponent_count = max(1, min(9, data.get('opponent_count', 3)))
        ai_player_names = get_celebrities(shuffled=True)[:opponent_count]

    # Check for duplicate names (e.g., AI personality matching human player name)
    if player_name.lower() in [n.lower() for n in ai_player_names]:
        return jsonify({
            'error': f'An opponent has the same name as you ("{player_name}"). Please choose a different player name or remove that opponent.',
            'code': 'DUPLICATE_PLAYER_NAME'
        }), 400

    # Enforce guest opponent limit
    if current_user and is_guest(current_user):
        allowed, error_msg = validate_guest_opponent_count(current_user, len(ai_player_names))
        if not allowed:
            return jsonify({
                'error': error_msg,
                'code': 'GUEST_LIMIT_OPPONENTS'
            }), 403

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

    # Create default game-level prompt config from game_mode (loaded from DB preset)
    default_prompt_config = load_game_mode_preset(game_mode)

    ai_controllers = {}

    for player in state_machine.game_state.players:
        if not player.is_human:
            # Use per-player config if set, otherwise use default
            player_config = player_llm_configs.get(player.name, default_llm_config)
            player_prompt_config = player_prompt_configs.get(player.name, default_prompt_config)
            new_controller = AIPlayerController(
                player.name,
                state_machine,
                llm_config=player_config,
                prompt_config=player_prompt_config,
                game_id=game_id,
                owner_id=owner_id,
                capture_label_repo=capture_label_repo, decision_analysis_repo=decision_analysis_repo
            )
            ai_controllers[player.name] = new_controller

    from poker.repositories.sqlite_repositories import PressureEventRepository
    event_repository = PressureEventRepository(config.DB_PATH)
    pressure_detector = PressureEventDetector()
    pressure_stats = PressureStatsTracker(game_id, event_repository)

    memory_manager = AIMemoryManager(game_id, persistence_db_path, owner_id=owner_id)
    memory_manager.set_hand_history_repo(hand_history_repo)  # Enable hand history saving
    for player in state_machine.game_state.players:
        if not player.is_human:
            memory_manager.initialize_for_player(player.name)
            controller = ai_controllers[player.name]
            controller.session_memory = memory_manager.get_session_memory(player.name)
            controller.opponent_model_manager = memory_manager.get_opponent_model_manager()
        else:
            # Initialize human player for opponent observation tracking
            memory_manager.initialize_human_observer(player.name)

    # Advance state machine to deal cards and post blinds before recording hand start,
    # so that hole cards are available when on_hand_start records them.
    state_machine.run_until_player_action()

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
        'pressure_detector': pressure_detector,
        'pressure_stats': pressure_stats,
        'memory_manager': memory_manager,
        'tournament_tracker': tournament_tracker,
        'owner_id': owner_id,
        'owner_name': owner_name,
        'llm_config': default_llm_config,  # Default config for new players
        'player_llm_configs': player_llm_configs,  # Per-player LLM overrides
        'player_prompt_configs': player_prompt_configs,  # Per-player prompt config overrides
        'default_game_mode': game_mode,  # Game-level mode setting
        'last_announced_phase': None,  # Track which phase we've announced cards for
        'guest_tracking_id': current_user.get('tracking_id') if current_user else None,
        'guest_messages_this_action': 0,  # Chat rate limiting for guests
        'messages': [{
            'id': '1',
            'sender': 'Table',
            'content': '***   GAME START   ***',
            'timestamp': datetime.now().isoformat(),
            'type': 'table'
        }],
        # Stack tracking for pressure events (double_up, crippled, short_stack)
        'hand_start_stacks': {
            p.name: p.stack for p in state_machine.game_state.players
        },
        'short_stack_players': set(),  # No one is short at game start
    }
    game_state_service.set_game(game_id, game_data)

    game_repo.save_game(
        game_id, state_machine._state_machine, owner_id, owner_name,
        llm_configs={'player_llm_configs': player_llm_configs, 'default_llm_config': default_llm_config}
    )
    game_repo.save_tournament_tracker(game_id, tournament_tracker)
    game_repo.save_opponent_models(game_id, memory_manager.get_opponent_model_manager())
    if config.ENABLE_AVATAR_GENERATION:
        start_background_avatar_generation(game_id, ai_player_names)

    # Record game creation timestamp to prevent rapid duplicate creation
    if owner_id:
        user_repo.update_last_game_creation_time(owner_id, time.time())

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

    is_valid, error_message = validate_player_action(state_machine.game_state, action, amount)
    if not is_valid:
        return jsonify({'error': error_message}), 400

    current_user = auth_manager.get_current_user()
    if current_user and is_guest(current_user) and GUEST_LIMITS_ENABLED:
        tracking_id = current_game_data.get('guest_tracking_id')
        if tracking_id:
            hands_played = guest_tracking_repo.get_hands_played(tracking_id)
            allowed, error_msg = check_guest_hands_limit(current_user, hands_played)
            if not allowed:
                return jsonify({'error': error_msg, 'code': 'GUEST_LIMIT_HANDS'}), 403

    try:
        current_player = state_machine.game_state.current_player
        highest_bet = state_machine.game_state.highest_bet
        pre_action_state = state_machine.game_state  # Save state before action for analysis
        game_state = play_turn(state_machine.game_state, action, amount)

        # Analyze decision quality (works for both human and AI)
        memory_manager = current_game_data.get('memory_manager')
        hand_number = memory_manager.hand_count if memory_manager else None
        analyze_player_decision(game_id, current_player.name, action, amount, state_machine, pre_action_state, hand_number, memory_manager)

        # Coach progression: evaluate human player actions against skill targets
        if current_player.is_human:
            _evaluate_coach_progression(game_id, current_player.name, action, amount, current_game_data, pre_action_state)

        record_action_in_memory(current_game_data, current_player.name, action, amount, game_state, state_machine)

        table_message_content = format_action_message(current_player.name, action, amount, highest_bet)
        send_message(game_id, "Table", table_message_content, "table")

        advanced_state = advance_to_next_active_player(game_state)
        # If None, no active players remain - keep current state, let progress_game handle phase transition
        if advanced_state is not None:
            game_state = advanced_state
        state_machine.game_state = game_state

        current_game_data['state_machine'] = state_machine
        current_game_data['guest_messages_this_action'] = 0
        game_state_service.set_game(game_id, current_game_data)

        owner_id, owner_name = game_state_service.get_game_owner_info(game_id)
        game_repo.save_game(game_id, state_machine._state_machine, owner_id, owner_name)
        if 'memory_manager' in current_game_data:
            game_repo.save_opponent_models(game_id, current_game_data['memory_manager'].get_opponent_model_manager())

        progress_game(game_id)

        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error processing action for game {game_id}: {e}", exc_info=True)
        return jsonify({'error': 'Failed to process action'}), 500


@game_bp.route('/api/game/<game_id>/message', methods=['POST'])
def api_send_message(game_id):
    """Send a chat message in the game."""
    data = request.json
    message = data.get('message', '')
    sender = data.get('sender', 'Player')

    current_user = auth_manager.get_current_user()
    is_guest_user = current_user and is_guest(current_user) and GUEST_LIMITS_ENABLED

    if is_guest_user:
        current_game_data = game_state_service.get_game(game_id)
        if current_game_data:
            msgs_this_action = current_game_data.get('guest_messages_this_action', 0)
            allowed, error_msg = check_guest_message_limit(current_user, msgs_this_action)
            if not allowed:
                return jsonify({'success': False, 'error': error_msg, 'code': 'GUEST_CHAT_LIMIT'}), 429
            # Increment before sending to close the race window between check and send
            current_game_data['guest_messages_this_action'] = msgs_this_action + 1
            game_state_service.set_game(game_id, current_game_data)

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
        'phase': state_machine.current_phase.name,
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
        game_repo.delete_game(game_id)

        return jsonify({'message': 'Game deleted successfully'}), 200
    except Exception as e:
        logger.error(f"Error deleting game {game_id}: {e}")
        return jsonify({'error': str(e)}), 500


@game_bp.route('/api/end_game/<game_id>', methods=['POST'])
def end_game(game_id):
    """Clean up game after tournament completes or user exits."""
    game_state_service.delete_game(game_id)

    try:
        game_repo.delete_game(game_id)
    except Exception as e:
        logger.warning(f"[DELETE] Error deleting game {game_id} from database: {e}")

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


@game_bp.route('/api/game/<game_id>/llm-configs', methods=['GET'])
def api_game_llm_configs(game_id):
    """Get LLM configurations for all players in a game (debug endpoint)."""
    current_game_data = game_state_service.get_game(game_id)

    if not current_game_data:
        # Try to load from database
        try:
            llm_configs = game_repo.load_llm_configs(game_id)
            if llm_configs:
                return jsonify(llm_configs)
            return jsonify({'error': 'Game not found'}), 404
        except Exception as e:
            logger.error(f"Error loading LLM configs for game {game_id}: {e}")
            return jsonify({'error': 'Game not found'}), 404

    # Get configs from memory
    state_machine = current_game_data['state_machine']
    ai_controllers = current_game_data.get('ai_controllers', {})
    default_llm_config = current_game_data.get('llm_config', {})
    player_llm_configs = current_game_data.get('player_llm_configs', {})

    # Build detailed player configs with actual controller info
    player_configs = []
    for player in state_machine.game_state.players:
        config_entry = {
            'name': player.name,
            'is_human': player.is_human,
        }

        if player.is_human:
            config_entry['llm_config'] = None
        elif player.name in ai_controllers:
            controller = ai_controllers[player.name]
            # Get the actual config from the controller
            actual_config = getattr(controller, 'llm_config', {})
            config_entry['llm_config'] = actual_config if actual_config else default_llm_config
            config_entry['has_custom_config'] = player.name in player_llm_configs
        else:
            # Fallback to stored configs
            config_entry['llm_config'] = player_llm_configs.get(player.name, default_llm_config)
            config_entry['has_custom_config'] = player.name in player_llm_configs

    return jsonify({
        'default_llm_config': default_llm_config,
        'player_configs': player_configs
    })


# SocketIO event handlers
def register_socket_events(sio):
    """Register SocketIO event handlers for game events."""

    @sio.on('join_game')
    @socket_rate_limit(max_calls=20, window_seconds=10)
    def on_join(game_id):
        game_id_str = str(game_id)
        game_data = game_state_service.get_game(game_id_str)
        if not game_data:
            return

        # Verify the current user is the game owner
        user = auth_manager.get_current_user() if auth_manager else None
        owner_id = game_data.get('owner_id')
        if not user or user.get('id') != owner_id:
            return

        join_room(game_id)
        logger.debug(f"[SOCKET] User joined room: {game_id}")
        socketio.emit('player_joined', {'message': 'A new player has joined!'}, to=game_id)

        if not game_data.get('game_started', False):
            game_data['game_started'] = True
            logger.debug(f"[SOCKET] Starting game progression for: {game_id_str}")
            progress_game(game_id_str)

    @sio.on('player_action')
    @socket_rate_limit(max_calls=10, window_seconds=10)
    def handle_player_action(data):
        try:
            game_id = data['game_id']
            action = data['action']
            amount = int(data.get('amount', 0))
        except KeyError:
            logger.debug(f"[SOCKET] player_action missing required fields: {data}")
            return

        current_game_data = game_state_service.get_game(game_id)
        if not current_game_data:
            logger.debug(f"[SOCKET] player_action game not found: {game_id}")
            return

        # Verify the current user is the game owner
        user = auth_manager.get_current_user() if auth_manager else None
        owner_id = current_game_data.get('owner_id')
        if not user or user.get('id') != owner_id:
            logger.debug(f"[SOCKET] player_action unauthorized: user={user.get('id') if user else None}, owner={owner_id}")
            return

        if user and is_guest(user) and GUEST_LIMITS_ENABLED:
            tracking_id = current_game_data.get('guest_tracking_id')
            if tracking_id:
                hands_played = guest_tracking_repo.get_hands_played(tracking_id)
                allowed, _ = check_guest_hands_limit(user, hands_played)
                if not allowed:
                    socketio.emit('guest_limit_reached', {
                        'hands_played': hands_played,
                        'hands_limit': GUEST_MAX_HANDS,
                    }, to=game_id)
                    return

        state_machine = current_game_data['state_machine']

        is_valid, error_message = validate_player_action(state_machine.game_state, action, amount)
        if not is_valid:
            logger.debug(f"[SOCKET] player_action validation failed: {error_message}")
            return

        current_player = state_machine.game_state.current_player
        highest_bet = state_machine.game_state.highest_bet
        pre_action_state = state_machine.game_state  # Save state before action for analysis
        game_state = play_turn(state_machine.game_state, action, amount)

        # Analyze decision quality (works for both human and AI)
        memory_manager = current_game_data.get('memory_manager')
        hand_number = memory_manager.hand_count if memory_manager else None
        analyze_player_decision(game_id, current_player.name, action, amount, state_machine, pre_action_state, hand_number, memory_manager)

        # Coach progression: evaluate human player actions against skill targets
        if current_player.is_human:
            _evaluate_coach_progression(game_id, current_player.name, action, amount, current_game_data, pre_action_state)

        table_message_content = format_action_message(current_player.name, action, amount, highest_bet)
        send_message(game_id, "Table", table_message_content, "table")

        record_action_in_memory(current_game_data, current_player.name, action, amount, game_state, state_machine)

        advanced_state = advance_to_next_active_player(game_state)
        # If None, no active players remain - keep current state, let progress_game handle phase transition
        if advanced_state is not None:
            game_state = advanced_state
        state_machine.game_state = game_state

        current_game_data['state_machine'] = state_machine
        current_game_data['guest_messages_this_action'] = 0
        game_state_service.set_game(game_id, current_game_data)

        owner_id, owner_name = game_state_service.get_game_owner_info(game_id)
        game_repo.save_game(game_id, state_machine._state_machine, owner_id, owner_name)
        if 'memory_manager' in current_game_data:
            game_repo.save_opponent_models(game_id, current_game_data['memory_manager'].get_opponent_model_manager())

        update_and_emit_game_state(game_id)
        progress_game(game_id)

    @sio.on('send_message')
    @socket_rate_limit(max_calls=5, window_seconds=10)
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
                            if controller.psychology is not None:
                                controller.psychology.apply_pressure_event(event_name, sender)

    @sio.on('progress_game')
    @socket_rate_limit(max_calls=5, window_seconds=10)
    def on_progress_game(game_id):
        progress_game(game_id)
