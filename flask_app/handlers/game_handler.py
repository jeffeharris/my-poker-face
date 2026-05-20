"""Game progression and AI action handling.

This module contains the core game loop logic, broken down into
manageable functions for maintainability.
"""

import logging
import sqlite3
import threading
from datetime import datetime
from typing import Dict, Any, Optional, List

from poker.controllers import AIPlayerController
from poker.rule_based_controller import RuleBasedController, RuleConfig
from poker.rule_bot_controller import RuleBotController
from poker.hybrid_ai_controller import HybridAIController
from poker.ai_resilience import get_fallback_chat_response, FallbackActionSelector, AIFallbackStrategy
from poker.betting_context import BettingContext
from poker.config import MIN_RAISE, AI_MESSAGE_CONTEXT_LIMIT
from poker.poker_game import determine_winner, play_turn, advance_to_next_active_player, award_pot_winnings
from poker.poker_state_machine import PokerPhase
from poker.hand_evaluator import HandEvaluator
from poker.card_utils import card_to_string
from .avatar_handler import get_avatar_url_with_fallback
from poker.player_psychology import ComposureState
from poker.emotional_state import EmotionalState
from poker.runout_reactions import compute_runout_reactions
from poker.equity_tracker import EquityTracker
from poker.equity_snapshot import HandEquityHistory
from poker.psychology_pipeline import PsychologyPipeline, PsychologyContext
from core.card import Card

from ..extensions import socketio, game_repo, guest_tracking_repo, tournament_repo, hand_history_repo, personality_repo, capture_label_repo, decision_analysis_repo, coach_repo, event_repository
from ..services import game_state_service
from ..services.elasticity_service import format_elasticity_data
from ..services.ai_debug_service import get_all_players_llm_stats
from .message_handler import send_message, format_action_message, record_action_in_memory, format_messages_for_api
from .. import config
from poker.game_helpers import should_clear_player_options
from poker.guest_limits import GUEST_LIMITS_ENABLED, GUEST_MAX_HANDS

logger = logging.getLogger(__name__)


def _get_hand_number(game_data: dict) -> int:
    """Get the current hand number from game_data's memory manager."""
    mm = game_data.get('memory_manager')
    return mm.hand_count if mm else 0


def _sandbox_id_for(game_data: dict) -> Optional[str]:
    """Resolve the sandbox_id for a cash-mode `game_data` dict.

    Prefers the value stamped on `game_data` at sit-down — that's the
    sandbox the session was created in, and avoids re-hitting the
    resolver on the hot path. Falls back to resolving from `owner_id`
    when the stamp is missing (defensive; covers cold-load + legacy
    pre-stamp sessions). Returns None when neither is available
    (tournament games or sessions with no owner_id).
    """
    sandbox_id = game_data.get('sandbox_id')
    if sandbox_id:
        return sandbox_id
    owner_id = game_data.get('owner_id')
    if not owner_id:
        return None
    try:
        from flask_app.services.sandbox_resolver import resolve_default_sandbox_for
        from flask_app.extensions import sandbox_repo
        sandbox_id = resolve_default_sandbox_for(
            owner_id, sandbox_repo=sandbox_repo,
        )
        # Stamp it so subsequent reads are O(1) dict hit.
        game_data['sandbox_id'] = sandbox_id
        return sandbox_id
    except Exception as e:
        logger.warning(
            "[CASH] sandbox_id fallback resolution failed for owner=%r: %s",
            owner_id, e,
        )
        return None


def _track_guest_hand(game_id: str, game_data: dict) -> bool:
    """Track hand completion for guest users and emit limit event if needed.

    Returns True if the guest hand limit has been reached, False otherwise.
    """
    if not GUEST_LIMITS_ENABLED:
        return False

    try:
        tracking_id = game_data.get('guest_tracking_id')
        if not tracking_id:
            owner_id, _ = game_state_service.get_game_owner_info(game_id)
            if not owner_id or not owner_id.startswith('guest_'):
                return False
            logger.info(f"Guest game {game_id} has no tracking_id (pre-migration game), skipping hand tracking")
            return False

        new_count = guest_tracking_repo.increment_hands_played(tracking_id)
        logger.debug(f"Guest hand tracked: tracking_id={tracking_id}, count={new_count}")
        if new_count >= GUEST_MAX_HANDS:
            socketio.emit('guest_limit_reached', {
                'hands_played': new_count,
                'hands_limit': GUEST_MAX_HANDS,
            }, to=game_id)
            return True
        return False
    except sqlite3.Error as e:
        logger.error(f"Database error tracking guest hand for game {game_id}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error tracking guest hand for game {game_id}: {e}")
        return False


def _emit_avatar_reaction(game_id: str, player_name: str, emotion: str) -> None:
    """Emit avatar update for a run-out reaction."""
    avatar_url = get_avatar_url_with_fallback(game_id, player_name, emotion)
    socketio.emit('avatar_update', {
        'player_name': player_name,
        'avatar_url': avatar_url,
        'avatar_emotion': emotion,
    }, to=game_id)


def _feed_opponent_observations(memory_manager, observer: str, observations: List[str]) -> None:
    """Feed opponent observations from commentary into opponent models.

    Parses observations to determine which opponent they reference, then
    adds them to the appropriate OpponentModel for future prompts.

    Args:
        memory_manager: The AIMemoryManager instance
        observer: The AI player making the observations
        observations: List of observation strings from commentary
    """
    if not observations or not hasattr(memory_manager, 'opponent_model_manager'):
        return

    opponent_models = memory_manager.opponent_model_manager

    for observation in observations:
        if not observation or not isinstance(observation, str):
            continue

        observation = observation.strip()
        if not observation:
            continue

        # Try to parse "OpponentName: observation" format
        # Common formats: "Trump: folds to pressure", "Trump is tight"
        opponent_name = None
        observation_text = observation

        if ':' in observation:
            parts = observation.split(':', 1)
            potential_name = parts[0].strip()
            # Check if the part before : is a known opponent
            if potential_name in opponent_models.models.get(observer, {}):
                opponent_name = potential_name
                observation_text = parts[1].strip()

        if not opponent_name:
            # Try to find opponent name at start of observation
            for opp_name in opponent_models.models.get(observer, {}).keys():
                if observation.lower().startswith(opp_name.lower()):
                    opponent_name = opp_name
                    # Keep full text as observation
                    break

        if opponent_name and observation_text:
            model = opponent_models.get_model(observer, opponent_name)
            model.add_narrative_observation(observation_text)
            logger.debug(f"[OpponentModel] Added observation for {observer}->{opponent_name}: {observation_text[:50]}...")


def _feed_strategic_reflection(memory_manager, player_name: str, reflection: str,
                               key_insight: Optional[str] = None) -> None:
    """Feed strategic reflection from commentary into session memory.

    Strategic reflections are included in future decision prompts so the AI
    can learn and build upon its insights across hands.

    Args:
        memory_manager: The AIMemoryManager instance
        player_name: The AI player name
        reflection: Full strategic reflection text
        key_insight: Optional one-liner summary (preferred if available)
    """
    if not reflection or not hasattr(memory_manager, 'session_memories'):
        return

    session_memory = memory_manager.session_memories.get(player_name)
    if session_memory:
        session_memory.add_reflection(reflection, key_insight)
        logger.debug(f"[SessionMemory] Added reflection for {player_name}")


def restore_ai_controllers(game_id: str, state_machine, game_repo,
                           owner_id: str = None,
                           player_llm_configs: Dict[str, Dict] = None,
                           default_llm_config: Dict = None,
                           capture_label_repo=None,
                           decision_analysis_repo=None,
                           bot_types: Dict[str, str] = None) -> Dict[str, Any]:
    """Restore AI controllers with their saved state.

    Args:
        game_id: The game identifier
        state_machine: The game's state machine
        game_repo: GameRepository for loading AI/controller/emotional states
        owner_id: The owner/user ID for tracking
        player_llm_configs: Per-player LLM configs (provider, model, etc.)
        default_llm_config: Default LLM config for players without specific config
        capture_label_repo: CaptureLabelRepository for auto-labeling
        decision_analysis_repo: DecisionAnalysisRepository for decision tracking
        bot_types: Dict mapping player name to bot strategy.
            - Rule-based strategies: "case_based", "abc", "always_fold", etc.
            - Hybrid mode: "hybrid" (uses HybridAIController)

    Returns:
        Dictionary mapping player names to their controllers (AIPlayerController, RuleBotController, or HybridAIController)
    """
    ai_controllers = {}
    ai_states = game_repo.load_ai_player_states(game_id)
    player_llm_configs = player_llm_configs or {}
    default_llm_config = default_llm_config or {}
    bot_types = bot_types or {}

    controller_states = {}
    emotional_states = {}
    try:
        controller_states = game_repo.load_all_controller_states(game_id)
        emotional_states = game_repo.load_all_emotional_states(game_id)
    except Exception as e:
        logger.warning(f"Could not load controller/emotional states: {e}")

    # Legacy bot_type aliases for stored games predating the chaos/standard/lean/sharp lineup.
    # hybrid → standard (full Hybrid path; previously also covered lean-bounded forced default)
    # tiered → sharp
    _BOT_TYPE_ALIASES = {'hybrid': 'standard', 'tiered': 'sharp'}

    for player in state_machine.game_state.players:
        if not player.is_human:
            # Check if this player should use a special controller type
            if player.name in bot_types:
                raw_strategy = bot_types[player.name]
                strategy = _BOT_TYPE_ALIASES.get(raw_strategy, raw_strategy)
                # Get player-specific llm_config or fall back to default (for personality loading)
                llm_config = player_llm_configs.get(player.name, default_llm_config)

                if strategy == 'standard':
                    # Standard: HybridAIController (full prompt pipeline + bounded options)
                    controller = HybridAIController(
                        player_name=player.name,
                        state_machine=state_machine,
                        llm_config=llm_config,
                        game_id=game_id,
                        owner_id=owner_id,
                        capture_label_repo=capture_label_repo,
                        decision_analysis_repo=decision_analysis_repo,
                    )
                    logger.info(f"[RESTORE] Created HybridAIController for {player.name}")
                elif strategy == 'sharp':
                    # Sharp: solver baselines + personality distortion + LLM expression
                    from flask_app.handlers.tiered_factory import build_tiered_controller
                    controller = build_tiered_controller(
                        player_name=player.name,
                        state_machine=state_machine,
                        llm_config=llm_config,
                        game_id=game_id,
                        owner_id=owner_id,
                        capture_label_repo=capture_label_repo,
                        decision_analysis_repo=decision_analysis_repo,
                        expression_enabled=True,
                        debug_logging=True,
                    )
                    logger.info(f"[RESTORE] Created TieredBotController for {player.name} (with expression)")
                elif strategy == 'baseline_solver':
                    # Pure solver, no personality distortion, no expression layer
                    from flask_app.handlers.tiered_factory import build_tiered_controller
                    controller = build_tiered_controller(
                        player_name=player.name,
                        state_machine=state_machine,
                        llm_config=llm_config,
                        game_id=game_id,
                        owner_id=owner_id,
                        capture_label_repo=capture_label_repo,
                        decision_analysis_repo=decision_analysis_repo,
                        baseline=True,
                    )
                    logger.info(f"[RESTORE] Created BaselineSolverBot for {player.name}")
                elif strategy in ('casebot', 'gto_lite'):
                    # Rule bots exposed in Custom Game for training/practice
                    strategy_for_type = {
                        'casebot': 'case_based',
                        'gto_lite': 'pot_odds_robot',
                    }[strategy]
                    controller = RuleBotController(
                        player_name=player.name,
                        state_machine=state_machine,
                        strategy=strategy_for_type,
                        llm_config=llm_config,
                        game_id=game_id,
                        owner_id=owner_id,
                        capture_label_repo=capture_label_repo,
                        decision_analysis_repo=decision_analysis_repo,
                    )
                    logger.info(f"[RESTORE] Created RuleBotController for {player.name} ({strategy} → {strategy_for_type})")
                elif strategy == 'chaos':
                    # Chaos: full LLM, full personality, no bounded options
                    controller = AIPlayerController(
                        player_name=player.name,
                        state_machine=state_machine,
                        llm_config=llm_config,
                        game_id=game_id,
                        owner_id=owner_id,
                        capture_label_repo=capture_label_repo,
                        decision_analysis_repo=decision_analysis_repo,
                    )
                    logger.info(f"[RESTORE] Created AIPlayerController (chaos) for {player.name}")
                elif strategy == 'lean':
                    # Lean: minimal LLM prompt, options-bounded
                    from poker.lean_bounded_controller import LeanBoundedController
                    controller = LeanBoundedController(
                        player_name=player.name,
                        state_machine=state_machine,
                        llm_config=llm_config,
                        game_id=game_id,
                        owner_id=owner_id,
                        capture_label_repo=capture_label_repo,
                        decision_analysis_repo=decision_analysis_repo,
                    )
                    logger.info(f"[RESTORE] Created LeanBoundedController for {player.name}")
                else:
                    # Rule-based controller with psychology (e.g., case_based, abc, always_fold)
                    controller = RuleBotController(
                        player_name=player.name,
                        state_machine=state_machine,
                        strategy=strategy,
                        llm_config=llm_config,
                        game_id=game_id,
                        owner_id=owner_id,
                        capture_label_repo=capture_label_repo,
                        decision_analysis_repo=decision_analysis_repo,
                    )
                    logger.info(f"[RESTORE] Created RuleBotController for {player.name} with strategy '{strategy}'")
            else:
                # No bot_types entry — match the new-game route's default of
                # 'standard' (HybridAIController). Without this, games where
                # the front-end omitted bot_types (all opponents on the
                # default) rehydrated every AI as plain chaos, losing
                # bounded options.
                llm_config = player_llm_configs.get(player.name, default_llm_config)
                controller = HybridAIController(
                    player_name=player.name,
                    state_machine=state_machine,
                    llm_config=llm_config,
                    game_id=game_id,
                    owner_id=owner_id,
                    capture_label_repo=capture_label_repo,
                    decision_analysis_repo=decision_analysis_repo,
                )
                logger.info(f"[RESTORE] Created HybridAIController for {player.name} (default fall-through)")

            # Restore persisted state (psychology, prompt_config, assistant
            # memory, confidence/attitude) for EVERY controller, regardless of
            # how it was dispatched above. Previously this block was only
            # reachable via the fall-through, so any game with bot_types
            # populated silently reset tilt/emotional state on every restore.
            if player.name in ai_states:
                saved_state = ai_states[player.name]

                if hasattr(controller, 'assistant') and controller.assistant:
                    saved_messages = saved_state.get('messages', [])
                    memory = [m for m in saved_messages if m.get('role') != 'system']
                    controller.assistant.memory.set_history(memory)

                if 'personality_state' in saved_state:
                    ps = saved_state['personality_state']
                    # personality_traits are now managed by psychology object
                    # They will be restored from controller_states below
                    if hasattr(controller, 'ai_player'):
                        controller.ai_player.confidence = ps.get('confidence', 'Normal')
                        controller.ai_player.attitude = ps.get('attitude', 'Neutral')

                logger.debug(f"[RESTORE] AI state for {player.name} with {len(saved_state.get('messages', []))} messages")

            if player.name in controller_states:
                ctrl_state = controller_states[player.name]

                # Restore unified psychology state
                if ctrl_state.get('psychology'):
                    # Load from new unified format
                    from poker.player_psychology import PlayerPsychology
                    controller.psychology = PlayerPsychology.from_dict(
                        ctrl_state['psychology'],
                        controller.ai_player.personality_config
                    )
                    logger.debug(
                        f"Restored psychology for {player.name}: "
                        f"tilt={controller.psychology.tilt_level:.2f}"
                    )
                else:
                    # Fallback: reconstruct from old separate states (if they exist)
                    if ctrl_state.get('tilt_state'):
                        controller.psychology.tilt = ComposureState.from_tilt_state(ctrl_state['tilt_state'])
                    # Note: elastic_personality is deprecated - new system uses anchors/axes
                    if player.name in emotional_states:
                        controller.psychology.emotional = EmotionalState.from_dict(emotional_states[player.name])

                # Restore prompt_config (toggleable prompt components)
                if ctrl_state.get('prompt_config'):
                    from poker.prompt_config import PromptConfig
                    controller.prompt_config = PromptConfig.from_dict(ctrl_state['prompt_config'])
                    logger.debug(f"Restored prompt_config for {player.name}: {controller.prompt_config}")
                elif ctrl_state.get('prompt_config') is None:
                    logger.warning(f"No prompt_config found for {player.name}, using defaults")

            ai_controllers[player.name] = controller

    return ai_controllers


def update_and_emit_game_state(game_id: str) -> None:
    """Emit the current game state to all clients in the game room.

    Args:
        game_id: The game identifier
    """
    current_game_data = game_state_service.get_game(game_id)
    if not current_game_data:
        return

    game_state = current_game_data['state_machine'].game_state
    game_state_dict = game_state.to_dict()

    # Resolve the human player name once so we can attach their observations of
    # each AI opponent to the corresponding player_dict below.
    human_player_name = next(
        (p.get('name') for p in game_state_dict.get('players', []) if p.get('is_human', False)),
        None,
    )
    memory_manager = current_game_data.get('memory_manager')
    opponent_models = (
        memory_manager.opponent_model_manager.models
        if memory_manager and hasattr(memory_manager, 'opponent_model_manager')
        else {}
    )
    pressure_stats = current_game_data.get('pressure_stats')

    # Lazy import to avoid circulars; used for is_rule_bot detection.
    from poker.rule_bot_controller import RuleBotController
    from poker.tiered_bot_controller import BaselineSolverBot

    # Add avatar data and psychology to AI players
    ai_controllers = current_game_data.get('ai_controllers', {})
    for player_dict in game_state_dict.get('players', []):
        player_name = player_dict.get('name', '')
        if not player_dict.get('is_human', True) and player_name in ai_controllers:
            controller = ai_controllers[player_name]
            # Run-out reaction overrides take priority over baseline emotion
            runout_overrides = current_game_data.get('runout_emotion_overrides', {})
            if player_name in runout_overrides:
                display_emotion = runout_overrides[player_name]
            elif controller.psychology is not None:
                display_emotion = controller.psychology.get_display_emotion()
            else:
                display_emotion = 'confident'  # Default for RuleBots
            avatar_url = get_avatar_url_with_fallback(game_id, player_name, display_emotion)
            player_dict['avatar_emotion'] = display_emotion
            player_dict['avatar_url'] = avatar_url

            # Rule-bot flag drives the UI's "bot" badge overlay.
            if isinstance(controller, (RuleBotController, BaselineSolverBot)):
                player_dict['is_rule_bot'] = True

            # Add nickname from personality config (for compact UI display)
            # RuleBasedController has no ai_player, so check first
            if hasattr(controller, 'ai_player') and controller.ai_player:
                nickname = controller.ai_player.personality_config.get('nickname')
                if nickname:
                    player_dict['nickname'] = nickname

            # Add psychology data for heads-up mode display (skip for RuleBots)
            psych = controller.psychology
            if psych is not None:
                psych_data = {
                    'narrative': psych.emotional.narrative if psych.emotional else None,
                    'inner_voice': psych.emotional.inner_voice if psych.emotional else None,
                    'tilt_level': psych.tilt_level,
                    'tilt_category': psych.tilt_category,
                    'tilt_source': psych.tilt.tilt_source if psych.tilt else None,
                    'losing_streak': psych.tilt.losing_streak if psych.tilt else 0,
                }
                player_dict['psychology'] = psych_data
                logger.debug(f"[HeadsUp] Psychology for {player_name}: {psych_data}")

        # Attach observation about this player (from human's perspective, with
        # fallback to any observer that has hands recorded). Powers
        # HeadsUpOpponentPanel without polling the admin-only memory-debug route.
        observation_model = None
        if human_player_name:
            observation_model = opponent_models.get(human_player_name, {}).get(player_name)
        if observation_model is None or observation_model.tendencies.hands_observed == 0:
            for observer_models in opponent_models.values():
                candidate = observer_models.get(player_name)
                if candidate and candidate.tendencies.hands_observed > 0:
                    observation_model = candidate
                    break
        if observation_model is not None:
            tendencies = observation_model.tendencies
            player_dict['observation'] = {
                'hands_observed': tendencies.hands_observed,
                'vpip': round(tendencies.vpip, 2),
                'pfr': round(tendencies.pfr, 2),
                'aggression_factor': round(tendencies.aggression_factor, 2),
                'play_style': tendencies.get_play_style_label(),
            }

        # Attach pressure stats summary so the panel can show heads-up record,
        # biggest pot, and signature move without polling pressure-stats.
        if pressure_stats is not None:
            player_pressure = pressure_stats.player_stats.get(player_name)
            if player_pressure is not None:
                player_dict['pressure_summary'] = player_pressure.get_summary()

    # Add LLM debug info for AI players (when enabled)
    if config.enable_ai_debug:
        ai_player_names = [
            p.get('name') for p in game_state_dict.get('players', [])
            if not p.get('is_human', True)
        ]
        if ai_player_names:
            llm_stats = get_all_players_llm_stats(game_id, ai_player_names)
            for player_dict in game_state_dict.get('players', []):
                player_name = player_dict.get('name', '')
                if player_name in llm_stats:
                    player_dict['llm_debug'] = llm_stats[player_name]

    # Include messages (transform to frontend format)
    messages = format_messages_for_api(current_game_data.get('messages', []))

    game_state_dict['messages'] = messages
    game_state_dict['current_dealer_idx'] = game_state.current_dealer_idx
    game_state_dict['small_blind_idx'] = game_state.small_blind_idx
    game_state_dict['big_blind_idx'] = game_state.big_blind_idx
    game_state_dict['highest_bet'] = game_state.highest_bet
    # Clear player options during run-it-out or non-betting phases (no actions possible).
    # This prevents stale action buttons from appearing in the frontend between hands.
    state_machine = current_game_data['state_machine']
    should_clear = should_clear_player_options(game_state, state_machine)
    if should_clear or not game_state.current_player_options:
        game_state_dict['player_options'] = []
    else:
        game_state_dict['player_options'] = list(game_state.current_player_options)
    game_state_dict['min_raise'] = game_state.min_raise_amount
    game_state_dict['big_blind'] = game_state.current_ante
    game_state_dict['phase'] = state_machine.current_phase.name
    memory_manager = current_game_data.get('memory_manager')
    game_state_dict['hand_number'] = memory_manager.hand_count if memory_manager else 0

    # Include betting context with opponent cover amounts
    betting_context = BettingContext.from_game_state(game_state).to_dict()
    opponent_covers = BettingContext.get_opponent_covers(game_state)
    for cover in opponent_covers:
        controller = ai_controllers.get(cover['name'])
        if controller:
            cover['nickname'] = controller.ai_player.personality_config.get('nickname', cover['name'].split()[0])
        else:
            cover['nickname'] = cover['name'].split()[0]
    betting_context['opponent_covers'] = opponent_covers
    game_state_dict['betting_context'] = betting_context

    # Cash mode metadata — surfaced so the React UI can show the
    # bankroll, the table's buy-in window, and gate the top-up /
    # rebuy buttons on. Tournament games omit this key entirely.
    cash_meta = build_cash_mode_payload(current_game_data, game_state)
    if cash_meta is not None:
        game_state_dict['cash_mode'] = cash_meta

    # Fast-forward indicator: tells the UI whether AI seats are currently
    # resolving via the no-LLM tiered path. Auto-clears in progress_game
    # when action returns to the human.
    game_state_dict['fast_forward'] = bool(current_game_data.get('fast_forward'))

    socketio.emit('update_game_state', {'game_state': game_state_dict}, to=game_id)


def build_cash_mode_payload(current_game_data: dict, game_state) -> Optional[dict]:
    """Cash-mode metadata block for game-state responses.

    Returns the dict the React `cash_mode` field expects (bankroll,
    stake_label, big_blind, buy-in window), or None for tournament
    games. Shared by the WebSocket emit and the REST cold-load
    endpoint so the bankroll pill renders on first paint instead of
    waiting for the first socket frame.
    """
    if not current_game_data.get('cash_mode'):
        return None
    from flask_app.extensions import bankroll_repo, stake_repo
    owner_id_cash = current_game_data.get('owner_id')
    game_id_cash = current_game_data.get('game_id')
    bankroll_chips = 0
    active_loan = None
    if owner_id_cash:
        try:
            bankroll = bankroll_repo.load_player_bankroll(owner_id_cash)
            if bankroll is not None:
                bankroll_chips = bankroll.chips
        except Exception:
            pass
    # `active_loan` shape preserves the legacy frontend contract
    # (amount/floor/rate/lender_id). Sourced from the active stake
    # row when one exists. `floor` defaults to 1.0 since the stake
    # model collapses floor+rate into a single `cut`; the frontend's
    # "amount × floor" display effectively becomes "amount × 1.0",
    # which is the right v1 shim until the React side adopts the
    # stake-native fields.
    if stake_repo is not None and game_id_cash:
        try:
            stake = stake_repo.load_active_for_session(game_id_cash)
            if stake is not None:
                active_loan = {
                    'amount': stake.principal,
                    'floor': 1.0,
                    'rate': stake.cut,
                    'lender_id': stake.staker_id,
                }
        except Exception:
            pass
    big_blind = game_state.current_ante
    return {
        'stake_label': current_game_data.get('cash_stake_label'),
        'bankroll': bankroll_chips,
        'big_blind': big_blind,
        'min_buy_in': big_blind * 40,
        'max_buy_in': big_blind * 100,
        'active_loan': active_loan,
    }


def emit_hole_cards_reveal(game_id: str, game_state) -> None:
    """Emit hole cards for all active players during run-it-out showdown."""
    active_players = [p for p in game_state.players if not p.is_folded]
    if len(active_players) < 2:
        logger.warning(f"Skipping hole card reveal with only {len(active_players)} active player(s)")
        return
    players_cards = {}

    for player in active_players:
        if player.hand:
            players_cards[player.name] = [
                card.to_dict() if hasattr(card, 'to_dict') else card
                for card in player.hand
            ]

    reveal_data = {
        'players_cards': players_cards,
        'community_cards': [
            card.to_dict() if hasattr(card, 'to_dict') else card
            for card in game_state.community_cards
        ]
    }

    socketio.emit('reveal_hole_cards', reveal_data, to=game_id)


def handle_phase_cards_dealt(game_id: str, state_machine, game_state, game_data: dict = None) -> None:
    """Send message about newly dealt community cards and record to hand history.

    Note: Caller is responsible for ensuring this is only called once per phase transition.
    """
    num_cards_dealt = 3 if state_machine.current_phase == PokerPhase.FLOP else 1
    cards = [str(c) for c in game_state.community_cards[-num_cards_dealt:]]
    phase_name = str(state_machine.current_phase)
    message_content = f"{phase_name}: {' '.join(cards)}"
    send_message(game_id, "Table", message_content, "table",
                 phase=phase_name.lower(), cards=cards)

    # Record community cards to hand history
    if game_data:
        memory_manager = game_data.get('memory_manager')
        if memory_manager:
            phase_name = state_machine.current_phase.name  # 'FLOP', 'TURN', 'RIVER'
            memory_manager.hand_recorder.record_community_cards(phase_name, cards)



def _refill_cash_seats(game_id: str, game_data: dict, state_machine) -> None:
    """Cash-mode helper: replace busted AI seats with fresh personalities.

    Called between hands (after HAND_OVER, before the next deal).
    For each non-human player whose stack is 0, picks a new
    personality from the eligible pool (not already seated), debits
    that AI's bankroll for a fresh buy-in, and swaps the Player tuple
    entry in-place. Also wires a new HybridAIController into
    `ai_controllers` keyed on the new display name.

    The human seat is left alone — player rebuy is a separate UX
    decision (v1: player has to leave + come back; v2 may add a
    "Rebuy" button between hands).
    """
    from datetime import datetime
    from poker.poker_game import Player
    from poker.hybrid_ai_controller import HybridAIController
    from cash_mode.bankroll import AIBankrollState, project_bankroll
    from flask_app.extensions import (
        bankroll_repo, personality_repo,
        capture_label_repo, decision_analysis_repo,
    )

    game_state = state_machine.game_state
    busted_indices = [
        i for i, p in enumerate(game_state.players)
        if not p.is_human and p.stack == 0
    ]
    if not busted_indices:
        return

    occupied_names = {
        p.name for p in game_state.players if p.stack > 0
    }
    big_blind = game_state.current_ante
    min_buy_in = big_blind * 40
    max_buy_in = big_blind * 100

    owner_id = game_data.get('owner_id')
    sandbox_id = _sandbox_id_for(game_data)
    eligible = personality_repo.list_eligible_for_cash_mode(user_id=owner_id)
    eligible_pool = [
        e for e in eligible
        if e['name'] not in occupied_names
        # Don't reseat a personality whose name matches a busted seat
        # we're about to remove (rare but possible if the eligible
        # query returns it twice).
        and e['name'] not in {game_state.players[i].name for i in busted_indices}
    ]

    now = datetime.utcnow()
    refilled_count = 0

    for seat_idx in busted_indices:
        old_player = game_state.players[seat_idx]
        replacement = None
        replacement_buy_in = 0
        replacement_state = None
        replacement_pre_regen_chips = 0

        # Find an affordable, eligible replacement
        for candidate in list(eligible_pool):
            pid = candidate['personality_id']
            knobs = bankroll_repo.load_personality_knobs(pid)
            threshold = round(min_buy_in * knobs.buy_in_multiplier)
            buy_in = min(threshold, max_buy_in)

            stored = bankroll_repo.load_ai_bankroll(pid, sandbox_id=sandbox_id)
            if stored is None:
                projected = knobs.starting_bankroll
                stored = AIBankrollState(personality_id=pid, chips=projected, last_regen_tick=None)
            else:
                projected = project_bankroll(
                    stored, knobs.starting_bankroll, knobs.bankroll_rate, now,
                )
            if projected < threshold:
                continue

            replacement = candidate
            replacement_buy_in = buy_in
            replacement_state = AIBankrollState(
                personality_id=pid,
                chips=projected - buy_in,
                last_regen_tick=now,
            )
            replacement_pre_regen_chips = stored.chips
            eligible_pool.remove(candidate)
            break

        if replacement is None:
            logger.info(
                "[CASH] Refill: no eligible replacement for busted %r at seat %d",
                old_player.name, seat_idx,
            )
            continue

        # Swap player tuple entry. update_player keeps a stable
        # position; we rebuild it with the new name + stack + zero bet.
        new_player = Player(
            name=replacement['name'],
            stack=replacement_buy_in,
            is_human=False,
        )
        new_players = tuple(
            new_player if i == seat_idx else p
            for i, p in enumerate(game_state.players)
        )
        game_state = game_state.update(players=new_players)
        state_machine.game_state = game_state

        # Persist AI bankroll debit
        bankroll_repo.save_ai_bankroll(replacement_state, sandbox_id=sandbox_id)
        # Record any regen that this write commits. Transfer to table
        # stack is a pure non-bank move and isn't ledger-worthy.
        from flask_app.extensions import chip_ledger_repo
        from core.economy import ledger as chip_ledger
        # replacement_state.chips = projected - buy_in, so we
        # reconstruct projected = chips + buy_in to compare against
        # the pre-regen stored value.
        chip_ledger.record_ai_regen(
            chip_ledger_repo,
            personality_id=replacement_state.personality_id,
            stored_chips=replacement_pre_regen_chips,
            projected_chips=replacement_state.chips + replacement_buy_in,
            context={'game_id': game_id, 'site': 'cash_refill', 'sandbox_id': sandbox_id},
            sandbox_id=sandbox_id,
        )

        # Swap controller registry: remove old, build new
        ai_controllers = game_data.get('ai_controllers', {})
        ai_controllers.pop(old_player.name, None)
        new_controller = HybridAIController(
            replacement['name'],
            state_machine,
            llm_config=game_data.get('llm_config', {}),
            prompt_config=None,  # default
            game_id=game_id,
            owner_id=owner_id,
            capture_label_repo=capture_label_repo,
            decision_analysis_repo=decision_analysis_repo,
        )
        ai_controllers[replacement['name']] = new_controller

        # Initialize memory for the new player
        memory_manager = game_data.get('memory_manager')
        if memory_manager is not None:
            try:
                pid = personality_repo.resolve_name_to_personality_id(
                    replacement['name'],
                )
            except Exception:
                pid = None
            memory_manager.initialize_for_player(
                replacement['name'], personality_id=pid,
            )
            new_controller.session_memory = memory_manager.get_session_memory(
                replacement['name'],
            )
            new_controller.opponent_model_manager = memory_manager.get_opponent_model_manager()
            new_controller.memory_manager = memory_manager

        # Update the cash-personality-id map so emit knows the mapping
        cash_pids = game_data.get('cash_personality_ids', {})
        cash_pids.pop(old_player.name, None)
        cash_pids[replacement['name']] = replacement['personality_id']
        game_data['cash_personality_ids'] = cash_pids

        refilled_count += 1
        logger.info(
            "[CASH] Refilled seat %d: %r → %r (buy_in=%d)",
            seat_idx, old_player.name, replacement['name'], replacement_buy_in,
        )

    if refilled_count > 0:
        # Sync the updated game_state back to the service
        game_data['state_machine'] = state_machine
        game_state_service.set_game(game_id, game_data)


def _refresh_lobby_table_for_session(game_id: str, game_data: dict, state_machine) -> None:
    """Hand-boundary lobby refresh for the player's active table.

    Four responsibilities, in order:

      1. Reconcile busted slots: any persisted AI slot whose pid is no
         longer in `cash_personality_ids` (because `_refill_cash_seats`
         already swapped it out in game state) is paired FIFO with the
         replacement pid and rewritten. Without this step, the next
         step's `refresh_table_roster` would see a chips=0 ghost AI in
         the slot, forced_leave it, and then live-fill a *different*
         AI on top of the in-memory refill — the duplicate-player bug.

      2. Sync the table's persisted AI chip counts to the live
         `Player.stack` values from this game. Without this, the AIs
         on the table look frozen at their previous chip counts even
         while a hand changed their stacks.

      3. Run `refresh_table_roster` so AI movement (stake_up,
         take_break, forced_leave, bored_move) and live-fill happen
         alongside the regular hand-end flow. Live-fill defers
         freshly-vacated seats by one tick — a chair sits empty for at
         least one hand before someone new sits down.

      4. Mirror persisted seat changes into game state: AIs that
         voluntarily left get removed from `game_state.players` /
         `ai_controllers` / `cash_personality_ids`; AIs that joined
         (via live-fill) get added through `_seat_freshly_filled_ais`.

    Failures are caught by the caller — a flaky lobby refresh shouldn't
    block the hand from advancing.
    """
    table_id = game_data.get('cash_table_id')
    if not table_id:
        # Lobby v1.5 didn't tag the game with a table id (older /api/cash/start
        # path). Nothing to refresh.
        return

    import random
    from datetime import datetime
    from cash_mode.lobby import _global_seated_set
    from cash_mode.movement import refresh_table_roster
    from cash_mode.stakes_ladder import STAKES_ORDER, table_buy_in_window
    from cash_mode.tables import ai_slot, human_slot, open_slot
    from flask_app.extensions import bankroll_repo, cash_table_repo, personality_repo
    from cash_mode.bankroll import AIBankrollState

    sandbox_id = _sandbox_id_for(game_data)
    table = cash_table_repo.load_table(table_id, sandbox_id=sandbox_id)
    if table is None:
        logger.warning("[CASH][LOBBY] table %r not found for hand-boundary refresh", table_id)
        return

    cash_pids = game_data.get('cash_personality_ids', {})
    current_ai_pids = set(cash_pids.values())
    name_to_pid = dict(cash_pids)

    # Live: pid → chips (from game state, the source of truth).
    pid_to_chips: Dict[str, int] = {}
    human_owner_id = game_data.get('owner_id')
    human_chips = 0
    for player in state_machine.game_state.players:
        if player.is_human:
            human_chips = int(player.stack)
            continue
        pid = name_to_pid.get(player.name)
        if pid:
            pid_to_chips[pid] = int(player.stack)

    # 1. Reconciliation: persisted AI slots whose pid isn't in the live
    # game state were busted+replaced by `_refill_cash_seats`. Pair them
    # FIFO with the fresh pids that are in game state but have no
    # persisted slot. Any leftover busted slot (no replacement was
    # found, e.g., no eligible AI affording the buy-in) becomes "open".
    persisted_ai_slot_indices = [
        (i, s["personality_id"]) for i, s in enumerate(table.seats)
        if s["kind"] == "ai"
    ]
    busted_slot_indices = [
        i for i, pid in persisted_ai_slot_indices
        if pid not in current_ai_pids
    ]
    persisted_ai_pid_set = {pid for _, pid in persisted_ai_slot_indices}
    fresh_pids_needing_slot = [
        pid for pid in current_ai_pids if pid not in persisted_ai_pid_set
    ]
    reseat_map: Dict[int, str] = {
        slot_idx: new_pid
        for slot_idx, new_pid in zip(busted_slot_indices, fresh_pids_needing_slot)
    }
    leftover_busted = busted_slot_indices[len(fresh_pids_needing_slot):]

    # 2. Sync: rewrite each persisted slot using game-state truth.
    synced_seats: List[Dict] = []
    for i, slot in enumerate(table.seats):
        if slot["kind"] == "ai":
            if i in reseat_map:
                new_pid = reseat_map[i]
                synced_seats.append(ai_slot(new_pid, pid_to_chips.get(new_pid, 0)))
            elif i in leftover_busted:
                synced_seats.append(open_slot())
            else:
                pid = slot["personality_id"]
                new_chips = pid_to_chips.get(pid, int(slot.get("chips", 0)))
                synced_seats.append(ai_slot(pid, new_chips))
        elif slot["kind"] == "human" and human_owner_id:
            synced_seats.append(human_slot(human_owner_id, human_chips))
        else:
            synced_seats.append(dict(slot))

    from cash_mode.tables import CashTableState
    synced_table = CashTableState(
        table_id=table.table_id,
        stake_label=table.stake_label,
        seats=synced_seats,
        created_at=table.created_at,
        last_activity_at=table.last_activity_at,
        dealer_idx=table.dealer_idx,
    )

    # Pids in the persisted table after reconciliation, used below to
    # detect voluntary departures by diffing against `result.new_table`.
    pre_refresh_ai_pids = {
        s["personality_id"] for s in synced_seats if s["kind"] == "ai"
    }

    # 2. Refresh movement + live-fill for this table.
    now = datetime.utcnow()
    big_blind, table_min_buy_in, table_max_buy_in = table_buy_in_window(synced_table.stake_label)
    try:
        stake_idx = STAKES_ORDER.index(synced_table.stake_label)
    except ValueError:
        return
    next_tier_min_buy_in = None
    if stake_idx + 1 < len(STAKES_ORDER):
        _, next_min, _ = table_buy_in_window(STAKES_ORDER[stake_idx + 1])
        next_tier_min_buy_in = next_min

    all_tables = cash_table_repo.list_all_tables(sandbox_id=sandbox_id)
    seated_globally = _global_seated_set([t for t in all_tables if t.table_id != table_id])
    seated_globally.update(s["personality_id"] for s in synced_seats if s["kind"] == "ai")

    eligible = personality_repo.list_eligible_for_cash_mode(user_id=human_owner_id)

    # Build a pid → controller map (live controllers carry psych state).
    # ai_controllers is keyed by display name, so resolve via cash_pids.
    ai_controllers = game_data.get('ai_controllers', {}) or {}
    pid_to_name = {pid: name for name, pid in cash_pids.items()}
    pid_to_controller = {
        pid: ai_controllers.get(pid_to_name.get(pid))
        for pid in current_ai_pids
    }

    # Advance per-controller detached-zone counter once per hand boundary.
    # Pure read of psychology.primary_zone — if 'detached', increment;
    # any other zone (including 'neutral') resets the streak. The counter
    # lives on the controller object so it survives across hands at the
    # same seat but resets when the AI leaves and a new controller is
    # built on re-entry.
    for pid, ctrl in pid_to_controller.items():
        if ctrl is None:
            continue
        psych = getattr(ctrl, 'psychology', None)
        if psych is None:
            continue
        try:
            zone = getattr(psych, 'primary_zone', 'neutral')
        except Exception:
            zone = 'neutral'
        prior = getattr(ctrl, '_detached_hands', 0)
        ctrl._detached_hands = (prior + 1) if zone == 'detached' else 0

    def _psych_lookup(pid: str) -> Dict[str, Any]:
        ctrl = pid_to_controller.get(pid)
        if ctrl is None:
            return {}
        psych = getattr(ctrl, 'psychology', None)
        if psych is None:
            return {}
        try:
            zone = getattr(psych, 'primary_zone', 'neutral')
        except Exception:
            zone = 'neutral'
        try:
            intensity = min(1.0, float(psych.zone_effects.total_penalty_strength))
        except Exception:
            intensity = 0.0
        return {
            'energy': float(getattr(psych, 'energy', 0.5)),
            'zone': zone,
            'hands_in_detached_zone': int(getattr(ctrl, '_detached_hands', 0)),
            'emotional_intensity': intensity,
        }

    def _bankroll_lookup(pid: str):
        current = bankroll_repo.load_ai_bankroll_current(pid, sandbox_id=sandbox_id, now=now)
        if current is not None:
            return current
        # No row yet — fall back to the personality's cap (mirrors the
        # seed path). Keeps a freshly-added personality from being
        # locked out of hand-boundary live-fill while the boot-time
        # `ensure_ai_bankrolls_seeded` is racing the first lobby load.
        return bankroll_repo.load_personality_knobs(pid).starting_bankroll

    _buy_in_cache: Dict[str, int] = {}

    def _buy_in_lookup(pid: str) -> int:
        if pid not in _buy_in_cache:
            knobs = bankroll_repo.load_personality_knobs(pid)
            threshold = round(table_min_buy_in * knobs.buy_in_multiplier)
            _buy_in_cache[pid] = min(threshold, table_max_buy_in)
        return _buy_in_cache[pid]

    idle_pool = cash_table_repo.list_idle(sandbox_id=sandbox_id)
    rng = random.Random()
    result = refresh_table_roster(
        synced_table,
        idle_pool=idle_pool,
        eligible_candidates=eligible,
        seated_globally=seated_globally,
        bankroll_lookup=_bankroll_lookup,
        buy_in_lookup=_buy_in_lookup,
        rng=rng,
        now=now,
        stake_idx=stake_idx,
        table_min_buy_in=table_min_buy_in,
        table_max_buy_in=table_max_buy_in,
        next_tier_min_buy_in=next_tier_min_buy_in,
        defer_freshly_vacated_live_fill=True,
        psych_lookup=_psych_lookup,
    )

    # Apply rebuy decisions: debit each AI's bankroll for the top-up
    # and mirror the new seat chips onto the live Player.stack.
    # refresh_table_roster has already updated result.new_table.seats
    # with the post-rebuy chip count, so persistence is correct; the
    # work here is the bankroll write and game-state mirror.
    if result.rebuy_changes:
        try:
            _apply_rebuys(
                game_id, game_data, state_machine,
                result.rebuy_changes, pid_to_name, bankroll_repo, now,
                sandbox_id=sandbox_id,
            )
        except Exception as e:
            logger.error("[CASH][LOBBY] rebuy application failed: %s", e, exc_info=True)

    # Persist table + idle changes.
    cash_table_repo.save_table(result.new_table, sandbox_id=sandbox_id, now=now)
    for change in result.idle_changes:
        if change.kind == "add" and change.entry is not None:
            cash_table_repo.save_idle(change.entry, sandbox_id=sandbox_id)
        elif change.kind == "remove":
            cash_table_repo.delete_idle(change.personality_id, sandbox_id=sandbox_id)

    # Surface movement to the seated player's in-game chat. The lobby
    # ticker (cash_mode/activity.py) is unaffected — those events are
    # written by the unseated-table refresh path, not this one. Here
    # we just want the player at the table to see "X left with $Y"
    # / "X rebought for $Z" alongside their hand history.
    try:
        _emit_seated_movement_chat(
            game_id, synced_table, result, pid_to_name,
        )
    except Exception as e:
        logger.warning("[CASH][LOBBY] movement chat emission failed: %s", e)

    # 4a. Mirror voluntary departures into game state: AIs that
    # `refresh_table_roster` moved off the persisted table (take_break,
    # stake_up, forced_leave, bored_move) must also leave the live game.
    # Without this step, the AI keeps a seat in `game_state.players`
    # while live-fill drops a *different* AI on top — the same drift
    # symptom as the bust path.
    post_refresh_ai_pids = {
        s["personality_id"] for s in result.new_table.seats if s["kind"] == "ai"
    }
    departed_pids = pre_refresh_ai_pids - post_refresh_ai_pids
    if departed_pids:
        try:
            _remove_departed_ais_from_game(
                game_id, game_data, state_machine, departed_pids,
            )
        except Exception as e:
            logger.error(
                "[CASH][LOBBY] departure-sync failed: %s", e, exc_info=True,
            )

    # 4b. Add controllers for freshly-seated AIs (mid-session live fill).
    if result.freshly_seated_personality_ids:
        try:
            _seat_freshly_filled_ais(
                game_id, game_data, state_machine,
                result.new_table, result.freshly_seated_personality_ids,
            )
        except Exception as e:
            logger.error(
                "[CASH][LOBBY] live-fill controller install failed: %s",
                e, exc_info=True,
            )


def _apply_rebuys(
    game_id: str,
    game_data: dict,
    state_machine,
    rebuy_changes,
    pid_to_name: Dict[str, str],
    bankroll_repo,
    now,
    *,
    sandbox_id: Optional[str],
) -> None:
    """Execute pressure-driven rebuys: bankroll debit + Player.stack bump.

    `refresh_table_roster` already wrote the post-rebuy chip count to
    `result.new_table.seats` (so the persisted table is correct on
    the upcoming `save_table` call). The remaining work is:

      1. Debit the AI's bankroll by the rebuy amount — chips moved
         from the AI's off-table chips into their seat.
      2. Mirror the new stack onto the live `Player` in game state so
         the engine deals the next hand with the right chip count.

    Failures are logged but don't propagate — a missed bankroll debit
    is a chip-leak, not a session-killer, and the next hand boundary
    will retry the refresh path. The "missing controller / missing
    name" cases are no-ops.
    """
    from cash_mode.bankroll import AIBankrollState, project_bankroll

    if not rebuy_changes:
        return

    game_state = state_machine.game_state
    name_to_player_idx = {p.name: i for i, p in enumerate(game_state.players)}
    updated = False

    for change in rebuy_changes:
        name = pid_to_name.get(change.personality_id)
        if not name:
            continue
        # 1. Bankroll debit. Mirror the pattern used by _refill_cash_seats
        # for fresh seats: project to `now`, subtract, persist. Pure
        # transfer — chips moved bankroll → seat, no ledger entry needed
        # (matches credit_ai_cash_out's complement on the leave path).
        try:
            knobs = bankroll_repo.load_personality_knobs(change.personality_id)
            stored = bankroll_repo.load_ai_bankroll(change.personality_id, sandbox_id=sandbox_id)
            if stored is None:
                # Defensive: an AI without a bankroll row shouldn't be
                # rolling rebuy in the first place (the pressure model
                # only fires when projected bankroll signals affordability).
                # Skip the debit and keep going.
                logger.warning(
                    "[CASH][LOBBY] rebuy: no bankroll row for %r; seat chips bumped without debit",
                    change.personality_id,
                )
            else:
                projected = project_bankroll(
                    stored, knobs.starting_bankroll, knobs.bankroll_rate, now,
                )
                new_chips = max(0, projected - change.amount)
                bankroll_repo.save_ai_bankroll(AIBankrollState(
                    personality_id=change.personality_id,
                    chips=new_chips,
                    last_regen_tick=now,
                ), sandbox_id=sandbox_id)
        except Exception as e:
            logger.warning(
                "[CASH][LOBBY] rebuy bankroll debit failed for %r (+%d): %s",
                change.personality_id, change.amount, e,
            )

        # 2. Mirror to live game state.
        idx = name_to_player_idx.get(name)
        if idx is None:
            continue
        state_machine.game_state = state_machine.game_state.update_player(
            idx, stack=change.new_seat_chips,
        )
        updated = True
        logger.info(
            "[CASH][LOBBY] %r rebought +%d (new stack %d)",
            name, change.amount, change.new_seat_chips,
        )

    if updated:
        game_data['state_machine'] = state_machine
        game_state_service.set_game(game_id, game_data)


def _emit_seated_movement_chat(
    game_id: str,
    table_pre_refresh,
    result,
    pid_to_name: Dict[str, str],
) -> None:
    """Push system chat messages for each movement event at the seated table.

    Four event kinds, each with differentiated phrasing per user spec:
      - rebuy:     "{name} added ${amount} in chips"
      - leave:     wording varies by reason
                     * forced_leave   → "{name} busted out with ${chips}"
                     * stake_up       → "{name} moved up to {next_stake}"
                     * take_break     → "{name} stepped away with ${chips}"
                     * bored_move     → "{name} got restless and left with ${chips}"
      - join:      "{name} sat down with ${amount}"   (live-fill)

    Sender is "Table". Message type is "system" so the React chat
    renders it with the settings/system styling (matches lobby
    join/leave ticker convention without being shouty).
    """
    from cash_mode.stakes_ladder import STAKES_ORDER

    pre_seats = {
        i: dict(s) for i, s in enumerate(table_pre_refresh.seats)
    }

    # Leaves: any pid whose seat went from 'ai' to 'open' in this
    # refresh, with reason from result.decisions.
    for change in result.idle_changes:
        if change.kind != "add" or change.entry is None:
            continue
        pid = change.personality_id
        name = pid_to_name.get(pid) or pid
        reason = change.entry.reason
        # Find the chips they left with from pre-refresh seats.
        prev_chips = 0
        for slot in pre_seats.values():
            if slot.get("kind") == "ai" and slot.get("personality_id") == pid:
                prev_chips = int(slot.get("chips", 0))
                break
        if reason == "forced_leave":
            text = f"{name} busted out with ${prev_chips}"
        elif reason == "stake_up_queued":
            target = change.entry.target_stake or "the next stake"
            text = f"{name} moved up to {target}"
        elif reason == "take_break":
            text = f"{name} stepped away with ${prev_chips}"
        elif reason == "bored_move":
            text = f"{name} got restless and left with ${prev_chips}"
        else:
            text = f"{name} left with ${prev_chips}"
        send_message(
            game_id=game_id,
            sender="Table",
            content=text,
            message_type="system",
        )

    # Rebuys.
    for change in result.rebuy_changes:
        name = pid_to_name.get(change.personality_id) or change.personality_id
        send_message(
            game_id=game_id,
            sender="Table",
            content=f"{name} added ${change.amount} in chips",
            message_type="system",
        )

    # Live-fill joins: pids freshly seated. Names come from the new
    # table (they may not be in pid_to_name yet — that mapping gets
    # updated by _seat_freshly_filled_ais).
    pid_to_chips_post = {
        s["personality_id"]: int(s.get("chips", 0))
        for s in result.new_table.seats
        if s["kind"] == "ai"
    }
    for pid in result.freshly_seated_personality_ids:
        # _seat_freshly_filled_ais resolves the display name from
        # personality_repo; we do the same lookup here. Fallback to
        # pid keeps the message visible even if the repo lookup fails.
        from flask_app.extensions import personality_repo
        try:
            personality = personality_repo.load_personality_by_id(pid)
        except Exception:
            personality = None
        display_name = (personality or {}).get("name") or pid
        chips = pid_to_chips_post.get(pid, 0)
        send_message(
            game_id=game_id,
            sender="Table",
            content=f"{display_name} sat down with ${chips}",
            message_type="system",
        )


def _remove_departed_ais_from_game(
    game_id: str,
    game_data: dict,
    state_machine,
    departed_pids,
) -> None:
    """Symmetric inverse of `_seat_freshly_filled_ais`: drop AIs that
    voluntarily left the persisted table from the running game so the
    next hand isn't dealt to ghost players.

    The chips on the departing player's seat are not credited back to
    the AI bankroll — that's the existing v1 behavior (chips stay
    "on the table" conceptually). If/when leave-time cash-out is
    extended to voluntary moves, plumb the credit through here.
    """
    if not departed_pids:
        return

    cash_pids = game_data.get('cash_personality_ids', {})
    pid_to_name = {pid: name for name, pid in cash_pids.items()}
    departed_names = {
        pid_to_name[pid] for pid in departed_pids if pid in pid_to_name
    }
    if not departed_names:
        return

    game_state = state_machine.game_state
    remaining_players = tuple(
        p for p in game_state.players if p.name not in departed_names
    )
    if len(remaining_players) == len(game_state.players):
        return

    state_machine.game_state = game_state.update(players=remaining_players)

    ai_controllers = game_data.get('ai_controllers', {})
    for name in departed_names:
        ai_controllers.pop(name, None)
        cash_pids.pop(name, None)
        logger.info(
            "[CASH][LOBBY] removed departed AI %r from game state", name,
        )

    game_data['ai_controllers'] = ai_controllers
    game_data['cash_personality_ids'] = cash_pids
    game_data['state_machine'] = state_machine
    game_state_service.set_game(game_id, game_data)


def _seat_freshly_filled_ais(
    game_id: str,
    game_data: dict,
    state_machine,
    new_table,
    freshly_seated_pids,
):
    """Mid-session live-fill: drop new AIs into the running game.

    Mirrors `_refill_cash_seats`'s controller-build path. For each
    freshly seated personality:
      - Insert a new `Player` into the game state with the buy-in stack
        carried from the lobby refresh.
      - Build a HybridAIController and register it in `ai_controllers`.
      - Initialize memory + opponent model wiring.
      - Update `cash_personality_ids` so the leave-time cash-out knows
        the personality.

    Game state is keyed on player NAME, not seat index; we append the
    new player at the end. The state machine's seat order will pick
    them up on the next hand.
    """
    from poker.hybrid_ai_controller import HybridAIController
    from poker.poker_game import Player
    from flask_app.extensions import (
        capture_label_repo, decision_analysis_repo, personality_repo,
    )

    game_state = state_machine.game_state
    occupied_names = {p.name for p in game_state.players}
    ai_controllers = game_data.get('ai_controllers', {})
    cash_pids = game_data.get('cash_personality_ids', {})
    memory_manager = game_data.get('memory_manager')
    owner_id = game_data.get('owner_id')

    # Find the AI chips on the new table for these personalities.
    pid_to_chips = {
        slot["personality_id"]: int(slot["chips"])
        for slot in new_table.seats
        if slot["kind"] == "ai" and slot["personality_id"] in freshly_seated_pids
    }

    for pid in freshly_seated_pids:
        try:
            personality = personality_repo.load_personality_by_id(pid)
        except Exception:
            personality = None
        name = (personality or {}).get("name") if personality else pid
        if not name or name in occupied_names:
            continue
        chips = pid_to_chips.get(pid, 0)
        if chips <= 0:
            continue

        new_player = Player(name=name, stack=chips, is_human=False)
        new_players = tuple(list(game_state.players) + [new_player])
        game_state = game_state.update(players=new_players)
        state_machine.game_state = game_state
        occupied_names.add(name)

        controller = HybridAIController(
            name,
            state_machine,
            llm_config=game_data.get('llm_config', {}),
            prompt_config=None,
            game_id=game_id,
            owner_id=owner_id,
            capture_label_repo=capture_label_repo,
            decision_analysis_repo=decision_analysis_repo,
        )
        ai_controllers[name] = controller

        if memory_manager is not None:
            try:
                memory_manager.initialize_for_player(name, personality_id=pid)
                controller.session_memory = memory_manager.get_session_memory(name)
                controller.opponent_model_manager = memory_manager.get_opponent_model_manager()
                controller.memory_manager = memory_manager
            except Exception as e:
                logger.warning(
                    "[CASH][LOBBY] memory init failed for live-fill %r: %s",
                    name, e,
                )

        cash_pids[name] = pid
        logger.info(
            "[CASH][LOBBY] mid-session live fill: seated %r at chips=%d",
            pid, chips,
        )

    game_data['ai_controllers'] = ai_controllers
    game_data['cash_personality_ids'] = cash_pids
    game_data['state_machine'] = state_machine
    game_state_service.set_game(game_id, game_data)


def _detect_human_cash_bust(game_id: str, game_data: dict, state_machine) -> None:
    """Emit a SocketIO bust event when the human's stack hits 0 between hands.

    Symmetric to `_refill_cash_seats` but for the player seat — the
    server already has authoritative game state, so we don't make the
    frontend poll. Two distinct events so the modal can branch cleanly:

      - `cash_rebuy_needed`: bankroll >= table's min_buy_in. Player
        can rebuy from their own bankroll. Modal offers Rebuy /
        Top-up-to-max / Leave.
      - `cash_bust`: bankroll < table's min_buy_in (typically 0).
        Player can't rebuy here; must leave to `/cash` to find a
        sponsor or pick a lower stake.

    Payload includes the data the frontend needs to render either
    modal without a follow-up fetch (bankroll, min_buy_in, max_buy_in,
    stake_label, has_active_loan). Safe no-op if the human's stack
    is non-zero — exits early.
    """
    from cash_mode.stakes_ladder import table_buy_in_window
    from flask_app.extensions import bankroll_repo, stake_repo

    game_state = state_machine.game_state
    human_idx = next(
        (i for i, p in enumerate(game_state.players) if p.is_human),
        None,
    )
    if human_idx is None:
        return
    human_player = game_state.players[human_idx]
    if human_player.stack != 0:
        return

    stake_label = game_data.get('cash_stake_label')
    if not stake_label:
        return
    try:
        _, min_buy_in, max_buy_in = table_buy_in_window(stake_label)
    except KeyError:
        return

    owner_id = game_data.get('owner_id')
    bankroll = bankroll_repo.load_player_bankroll(owner_id) if owner_id else None
    bankroll_chips = bankroll.chips if bankroll else 0
    # An active stake blocks rebuy regardless of bankroll — chips on
    # the stake settle at /leave; mingling fresh bankroll chips would
    # corrupt the staker's `cut` math (see rebuy / top_up gates).
    has_active_loan = bool(
        stake_repo is not None
        and stake_repo.load_active_for_session(game_id) is not None
    )

    event_name = (
        'cash_rebuy_needed'
        if bankroll_chips >= min_buy_in and not has_active_loan
        else 'cash_bust'
    )
    socketio.emit(event_name, {
        'game_id': game_id,
        'stake_label': stake_label,
        'min_buy_in': min_buy_in,
        'max_buy_in': max_buy_in,
        'bankroll': bankroll_chips,
        'has_active_loan': has_active_loan,
    }, to=game_id)
    logger.info(
        "[CASH] Human bust at %r owner=%r stake=%r bankroll=%d had_loan=%s emitted=%s",
        game_id, owner_id, stake_label, bankroll_chips, has_active_loan, event_name,
    )


def handle_eliminations(game_id: str, game_data: dict, game_state,
                        winning_player_names: list, pot_size: int,
                        final_hand_data: dict = None) -> Optional[bool]:
    """Handle player eliminations. Returns True if human was eliminated.

    Args:
        final_hand_data: Winner announcement data to include in tournament_complete event
    """
    if 'tournament_tracker' not in game_data:
        return None

    tracker = game_data['tournament_tracker']
    tracker.on_hand_complete(pot_size)

    eliminated_players = [p for p in game_state.players if p.stack == 0]
    eliminator = winning_player_names[0] if winning_player_names else None

    human_eliminated = False
    human_elimination_event = None

    for player in eliminated_players:
        try:
            event = tracker.on_player_eliminated(
                player_name=player.name,
                eliminator=eliminator,
                pot_size=pot_size
            )

            if player.is_human:
                human_eliminated = True
                human_elimination_event = event

            socketio.emit('player_eliminated', {
                'eliminated': player.name,
                'eliminator': eliminator,
                'finishing_position': event.finishing_position,
                'hand_number': event.hand_number,
                'remaining_players': tracker.active_player_count
            }, to=game_id)

            position_suffix = 'st' if event.finishing_position == 1 else 'nd' if event.finishing_position == 2 else 'rd' if event.finishing_position == 3 else 'th'
            send_message(game_id, "Table",
                f"{player.name} has been eliminated in {event.finishing_position}{position_suffix} place!",
                "system")
        except ValueError as e:
            logger.warning(f"Failed to record elimination for {player.name} in game {game_id}: {e}")

    if human_eliminated and human_elimination_event:
        result = tracker.get_result()
        result['winner_name'] = None
        result['human_eliminated'] = True

        try:
            owner_id, owner_name = game_state_service.get_game_owner_info(game_id)
            result['owner_id'] = owner_id
            tournament_repo.save_tournament_result(game_id, result)
            human_player = tracker.get_human_player()
            if human_player and owner_id:
                tournament_repo.update_career_stats(owner_id, human_player['name'], result)
        except Exception as e:
            logger.error(f"Failed to save tournament result after human elimination: {e}")

        position_suffix = 'st' if human_elimination_event.finishing_position == 1 else 'nd' if human_elimination_event.finishing_position == 2 else 'rd' if human_elimination_event.finishing_position == 3 else 'th'
        socketio.emit('tournament_complete', {
            'winner': None,
            'standings': result['standings'],
            'total_hands': result['total_hands'],
            'biggest_pot': result['biggest_pot'],
            'human_position': human_elimination_event.finishing_position,
            'human_eliminated': True,
            'game_id': game_id,
            'final_hand_data': final_hand_data
        }, to=game_id)

        send_message(game_id, "Table",
            f"You finished in {human_elimination_event.finishing_position}{position_suffix} place!",
            "system")

        return True

    return False


def prepare_showdown_data(game_state, winner_info: dict, winning_player_names: list,
                          is_final_hand: bool = False,
                          tournament_outcome: dict = None) -> dict:
    """Prepare winner announcement data for showdown.

    Args:
        game_state: Current game state
        winner_info: Winner info from determine_winner
        winning_player_names: List of winner names
        is_final_hand: Whether this is the final hand of the tournament
        tournament_outcome: Dict with 'human_won' (bool) and 'human_position' (int)
    """
    active_players = [p for p in game_state.players if not p.is_folded]
    is_showdown = len(active_players) > 1

    # Get pot contributions for each player (for net profit display)
    pot_contributions = {}
    if isinstance(game_state.pot, dict):
        for key, value in game_state.pot.items():
            if key != 'total':  # Skip the 'total' key, only include player contributions
                pot_contributions[key] = value

    winner_data = {
        'winners': winning_player_names,
        'pot_breakdown': winner_info.get('pot_breakdown', []),
        'pot_contributions': pot_contributions,  # Player name -> amount contributed
        'showdown': is_showdown,
        'community_cards': [],
    }

    if is_final_hand:
        winner_data['is_final_hand'] = True
    if tournament_outcome:
        winner_data['tournament_outcome'] = tournament_outcome

    if is_showdown:
        winner_data['hand_name'] = winner_info['hand_name']

    # Include community cards
    for card in game_state.community_cards:
        if hasattr(card, 'to_dict'):
            winner_data['community_cards'].append(card.to_dict())
        elif isinstance(card, dict):
            winner_data['community_cards'].append(card)
        else:
            winner_data['community_cards'].append({'rank': str(card), 'suit': ''})

    if is_showdown:
        players_showdown = {}
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

                    if isinstance(card, Card):
                        player_cards_for_eval.append(card)
                    elif isinstance(card, dict):
                        player_cards_for_eval.append(Card(card['rank'], card['suit']))

                try:
                    full_hand = player_cards_for_eval + community_cards_for_eval
                    hand_result = HandEvaluator(full_hand).evaluate_hand()

                    kicker_values = hand_result.get('kicker_values', [])
                    if kicker_values and isinstance(kicker_values[0], list):
                        kicker_values = kicker_values[0] if kicker_values[0] else []

                    value_names = {14: 'A', 13: 'K', 12: 'Q', 11: 'J', 10: '10',
                                   9: '9', 8: '8', 7: '7', 6: '6', 5: '5',
                                   4: '4', 3: '3', 2: '2'}
                    kicker_names = [value_names.get(v, str(v)) for v in kicker_values if isinstance(v, int)]

                    # eval7 score breaks ties within the same category (higher = stronger).
                    hand_score = 0
                    try:
                        import eval7
                        eval_cards = [eval7.Card(card_to_string(c)) for c in full_hand]
                        hand_score = eval7.evaluate(eval_cards)
                    except Exception as e:
                        logger.warning(f"eval7 scoring failed for {player.name}: {e}")

                    players_showdown[player.name] = {
                        'cards': formatted_cards,
                        'hand_name': hand_result.get('hand_name', 'Unknown'),
                        'hand_rank': hand_result.get('hand_rank', 10),
                        'hand_score': hand_score,
                        'kickers': kicker_names
                    }
                except Exception as e:
                    logger.warning(f"Failed to evaluate hand for {player.name}: {e}")
                    players_showdown[player.name] = {
                        'cards': formatted_cards,
                        'hand_name': None,
                        'hand_rank': 99,
                        'hand_score': 0,
                        'kickers': []
                    }

        winner_data['players_showdown'] = players_showdown

    return winner_data


def generate_ai_commentary(game_id: str, game_data: dict) -> None:
    """Generate AI commentary after hand completion."""
    if 'memory_manager' not in game_data:
        return

    memory_manager = game_data['memory_manager']
    ai_controllers = game_data.get('ai_controllers', {})
    state_machine = game_data.get('state_machine')
    tournament_tracker = game_data.get('tournament_tracker')

    # Get big blind for dynamic thresholds
    big_blind = None
    if state_machine and hasattr(state_machine, 'game_state'):
        big_blind = getattr(state_machine.game_state, 'current_ante', None)

    # Get active players from tournament tracker
    active_players = None
    if tournament_tracker:
        active_players = tournament_tracker._active_players

    # Build elimination lookup for spectator context
    elimination_lookup = {}
    if tournament_tracker:
        for event in tournament_tracker.eliminations:
            elimination_lookup[event.eliminated_player] = event

    # Build ai_players dict with context for each player
    ai_players_with_context = {}
    for name, controller in ai_controllers.items():
        is_eliminated = (active_players is not None and name not in active_players)

        # Build spectator context for eliminated players
        spectator_context = None
        if is_eliminated and name in elimination_lookup:
            event = elimination_lookup[name]
            spectator_context = (
                f"\n\n** SPECTATOR MODE **\n"
                f"You were eliminated in {_ordinal(event.finishing_position)} place "
                f"by {event.eliminator}. You're watching from the rail. "
                f"Heckle your rivals! Mock your eliminator! Root for underdogs!"
            )

        ai_players_with_context[name] = {
            'ai_player': controller.ai_player,
            'is_eliminated': is_eliminated,
            'spectator_context': spectator_context,
        }

    def emit_commentary_immediately(player_name: str, commentary) -> None:
        """Callback to emit commentary as soon as it's ready.

        Also persists commentary to database and attaches decision plans.
        """
        if not commentary:
            return

        # Emit table comment to UI
        if commentary.table_comment:
            logger.info(f"[Commentary] {player_name}: {commentary.table_comment[:80]}...")
            send_message(
                game_id, player_name, commentary.table_comment, "ai",
                addressing=commentary.addressing,
            )

        # Attach decision plans from controller and set hand number
        if player_name in ai_controllers:
            controller = ai_controllers[player_name]
            # Get and clear decision plans for this hand
            plans = controller.clear_decision_plans()
            controller.clear_hand_bluff_likelihood()
            commentary.decision_plans = plans
            logger.debug(f"[Commentary] Attached {len(plans)} decision plans for {player_name}")

        # Set hand number for persistence
        hand_number = memory_manager.hand_count if memory_manager else 0
        commentary.hand_number = hand_number

        # Persist commentary to database
        try:
            if hand_history_repo:
                hand_history_repo.save_hand_commentary(
                    game_id=game_id,
                    hand_number=hand_number,
                    player_name=player_name,
                    commentary=commentary
                )
                logger.info(f"[Commentary] Persisted commentary for {player_name} hand {hand_number}")
            else:
                logger.warning(f"[Commentary] hand_history_repo not available for {player_name}")
        except Exception as e:
            logger.warning(f"[Commentary] Failed to persist commentary for {player_name}: {e}")

        # Feed opponent observations to opponent model
        if memory_manager and hasattr(commentary, 'opponent_observations') and commentary.opponent_observations:
            _feed_opponent_observations(
                memory_manager=memory_manager,
                observer=player_name,
                observations=commentary.opponent_observations
            )

        # Feed strategic reflection to session memory
        if memory_manager and hasattr(commentary, 'strategic_reflection') and commentary.strategic_reflection:
            _feed_strategic_reflection(
                memory_manager=memory_manager,
                player_name=player_name,
                reflection=commentary.strategic_reflection,
                key_insight=getattr(commentary, 'key_insight', None)
            )

    try:
        logger.info(f"[Commentary] Starting generation for {len(ai_players_with_context)} AI players")
        # Pass callback to emit each commentary immediately as it completes
        commentaries = memory_manager.generate_commentary_for_hand(
            ai_players_with_context,
            on_commentary_ready=emit_commentary_immediately,
            big_blind=big_blind
        )
        logger.info(f"[Commentary] Generated {len(commentaries)} commentaries")

        for name, controller in ai_controllers.items():
            # Pass None to skip adjustment (function handles None gracefully)
            memory_manager.apply_learned_adjustments(name, None)
    except Exception as e:
        logger.warning(f"Commentary generation failed: {e}")


def _ordinal(n: int) -> str:
    """Convert number to ordinal string (1st, 2nd, 3rd, etc.)."""
    if 11 <= (n % 100) <= 13:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f"{n}{suffix}"


def check_tournament_complete(game_id: str, game_data: dict, final_hand_data: dict = None) -> bool:
    """Check if tournament is complete and handle if so. Returns True if complete.

    Args:
        final_hand_data: Winner announcement data to include in tournament_complete event
    """
    if 'tournament_tracker' not in game_data:
        return False

    tracker = game_data['tournament_tracker']
    if not tracker.is_complete():
        return False

    result = tracker.get_result()

    try:
        owner_id, owner_name = game_state_service.get_game_owner_info(game_id)
        result['owner_id'] = owner_id
        tournament_repo.save_tournament_result(game_id, result)
        logger.info(f"Tournament {game_id} saved: winner={result['winner_name']}")

        human_player_name = result.get('human_player_name')
        if human_player_name and owner_id:
            tournament_repo.update_career_stats(owner_id, human_player_name, result)
            logger.info(f"Career stats updated for {human_player_name} (owner: {owner_id})")
    except Exception as e:
        logger.error(f"Failed to save tournament result: {e}")

    socketio.emit('tournament_complete', {
        'winner': result['winner_name'],
        'standings': result['standings'],
        'total_hands': result['total_hands'],
        'biggest_pot': result['biggest_pot'],
        'human_position': result.get('human_finishing_position'),
        'game_id': game_id,
        'final_hand_data': final_hand_data
    }, to=game_id)

    send_message(game_id, "Table", f"TOURNAMENT OVER! {result['winner_name']} wins!", "system")
    return True


def _run_async_commentary(game_id: str, game_data: dict,
                          completion_event: threading.Event = None) -> None:
    """Run async commentary generation after winner announcement.

    Psychology updates (tilt, emotional state, recovery) are handled
    synchronously by PsychologyPipeline before this is called.

    Args:
        game_id: The game identifier
        game_data: Game data dictionary
        completion_event: Optional event to signal when commentary completes
    """
    try:
        generate_ai_commentary(game_id, game_data)
    except Exception as e:
        logger.warning(f"Async commentary generation failed: {e}")
    finally:
        if completion_event:
            completion_event.set()


def _apply_player_table_rake(
    *,
    game_id: str,
    game_data: dict,
    game_state,
    winner_info: dict,
    pot_size: int,
):
    """Skim per-hand rake from the headline winner at a player cash table.

    Returns a (possibly updated) game_state. Mirrors the AI-only sim
    helper (`cash_mode.full_sim._apply_rake_to_winner`) but operates
    on the live `game_state` and resolves winner identity via the
    cash-mode `cash_personality_ids` map (AI) or `owner_id` (human).

    No-op unless: cash mode active AND `RAKE_ENABLED` AND
    `RAKE_PLAYER_TABLES`. The first gate is the caller's; the latter
    two are checked here.
    """
    from cash_mode import economy_flags
    from core.economy import ledger as chip_ledger

    if not economy_flags.RAKE_ENABLED or not economy_flags.RAKE_PLAYER_TABLES:
        return game_state

    big_blind = game_state.current_ante
    rake = economy_flags.compute_rake(pot_size, big_blind)
    if rake <= 0:
        return game_state

    # Identify the largest winner from pot_breakdown.
    winnings_by_name: Dict[str, int] = {}
    for pot in winner_info.get('pot_breakdown', []):
        for winner in pot['winners']:
            winnings_by_name[winner['name']] = (
                winnings_by_name.get(winner['name'], 0) + winner['amount']
            )
    if not winnings_by_name:
        return game_state
    headline_name = max(winnings_by_name, key=winnings_by_name.get)
    headline_winnings = winnings_by_name[headline_name]
    rake = min(rake, max(0, headline_winnings))
    if rake <= 0:
        return game_state

    # Deduct from the headline winner's stack.
    _, player_idx = game_state.get_player_by_name(headline_name)
    winner_player = game_state.players[player_idx]
    new_stack = max(0, winner_player.stack - rake)
    game_state = game_state.update_player(player_idx=player_idx, stack=new_stack)

    # Resolve the ledger source string. For AI seats we use the
    # cash-mode personality map; for the human seat we use owner_id.
    cash_pids: Dict[str, str] = game_data.get('cash_personality_ids', {}) or {}
    if winner_player.is_human:
        owner_id = game_data.get('owner_id')
        if not owner_id:
            logger.warning(
                f"[Game {game_id}] rake skipped: human winner with no owner_id"
            )
            return game_state
        source = chip_ledger.player(owner_id)
    else:
        pid = cash_pids.get(headline_name)
        if not pid:
            logger.warning(
                f"[Game {game_id}] rake skipped: no personality_id for AI winner {headline_name!r}"
            )
            return game_state
        source = chip_ledger.ai(pid)

    sandbox_id = _sandbox_id_for(game_data)
    ctx = {
        'site': 'handle_evaluating_hand_phase',
        'game_id': game_id,
        'pot': pot_size,
        'big_blind': big_blind,
        'winner_name': headline_name,
        'winner_is_human': winner_player.is_human,
    }
    from flask_app.extensions import chip_ledger_repo as _ledger_repo
    chip_ledger.record_table_rake(
        _ledger_repo,
        source=source,
        amount=rake,
        context=ctx,
        sandbox_id=sandbox_id,
    )
    logger.info(
        f"[Game {game_id}] table_rake skim: {rake} chips from {headline_name}"
        f" (pot={pot_size}, bb={big_blind})"
    )
    return game_state


def handle_evaluating_hand_phase(game_id: str, game_data: dict, state_machine, game_state):
    """Handle the EVALUATING_HAND phase.

    Returns:
        tuple: (updated_game_state, should_return) - should_return is True if game should end
    """
    winner_info = determine_winner(game_state)
    # Compute winning player names from pot_breakdown
    all_winners = set()
    for pot in winner_info.get('pot_breakdown', []):
        for winner in pot['winners']:
            all_winners.add(winner['name'])
    winning_player_names = list(all_winners)
    pot_size_before_award = game_state.pot.get('total', 0) if isinstance(game_state.pot, dict) else 0

    # Award winnings FIRST so chip counts are updated
    game_state = award_pot_winnings(game_state, winner_info)

    # Cash mode rake — deducts a % of the pot from the headline winner
    # and ledgers the destruction. No-op when cash mode is inactive,
    # the rake flag is off, or RAKE_PLAYER_TABLES is False (which is
    # the sandbox-friendly default for player-occupied tables).
    if game_data.get('cash_mode'):
        game_state = _apply_player_table_rake(
            game_id=game_id,
            game_data=game_data,
            game_state=game_state,
            winner_info=winner_info,
            pot_size=pot_size_before_award,
        )

    if not winning_player_names:
        logger.error(f"[Game {game_id}] No winning player names found in pot_breakdown")
        return game_state, False

    # Prepare winner announcement data
    winning_players_string = (', '.join(winning_player_names[:-1]) +
                              f" and {winning_player_names[-1]}") if len(winning_player_names) > 1 else winning_player_names[0]

    active_players = [p for p in game_state.players if not p.is_folded]
    is_showdown = len(active_players) > 1

    # Determine if this is the final hand of the tournament
    is_final_hand = False
    tournament_outcome = None
    if 'tournament_tracker' in game_data:
        # Count players who still have chips after this hand
        players_with_chips = [p for p in game_state.players if p.stack > 0]
        if len(players_with_chips) == 1:
            # Only one player has chips - this is the final hand
            is_final_hand = True
            tracker = game_data['tournament_tracker']
            human_player = tracker.get_human_player()
            if human_player:
                winner = players_with_chips[0]
                human_won = winner.name == human_player['name']
                # Position: 1st if won, 2nd if lost (this only runs when 1 player has chips left)
                human_position = 1 if human_won else 2
                tournament_outcome = {
                    'human_won': human_won,
                    'human_position': human_position
                }

    winner_data = prepare_showdown_data(game_state, winner_info, winning_player_names,
                                        is_final_hand, tournament_outcome)

    # Calculate total pot and net profit from pot_breakdown (split-pot support)
    total_pot = sum(pot['total_amount'] for pot in winner_info.get('pot_breakdown', []))
    pot_dict = game_state.pot if isinstance(game_state.pot, dict) else {}
    winner_contributions = sum(pot_dict.get(name, 0) for name in winning_player_names)
    net_profit = total_pot - winner_contributions

    if is_showdown:
        message_content = (
            f"{winning_players_string} won ${net_profit} with {winner_info['hand_name']}. "
            f"Winning hand: {winner_info['winning_hand']}"
        )
        # Build structured win_result for rich chat rendering
        winner_hole_cards = []
        if winning_player_names:
            winner_player = next(
                (p for p in game_state.players if p.name == winning_player_names[0]),
                None
            )
            winner_hole_cards = [str(c) for c in winner_player.hand] if winner_player and winner_player.hand else []
        community_card_strings = [str(c) for c in game_state.community_cards]
        win_result = {
            'winners': winning_players_string,
            'pot': net_profit,
            'hand_name': winner_info['hand_name'],
            'winner_cards': winner_hole_cards,
            'community_cards': community_card_strings,
            'winning_combo': winner_info['winning_hand'],
            'is_showdown': True,
        }
    else:
        message_content = f"{winning_players_string} won +${net_profit}."
        win_result = {
            'winners': winning_players_string,
            'pot': net_profit,
            'is_showdown': False,
        }

    # Record the hand BEFORE emitting the winner announcement.
    # Order matters: clients can request post-round chat suggestions as soon
    # as they see the overlay, and the chat handler reads from
    # memory_manager.hand_recorder.completed_hands. The psychology pipeline
    # below can take several seconds (LLM-driven emotional narration), so
    # filing the hand after the emit produces a race where the chat handler
    # picks hand N-1 instead of hand N. Equity calc needs current_hand (which
    # on_hand_complete clears), so it runs first; equity persistence needs
    # hand_history_id from the DB save, so it runs after.
    equity_history = None
    memory_manager = game_data.get('memory_manager')
    ai_controllers = game_data.get('ai_controllers', {})
    if memory_manager:
        hand_in_progress = memory_manager.hand_recorder.current_hand
        if hand_in_progress and hand_in_progress.hole_cards:
            try:
                equity_tracker = EquityTracker()
                equity_history = equity_tracker.calculate_hand_equity_history(hand_in_progress)
                logger.debug(
                    f"[Game {game_id}] Calculated equity history: "
                    f"{len(equity_history.snapshots)} snapshots"
                )
            except Exception as e:
                logger.warning(f"[Game {game_id}] Equity calculation failed: {e}")

        ai_players = {name: controller.ai_player for name, controller in ai_controllers.items()}
        try:
            # Phase 3: forward equity_history (computed just above) to
            # the relationship detector via on_hand_complete. This
            # enables BAD_BEAT events in live user games — the only
            # event in the Phase 3 vocabulary that needs pre-river
            # equity data to fire.
            memory_manager.on_hand_complete(
                winner_info=winner_info,
                game_state=game_state,
                ai_players=ai_players,
                skip_commentary=True,
                equity_history=equity_history,
            )
        except Exception as e:
            logger.warning(f"Memory manager hand completion failed: {e}")

        # Persist equity history (needs hand_history_id assigned by on_hand_complete's DB save)
        if equity_history and equity_history.snapshots:
            try:
                from poker.repositories.hand_equity_repository import HandEquityRepository
                from poker.equity_snapshot import HandEquityHistory
                equity_repo = HandEquityRepository(hand_history_repo.db_path)

                hand_history_id = hand_history_repo.get_hand_history_id(
                    game_id, equity_history.hand_number
                )

                if hand_history_id:
                    equity_history_with_id = HandEquityHistory(
                        hand_history_id=hand_history_id,
                        game_id=equity_history.game_id,
                        hand_number=equity_history.hand_number,
                        snapshots=equity_history.snapshots,
                    )
                    equity_repo.save_equity_history(equity_history_with_id)
                    logger.debug(
                        f"[Game {game_id}] Saved {len(equity_history.snapshots)} equity snapshots "
                        f"with hand_history_id={hand_history_id}"
                    )
                else:
                    equity_repo.save_equity_history(equity_history)
                    logger.warning(
                        f"[Game {game_id}] No hand_history_id found for hand {equity_history.hand_number}, "
                        f"saving equity without ID"
                    )
            except Exception as e:
                logger.warning(f"[Game {game_id}] Failed to save equity history: {e}")

    # EMIT WINNER ANNOUNCEMENT (hand is now safely recorded)
    send_message(game_id, "Table", message_content, "table", 1, win_result=win_result)
    socketio.emit('winner_announcement', winner_data, to=game_id)

    # === UNIFIED PSYCHOLOGY PIPELINE ===
    # Runs synchronously: detect -> resolve -> persist -> update -> recover -> save
    hand_number = _get_hand_number(game_data)

    if 'pressure_detector' not in game_data:
        logger.warning(f"[Game {game_id}] No pressure_detector, skipping psychology pipeline")
    elif not ai_controllers:
        logger.debug(f"[Game {game_id}] No AI controllers, skipping psychology pipeline")

    if 'pressure_detector' in game_data and ai_controllers:
        hand_history_repo_for_pipeline = None
        if memory_manager:
            hand_history_repo_for_pipeline = getattr(memory_manager, '_persistence', None)

        pipeline = PsychologyPipeline(
            pressure_detector=game_data['pressure_detector'],
            pressure_event_repo=event_repository,
            game_repo=game_repo,
            hand_history_repo=hand_history_repo_for_pipeline,
            enable_emotional_narration=True,
            persist_controller_state=False,  # game handler saves per-decision instead
        )

        big_blind = game_state.current_ante if hasattr(game_state, 'current_ante') else 100

        psych_ctx = PsychologyContext(
            game_id=game_id,
            hand_number=hand_number,
            game_state=game_state,
            winner_info=winner_info,
            winner_names=winning_player_names,
            pot_size=pot_size_before_award,
            controllers=ai_controllers,
            hand_start_stacks=game_data.get('hand_start_stacks'),
            was_short_stack=game_data.get('short_stack_players', set()),
            equity_history=equity_history,
            memory_manager=memory_manager,
            big_blind=big_blind,
        )

        def _on_events_resolved(all_events, resolved_results, controllers):
            """Callback for UI updates after events are resolved."""
            if 'pressure_stats' in game_data:
                pressure_stats = game_data['pressure_stats']
                for event_name, affected_players in all_events:
                    details = {
                        'pot_size': pot_size_before_award,
                        'hand_rank': winner_info.get('hand_rank'),
                        'hand_name': winner_info.get('hand_name'),
                    }
                    pressure_stats.record_event(event_name, affected_players, details)

            if controllers:
                elasticity_data = format_elasticity_data(controllers)
                socketio.emit('elasticity_update', elasticity_data, to=game_id)

        psych_result = pipeline.process_hand(psych_ctx, on_events_resolved=_on_events_resolved)
        game_data['short_stack_players'] = psych_result.current_short_stack

        # Save emotional states (since persist_controller_state=False skips full save)
        for player_name, controller in ai_controllers.items():
            try:
                if hasattr(controller, 'psychology') and controller.psychology and controller.psychology.emotional:
                    game_repo.save_emotional_state(
                        game_id, player_name, controller.psychology.emotional
                    )
            except Exception as e:
                logger.error(
                    f"[Game {game_id}] Failed to save emotional state for {player_name}: {e}",
                    exc_info=True,
                )

    # Start async commentary (genuinely slow — multiple LLM calls)
    commentary_complete = threading.Event()

    if not config.ENABLE_AI_COMMENTARY:
        commentary_complete.set()
    else:
        socketio.start_background_task(
            _run_async_commentary,
            game_id, game_data, commentary_complete
        )

    # Run end-of-hand coach progression checks (gate unlocks, silent downgrades)
    try:
        from flask_app.services.coach_progression import CoachProgressionService
        user_id = game_data.get('owner_id', '')
        if user_id:
            coach_service = CoachProgressionService(coach_repo)
            coach_service.check_hand_end(user_id)
    except Exception as e:
        logger.warning(f"Coach progression hand-end check failed: {e}")

    # Handle eliminations (needs updated game_state)
    # Pass winner_data so it can be included in tournament_complete event
    human_eliminated = handle_eliminations(game_id, game_data, game_state, winning_player_names,
                                           pot_size_before_award, final_hand_data=winner_data)
    if human_eliminated:
        # Set phase to GAME_OVER and save before returning
        state_machine.current_phase = PokerPhase.GAME_OVER
        game_data['state_machine'] = state_machine
        game_state_service.set_game(game_id, game_data)
        update_and_emit_game_state(game_id)
        # Save final state to persistence
        owner_id, owner_name = game_state_service.get_game_owner_info(game_id)
        game_repo.save_game(game_id, state_machine._state_machine, owner_id, owner_name)
        if 'tournament_tracker' in game_data:
            game_repo.save_tournament_tracker(game_id, game_data['tournament_tracker'])
        return game_state, True

    # Check tournament completion
    if check_tournament_complete(game_id, game_data, final_hand_data=winner_data):
        # Set phase to GAME_OVER and save before returning
        state_machine.current_phase = PokerPhase.GAME_OVER
        game_data['state_machine'] = state_machine
        game_state_service.set_game(game_id, game_data)
        update_and_emit_game_state(game_id)
        # Save final state to persistence
        owner_id, owner_name = game_state_service.get_game_owner_info(game_id)
        game_repo.save_game(game_id, state_machine._state_machine, owner_id, owner_name)
        if 'tournament_tracker' in game_data:
            game_repo.save_tournament_tracker(game_id, game_data['tournament_tracker'])
        return game_state, True

    # Wait for commentary to complete before starting new hand
    # Commentary runs in parallel across AI players, but we need all to finish
    # Use a timeout to prevent indefinite blocking if something goes wrong
    commentary_timeout = 10  # seconds
    if not commentary_complete.wait(timeout=commentary_timeout):
        logger.warning(f"Commentary did not complete within {commentary_timeout}s timeout")

    # Small additional delay for visual pacing
    delay = (1 if is_showdown else 0.5) * config.ANIMATION_SPEED
    if delay > 0:
        _ff_aware_sleep(game_id, delay)

    # Clear fast-forward at the hand boundary. FF is a single-hand
    # affordance — the player asked to skip *this* orbit, not commit to
    # zooming through every future hand. The next hand starts with full
    # personality-aware controllers and normal pacing. (The per-turn
    # reset in progress_game still catches the within-hand case where
    # action returns to the human mid-street.)
    if game_data.get('fast_forward'):
        game_data['fast_forward'] = False

    # Clear hole cards and set phase to HAND_OVER. Prevents stale cards
    # from flashing and triggers frontend exit animation + shuffle overlay.
    try:
        cleared_players = tuple(p.update(hand=()) for p in game_state.players)
        cleared_game_state = game_state.update(players=cleared_players)
        state_machine.game_state = cleared_game_state
        state_machine.current_phase = PokerPhase.HAND_OVER
        game_data['state_machine'] = state_machine
        game_state_service.set_game(game_id, game_data)
        update_and_emit_game_state(game_id)
    except (ValueError, KeyError, RuntimeError, OSError) as e:
        logger.error(f"Failed to clear hole cards for game {game_id}: {e}", exc_info=True)
        # Persist whatever state we have to prevent inconsistency
        game_state_service.set_game(game_id, game_data)
        socketio.emit('game_error', {
            'error': 'Failed to transition between hands',
            'recoverable': True
        }, to=game_id)
        return state_machine.game_state, False

    # Brief delay (150ms at 1x speed) for frontend to receive cleared state and begin card exit animation
    _ff_aware_sleep(game_id, 0.15 * config.ANIMATION_SPEED)

    send_message(game_id, "Table", "***   NEW HAND DEALT   ***", "table")

    # Reset card announcement and run-out reaction tracking for new hand
    game_data['last_announced_phase'] = None
    game_data.pop('runout_reaction_schedule', None)
    game_data.pop('runout_emotion_overrides', None)

    # Cash mode: refill empty seats with fresh AIs before dealing the
    # next hand. Tournament mode skips this — busted players stay
    # eliminated, and the tracker drives the end-of-game flow.
    if game_data.get('cash_mode'):
        try:
            _refill_cash_seats(game_id, game_data, state_machine)
        except Exception as e:
            logger.error(
                f"[CASH] Failed to refill seats for {game_id}: {e}",
                exc_info=True,
            )
        # Symmetric check for the human seat: emit a bust event so
        # the frontend can open the rebuy/sponsor modal. Wrapped
        # separately so a bust-emit failure doesn't taint the
        # refill flow above.
        try:
            _detect_human_cash_bust(game_id, game_data, state_machine)
        except Exception as e:
            logger.error(
                f"[CASH] Bust detection failed for {game_id}: {e}",
                exc_info=True,
            )
        # Lobby v1.5: refresh the persisted `cash_tables` row for the
        # human's table. Runs AFTER _refill_cash_seats so the live-fill
        # candidate pool doesn't include AIs we just brought in to fill
        # a bust. Wrapped separately for the same isolation reason.
        try:
            _refresh_lobby_table_for_session(game_id, game_data, state_machine)
        except Exception as e:
            logger.error(
                f"[CASH] Lobby refresh failed for {game_id}: {e}",
                exc_info=True,
            )

        # Heads-up + busted human (or any cash table that's lost all but
        # one chip-holder) can't deal another hand. The state machine
        # would loop HAND_OVER → INIT_HAND → SHOWDOWN → HAND_OVER, hit
        # the 50-iteration cap, and pin progress_game's lock — blocking
        # /api/cash/leave for the user staring at the bust modal. Pause
        # the game in HAND_OVER and return; rebuy or leave will unstick
        # it.
        chip_holders = sum(
            1 for p in state_machine.game_state.players if p.stack > 0
        )
        if chip_holders < 2:
            game_data['state_machine'] = state_machine
            game_state_service.set_game(game_id, game_data)
            update_and_emit_game_state(game_id)
            owner_id, owner_name = game_state_service.get_game_owner_info(game_id)
            game_repo.save_game(game_id, state_machine._state_machine, owner_id, owner_name)
            logger.info(
                "[CASH] Paused game_id=%r in HAND_OVER — only %d player(s) "
                "with chips; waiting for rebuy or leave",
                game_id, chip_holders,
            )
            return state_machine.game_state, True

    # Advance to next hand - run until player action needed (deals cards, posts blinds)
    try:
        state_machine.run_until_player_action()
        game_data['state_machine'] = state_machine
        game_state_service.set_game(game_id, game_data)
        update_and_emit_game_state(game_id)
    except (ValueError, KeyError, RuntimeError, OSError, ArithmeticError) as e:
        logger.error(f"Failed to advance to next hand for game {game_id}: {e}", exc_info=True)
        # Persist whatever state we have to prevent inconsistency
        game_state_service.set_game(game_id, game_data)
        socketio.emit('game_error', {
            'error': 'Failed to start new hand',
            'recoverable': True
        }, to=game_id)
        return state_machine.game_state, False

    # Start recording new hand AFTER cards are dealt
    if 'memory_manager' in game_data:
        memory_manager = game_data['memory_manager']
        new_hand_number = memory_manager.hand_count + 1
        memory_manager.on_hand_start(
            state_machine.game_state,
            hand_number=new_hand_number,
            deck_seed=state_machine.current_hand_seed
        )
        memory_manager.record_blinds(state_machine.game_state)

    # Track hand_start_stacks for stack-based pressure events (double_up, crippled, short_stack)
    # Capture after blinds are posted but before any betting action
    game_data['hand_start_stacks'] = {
        p.name: p.stack for p in state_machine.game_state.players
    }

    # Initialize short_stack_players tracking if not exists
    if 'short_stack_players' not in game_data:
        big_blind = state_machine.game_state.current_ante if hasattr(state_machine.game_state, 'current_ante') else 100
        game_data['short_stack_players'] = {
            p.name for p in state_machine.game_state.players
            if p.stack < 10 * big_blind and p.stack > 0
        }

    # Save state after hand evaluation completes (now in stable phase)
    owner_id, owner_name = game_state_service.get_game_owner_info(game_id)
    game_repo.save_game(game_id, state_machine._state_machine, owner_id, owner_name)
    if 'tournament_tracker' in game_data:
        game_repo.save_tournament_tracker(game_id, game_data['tournament_tracker'])

    limit_reached = _track_guest_hand(game_id, game_data)
    if limit_reached:
        return state_machine.game_state, True

    return state_machine.game_state, False


def handle_human_turn(game_id: str, game_data: dict, game_state) -> None:
    """Handle when it's a human player's turn."""
    cost_to_call = game_state.highest_bet - game_state.current_player.bet
    player_options = list(game_state.current_player_options) if game_state.current_player_options else []
    socketio.emit('player_turn_start', {'current_player_options': player_options, 'cost_to_call': cost_to_call}, to=game_id)

    # Emit elasticity update for UI display
    if 'ai_controllers' in game_data:
        elasticity_data = format_elasticity_data(game_data['ai_controllers'])
        socketio.emit('elasticity_update', elasticity_data, to=game_id)


def recover_stuck_runout(state_machine) -> bool:
    """Fast-forward a game persisted mid-all-in-runout to a stable state.

    When a game is saved while `game_state.run_it_out` is True (e.g.
    the server crashed during a multi-street all-in run-out), restoring
    from the DB lands in a stuck state: the state machine sets
    awaiting_action=True and waits for the live progress_game loop to
    consume run_it_out — but that loop is only re-entered when an event
    fires, and the UI also clears action options whenever run_it_out is
    True, so the player sees no buttons and the game freezes.

    This helper drives the state machine forward without replaying the
    live-play animations (which would be confusing on restore — the
    cards were already dealt, the player just didn't see them). For
    each iteration of the stuck run_it_out flag it forces the next
    phase (DEALING_CARDS for non-river, SHOWDOWN for river), clears
    awaiting/run_it_out, and lets the state machine settle via
    run_until_player_action.

    Returns True when recovery was applied, False if the state was
    already stable. Safe to call on any loaded game.
    """
    if not getattr(state_machine.game_state, 'run_it_out', False):
        return False

    from poker.poker_state_machine import PokerPhase

    safety = 20  # Defensive — should converge in 2-3 iterations
    while getattr(state_machine.game_state, 'run_it_out', False) and safety > 0:
        safety -= 1
        current_phase = state_machine.current_phase
        if current_phase == PokerPhase.RIVER:
            next_phase = PokerPhase.SHOWDOWN
        else:
            next_phase = PokerPhase.DEALING_CARDS
        cleared = state_machine._state_machine.game_state.update(
            awaiting_action=False, run_it_out=False,
        )
        state_machine._state_machine = (
            state_machine._state_machine
            .with_game_state(cleared)
            .with_phase(next_phase)
        )

    state_machine.run_until_player_action()
    logger.warning(
        f"[RECOVER] Recovered stuck run_it_out — settled at "
        f"phase={state_machine.current_phase.name}, "
        f"awaiting={state_machine.game_state.awaiting_action}"
    )
    return True


def progress_game(game_id: str) -> None:
    """Main game progression loop.

    This function runs the game forward, handling AI turns, phase transitions,
    and hand evaluations until a human action is required.
    """
    lock = game_state_service.get_game_lock(game_id)
    if not lock.acquire(blocking=False):
        logger.debug(f"[SKIP] progress_game already running for game {game_id}")
        return

    try:
        current_game_data = game_state_service.get_game(game_id)
        if not current_game_data:
            return

        while True:
            # Refresh game data (may have been updated by handle_ai_action)
            current_game_data = game_state_service.get_game(game_id)
            if not current_game_data:
                return  # Game was deleted

            # Cooperative cancellation: `/api/cash/leave` sets this flag
            # before blocking on the per-game lock. Bail before kicking
            # off another AI orbit so the leave route gets the lock
            # within one iteration instead of waiting for the loop to
            # happen to land on a human-turn break.
            if current_game_data.get('leave_requested'):
                logger.info(f"[CASH] progress_game yielding to leave_requested for game {game_id}")
                return

            state_machine = current_game_data['state_machine']

            state_machine.run_until([PokerPhase.EVALUATING_HAND])
            current_game_data['state_machine'] = state_machine
            game_state_service.set_game(game_id, current_game_data)
            game_state = state_machine.game_state

            update_and_emit_game_state(game_id)

            # Only save state when in a stable phase (not transitional phases like EVALUATING_HAND)
            # This prevents getting stuck if the client disconnects during evaluation
            if state_machine.current_phase != PokerPhase.EVALUATING_HAND:
                owner_id, owner_name = game_state_service.get_game_owner_info(game_id)
                game_repo.save_game(game_id, state_machine._state_machine, owner_id, owner_name)

            # Only announce cards when phase just changed to a card-dealing phase
            # Track in game_data to persist across progress_game calls
            current_phase = state_machine.current_phase
            last_announced_phase = current_game_data.get('last_announced_phase')
            if current_phase != last_announced_phase and current_phase in [PokerPhase.FLOP, PokerPhase.TURN, PokerPhase.RIVER]:
                handle_phase_cards_dealt(game_id, state_machine, game_state, current_game_data)
                current_game_data['last_announced_phase'] = current_phase
                game_state_service.set_game(game_id, current_game_data)

            # Handle "run it out" scenario - auto-advance with delays
            if game_state.run_it_out:
                # Reveal hole cards once before first run-out (dramatic showdown reveal)
                if not game_state.has_revealed_cards:
                    emit_hole_cards_reveal(game_id, game_state)
                    game_state = game_state.update(has_revealed_cards=True)
                    state_machine._state_machine = state_machine._state_machine.with_game_state(game_state)
                    current_game_data['state_machine'] = state_machine
                    game_state_service.set_game(game_id, current_game_data)

                    # Pre-compute run-out reactions while players view hole cards
                    reaction_schedule = compute_runout_reactions(
                        game_state,
                        current_game_data.get('ai_controllers', {})
                    )
                    current_game_data['runout_reaction_schedule'] = reaction_schedule

                    # Emit initial reactions based on equity at moment of reveal
                    # Build current emotions so we can skip no-ops
                    ai_controllers = current_game_data.get('ai_controllers', {})
                    current_emotions = {
                        name: ctrl.psychology.get_display_emotion()
                        for name, ctrl in ai_controllers.items()
                    }
                    overrides = {}
                    for reaction in reaction_schedule.reactions_by_phase.get('INITIAL', []):
                        if reaction.emotion == current_emotions.get(reaction.player_name):
                            continue  # Already showing this emotion
                        overrides[reaction.player_name] = reaction.emotion
                        _emit_avatar_reaction(game_id, reaction.player_name, reaction.emotion)
                    current_game_data['runout_emotion_overrides'] = overrides
                    game_state_service.set_game(game_id, current_game_data)

                    # Extra pause for players to see the cards
                    delay = 4 * config.ANIMATION_SPEED
                    if delay > 0:
                        _ff_aware_sleep(game_id, delay)

                # Wait for card animation to finish, then emit reactions,
                # then hold so the player can absorb before next street.
                # Flop (3 cards): ~2.825s animation (2s stagger + 0.825s)
                # Turn/River (1 card): ~0.825s animation
                animation_sleep = 3 if current_phase == PokerPhase.FLOP else 1
                reaction_hold = 1.5
                delay = animation_sleep * config.ANIMATION_SPEED
                if delay > 0:
                    _ff_aware_sleep(game_id, delay)

                # Check if game was deleted during sleep
                current_game_data = game_state_service.get_game(game_id)
                if not current_game_data:
                    return

                # Emit pre-computed avatar reactions for this street
                reaction_schedule = current_game_data.get('runout_reaction_schedule')
                if reaction_schedule:
                    phase_name = current_phase.name
                    overrides = current_game_data.get('runout_emotion_overrides', {})
                    for reaction in reaction_schedule.reactions_by_phase.get(phase_name, []):
                        current = overrides.get(reaction.player_name)
                        if current == reaction.emotion:
                            continue  # Already showing this emotion
                        overrides[reaction.player_name] = reaction.emotion
                        _emit_avatar_reaction(game_id, reaction.player_name, reaction.emotion)
                    current_game_data['runout_emotion_overrides'] = overrides
                    game_state_service.set_game(game_id, current_game_data)

                # Hold so the player can see reactions before next street
                delay = reaction_hold * config.ANIMATION_SPEED
                if delay > 0:
                    _ff_aware_sleep(game_id, delay)
                # Emit showdown reactions after all cards are dealt
                current_phase = state_machine.current_phase
                if current_phase == PokerPhase.RIVER:
                    current_game_data = game_state_service.get_game(game_id)
                    if current_game_data:
                        reaction_schedule = current_game_data.get('runout_reaction_schedule')
                        if reaction_schedule:
                            overrides = current_game_data.get('runout_emotion_overrides', {})
                            for reaction in reaction_schedule.reactions_by_phase.get('SHOWDOWN', []):
                                if overrides.get(reaction.player_name) == reaction.emotion:
                                    continue
                                overrides[reaction.player_name] = reaction.emotion
                                _emit_avatar_reaction(game_id, reaction.player_name, reaction.emotion)
                            current_game_data['runout_emotion_overrides'] = overrides
                            game_state_service.set_game(game_id, current_game_data)
                        delay = 1.5 * config.ANIMATION_SPEED
                        if delay > 0:
                            _ff_aware_sleep(game_id, delay)

                # Determine next phase (skip betting, go to dealing or showdown)
                if current_phase == PokerPhase.RIVER:
                    next_phase = PokerPhase.SHOWDOWN
                else:
                    next_phase = PokerPhase.DEALING_CARDS
                # Clear flags and set next phase directly (avoid re-running same transition)
                new_game_state = game_state.update(awaiting_action=False, run_it_out=False)
                state_machine._state_machine = state_machine._state_machine.with_game_state(new_game_state).with_phase(next_phase)
                current_game_data['state_machine'] = state_machine
                game_state_service.set_game(game_id, current_game_data)
                continue  # Continue loop to deal next cards

            if not game_state.current_player.is_human and game_state.awaiting_action:
                logger.info(f"[AI_TURN] {game_state.current_player.name}")
                handle_ai_action(game_id)
                continue  # Re-evaluate game state after AI action

            elif state_machine.current_phase == PokerPhase.EVALUATING_HAND:
                game_state, should_return = handle_evaluating_hand_phase(
                    game_id, current_game_data, state_machine, game_state
                )
                if should_return:
                    return
                state_machine = current_game_data['state_machine']

            else:
                # Action returned to the human — clear FF so the next AI turn
                # uses the normal personality-aware controllers again.
                if current_game_data.get('fast_forward'):
                    current_game_data['fast_forward'] = False
                    game_state_service.set_game(game_id, current_game_data)
                handle_human_turn(game_id, current_game_data, game_state)
                break
    finally:
        lock.release()


def detect_and_apply_pressure(game_id: str, event_type: str, **kwargs) -> None:
    """Helper function to detect and apply pressure events.

    Routes events through PlayerPsychology for unified tilt + elastic handling.
    """
    current_game_data = game_state_service.get_game(game_id)
    if not current_game_data:
        return

    game_state = current_game_data['state_machine'].game_state
    ai_controllers = current_game_data.get('ai_controllers', {})

    events = []

    if event_type == 'fold':
        folding_player_name = kwargs.get('player_name')
        folding_player = next(
            (p for p in game_state.players if p.name == folding_player_name), None
        )
        if folding_player:
            pot_total = game_state.pot.get('total', 0) if isinstance(game_state.pot, dict) else 0
            cost_to_call = game_state.highest_bet - folding_player.bet

            # Overbet: the bet they're facing exceeds the pot before it was placed
            pot_before_bet = pot_total - cost_to_call
            is_overbet = cost_to_call > 0 and cost_to_call > pot_before_bet

            # Shove: an opponent went all-in
            is_facing_shove = any(
                p.is_all_in for p in game_state.players
                if p.name != folding_player_name and not p.is_folded
            )

            if is_overbet or is_facing_shove:
                events.append(('fold_under_pressure', [folding_player_name]))

    elif event_type == 'big_bet':
        betting_player = kwargs.get('player_name')
        bet_size = kwargs.get('bet_size', 0)
        pot_size = game_state.pot.get('total', 0) if isinstance(game_state.pot, dict) else 0
        if bet_size > pot_size * 0.75:
            events.append(('aggressive_bet', [betting_player]))

    if not events:
        return

    # Record stats
    if 'pressure_stats' in current_game_data:
        pressure_stats = current_game_data['pressure_stats']
        for event_name, affected_players in events:
            details = kwargs.copy()
            pressure_stats.record_event(event_name, affected_players, details)

    # Route through PlayerPsychology for unified tilt + elastic handling
    for event_name, affected_players in events:
        for player_name in affected_players:
            if player_name in ai_controllers:
                controller = ai_controllers[player_name]
                # RuleBasedController has no psychology - skip pressure events
                if controller.psychology is not None:
                    controller.psychology.apply_pressure_event(event_name, opponent=None)

    # Emit elasticity update from psychology state
    if ai_controllers:
        elasticity_data = format_elasticity_data(ai_controllers)
        socketio.emit('elasticity_update', elasticity_data, to=game_id)


def _ff_aware_sleep(game_id: str, seconds: float) -> None:
    """`socketio.sleep` that compresses to ~10% when FF is on.

    Reads the per-game `fast_forward` flag and scales the wait. Card-reveal
    pacing, run-it-out holds, and reaction beats all flow through here, so
    FF visibly skims the visuals (cards still flip, just much faster) on
    top of skipping LLM deliberation. Outside FF, behaves identically to
    `socketio.sleep`.
    """
    if seconds <= 0:
        return
    game_data = game_state_service.get_game(game_id)
    if game_data and game_data.get('fast_forward'):
        seconds = seconds * 0.1
    try:
        socketio.sleep(seconds)
    except (OSError, RuntimeError) as e:
        logger.warning(f"FF-aware sleep interrupted for game {game_id}: {e}")


def _get_or_build_ff_controller(
    current_game_data: dict,
    player_name: str,
    state_machine,
    game_id: str,
):
    """Return a per-game tiered FF controller for `player_name`, lazily built.

    Used by `handle_ai_action` when `fast_forward` is set on game_data. Each
    AI seat gets a TieredBotController with `expression_enabled=False`, so
    decisions come from the solver tables + personality distortion with zero
    LLM calls — sub-100ms per decision instead of multi-second.

    Controllers are cached on `current_game_data['ff_controllers']`. They are
    intentionally lightweight (no memory_manager / opponent models wired);
    FF is a "skip the orbit" affordance, not a long-running session.
    """
    ff_controllers = current_game_data.setdefault('ff_controllers', {})
    cached = ff_controllers.get(player_name)
    if cached is not None:
        return cached

    from flask_app.handlers.tiered_factory import build_tiered_controller

    controller = build_tiered_controller(
        player_name=player_name,
        state_machine=state_machine,
        llm_config={},
        game_id=game_id,
        owner_id=current_game_data.get('owner_id'),
        capture_label_repo=None,
        decision_analysis_repo=None,
        expression_enabled=False,
    )
    ff_controllers[player_name] = controller
    return controller


def handle_ai_action(game_id: str) -> None:
    """Handle an AI player's action in the game."""
    logger.debug(f"[AI_ACTION] Starting AI action for game {game_id}")
    current_game_data = game_state_service.get_game(game_id)
    if not current_game_data:
        logger.debug(f"[AI_ACTION] No game data found for {game_id}")
        return

    state_machine = current_game_data['state_machine']
    game_messages = current_game_data['messages']
    ai_controllers = current_game_data['ai_controllers']

    current_player = state_machine.game_state.current_player
    logger.debug(f"[AI_ACTION] Current AI player: {current_player.name}")

    # Fast-forward dispatch: swap to a tiered controller (solver + personality,
    # no LLM expression) so the rest of the orbit resolves quickly. The flag
    # auto-resets in progress_game once action returns to the human. Per-game
    # FF controllers are cached in `ff_controllers` to avoid rebuilding the
    # strategy tables on every decision.
    if current_game_data.get('fast_forward'):
        controller = _get_or_build_ff_controller(
            current_game_data, current_player.name, state_machine, game_id,
        )
    else:
        controller = ai_controllers[current_player.name]

    # Set current hand number for tracking
    if 'memory_manager' in current_game_data:
        controller.current_hand_number = current_game_data['memory_manager'].hand_count

    response_addressing = []  # Default for fallback / exception paths
    try:
        if config.AI_DECISION_MODE != 'llm':
            # Fallback mode: use random valid action (no LLM call)
            valid_actions = state_machine.game_state.current_player_options
            call_amount = state_machine.game_state.highest_bet - current_player.bet
            max_raise = current_player.stack

            fallback_result = FallbackActionSelector.select_action(
                valid_actions=valid_actions,
                strategy=AIFallbackStrategy.RANDOM_VALID,
                call_amount=call_amount,
                min_raise=MIN_RAISE,
                max_raise=max_raise
            )
            action = fallback_result['action']
            amount = fallback_result['raise_to']
            full_message = ''
        else:
            player_response_dict = controller.decide_action(game_messages[-AI_MESSAGE_CONTEXT_LIMIT:])

            action = player_response_dict['action']
            # Ensure amount is int (defensive - controllers.py should handle this, but be safe)
            amount = int(player_response_dict.get('raise_to', 0) or 0)

            # Extract dramatic_sequence beats
            dramatic_sequence = player_response_dict.get('dramatic_sequence', [])
            if isinstance(dramatic_sequence, list) and dramatic_sequence:
                # Join beats with newlines for display
                full_message = '\n'.join(dramatic_sequence)
            elif isinstance(dramatic_sequence, str) and dramatic_sequence.strip():
                # String format (LLM returned single string instead of list)
                full_message = dramatic_sequence.strip()
            else:
                full_message = ''

            # Direct callout targets — list of opponent names this player
            # is addressing. Attached to the outgoing AI message so the
            # next bot's find_callouts has an authoritative signal.
            response_addressing = player_response_dict.get('addressing', [])
            if not isinstance(response_addressing, list):
                response_addressing = []

    except Exception as e:
        # Critical error fell through the decision pipeline — surface it
        # as a warning with traceback so it's visible in logs. The user
        # sees a silent AI action (no canned chat) rather than misleading
        # filler text like "Time to make a move." that doesn't match the
        # action the bot actually took.
        logger.warning(
            f"[AI_ACTION] Critical error getting AI decision for "
            f"{current_player.name}: {e}",
            exc_info=True,
        )

        valid_actions = state_machine.game_state.current_player_options
        personality_traits = getattr(controller, 'personality_traits', {})
        call_amount = state_machine.game_state.highest_bet - current_player.bet
        max_raise = current_player.stack

        fallback_result = FallbackActionSelector.select_action(
            valid_actions=valid_actions,
            strategy=AIFallbackStrategy.MIMIC_PERSONALITY,
            personality_traits=personality_traits,
            call_amount=call_amount,
            min_raise=MIN_RAISE,
            max_raise=max_raise
        )

        action = fallback_result['action']
        amount = fallback_result['raise_to']
        # Silent fallback — no AI chat bubble. The Table action message
        # below still announces what the bot did; we just don't fabricate
        # in-character commentary the bot didn't actually produce.
        full_message = ''

    highest_bet = state_machine.game_state.highest_bet
    action_text = format_action_message(current_player.name, action, amount, highest_bet)

    # Send action as Table message (consistent with human actions)
    send_message(game_id, "Table", action_text, "table")

    # Send AI message if player has something to say or show. addressing
    # carries the speaker's declared callout targets so the next bot's
    # find_callouts has an explicit signal instead of substring scanning.
    if full_message and full_message != '...':
        send_message(
            game_id, current_player.name, full_message, "ai",
            sleep=1, addressing=response_addressing,
        )

    if action == 'fold':
        detect_and_apply_pressure(game_id, 'fold', player_name=current_player.name)
    elif action in ['raise', 'all_in'] and amount > 0:
        detect_and_apply_pressure(game_id, 'big_bet', player_name=current_player.name, bet_size=amount)

    # Phase 2: Detect action-based energy events (all_in_moment, heads_up)
    if 'pressure_detector' in current_game_data:
        pressure_detector = current_game_data['pressure_detector']
        action_events = pressure_detector.detect_action_events(
            game_state=state_machine.game_state,
            player_name=current_player.name,
            action=action,
            amount=amount,
            hand_number=_get_hand_number(current_game_data),
        )
        # Apply energy events to player psychology and persist
        if action_events and current_player.name in ai_controllers:
            controller = ai_controllers[current_player.name]
            hand_number = _get_hand_number(current_game_data)
            for event_name, affected_players in action_events:
                for pname in affected_players:
                    ctrl = ai_controllers.get(pname)
                    if ctrl and hasattr(ctrl, 'psychology'):
                        e_before = ctrl.psychology.energy
                        c_before = ctrl.psychology.confidence
                        m_before = ctrl.psychology.composure
                        ctrl.psychology.apply_pressure_event(event_name)
                        if event_repository:
                            event_repository.save_event(
                                game_id=game_id,
                                player_name=pname,
                                event_type=event_name,
                                hand_number=hand_number,
                                details={
                                    'conf_delta': round(ctrl.psychology.confidence - c_before, 6),
                                    'comp_delta': round(ctrl.psychology.composure - m_before, 6),
                                    'energy_delta': round(ctrl.psychology.energy - e_before, 6),
                                },
                            )
            logger.debug(f"[Energy] Applied action events for {current_player.name}: {[e[0] for e in action_events]}")

    # Save pre-action state for decision analysis
    pre_action_state = state_machine.game_state

    game_state = play_turn(state_machine.game_state, action, amount)

    # Analyze decision quality (for all AI players including RuleBots)
    from flask_app.routes.game_routes import analyze_player_decision
    memory_manager = current_game_data.get('memory_manager')
    hand_number = memory_manager.hand_count if memory_manager else None
    analyze_player_decision(
        game_id, current_player.name, action, amount, state_machine,
        pre_action_state, hand_number, memory_manager,
        ai_controllers=current_game_data.get('ai_controllers'),
    )

    # Normalize the recorded amount for calls: the LLM/UI passes raise_to=0 for
    # calls since they're not raising, but downstream consumers (opponent model,
    # c-bet detector, hand recap, decision analysis) expect the actual call
    # cost. Compute it from the pre-action state.
    record_amount = amount
    if action == 'call':
        record_amount = max(0, min(pre_action_state.highest_bet - current_player.bet, current_player.stack))
    record_action_in_memory(current_game_data, current_player.name, action, record_amount, game_state, state_machine)
    advanced_state = advance_to_next_active_player(game_state)
    # If None, no active players remain - keep current state, let progress_game handle phase transition
    if advanced_state is not None:
        game_state = advanced_state
    state_machine.game_state = game_state
    current_game_data['state_machine'] = state_machine
    game_state_service.set_game(game_id, current_game_data)

    owner_id, owner_name = game_state_service.get_game_owner_info(game_id)
    game_repo.save_game(game_id, state_machine._state_machine, owner_id, owner_name)

    if hasattr(controller, 'assistant') and controller.assistant:
        personality_state = {
            'traits': getattr(controller, 'personality_traits', {}),
            'confidence': getattr(controller.ai_player, 'confidence', 'Normal'),
            'attitude': getattr(controller.ai_player, 'attitude', 'Neutral')
        }
        game_repo.save_ai_player_state(
            game_id,
            current_player.name,
            controller.assistant.memory.get_history(),
            personality_state
        )

        # Save unified psychology state and prompt config
        psychology_dict = controller.psychology.to_dict()
        prompt_config_dict = controller.prompt_config.to_dict() if hasattr(controller, 'prompt_config') else None
        game_repo.save_controller_state(
            game_id,
            current_player.name,
            psychology=psychology_dict,
            prompt_config=prompt_config_dict
        )

    update_and_emit_game_state(game_id)
