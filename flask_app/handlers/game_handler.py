"""Game progression and AI action handling.

This module contains the core game loop logic, broken down into
manageable functions for maintainability.
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.card import Card
from poker.ai_resilience import (
    AIFallbackStrategy,
    FallbackActionSelector,
    get_fallback_chat_response,
)
from poker.betting_context import BettingContext
from poker.card_utils import card_to_string
from poker.config import AI_MESSAGE_CONTEXT_LIMIT, MIN_RAISE
from poker.equity_snapshot import HandEquityHistory
from poker.equity_tracker import EquityTracker
from poker.game_helpers import should_clear_player_options
from poker.guest_limits import GUEST_LIMITS_ENABLED, GUEST_MAX_HANDS
from poker.hand_evaluator import HandEvaluator
from poker.player_psychology import ComposureState
from poker.poker_game import (
    advance_to_next_active_player,
    award_pot_winnings,
    determine_winner,
    play_turn,
)
from poker.poker_state_machine import PokerPhase
from poker.psychology_pipeline import PsychologyContext, PsychologyPipeline
from poker.rule_based_controller import RuleBasedController, RuleConfig
from poker.rule_bot_controller import RuleBotController
from poker.runout_reactions import compute_runout_reactions, runout_schedule_payload

from .. import config
from ..extensions import (
    capture_label_repo,
    coach_repo,
    decision_analysis_repo,
    event_repository,
    game_repo,
    guest_tracking_repo,
    hand_history_repo,
    personality_repo,
    socketio,
    tournament_repo,
)
from ..services import game_state_service
from ..services.ai_debug_service import get_all_players_llm_stats
from ..services.elasticity_service import format_elasticity_data
from .avatar_handler import get_avatar_url_with_fallback, start_single_emotion_generation
from .message_handler import (
    format_action_message,
    format_messages_for_api,
    record_action_in_memory,
    send_message,
)

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
        from flask_app.extensions import sandbox_repo
        from flask_app.services.sandbox_resolver import resolve_default_sandbox_for

        sandbox_id = resolve_default_sandbox_for(
            owner_id,
            sandbox_repo=sandbox_repo,
        )
        # Stamp it so subsequent reads are O(1) dict hit.
        game_data['sandbox_id'] = sandbox_id
        return sandbox_id
    except Exception as e:
        logger.warning(
            "[CASH] sandbox_id fallback resolution failed for owner=%r: %s",
            owner_id,
            e,
        )
        return None


def _off_grid_pids(sandbox_id: Optional[str], now: datetime) -> set:
    """Personality ids currently off-grid (on a vice OR a side hustle).

    The mirror of the filter `cash_mode/lobby.py:refresh_unseated_tables`
    applies before seating from the idle pool / eligible list (the
    `off_grid = on_vice | on_hustle` block). The autonomous lobby refresh
    excludes these AIs from every seating surface; the player-facing
    seat-fill paths in this module (`_refill_cash_seats`,
    `select_rejoin_candidates`, `_refresh_lobby_table_for_session`) must
    apply the same exclusion or they'll pull an off-grid AI into the
    human's table — the `seated_and_offgrid` split-brain (a broke AI shows
    up at the table mid-hustle, then the ticker narrates it "stepping out"
    while it's visibly seated).

    Best-effort and fail-soft: any repo error returns the empty set so a
    flaky read never blocks the table from refilling.
    """
    if not sandbox_id:
        return set()
    from flask_app.extensions import side_hustle_state_repo, vice_state_repo

    pids: set = set()
    for repo in (vice_state_repo, side_hustle_state_repo):
        if repo is None:
            continue
        try:
            pids |= repo.active_pids(sandbox_id=sandbox_id, now=now)
        except Exception as e:
            logger.warning("[CASH] off-grid pid lookup failed: %s", e)
    return pids


def _tournament_bound_pids(owner_id: Optional[str]) -> set:
    """Personas reserved for the owner's gathering Main Event (cash→tournament
    draw). The human-table mirror of `_off_grid_pids`: these are kept out of the
    seat-fill paths (`_refill_cash_seats`, `select_rejoin_candidates`,
    `_refresh_lobby_table_for_session`) AND force-left via `called_up_pids`, so a
    reserved opponent drifts to the tournament instead of being re-seated at the
    human's table. Empty behind `TOURNAMENT_DRAW_ENABLED` or when there's no
    gather-eligible invite. Best-effort: any error → empty (never blocks refill)."""
    try:
        from flask_app.extensions import tournament_invite_repo
        from flask_app.services import tournament_invites

        return tournament_invites.bound_pids(tournament_invite_repo, owner_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("[CASH] tournament-bound pid lookup failed: %s", e)
        return set()


def live_cash_seated_pids(sandbox_id: Optional[str]) -> set:
    """Personality ids seated as AI opponents in any *live* cash game for
    `sandbox_id` — the authoritative "who is the human playing right now".

    The world sim's occupancy view (`cash_mode/lobby.py:_global_seated_set`)
    is built only from the persisted `cash_tables` snapshot. A human's live
    hand lives in the in-memory game registry (`game_state_service`), and
    its `cash_tables` row can lag or be absent (the legacy `/api/cash/start`
    path never writes a human slot; a mid-session table/stake move frees the
    old row; the hand-boundary refresh early-returns when `cash_table_id` is
    unset). When that happens, an AI that is the human's *current* live
    opponent stays visible to the world ticker, which can seat — and bust —
    it at another table mid-hand: the double-booked-persona corruption.

    Returning the live opponents straight from the registry lets the world
    sim treat them as occupied regardless of snapshot staleness. Reads the
    registry dict directly (not `get_game`) so enumeration never refreshes
    the TTL and pins abandoned games in memory. Fail-soft: any error yields
    the empty set so a flaky read never blocks the world from advancing.
    """
    if not sandbox_id:
        return set()
    pids: set = set()
    try:
        for game_id in game_state_service.list_game_ids():
            if not game_id.startswith('cash-'):
                continue
            gd = game_state_service.games.get(game_id)
            if not gd:
                continue
            if _sandbox_id_for(gd) != sandbox_id:
                continue
            cash_pids = gd.get('cash_personality_ids') or {}
            pids.update(cash_pids.values())
    except Exception as e:
        logger.warning("[CASH] live_cash_seated_pids lookup failed: %s", e)
        return set()
    return pids


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
            logger.info(
                f"Guest game {game_id} has no tracking_id (pre-migration game), skipping hand tracking"
            )
            return False

        new_count = guest_tracking_repo.increment_hands_played(tracking_id)
        logger.debug(f"Guest hand tracked: tracking_id={tracking_id}, count={new_count}")
        if new_count >= GUEST_MAX_HANDS:
            socketio.emit(
                'guest_limit_reached',
                {
                    'hands_played': new_count,
                    'hands_limit': GUEST_MAX_HANDS,
                },
                to=game_id,
            )
            return True
        return False
    except sqlite3.Error as e:
        logger.error(f"Database error tracking guest hand for game {game_id}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error tracking guest hand for game {game_id}: {e}")
        return False


def _emit_avatar_reaction(game_id: str, player_name: str, emotion: str) -> None:
    """Emit avatar update for a run-out reaction.

    `is_reaction` marks this as an authoritative emotion change (a run-out
    reaction the player should see *now*), distinct from a late-arriving
    generated avatar image. The frontend applies the emotion immediately for
    reactions but must not clobber the displayed emotion for generation
    arrivals — see the `avatar_update` handler in usePokerGame.ts.
    """
    avatar_url = get_avatar_url_with_fallback(game_id, player_name, emotion)
    socketio.emit(
        'avatar_update',
        {
            'player_name': player_name,
            'avatar_url': avatar_url,
            'avatar_emotion': emotion,
            'is_reaction': True,
        },
        to=game_id,
    )


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
            logger.debug(
                f"[OpponentModel] Added observation for {observer}->{opponent_name}: {observation_text[:50]}..."
            )


def _feed_strategic_reflection(
    memory_manager, player_name: str, reflection: str, key_insight: Optional[str] = None
) -> None:
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


def restore_ai_controllers(
    game_id: str,
    state_machine,
    game_repo,
    owner_id: str = None,
    player_llm_configs: Dict[str, Dict] = None,
    default_llm_config: Dict = None,
    capture_label_repo=None,
    decision_analysis_repo=None,
    bot_types: Dict[str, str] = None,
    ai_chat: bool = True,
) -> Dict[str, Any]:
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
    try:
        # The loaders now skip individual corrupt rows internally, so an
        # empty dict here means "no saved state" (a fresh/legacy game) —
        # benign. Reaching this except means a catastrophic load failure
        # (e.g. DB/connection error): every AI silently reverts to default
        # tilt/emotion, so log it loudly (error + traceback), not as a warning.
        controller_states = game_repo.load_all_controller_states(game_id)
    except Exception as e:
        logger.error(
            f"Failed to load controller/emotional states for {game_id}; "
            f"all AIs will restore at default psychology: {e}",
            exc_info=True,
        )

    # Legacy bot_type aliases for stored games predating the chaos/standard/lean/sharp lineup.
    # hybrid → standard (full Hybrid path; previously also covered lean-bounded forced default)
    # tiered → sharp
    _BOT_TYPE_ALIASES = {'hybrid': 'standard', 'tiered': 'sharp'}

    for player in state_machine.game_state.players:
        if not player.is_human:
            # Check if this player should use a special controller type
            from flask_app.handlers.tiered_factory import build_controller

            if player.name in bot_types:
                raw_strategy = bot_types[player.name]
                strategy = _BOT_TYPE_ALIASES.get(raw_strategy, raw_strategy)
                # Get player-specific llm_config or fall back to default (for personality loading)
                llm_config = player_llm_configs.get(player.name, default_llm_config)

                # Unified dispatch. `default_strategy` flips the unknown-bot_type
                # fallback to RuleBotController(strategy=strategy) — the restore
                # path treats unrecognised strategies (e.g. 'abc', 'always_fold',
                # 'case_based') as rule-bot names rather than Hybrid. 'standard'
                # (the `hybrid` alias) still resolves to HybridAIController.
                # `debug_logging=True` is honored on the tiered (sharp /
                # baseline_solver) branches only.
                controller = build_controller(
                    bot_type=strategy,
                    player_name=player.name,
                    state_machine=state_machine,
                    llm_config=llm_config,
                    game_id=game_id,
                    owner_id=owner_id,
                    capture_label_repo=capture_label_repo,
                    decision_analysis_repo=decision_analysis_repo,
                    expression_enabled=ai_chat,
                    debug_logging=True,
                    default_strategy=strategy,
                )
                logger.info(f"[RESTORE] Created controller for {player.name} (bot_type={strategy})")
            else:
                # No bot_types entry — match the new-game route's default of
                # 'sharp' (the tiered solver bot, the core engine). Legacy
                # saves with no bot_types stamp fall through here.
                llm_config = player_llm_configs.get(player.name, default_llm_config)
                controller = build_controller(
                    bot_type='sharp',
                    player_name=player.name,
                    state_machine=state_machine,
                    llm_config=llm_config,
                    game_id=game_id,
                    owner_id=owner_id,
                    capture_label_repo=capture_label_repo,
                    decision_analysis_repo=decision_analysis_repo,
                    expression_enabled=ai_chat,
                )
                logger.info(
                    f"[RESTORE] Created TieredBotController for {player.name} (default fall-through)"
                )

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

                logger.debug(
                    f"[RESTORE] AI state for {player.name} with {len(saved_state.get('messages', []))} messages"
                )

            if player.name in controller_states:
                ctrl_state = controller_states[player.name]

                # Restore unified psychology state
                if ctrl_state.get('psychology'):
                    # Load from new unified format
                    from poker.player_psychology import PlayerPsychology

                    controller.psychology = PlayerPsychology.from_dict(
                        ctrl_state['psychology'], controller.ai_player.personality_config
                    )
                    logger.debug(
                        f"Restored psychology for {player.name}: "
                        f"tilt={controller.psychology.tilt_level:.2f}"
                    )
                else:
                    # Fallback: reconstruct from old separate states (if they exist)
                    if ctrl_state.get('tilt_state'):
                        controller.psychology.tilt = ComposureState.from_tilt_state(
                            ctrl_state['tilt_state']
                        )
                    # Note: elastic_personality is deprecated - new system uses anchors/axes.
                    # The legacy emotional_state table was retired in v136; narrative/
                    # inner_voice now restore from psychology_json on the primary path above.

                # Restore prompt_config (toggleable prompt components)
                if ctrl_state.get('prompt_config'):
                    from poker.prompt_config import PromptConfig

                    controller.prompt_config = PromptConfig.from_dict(ctrl_state['prompt_config'])
                    logger.debug(
                        f"Restored prompt_config for {player.name}: {controller.prompt_config}"
                    )
                elif ctrl_state.get('prompt_config') is None:
                    logger.warning(f"No prompt_config found for {player.name}, using defaults")

            ai_controllers[player.name] = controller

    return ai_controllers


def _all_ai_no_llm(ai_controllers: dict) -> bool:
    """True iff every AI seat resolves with ZERO LLM calls.

    Tiered ("Solver") bots make no LLM call when expression (chat) is off;
    rule bots never call an LLM. Any LLM-driven seat (Guided/Improv/Lean) or a
    tiered bot with chat on (which narrates) → False. Used to surface
    `ai_instant` so the UI can hide the pointless fast-forward button.
    """
    if not ai_controllers:
        return False
    from poker.tiered_bot_controller import TieredBotController

    for ctrl in ai_controllers.values():
        if isinstance(ctrl, TieredBotController):
            if getattr(ctrl, 'expression_generator', None) is not None:
                return False  # tiered with chat on → one narration LLM call
        elif not isinstance(ctrl, RuleBotController | RuleBasedController):
            return False  # LLM-driven decision (chaos/hybrid/lean)
    return True


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

    # Resolve the human owner's custom profile avatar once per emit (cheap
    # indexed lookup; kept fresh rather than cached so a mid-game change in
    # /profile shows up on the next state push). Applied to the human seat
    # below so the player isn't a bare initial like the AIs aren't.
    human_avatar_url = None
    owner_id = current_game_data.get('owner_id')
    if owner_id:
        try:
            from ..extensions import user_avatar_service

            if user_avatar_service:
                human_avatar_url = user_avatar_service.get_avatar_url(owner_id)
        except Exception as e:
            logger.debug(f"Could not resolve human avatar for {owner_id}: {e}")

    # Add avatar data and psychology to AI players
    ai_controllers = current_game_data.get('ai_controllers', {})
    for player_dict in game_state_dict.get('players', []):
        player_name = player_dict.get('name', '')
        # Human seat: surface the owner's profile avatar (the human player is
        # the game owner). Matches by owner_name when present so a stray second
        # human in a shared room doesn't borrow the owner's face.
        if player_dict.get('is_human', False) and human_avatar_url:
            if not human_player_name or player_name == human_player_name:
                player_dict['avatar_url'] = human_avatar_url
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
            # Fish are permanent personas now (real DB rows, stable names),
            # so they resolve avatars through the standard on-demand
            # pipeline like every other AI — no zombie-personality risk
            # that the old synthetic-tourist path had.
            avatar_url = get_avatar_url_with_fallback(game_id, player_name, display_emotion)
            player_dict['avatar_emotion'] = display_emotion
            player_dict['avatar_url'] = avatar_url

            # Rule-bot flag drives the UI's "bot" badge overlay.
            if isinstance(controller, RuleBotController | BaselineSolverBot):
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
            p.get('name') for p in game_state_dict.get('players', []) if not p.get('is_human', True)
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
            cover['nickname'] = controller.ai_player.personality_config.get(
                'nickname', cover['name'].split()[0]
            )
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

    # Instant mode: every AI seat resolves with ZERO LLM calls (tiered/Solver
    # with chat off, or pure rule bots). When true there's no deliberation to
    # skip, so the UI hides the fast-forward button.
    game_state_dict['ai_instant'] = _all_ai_no_llm(current_game_data.get('ai_controllers') or {})

    # Always-fast-forward: the owner set game speed to 'always', so every AI turn
    # resolves via the no-LLM path. Like ai_instant, this lets the UI hide the
    # (now permanently-on) fast-forward button.
    game_state_dict['always_fast_forward'] = _resolve_game_speed(current_game_data) == 'always'

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
    bankroll_unavailable = False
    active_loan = None
    if owner_id_cash:
        try:
            bankroll = bankroll_repo.load_player_bankroll(owner_id_cash)
            if bankroll is not None:
                bankroll_chips = bankroll.chips
        except Exception as e:
            # Don't silently render a transient load failure as "$0 bankroll":
            # that's indistinguishable from genuinely broke and wrongly gates
            # top-up/rebuy. Log it and flag it so the UI can show "couldn't
            # load balance" instead of a false zero.
            logger.warning(
                "[CASH] failed to load bankroll for %r: %s", owner_id_cash, e, exc_info=True
            )
            bankroll_unavailable = True
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
        except Exception as e:
            # A failed stake load masks an active loan as "no loan"; log it
            # rather than swallowing so a persistent failure isn't invisible.
            logger.warning(
                "[CASH] failed to load active stake for %r: %s", game_id_cash, e, exc_info=True
            )
    big_blind = game_state.current_ante
    return {
        'stake_label': current_game_data.get('cash_stake_label'),
        'bankroll': bankroll_chips,
        # True when the bankroll load above threw — lets the UI distinguish
        # "couldn't load balance" from a real $0 (which gates top-up/rebuy).
        'bankroll_unavailable': bankroll_unavailable,
        'big_blind': big_blind,
        'min_buy_in': big_blind * 40,
        'max_buy_in': big_blind * 100,
        'active_loan': active_loan,
        # Table identity for the in-game location chip + arrival toast.
        # Stamped on game_data alongside cash_table_id at seat-in /
        # cold-load; both nullable, so the frontend degrades to "no chip"
        # for legacy sessions where the name wasn't resolved.
        'table_id': current_game_data.get('cash_table_id'),
        'table_name': current_game_data.get('cash_table_name'),
        # "Everyone left" solo-table state. Set deliberately at the
        # hand-boundary pause in `progress_game` (after refill has had
        # its chance) — never recomputed live here, so a normal heads-up
        # win (last opponent at 0 chips for one HAND_OVER frame, about to
        # be refilled) can't flash the prompt. `rejoin_candidates` names
        # the AIs the "Stay & play" option would seat.
        'human_alone': bool(current_game_data.get('cash_solo_paused')),
        'rejoin_candidates': current_game_data.get('cash_rejoin_candidates') or [],
    }


def emit_hole_cards_reveal(game_id: str, game_state) -> None:
    """Emit hole cards for all active players during run-it-out showdown."""
    active_players = [p for p in game_state.players if not p.is_folded]
    if len(active_players) < 2:
        logger.warning(
            f"Skipping hole card reveal with only {len(active_players)} active player(s)"
        )
        return
    players_cards = {}

    for player in active_players:
        if player.hand:
            players_cards[player.name] = [
                card.to_dict() if hasattr(card, 'to_dict') else card for card in player.hand
            ]

    reveal_data = {
        'players_cards': players_cards,
        'community_cards': [
            card.to_dict() if hasattr(card, 'to_dict') else card
            for card in game_state.community_cards
        ],
    }

    socketio.emit('reveal_hole_cards', reveal_data, to=game_id)


def handle_phase_cards_dealt(
    game_id: str, state_machine, game_state, game_data: dict = None
) -> None:
    """Send message about newly dealt community cards and record to hand history.

    Note: Caller is responsible for ensuring this is only called once per phase transition.
    """
    num_cards_dealt = 3 if state_machine.current_phase == PokerPhase.FLOP else 1
    cards = [str(c) for c in game_state.community_cards[-num_cards_dealt:]]
    phase_name = str(state_machine.current_phase)
    message_content = f"{phase_name}: {' '.join(cards)}"
    send_message(game_id, "Table", message_content, "table", phase=phase_name.lower(), cards=cards)

    # Record community cards to hand history
    if game_data:
        memory_manager = game_data.get('memory_manager')
        if memory_manager:
            phase_name = state_machine.current_phase.name  # 'FLOP', 'TURN', 'RIVER'
            memory_manager.hand_recorder.record_community_cards(phase_name, cards)


def _reputation_order_refill_pool(eligible_pool, *, owner_id, sandbox_id, now):
    """Reorder the cash refill candidate pool by the human's reputation.

    Player-prestige hook 1 (table pull / rival-draw): *who* sits down with the
    human reflects their room-level reputation. Returns the pool unchanged
    unless the human is a high-renown figure, in which case candidates are
    stable-sorted by `cash_mode.prestige.refill_affinity`:

      - Beloved Legend → warm admirers (high inbound likability+respect) lead.
      - Infamous Villain → a rival cohort (AIs with heat to settle) leads;
        the cold/neutral room hangs back.

    No candidate is removed — the table still fills, so the human always has
    opponents (no wedge); the reputation effect is *ordering* only. Best-effort:
    any failure (no snapshot, repo down, low-renown quadrant) returns the input
    order, preserving the pre-hook behavior. Python's `sorted` is stable, so
    equal-affinity candidates keep the pool's deterministic personality_id order.
    """
    if not eligible_pool or not owner_id or not sandbox_id:
        return eligible_pool
    try:
        from cash_mode.prestige import (
            QUADRANT_BELOVED_LEGEND,
            QUADRANT_INFAMOUS_VILLAIN,
            refill_affinity,
        )

        from ..extensions import prestige_snapshots_repo, relationship_repo

        if prestige_snapshots_repo is None or relationship_repo is None:
            return eligible_pool
        snap = prestige_snapshots_repo.load_latest(sandbox_id, owner_id)
        if snap is None or snap["quadrant"] not in (
            QUADRANT_BELOVED_LEGEND,
            QUADRANT_INFAMOUS_VILLAIN,
        ):
            return eligible_pool  # no figure → the room doesn't reorder
        quadrant = snap["quadrant"]
        inbound = relationship_repo.load_inbound_relationships(owner_id, now=now)
        return sorted(
            eligible_pool,
            key=lambda e: refill_affinity(quadrant, inbound.get(e["personality_id"])),
            reverse=True,
        )
    except Exception as e:
        logger.debug("Could not reputation-order refill pool: %s", e)
        return eligible_pool


def _apply_reputation_demeanor(game_data: dict, state_machine) -> None:
    """Player-prestige hook 4 (AI demeanor): once-per-hand reputation nudge.

    Nudges the psychology of the AIs seated with the human by the human's
    room-level reputation quadrant: a feared **Infamous Villain** rattles
    low-poise opponents (a composure press → scared / tilt-prone, the
    exploitable edge), while a **Beloved Legend** loosens them up (a confidence
    / energy lift). The poise/ego filter inside `apply_pressure_event` does the
    "low-poise rattle, composed shrug" split for free. The nudge drives both
    decisions (the emotional-window shift on bounded options) AND table-talk
    demeanor (the expression generator reflects the axes), so one mechanism
    delivers both.

    This is the ONE prestige hook that touches the decision path, so it's
    behind a dedicated kill switch (`economy_flags.REPUTATION_DEMEANOR_ENABLED`):
    flip that to False to disable it completely with zero residual effect.
    Best-effort and called once at the hand boundary (not per action, which
    would compound) — never blocks the hand.
    """
    from cash_mode import economy_flags

    if not economy_flags.REPUTATION_DEMEANOR_ENABLED:
        return
    owner_id = game_data.get('owner_id')
    sandbox_id = _sandbox_id_for(game_data)
    if not owner_id or not sandbox_id:
        return
    try:
        from cash_mode.prestige import reputation_demeanor_stimulus

        from ..extensions import prestige_snapshots_repo

        if prestige_snapshots_repo is None:
            return
        snap = prestige_snapshots_repo.load_latest(sandbox_id, owner_id)
        if snap is None:
            return
        stimulus = reputation_demeanor_stimulus(snap['quadrant'])
        if stimulus is None:
            return  # low-renown human — the room doesn't react
        ai_controllers = game_data.get('ai_controllers', {})
        for player in state_machine.game_state.players:
            if getattr(player, 'is_human', False):
                continue
            ctrl = ai_controllers.get(player.name)
            if ctrl is not None and getattr(ctrl, 'psychology', None) is not None:
                ctrl.psychology.react_to_table_reputation(stimulus)
    except Exception as e:
        logger.debug("Could not apply reputation demeanor: %s", e)


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

    from flask_app.extensions import (
        bankroll_repo,
        capture_label_repo,
        decision_analysis_repo,
        personality_repo,
    )
    from poker.hybrid_ai_controller import HybridAIController
    from poker.poker_game import Player

    game_state = state_machine.game_state
    busted_indices = [
        i for i, p in enumerate(game_state.players) if not p.is_human and p.stack == 0
    ]
    if not busted_indices:
        return

    occupied_names = {p.name for p in game_state.players if p.stack > 0}
    big_blind = game_state.current_ante
    min_buy_in = big_blind * 40
    max_buy_in = big_blind * 100

    owner_id = game_data.get('owner_id')
    sandbox_id = _sandbox_id_for(game_data)
    now = datetime.utcnow()
    # Exclude AIs currently off-grid (on a vice / side hustle) — same
    # exclusion the autonomous lobby refresh applies. Without it a broke,
    # hustling AI gets pulled into the human's table mid-hustle (the
    # `seated_and_offgrid` split-brain). See `_off_grid_pids`.
    off_grid = _off_grid_pids(sandbox_id, now) | _tournament_bound_pids(owner_id)
    eligible = personality_repo.list_eligible_for_cash_mode(user_id=owner_id)
    eligible_pool = [
        e
        for e in eligible
        if e['name'] not in occupied_names
        and e['personality_id'] not in off_grid
        # Don't reseat a personality whose name matches a busted seat
        # we're about to remove (rare but possible if the eligible
        # query returns it twice).
        and e['name'] not in {game_state.players[i].name for i in busted_indices}
    ]

    # Player-prestige hook 1 (table pull / rival-draw): bias WHO refills the
    # human's table by their reputation — warm admirers lead a Beloved Legend's
    # table, a rival cohort leads an Infamous Villain's. No-op for low-renown /
    # no-snapshot. The affordability loop below then picks from the head.
    eligible_pool = _reputation_order_refill_pool(
        eligible_pool, owner_id=owner_id, sandbox_id=sandbox_id, now=now
    )

    refilled_count = 0

    for seat_idx in busted_indices:
        old_player = game_state.players[seat_idx]
        replacement = None
        replacement_buy_in = 0
        replacement_state = None
        replacement_pre_regen_chips = 0

        # Find an affordable, eligible replacement. Affordability +
        # projected-regen math lives in `_project_candidate_buy_in` so
        # this path and the rejoin path (`select_rejoin_candidates` /
        # `/api/cash/reseat`) can't drift.
        for candidate in list(eligible_pool):
            proj = _project_candidate_buy_in(
                candidate['personality_id'],
                min_buy_in,
                max_buy_in,
                sandbox_id,
                now,
                bankroll_repo,
            )
            if proj is None:
                continue
            replacement = candidate
            replacement_buy_in, replacement_state, replacement_pre_regen_chips = proj
            eligible_pool.remove(candidate)
            break

        if replacement is None:
            logger.info(
                "[CASH] Refill: no eligible replacement for busted %r at seat %d",
                old_player.name,
                seat_idx,
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
            new_player if i == seat_idx else p for i, p in enumerate(game_state.players)
        )
        game_state = game_state.update(players=new_players)
        state_machine.game_state = game_state

        # Persist AI bankroll debit. Pass chip_ledger_repo so a first-write
        # for this personality+sandbox emits the `ai_seed` audit entry (the
        # lobby seed path does the same) — without it, refilled chips could
        # enter the economy with no ledger row → conservation drift.
        from core.economy import ledger as chip_ledger
        from flask_app.extensions import chip_ledger_repo

        bankroll_repo.save_ai_bankroll(
            replacement_state, sandbox_id=sandbox_id, chip_ledger_repo=chip_ledger_repo
        )
        # (The seated⇒not-idle invariant is enforced when this seat is
        # persisted to cash_tables by `_refresh_lobby_table_for_session`'s
        # save_table — see CashTableRepository.save_table.)
        # Record any regen that this write commits. Transfer to table
        # stack is a pure non-bank move and isn't ledger-worthy.
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

        # Swap controller registry: remove old, build new (tiered = core engine)
        from flask_app.handlers.tiered_factory import build_tiered_controller

        ai_controllers = game_data.get('ai_controllers', {})
        ai_controllers.pop(old_player.name, None)
        new_controller = build_tiered_controller(
            player_name=replacement['name'],
            state_machine=state_machine,
            llm_config=game_data.get('llm_config', {}),
            game_id=game_id,
            owner_id=owner_id,
            capture_label_repo=capture_label_repo,
            decision_analysis_repo=decision_analysis_repo,
            expression_enabled=True,
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
                replacement['name'],
                personality_id=pid,
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
            seat_idx,
            old_player.name,
            replacement['name'],
            replacement_buy_in,
        )

    if refilled_count > 0:
        # Sync the updated game_state back to the service
        game_data['state_machine'] = state_machine
        game_state_service.set_game(game_id, game_data)


def _project_candidate_buy_in(pid, min_buy_in, max_buy_in, sandbox_id, now, bankroll_repo):
    """Affordability check for seating an AI from its bankroll.

    Returns ``(buy_in, post_debit_state, pre_regen_chips)`` when the
    personality's projected (regen-applied) bankroll covers a fresh
    buy-in at this table, else ``None``. Mirrors the inner affordability
    gate of ``_refill_cash_seats`` so the rejoin path
    (``select_rejoin_candidates`` + ``/api/cash/reseat``) seats only AIs
    that can actually fund the seat, using the same projected-regen math.
    """
    from cash_mode.bankroll import AIBankrollState, project_bankroll

    knobs = bankroll_repo.load_personality_knobs(pid)
    threshold = round(min_buy_in * knobs.buy_in_multiplier)
    buy_in = min(threshold, max_buy_in)
    stored = bankroll_repo.load_ai_bankroll(pid, sandbox_id=sandbox_id)
    if stored is None:
        projected = knobs.starting_bankroll
        stored = AIBankrollState(personality_id=pid, chips=projected, last_regen_tick=None)
    else:
        projected = project_bankroll(stored, knobs.starting_bankroll, knobs.bankroll_rate, now)
    if projected < threshold:
        return None
    return (
        buy_in,
        AIBankrollState(personality_id=pid, chips=projected - buy_in, last_regen_tick=now),
        stored.chips,
    )


def select_rejoin_candidates(game_data, game_state, limit=2, prefer_pids=None):
    """Pick eligible AIs to offer when the human is alone at a cash table.

    Returns up to ``limit`` ``{'personality_id', 'name'}`` dicts for AIs
    that are (a) not already seated and (b) can fund a buy-in at this
    table. Used to name the "Stay & play with X & Y" option on the
    solo-table prompt; ``/api/cash/reseat`` re-runs the same gate at seat
    time. ``prefer_pids`` (the personalities the player was shown) are
    tried first so the prompt's promise holds when they re-seat.
    """
    from datetime import datetime

    from flask_app.extensions import bankroll_repo, personality_repo

    big_blind = game_state.current_ante
    min_buy_in = big_blind * 40
    max_buy_in = big_blind * 100
    owner_id = game_data.get('owner_id')
    sandbox_id = _sandbox_id_for(game_data)
    # Busted AIs (stack 0) linger in the tuple until the reseat route
    # prunes them, so exclude them from "occupied" — otherwise a small
    # eligible pool could be emptied and the prompt would hide the Stay
    # option even though those personalities are re-seatable.
    occupied = {p.name for p in game_state.players if p.is_human or p.stack > 0}

    now = datetime.utcnow()
    # Don't offer an off-grid AI (on a vice / side hustle) — or a persona
    # reserved for the gathering Main Event — as a rejoin candidate; seating one
    # would re-create the `seated_and_offgrid` (or seated-and-tournament-bound)
    # split-brain. Mirrors the autonomous lobby refresh's exclusion.
    off_grid = _off_grid_pids(sandbox_id, now) | _tournament_bound_pids(owner_id)
    eligible = personality_repo.list_eligible_for_cash_mode(user_id=owner_id)
    pool = [
        e for e in eligible if e['name'] not in occupied and e['personality_id'] not in off_grid
    ]
    if prefer_pids:
        order = {pid: i for i, pid in enumerate(prefer_pids)}
        pool.sort(key=lambda e: order.get(e['personality_id'], len(order)))
    picks = []
    for cand in pool:
        if len(picks) >= limit:
            break
        if (
            _project_candidate_buy_in(
                cand['personality_id'], min_buy_in, max_buy_in, sandbox_id, now, bankroll_repo
            )
            is None
        ):
            continue
        picks.append({'personality_id': cand['personality_id'], 'name': cand['name']})
    return picks


def _restore_cash_table_binding(game_id: str, game_data: dict) -> Optional[str]:
    """Re-attach a cash game's lobby-table binding from the durable
    ``cash_sessions`` row when it's missing from ``game_data``.

    ``cash_table_id`` / ``cash_seat_index`` are memory-only fields: they're
    stamped at sit-down but are NOT part of the persisted
    ``game_state_json``. Any cold-load that doesn't pass through the
    ``/api/game-state`` restore block (e.g. a background hand advance after
    the game was evicted from memory) leaves them ``None``. Without the
    binding the hand-boundary refresh below early-returns, so the human seat
    is never re-stamped into the ``cash_tables`` row; ``refresh_unseated_tables``
    then treats the table as empty and refills the human's seat with AIs —
    the "my seat got taken, Resume shows different players" split-brain.

    Mirrors the same ``cash_sessions`` fallback ``leave_table`` already uses
    (cash_routes.py). Writes the recovered ids back onto ``game_data`` via
    ``set_game`` so subsequent hand-boundary refreshes and the eventual leave
    see them too. Returns the resolved ``table_id`` (or ``None`` if it can't
    be recovered — legacy ``/api/cash/start`` games never had a binding).
    """
    table_id = game_data.get('cash_table_id')
    if table_id:
        return table_id
    try:
        from flask_app.extensions import cash_session_repo

        if cash_session_repo is None:
            return None
        cs = cash_session_repo.load(game_id)
    except Exception as e:
        logger.warning(
            "[CASH][LOBBY] cash_sessions binding restore failed for %r: %s",
            game_id,
            e,
        )
        return None
    if cs is None:
        return None
    # Read-side migration: prefer the AUTHORITATIVE entity_presence row when the
    # flip is on — it's updated on every save_table (the live seat), whereas the
    # cash_sessions binding is the sit-time seat (stale if the player ever moved
    # seats). Falls back to cash_sessions when authority is off or presence has
    # no SEATED row. Best-effort: a presence lookup failure never blocks the
    # cash_sessions recovery below.
    resolved_table_id = cs.cash_table_id
    resolved_seat = cs.cash_seat_index
    from cash_mode import economy_flags as _ef

    if _ef.PRESENCE_AUTHORITY_ENABLED:
        try:
            from cash_mode.presence import Presence, player_entity_id
            from flask_app.extensions import entity_presence_repo as _epr

            if _epr is not None:
                st = _epr.load(player_entity_id(cs.owner_id), cs.sandbox_id)
                if st.state is Presence.SEATED and st.table_id is not None:
                    if st.table_id != cs.cash_table_id or st.seat_index != cs.cash_seat_index:
                        logger.info(
                            "[CASH][PRESENCE] cold-load binding from presence %r:%s "
                            "(cash_sessions had %r:%s) for %r",
                            st.table_id,
                            st.seat_index,
                            cs.cash_table_id,
                            cs.cash_seat_index,
                            game_id,
                        )
                    resolved_table_id, resolved_seat = st.table_id, st.seat_index
        except Exception as e:  # noqa: BLE001 — presence read must not block recovery
            logger.warning("[CASH][PRESENCE] presence binding lookup failed for %r: %s", game_id, e)
    if resolved_table_id is None:
        return None
    game_data['cash_table_id'] = resolved_table_id
    if game_data.get('cash_seat_index') is None:
        game_data['cash_seat_index'] = resolved_seat
    from flask_app.services import game_state_service

    game_state_service.set_game(game_id, game_data)
    logger.info(
        "[CASH][LOBBY] restored cash-table binding %r:%s for orphaned cash game %r",
        resolved_table_id,
        resolved_seat,
        game_id,
    )
    return resolved_table_id


def _ensure_cash_mode(game_id: str, game_data: dict) -> bool:
    """Guarantee a cash game's memory-only cash metadata is present, and
    report whether the hand-end cash flow should run for it.

    ``cash_mode``, ``cash_table_id``, ``cash_seat_index``,
    ``cash_stake_label``, ``cash_personality_ids`` and ``sandbox_id`` are
    stamped at sit-down but are NOT part of the persisted
    ``game_state_json``. The ``/api/game-state`` restore block rehydrates
    them on a warm reload, but a *background* hand advance after the game was
    evicted from memory (the world ticker / socket loop) bypasses that block,
    so ``cash_mode`` comes back falsy. Every hand-end cash step is gated on
    ``cash_mode`` — refill, human-bust detection, the lobby-table refresh, and
    the table-binding self-heal (`_restore_cash_table_binding`) inside it — so
    a dropped flag silently skips all of them: the human seat is never
    re-stamped into ``cash_tables``, ``refresh_unseated_tables`` treats it as
    empty and refills it, and the live game decays out of sync with the world
    table. The cold-load ghost-seat split-brain (the binding self-heal was
    unreachable because it lived behind the very flag the cold-load dropped).

    ``game_id.startswith("cash-")`` is the durable, memory-free signal — it's
    exactly how the restore block decides ``is_cash_game``. When it's a cash
    game whose flag is missing, rebuild the dropped fields from the durable
    ``cash_sessions`` row + the live players so the cash flow can run.
    Idempotent: a no-op once ``cash_mode`` is set (the warm path).
    """
    if game_data.get('cash_mode'):
        return True
    if not game_id.startswith('cash-'):
        return False

    game_data['cash_mode'] = True
    # Table + seat binding from the durable cash_sessions row (persists itself
    # when it recovers one; legacy /api/cash/start games have none).
    _restore_cash_table_binding(game_id, game_data)

    state_machine = game_data.get('state_machine')
    players = state_machine.game_state.players if state_machine is not None else ()

    # Stake label + table buy-in cap, resolved from the big blind. Mirrors the
    # /api/game-state restore block so refill's affordability math matches.
    try:
        from flask_app.routes.cash_routes import STAKES_LADDER

        big_blind = (state_machine.game_state.current_ante or 100) if state_machine else 100
        stake_label = next(
            (label for label, cfg in STAKES_LADDER.items() if cfg["big_blind"] == big_blind),
            None,
        )
        if game_data.get('cash_stake_label') is None:
            game_data['cash_stake_label'] = stake_label
        if stake_label is not None:
            mm = game_data.get('memory_manager')
            if mm is not None:
                from cash_mode.stakes_ladder import table_buy_in_window

                _, _, cold_load_max_buy_in = table_buy_in_window(stake_label)
                mm.set_table_max_buy_in(cold_load_max_buy_in)
    except Exception as e:
        logger.warning("[CASH] stake-label rehydrate failed for %r: %s", game_id, e)

    # cash_personality_ids feeds the lobby refresh's busted-slot reconciliation
    # AND the world sim's live_cash_seated_pids (so the human's live opponents
    # aren't double-booked at another table). Rebuild from current opponents.
    if not game_data.get('cash_personality_ids'):
        from flask_app.extensions import personality_repo

        cash_personality_ids = {}
        for player in players:
            if player.is_human:
                continue
            try:
                pid = personality_repo.resolve_name_to_personality_id(player.name)
            except Exception:
                pid = None
            if pid:
                cash_personality_ids[player.name] = pid
        game_data['cash_personality_ids'] = cash_personality_ids

    game_state_service.set_game(game_id, game_data)
    logger.info(
        "[CASH] rehydrated cash metadata for cold-loaded game %r (background advance)",
        game_id,
    )
    return True


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
    # Self-heal the table binding from the durable cash_sessions row when a
    # cold-loaded game lost it (cash_table_id isn't in game_state_json). This
    # keeps the human seat re-stamped each hand so refresh_unseated_tables
    # never refills it — closing the cold-load ghost-seat split-brain.
    table_id = _restore_cash_table_binding(game_id, game_data)
    if not table_id:
        # No durable binding (legacy /api/cash/start games never had one).
        # Nothing to refresh.
        return

    import random
    from datetime import datetime

    from cash_mode.bankroll import AIBankrollState
    from cash_mode.lobby import _global_seated_set
    from cash_mode.movement import SEATED_ENERGY_DRAIN_PER_HAND, refresh_table_roster
    from cash_mode.seat_registry import SeatOccupancyRegistry
    from cash_mode.stakes_ladder import STAKES_ORDER, table_buy_in_window
    from cash_mode.tables import ai_slot, ai_slot_fish, human_slot, open_slot
    from flask_app.extensions import bankroll_repo, cash_table_repo, personality_repo

    sandbox_id = _sandbox_id_for(game_data)
    table = cash_table_repo.load_table(table_id, sandbox_id=sandbox_id)
    if table is None:
        logger.warning("[CASH][LOBBY] table %r not found for hand-boundary refresh", table_id)
        return

    # Closed-economy: human-played hand at a closing casino decrements
    # the smooth-shutdown countdown. Mirrors the sim-loop decrement in
    # `cash_mode/lobby.py:refresh_unseated_tables` so the casino's
    # 10-hand wind-down advances regardless of who's seated.
    if table.table_type == 'casino':
        from cash_mode.casino_provisioning import (
            decrement_closing_hands,
            is_closing,
        )

        if is_closing(cash_table_repo, sandbox_id, table.table_id):
            decrement_closing_hands(
                cash_table_repo,
                sandbox_id,
                table.table_id,
            )

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
        (i, s["personality_id"]) for i, s in enumerate(table.seats) if s["kind"] == "ai"
    ]
    busted_slot_indices = [i for i, pid in persisted_ai_slot_indices if pid not in current_ai_pids]
    persisted_ai_pid_set = {pid for _, pid in persisted_ai_slot_indices}
    fresh_pids_needing_slot = [pid for pid in current_ai_pids if pid not in persisted_ai_pid_set]
    reseat_map: Dict[int, str] = {
        slot_idx: new_pid
        for slot_idx, new_pid in zip(busted_slot_indices, fresh_pids_needing_slot, strict=False)
    }
    leftover_busted = busted_slot_indices[len(fresh_pids_needing_slot) :]

    # 2. Sync: rewrite each persisted slot using game-state truth.
    # Fish identity is intrinsic to the persona (config archetype='fish'),
    # but a *seat* only carries the `archetype='fish'` stamp when it's built
    # via `ai_slot_fish`. Rebuilding fish seats through plain `ai_slot` here
    # stripped that stamp every hand on the human's table — so the casino
    # read as fishless, the `dead` push fired on everyone, and the fish lost
    # their `_coerce_fish_movement` pin. Re-derive fish-ness from the persona
    # (not the seat) so the rebuild re-stamps it — self-healing for seats a
    # prior hand already stripped.
    from cash_mode.closed_economy import load_fish_ids

    fish_pids = load_fish_ids(bankroll_repo, sandbox_id=sandbox_id)

    def _synced_ai_slot(pid: str, chips: int, prev: Optional[Dict] = None) -> Dict:
        out = ai_slot_fish(pid, chips) if pid in fish_pids else ai_slot(pid, chips)
        # Carry the per-seat dwell/rebuy counters across this rebuild — the
        # human-table sync re-creates the slot every hand, so without this
        # the dwell floor and rebuy-decay counters would reset each hand.
        if prev is not None:
            for _k in ("hands_here", "rebuys_here"):
                if _k in prev:
                    out[_k] = prev[_k]
        return out

    synced_seats: List[Dict] = []
    for i, slot in enumerate(table.seats):
        if slot["kind"] == "ai":
            if i in reseat_map:
                # A fresh AI took a busted seat — counters start at 0.
                new_pid = reseat_map[i]
                synced_seats.append(_synced_ai_slot(new_pid, pid_to_chips.get(new_pid, 0)))
            elif i in leftover_busted:
                synced_seats.append(open_slot())
            else:
                pid = slot["personality_id"]
                new_chips = pid_to_chips.get(pid, int(slot.get("chips", 0)))
                synced_seats.append(_synced_ai_slot(pid, new_chips, prev=slot))
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
        # v111: preserve table identity through the live-sync rewrite
        # so the subsequent save_table doesn't blank these fields.
        name=table.name,
        table_type=table.table_type,
        # v113: preserve casino closing state through the live-sync
        # rewrite.
        closing_hand_countdown=table.closing_hand_countdown,
    )

    # Pids in the persisted table after reconciliation, used below to
    # detect voluntary departures by diffing against `result.new_table`.
    pre_refresh_ai_pids = {s["personality_id"] for s in synced_seats if s["kind"] == "ai"}

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
    seated_globally = SeatOccupancyRegistry(
        _global_seated_set([t for t in all_tables if t.table_id != table_id]),
        label="game_handler.hand_boundary_refresh",
    )
    seated_globally.update(s["personality_id"] for s in synced_seats if s["kind"] == "ai")

    # Exclude off-grid AIs (on a vice / side hustle) from BOTH seating
    # surfaces (`eligible` and the `idle_pool` below), exactly as the
    # autonomous lobby refresh does. Live-filling a hustling AI here is
    # what produces the `seated_and_offgrid` split-brain at the human's
    # table. See `_off_grid_pids`.
    # Cash→tournament draw: reserved personas are gathered off cash — kept out
    # of this table's re-fill AND force-left below via called_up_pids. Computed
    # once and reused so the exclusion and the call-up agree.
    tournament_bound = _tournament_bound_pids(human_owner_id)
    off_grid = _off_grid_pids(sandbox_id, now) | tournament_bound
    eligible = [
        cand
        for cand in personality_repo.list_eligible_for_cash_mode(user_id=human_owner_id)
        if cand.get("personality_id") not in off_grid
    ]

    # Build a pid → controller map (live controllers carry psych state).
    # ai_controllers is keyed by display name, so resolve via cash_pids.
    ai_controllers = game_data.get('ai_controllers', {}) or {}
    pid_to_name = {pid: name for name, pid in cash_pids.items()}
    pid_to_controller = {pid: ai_controllers.get(pid_to_name.get(pid)) for pid in current_ai_pids}

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
        # Seated fatigue: one hand of seated play wears energy down (the drain
        # half of the energy loop — idle rest springs it back). Applied here so
        # the lowered value feeds `_psych_lookup` → refresh_table_roster's
        # `tenure` leave term THIS hand. Best-effort; never block the refresh.
        try:
            psych.apply_seated_fatigue(SEATED_ENERGY_DRAIN_PER_HAND)
        except Exception:  # noqa: BLE001 — fatigue is non-critical bookkeeping
            pass

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

    idle_pool = [
        entry
        for entry in cash_table_repo.list_idle(sandbox_id=sandbox_id)
        if entry.personality_id not in off_grid
    ]
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
        # Robust fish detection by persona identity (re-uses the set we
        # built for the seat re-stamp above) so a missing `archetype='fish'`
        # stamp can't spuriously fire the dead-push or drop a fish's rebuy.
        fish_ids=fish_pids,
        # Cash→tournament call-up: force a reserved opponent at the human's
        # table to leave for the gathering Main Event (mirrors the lobby tick).
        called_up_pids=tournament_bound or None,
    )

    # Apply rebuy decisions: debit each AI's bankroll for the top-up
    # and mirror the new seat chips onto the live Player.stack.
    # refresh_table_roster has already updated result.new_table.seats
    # with the post-rebuy chip count, so persistence is correct; the
    # work here is the bankroll write and game-state mirror.
    if result.rebuy_changes:
        try:
            _apply_rebuys(
                game_id,
                game_data,
                state_machine,
                result.rebuy_changes,
                pid_to_name,
                bankroll_repo,
                now,
                sandbox_id=sandbox_id,
            )
        except Exception as e:
            logger.error("[CASH][LOBBY] rebuy application failed: %s", e, exc_info=True)

    # Persist table + idle changes. Thread the real leave reason/target into
    # save_table so the presence-machine idle satellite (cash_idle_metadata)
    # records the actual reason instead of defaulting every row to
    # 'forced_leave' (the metadata write has no other source for it).
    idle_metadata = {
        change.entry.personality_id: change.entry
        for change in result.idle_changes
        if change.kind == "add" and change.entry is not None
    }
    cash_table_repo.save_table(
        result.new_table, sandbox_id=sandbox_id, now=now, idle_metadata=idle_metadata
    )
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
            game_id,
            synced_table,
            result,
            pid_to_name,
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
        # PRH-3: credit each departing AI's seat chips back to its bankroll
        # (with a ledger row) BEFORE dropping it from game state. The seated
        # path previously destroyed these chips on voluntary leaves
        # (bored_move / stake_up / take_break) — a monotonic ledger drift.
        try:
            from flask_app.extensions import (
                chip_ledger_repo,
                relationship_repo,
                stake_repo,
            )

            _credit_departed_ai_bankrolls(
                result,
                departed_pids,
                bankroll_repo=bankroll_repo,
                chip_ledger_repo=chip_ledger_repo,
                sandbox_id=sandbox_id,
                now=now,
                table_id=result.new_table.table_id,
                stake_repo=stake_repo,
                relationship_repo=relationship_repo,
                personality_repo=personality_repo,
            )
        except Exception as e:
            logger.error(
                "[CASH][LOBBY] departed-AI bankroll credit failed: %s",
                e,
                exc_info=True,
            )
        try:
            _remove_departed_ais_from_game(
                game_id,
                game_data,
                state_machine,
                departed_pids,
            )
        except Exception as e:
            logger.error(
                "[CASH][LOBBY] departure-sync failed: %s",
                e,
                exc_info=True,
            )

    # 4b. Add controllers for freshly-seated AIs (mid-session live fill).
    if result.freshly_seated_personality_ids:
        try:
            _seat_freshly_filled_ais(
                game_id,
                game_data,
                state_machine,
                result.new_table,
                result.freshly_seated_personality_ids,
            )
        except Exception as e:
            logger.error(
                "[CASH][LOBBY] live-fill controller install failed: %s",
                e,
                exc_info=True,
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
    from cash_mode.bankroll import debit_bankroll_for_seat
    from flask_app.extensions import chip_ledger_repo

    if not rebuy_changes:
        return

    game_state = state_machine.game_state
    name_to_player_idx = {p.name: i for i, p in enumerate(game_state.players)}
    updated = False

    for change in rebuy_changes:
        name = pid_to_name.get(change.personality_id)
        if not name:
            continue
        # 1. Audit-safe bankroll debit. debit_bankroll_for_seat projects
        # regen forward, commits it as an ai_regen ledger row, and REFUSES
        # (returns None) if the projected bankroll can't cover the rebuy.
        # This replaces the old project + max(0, projected - amount) clamp,
        # which silently MINTED `amount - projected` phantom chips when the
        # AI was short while still bumping the seat the full amount.
        try:
            debited = debit_bankroll_for_seat(
                bankroll_repo,
                change.personality_id,
                change.amount,
                sandbox_id=sandbox_id,
                chip_ledger_repo=chip_ledger_repo,
                now=now,
            )
        except Exception as e:
            logger.warning(
                "[CASH][LOBBY] rebuy bankroll debit failed for %r (+%d): %s",
                change.personality_id,
                change.amount,
                e,
            )
            continue
        if debited is None:
            # Insufficient or missing bankroll — do NOT bump the seat, or
            # we'd add chips to the table with no backing debit (the exact
            # phantom-chip leak this helper exists to prevent).
            logger.warning(
                "[CASH][LOBBY] rebuy refused for %r (+%d): insufficient/missing "
                "bankroll; seat left unchanged",
                change.personality_id,
                change.amount,
            )
            continue

        # 2. Mirror to live game state (only after a successful debit).
        idx = name_to_player_idx.get(name)
        if idx is None:
            continue
        state_machine.game_state = state_machine.game_state.update_player(
            idx,
            stack=change.new_seat_chips,
        )
        updated = True
        logger.info(
            "[CASH][LOBBY] %r rebought +%d (new stack %d)",
            name,
            change.amount,
            change.new_seat_chips,
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
    """Push system + AI chat messages for each movement event at the seated table.

    Four event kinds, each with differentiated phrasing per user spec:
      - rebuy:     "{name} added ${amount} in chips"
      - leave:     wording varies by reason
                     * forced_leave   → "{name} busted out with ${chips}"
                     * stake_up       → "{name} moved up to {next_stake}"
                     * take_break     → "{name} stepped away with ${chips}"
                     * bored_move     → "{name} got restless and left with ${chips}"
      - join:      "{name} sat down with ${amount}"   (live-fill)

    Sender is "Table". Message type is "system" so the React chat
    renders it with the settings/system styling.

    On top of the system line, leaves and joins also fire a
    fire-and-forget LLM call (`cash_mode.leave_narrative`) that, when
    it returns, sends an in-character AI chat message ("ai" type) so
    the player at the table reads it as if the AI typed in chat as
    they sat down or walked out. Skipped when the LLM call fails or
    is disabled — the system line always lands first either way.
    """
    from cash_mode.stakes_ladder import STAKES_ORDER, table_buy_in_window
    from cash_mode.tables import personality_for_seat
    from flask_app.extensions import personality_repo

    pre_seats = {i: dict(s) for i, s in enumerate(table_pre_refresh.seats)}
    # Index pre-refresh seats by pid for leave-path personality lookups
    # — a seat that just left is gone from the post-refresh table, so we
    # need the pre-refresh snapshot to resolve its personality.
    pre_seats_by_pid: Dict[str, Dict[str, Any]] = {
        s.get("personality_id"): s
        for s in pre_seats.values()
        if s.get("kind") == "ai" and s.get("personality_id")
    }
    post_seats_by_pid: Dict[str, Dict[str, Any]] = {
        s.get("personality_id"): s
        for s in result.new_table.seats
        if s.get("kind") == "ai" and s.get("personality_id")
    }

    stake_label = table_pre_refresh.stake_label
    try:
        _, table_min_buy_in, _ = table_buy_in_window(stake_label)
    except Exception:
        table_min_buy_in = 0

    leave_signals = getattr(result, "leave_signals", {}) or {}

    def _personality_for(pid: str, *, seat: Optional[Dict[str, Any]] = None):
        """Resolve a seat's personality dict.

        Resolves via the pre/post seat snapshots when available (so a
        just-left seat still resolves), falling back to a direct
        `PersonalityRepository` lookup by pid.
        """
        resolved_seat = seat or post_seats_by_pid.get(pid) or pre_seats_by_pid.get(pid)
        if resolved_seat is not None:
            # personality_for_seat already catches DB/decode failures and
            # logs them — programmer bugs (AttributeError/TypeError)
            # propagate so they get fixed instead of silently masking.
            return personality_for_seat(resolved_seat, personality_repo)
        # Bare-pid lookup (no seat in scope) — same exception philosophy:
        # catch DB/decode failures, let programmer bugs propagate.
        try:
            return personality_repo.load_personality_by_id(pid)
        except (sqlite3.Error, json.JSONDecodeError) as exc:
            logger.warning(
                "[CASH][LOBBY] personality lookup failed for pid=%r: %s",
                pid,
                exc,
            )
            return None

    def _ai_chat_callback(game_id: str, sender: str):
        """Return a `comment -> None` callback that emits an AI chat.

        Bound per-leaver/joiner because the lobby refresh that fires
        these workers may be racing the next hand — capturing game_id
        + sender at queue time keeps each callback self-contained.
        """

        def _send(comment: str) -> None:
            send_message(
                game_id=game_id,
                sender=sender,
                content=comment,
                message_type="ai",
            )

        return _send

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
        # Queue the in-character farewell. Worker fires async; the
        # AI's chat message lands a couple seconds after the system
        # line, which reads naturally as the AI "typing in chat" as
        # they walk out.
        try:
            from cash_mode.leave_narrative import (
                LeaveNarrativeContext,
                queue_leave_comment,
            )

            # Pull seat from pre-refresh snapshot — tourists' personality
            # is inline on the seat dict, not in the DB.
            leaver_seat = pre_seats_by_pid.get(pid)
            personality = _personality_for(pid, seat=leaver_seat)
            if personality is None:
                continue
            # Tourist display_name lives on the seat; use it for chat.
            name_for_chat = (leaver_seat or {}).get("display_name") or name
            ctx = LeaveNarrativeContext(
                personality_name=name_for_chat,
                play_style=str(personality.get("play_style") or ""),
                default_attitude=str(personality.get("default_attitude") or ""),
                verbal_tics=tuple(personality.get("verbal_tics") or ()),
                physical_tics=tuple(personality.get("physical_tics") or ()),
                decision=reason,
                dominant_signal=leave_signals.get(pid, ""),
                stake_label=stake_label,
                chips_at_exit=prev_chips,
                min_buy_in=table_min_buy_in,
            )
            queue_leave_comment(
                table_id=table_pre_refresh.table_id,
                personality_id=pid,
                created_at=datetime.now().isoformat(),
                ctx=ctx,
                on_complete=_ai_chat_callback(game_id, name),
            )
        except Exception as exc:
            logger.debug(
                "[CASH][SEATED] leave_narrative queue failed for %s: %s",
                pid,
                exc,
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
        # Tourists carry display_name on the seat; resolve through
        # `_personality_for` (which consults post_seats_by_pid first).
        joined_seat = post_seats_by_pid.get(pid)
        personality = _personality_for(pid, seat=joined_seat)
        # Fallback to pid keeps the message visible even if the repo
        # lookup fails — but the LLM call is skipped (no traits to
        # work with).
        display_name = (
            (joined_seat or {}).get("display_name") or (personality or {}).get("name") or pid
        )
        chips = pid_to_chips_post.get(pid, 0)
        send_message(
            game_id=game_id,
            sender="Table",
            content=f"{display_name} sat down with ${chips}",
            message_type="system",
        )
        if personality is None:
            continue
        # Queue the in-character arrival.
        try:
            from cash_mode.leave_narrative import (
                JoinNarrativeContext,
                queue_join_comment,
            )

            jctx = JoinNarrativeContext(
                personality_name=display_name,
                play_style=str(personality.get("play_style") or ""),
                default_attitude=str(personality.get("default_attitude") or ""),
                verbal_tics=tuple(personality.get("verbal_tics") or ()),
                physical_tics=tuple(personality.get("physical_tics") or ()),
                stake_label=stake_label,
                chips_at_sit=chips,
                min_buy_in=table_min_buy_in,
            )
            queue_join_comment(
                table_id=result.new_table.table_id,
                personality_id=pid,
                created_at=datetime.now().isoformat(),
                ctx=jctx,
                on_complete=_ai_chat_callback(game_id, display_name),
            )
        except Exception as exc:
            logger.debug(
                "[CASH][SEATED] join_narrative queue failed for %s: %s",
                pid,
                exc,
            )


def _credit_departed_ai_bankrolls(
    result,
    departed_pids,
    *,
    bankroll_repo,
    chip_ledger_repo,
    sandbox_id,
    now,
    table_id,
    stake_repo=None,
    relationship_repo=None,
    personality_repo=None,
) -> int:
    """PRH-3: return each voluntarily-departed seated AI's seat chips to its
    bankroll (with a ledger row), instead of destroying them.

    Mirrors the unseated path's `from_seat` credit
    (`cash_mode/lobby.py` `refresh_table_roster` consumer): the human's
    seated table is processed ONLY here (`refresh_unseated_tables` skips
    human-seated tables), so this is the sole place these chips get
    credited — there is no double-credit.

    Only `from_seat` changes are credited. `to_seat` (rebuy / live-fill) is
    the *debit* channel, consumed separately by `_apply_rebuys` /
    `_seat_freshly_filled_ais`; crediting it here would double-handle (see
    the rebuy note in `cash_mode/movement.py`). Keyed on the stable
    `personality_id` carried on each `BankrollChange`, never the name map
    (PRH-13 — that map has a desync history).

    Stake settlement: a staked AI leaving the human's table settles its
    stake HERE, at this seat's stack, via the shared `settle_departed_ai_stake`
    helper — the SAME settlement the unseated world-tick path runs. Without
    it the staker's upside was silently transferred into the AI's own
    bankroll and the stake settled later at an unrelated table (the
    "AI walks with $43.6k, staker gets scraps" bug). When a stake settles,
    its flows already credit the borrower's share, so that `from_seat` index
    is excluded from the full-stack credit below (mirrors the unseated path's
    `settled_from_seat_indices`). Requires `stake_repo`; when it (or the
    relationship/personality repos) is None the settlement is skipped and the
    AI is credited its full seat chips as before.

    Returns the total chips credited (for logging / test assertions).
    """
    from cash_mode.bankroll import credit_ai_cash_out

    # Settle active stakes at the session-end leave (the LAST from_seat per
    # departed pid — any earlier from_seat is a take_stake bust-chips return
    # that must still credit normally), keyed by index so only the settled
    # leave is excluded from the full-stack credit.
    settled_indices: set = set()
    if stake_repo is not None:
        from cash_mode.lobby import settle_departed_ai_stake

        last_from_seat_index: dict = {}
        for i, bc in enumerate(result.bankroll_changes):
            if bc.direction == "from_seat" and bc.personality_id in departed_pids:
                last_from_seat_index[bc.personality_id] = i
        for pid, idx in last_from_seat_index.items():
            try:
                settlement = settle_departed_ai_stake(
                    pid,
                    result.bankroll_changes[idx].amount,
                    stake_repo=stake_repo,
                    bankroll_repo=bankroll_repo,
                    chip_ledger_repo=chip_ledger_repo,
                    relationship_repo=relationship_repo,
                    personality_repo=personality_repo,
                    table_id=table_id,
                    sandbox_id=sandbox_id,
                    now=now,
                )
            except Exception as exc:
                logger.error(
                    "[CASH][SEATED] stake settlement on seated leave failed " "for %s: %s",
                    pid,
                    exc,
                    exc_info=True,
                )
                settlement = None
            if settlement is not None:
                settled_indices.add(idx)

    credited = 0
    for i, bc in enumerate(result.bankroll_changes):
        if bc.direction != "from_seat" or bc.amount <= 0:
            continue
        if bc.personality_id not in departed_pids:
            continue
        if i in settled_indices:
            # Stake settlement already credited the borrower's share via its
            # flows — crediting the full seat stack too would double-handle.
            continue
        credit_ai_cash_out(
            bankroll_repo,
            bc.personality_id,
            bc.amount,
            sandbox_id=sandbox_id,
            now=now,
            chip_ledger_repo=chip_ledger_repo,
            ledger_context={
                "site": "seated_table_vacate",
                "table_id": table_id,
            },
        )
        credited += bc.amount
    return credited


def _remove_departed_ais_from_game(
    game_id: str,
    game_data: dict,
    state_machine,
    departed_pids,
) -> None:
    """Symmetric inverse of `_seat_freshly_filled_ais`: drop AIs that
    voluntarily left the persisted table from the running game so the
    next hand isn't dealt to ghost players.

    The departing AI's seat chips are credited back to its bankroll by
    `_credit_departed_ai_bankrolls` in the caller (PRH-3) *before* this
    runs — this function only reconciles game state, not chips.
    """
    if not departed_pids:
        return

    cash_pids = game_data.get('cash_personality_ids', {})
    pid_to_name = {pid: name for name, pid in cash_pids.items()}
    departed_names = {pid_to_name[pid] for pid in departed_pids if pid in pid_to_name}
    if not departed_names:
        return

    game_state = state_machine.game_state
    remaining_players = tuple(p for p in game_state.players if p.name not in departed_names)
    if len(remaining_players) == len(game_state.players):
        return

    # Reset current_player_idx: dropping seats shrinks the tuple, so a stale index
    # can point past the end and IndexError on the next read (current_player /
    # to_dict), 500-storming the poll loop while the game sits paused. The index is
    # meaningless between hands; the next deal re-derives it.
    state_machine.game_state = game_state.update(players=remaining_players, current_player_idx=0)

    # T3-77 — an AI leaving the human's table heads back to the cash world
    # (idle pool → re-seat / off-screen sim), so hand its evolved mood off NOW by
    # flushing to the per-persona emotional_state_json. Doing it per-vacate (not
    # only at human-leave) means a persona you tilted carries that mood onward
    # even if it departs mid-session — and it's race-free, since a seated persona
    # isn't simultaneously sim-played. Best-effort; runs before the controller is
    # dropped.
    from flask_app import extensions

    flush_sandbox_id = game_data.get('sandbox_id')
    flush_bankroll_repo = getattr(extensions, 'bankroll_repo', None)

    ai_controllers = game_data.get('ai_controllers', {})
    for name in departed_names:
        if flush_sandbox_id and flush_bankroll_repo is not None:
            ctrl = ai_controllers.get(name)
            flush_pid = cash_pids.get(name)
            if ctrl is not None and flush_pid:
                try:
                    from cash_mode.psychology_persistence import flush_persona_psychology

                    flush_persona_psychology(ctrl, flush_pid, flush_bankroll_repo, flush_sandbox_id)
                except Exception as e:  # noqa: BLE001 — flush is best-effort
                    logger.warning(
                        "[CASH][LOBBY] psychology flush on vacate failed for %r: %s", name, e
                    )
        ai_controllers.pop(name, None)
        cash_pids.pop(name, None)
        logger.info(
            "[CASH][LOBBY] removed departed AI %r from game state",
            name,
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
    from cash_mode.tables import personality_for_seat
    from flask_app.extensions import (
        capture_label_repo,
        decision_analysis_repo,
        personality_repo,
    )
    from poker.hybrid_ai_controller import HybridAIController
    from poker.poker_game import Player

    game_state = state_machine.game_state
    occupied_names = {p.name for p in game_state.players}
    ai_controllers = game_data.get('ai_controllers', {})
    cash_pids = game_data.get('cash_personality_ids', {})
    memory_manager = game_data.get('memory_manager')
    owner_id = game_data.get('owner_id')

    # Find the AI chips + seat on the new table for these personalities.
    # Tourists carry their personality inline on the seat; regular AIs
    # fall through to the personality_repo lookup via personality_for_seat.
    seats_by_pid = {
        slot["personality_id"]: slot
        for slot in new_table.seats
        if slot["kind"] == "ai" and slot["personality_id"] in freshly_seated_pids
    }
    pid_to_chips = {pid: int(slot["chips"]) for pid, slot in seats_by_pid.items()}

    for pid in freshly_seated_pids:
        seat = seats_by_pid.get(pid)
        # `personality_for_seat` catches expected repo failures internally
        # and logs them; programmer bugs propagate so they get fixed.
        personality = personality_for_seat(seat, personality_repo) if seat else None
        # Prefer the seat's display_name when present; fall back to the
        # personality's name; fall back to the raw pid.
        name = (seat or {}).get("display_name") or (personality or {}).get("name") or pid
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

        # Route fish-archetype personalities to RuleBotController with
        # strategy='fish'. Without this, mid-session live fills would
        # build a HybridAIController for a fish — wasting LLM calls and
        # ignoring the designated `fish_leak`. Mirrors the sit-route's
        # `rule_strategy_override` branch in cash_routes.py.
        rule_strategy_override = (
            (personality or {}).get("rule_strategy") if isinstance(personality, dict) else None
        )
        from flask_app.handlers.tiered_factory import build_controller

        if rule_strategy_override == "fish":
            fish_leak = (personality or {}).get("fish_leak")
            controller = build_controller(
                bot_type="fish",
                player_name=name,
                state_machine=state_machine,
                game_id=game_id,
                owner_id=owner_id,
                capture_label_repo=capture_label_repo,
                decision_analysis_repo=decision_analysis_repo,
                fish_leak=fish_leak,
            )
        else:
            controller = build_controller(
                bot_type="sharp",
                player_name=name,
                state_machine=state_machine,
                llm_config=game_data.get('llm_config', {}),
                game_id=game_id,
                owner_id=owner_id,
                capture_label_repo=capture_label_repo,
                decision_analysis_repo=decision_analysis_repo,
                expression_enabled=True,
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
                    name,
                    e,
                )

        cash_pids[name] = pid
        logger.info(
            "[CASH][LOBBY] mid-session live fill: seated %r at chips=%d",
            pid,
            chips,
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
        stake_repo is not None and stake_repo.load_active_for_session(game_id) is not None
    )

    event_name = (
        'cash_rebuy_needed' if bankroll_chips >= min_buy_in and not has_active_loan else 'cash_bust'
    )
    socketio.emit(
        event_name,
        {
            'game_id': game_id,
            'stake_label': stake_label,
            'min_buy_in': min_buy_in,
            'max_buy_in': max_buy_in,
            'bankroll': bankroll_chips,
            'has_active_loan': has_active_loan,
        },
        to=game_id,
    )
    logger.info(
        "[CASH] Human bust at %r owner=%r stake=%r bankroll=%d had_loan=%s emitted=%s",
        game_id,
        owner_id,
        stake_label,
        bankroll_chips,
        has_active_loan,
        event_name,
    )


def prepare_showdown_data(
    game_state,
    winner_info: dict,
    winning_player_names: list,
    is_final_hand: bool = False,
    tournament_outcome: dict = None,
) -> dict:
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

                    value_names = {
                        14: 'A',
                        13: 'K',
                        12: 'Q',
                        11: 'J',
                        10: '10',
                        9: '9',
                        8: '8',
                        7: '7',
                        6: '6',
                        5: '5',
                        4: '4',
                        3: '3',
                        2: '2',
                    }
                    kicker_names = [
                        value_names.get(v, str(v)) for v in kicker_values if isinstance(v, int)
                    ]

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
                        'kickers': kicker_names,
                    }
                except Exception as e:
                    logger.warning(f"Failed to evaluate hand for {player.name}: {e}")
                    players_showdown[player.name] = {
                        'cards': formatted_cards,
                        'hand_name': None,
                        'hand_rank': 99,
                        'hand_score': 0,
                        'kickers': [],
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

    # Get big blind for dynamic thresholds
    big_blind = None
    if state_machine and hasattr(state_machine, 'game_state'):
        big_blind = getattr(state_machine.game_state, 'current_ante', None)

    # Active players + elimination context for spectator commentary — from the
    # tournament session field (single- or multi-table). Drives heckling by
    # busted AIs. Cash games have no session, so this stays empty.
    tournament_session = game_data.get('tournament_session')
    active_players = None
    elimination_lookup = {}
    if tournament_session is not None:
        active_players = set(tournament_session.field.active_ids())
        for event in tournament_session.field.eliminations:
            elimination_lookup[event.player_id] = event

    # Build ai_players dict with context for each player
    ai_players_with_context = {}
    for name, controller in ai_controllers.items():
        is_eliminated = active_players is not None and name not in active_players

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
            'controller': controller,
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
                game_id,
                player_name,
                commentary.table_comment,
                "ai",
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
                    commentary=commentary,
                )
                logger.info(
                    f"[Commentary] Persisted commentary for {player_name} hand {hand_number}"
                )
            else:
                logger.warning(f"[Commentary] hand_history_repo not available for {player_name}")
        except Exception as e:
            logger.warning(f"[Commentary] Failed to persist commentary for {player_name}: {e}")

        # Feed opponent observations to opponent model
        if (
            memory_manager
            and hasattr(commentary, 'opponent_observations')
            and commentary.opponent_observations
        ):
            _feed_opponent_observations(
                memory_manager=memory_manager,
                observer=player_name,
                observations=commentary.opponent_observations,
            )

        # Feed strategic reflection to session memory
        if (
            memory_manager
            and hasattr(commentary, 'strategic_reflection')
            and commentary.strategic_reflection
        ):
            _feed_strategic_reflection(
                memory_manager=memory_manager,
                player_name=player_name,
                reflection=commentary.strategic_reflection,
                key_insight=getattr(commentary, 'key_insight', None),
            )

    try:
        logger.info(
            f"[Commentary] Starting generation for {len(ai_players_with_context)} AI players"
        )
        # Pass callback to emit each commentary immediately as it completes
        commentaries = memory_manager.generate_commentary_for_hand(
            ai_players_with_context,
            on_commentary_ready=emit_commentary_immediately,
            big_blind=big_blind,
            human_bio=_resolve_human_bio(game_data),
            human_name=game_data.get('owner_name'),
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


def _run_async_narration(game_id: str, game_data: dict, pending: list) -> None:
    """Generate deferred emotional narration off the inter-hand critical path.

    The post-hand psychology pipeline updates composure synchronously (it
    affects play) and hands us the narration jobs for players who actually
    consume the prose — the LLM table-talk bots, or any bot in heads-up (whose
    narrative/inner_voice the opponent panel displays). Running the LLM calls
    here keeps them off the next-hand gate; the prose lands in memory and the
    next state emit carries it to the heads-up panel. We persist it ourselves
    since the synchronous emotional-state save was removed with the deferral.
    """
    ai_controllers = game_data.get('ai_controllers', {})
    for req in pending:
        controller = ai_controllers.get(req.player_name)
        if controller is None or getattr(controller, 'psychology', None) is None:
            continue
        try:
            controller.psychology.generate_narration(**req.kwargs)
            if controller.psychology.emotional:
                # Persist the freshly narrated emotional state via the unified
                # controller_state row (psychology_json carries narrative/
                # inner_voice). The emotional_state table was retired in v136.
                prompt_config = getattr(controller, 'prompt_config', None)
                game_repo.save_controller_state(
                    game_id,
                    req.player_name,
                    psychology=controller.psychology.to_dict(),
                    prompt_config=prompt_config.to_dict() if prompt_config else None,
                )
        except Exception as e:
            logger.warning(
                f"[Game {game_id}] Async narration failed for {req.player_name}: {e}",
                exc_info=True,
            )


def _run_async_commentary(
    game_id: str, game_data: dict, completion_event: threading.Event = None
) -> None:
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
    sandbox_id = _sandbox_id_for(game_data)
    from flask_app.extensions import chip_ledger_repo as _ledger_repo

    # Director rake (reserve-gated, flag-off by default): may expand the raked
    # stakes / rate when the bank is empty; otherwise the static $1000 skim.
    rake_stakes, rake_rate = economy_flags.resolve_rake_params(_ledger_repo, sandbox_id)
    rake = economy_flags.compute_rake(
        pot_size, big_blind, stake_big_blinds=rake_stakes, rate=rake_rate
    )
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
    from cash_mode import economy_flags

    custody = economy_flags.CHIP_CUSTODY_ENABLED
    if winner_player.is_human:
        owner_id = game_data.get('owner_id')
        if not owner_id:
            logger.warning(f"[Game {game_id}] rake skipped: human winner with no owner_id")
            return game_state
        # Rake comes off the winner's at-table STACK (deducted above), not the
        # bankroll. Under chip custody those chips live in the seat account
        # (`seat:<game_id>` for humans, `seat:ai:...` for AI — Cut 2 / Phase 1),
        # so sourcing the rake there keeps the ledger-derived bankroll in step
        # with the stored int. Reason stays `table_rake` (pool depth unchanged).
        source = chip_ledger.seat(game_id) if custody else chip_ledger.player(owner_id)
    else:
        pid = cash_pids.get(headline_name)
        if not pid:
            logger.warning(
                f"[Game {game_id}] rake skipped: no personality_id for AI winner {headline_name!r}"
            )
            return game_state
        source = (
            chip_ledger.ai_seat(sandbox_id, pid)
            if (custody and sandbox_id)
            else chip_ledger.ai(pid)
        )
    ctx = {
        'site': 'handle_evaluating_hand_phase',
        'game_id': game_id,
        'pot': pot_size,
        'big_blind': big_blind,
        'winner_name': headline_name,
        'winner_is_human': winner_player.is_human,
    }
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


def _record_cash_scalps(game_data: dict, game_state, winner_name: str) -> None:
    """Record this hand's eliminations at a human-occupied cash table (scalp
    tracker §3b). Eliminator = the headline pot winner (the human's `owner_id`
    or an AI `personality_id`); victims = non-human players busted (stack 0) by
    the just-applied award. Headline-winner rule, consistent with the world-sim
    path. Pure best-effort — the caller wraps it; this also self-guards so a
    missing repo / unmapped id degrades to a no-op."""
    from flask_app import extensions

    repo = getattr(extensions, "cash_scalps_repo", None)
    if repo is None:
        return
    sandbox_id = _sandbox_id_for(game_data)
    if not sandbox_id:
        return
    cash_pids = game_data.get("cash_personality_ids") or {}  # display name -> pid

    winner_player = next((p for p in game_state.players if p.name == winner_name), None)
    if winner_player is None:
        return
    eliminator_id = (
        game_data.get("owner_id")
        if getattr(winner_player, "is_human", False)
        else cash_pids.get(winner_name)
    )
    if not eliminator_id:
        return

    victim_ids = [
        cash_pids[p.name]
        for p in game_state.players
        if not getattr(p, "is_human", False) and p.stack == 0 and p.name in cash_pids
    ]
    if not victim_ids:
        return

    from datetime import datetime

    from cash_mode.scalps import eliminations_from_human_hand

    scalps = eliminations_from_human_hand(eliminator_id, victim_ids)
    if scalps:
        repo.record_many(sandbox_id, scalps, now=datetime.now().isoformat())


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
    pot_size_before_award = (
        game_state.pot.get('total', 0) if isinstance(game_state.pot, dict) else 0
    )

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

    # Scalp attribution (CASH_MODE_SCALP_TRACKER.md §3b): record eliminations at
    # the human's table now that the award is applied (stacks final). Best-effort
    # — never let scalp recording taint the hand flow.
    if game_data.get('cash_mode'):
        try:
            _record_cash_scalps(game_data, game_state, winning_player_names[0])
        except Exception:
            logger.debug("[CASH] scalp record failed (non-fatal)", exc_info=True)

    # Prepare winner announcement data
    winning_players_string = (
        (', '.join(winning_player_names[:-1]) + f" and {winning_player_names[-1]}")
        if len(winning_player_names) > 1
        else winning_player_names[0]
    )

    active_players = [p for p in game_state.players if not p.is_folded]
    is_showdown = len(active_players) > 1

    # Determine if this is the final hand of the tournament
    is_final_hand = False
    tournament_outcome = None
    # Final-hand banner for any tournament game — legacy single-table
    # (tracker), single-table session, or multi-table session. The human is read
    # straight off the live seats, so this is wrapper-agnostic.
    if game_data.get('tournament_session') is not None:
        # Count players who still have chips after this hand
        players_with_chips = [p for p in game_state.players if p.stack > 0]
        if len(players_with_chips) == 1:
            # Only one player has chips - this is the final hand
            is_final_hand = True
            human_player = next((p for p in game_state.players if p.is_human), None)
            if human_player is not None:
                winner = players_with_chips[0]
                human_won = winner.name == human_player.name
                # Position: 1st if won, 2nd if lost (this only runs when 1 player has chips left)
                human_position = 1 if human_won else 2
                tournament_outcome = {'human_won': human_won, 'human_position': human_position}

    winner_data = prepare_showdown_data(
        game_state, winner_info, winning_player_names, is_final_hand, tournament_outcome
    )

    # Calculate total pot and net profit from pot_breakdown (split-pot support)
    total_pot = sum(pot['total_amount'] for pot in winner_info.get('pot_breakdown', []))
    pot_dict = game_state.pot if isinstance(game_state.pot, dict) else {}
    winner_contributions = sum(pot_dict.get(name, 0) for name in winning_player_names)
    net_profit = total_pot - winner_contributions

    # Track the biggest pot at a session-backed tournament's human table — the
    # TournamentSession owns chips/eliminations but not pot sizes, so the live
    # game feeds `biggest_pot` for the unified completion result + career stats.
    if game_data.get('tournament_session') is not None:
        game_data['tournament_biggest_pot'] = max(
            game_data.get('tournament_biggest_pot', 0), total_pot
        )

    if is_showdown:
        message_content = (
            f"{winning_players_string} won ${net_profit} with {winner_info['hand_name']}. "
            f"Winning hand: {winner_info['winning_hand']}"
        )
        # Build structured win_result for rich chat rendering
        winner_hole_cards = []
        if winning_player_names:
            winner_player = next(
                (p for p in game_state.players if p.name == winning_player_names[0]), None
            )
            winner_hole_cards = (
                [str(c) for c in winner_player.hand] if winner_player and winner_player.hand else []
            )
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
                from poker.equity_snapshot import HandEquityHistory
                from poker.repositories.hand_equity_repository import HandEquityRepository

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
            # Composure updates inline (it affects play); the emotional narration
            # LLM calls are returned as pending jobs and run in a background task
            # so they don't gate the next hand. See _run_async_narration.
            defer_narration=True,
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

        # Emotional narration (prose) is deferred off the inter-hand critical
        # path: composure already updated synchronously above; the LLM calls run
        # in the background (concurrent with commentary) and persist the prose
        # themselves. Only the consumers (chaos/hybrid, or any bot in heads-up)
        # produce pending jobs — see PsychologyPipeline._update_composure.
        if psych_result.pending_narrations:
            socketio.start_background_task(
                _run_async_narration, game_id, game_data, psych_result.pending_narrations
            )

    # Start async commentary (genuinely slow — multiple LLM calls)
    commentary_complete = threading.Event()

    if not config.ENABLE_AI_COMMENTARY:
        commentary_complete.set()
    else:
        socketio.start_background_task(
            _run_async_commentary, game_id, game_data, commentary_complete
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

    # Single-table tournament hand boundary. Every non-cash game is a one-table
    # TournamentSession (TournamentTracker is retired): fold the finished hand
    # into the field, feed per-elimination beats, and end the game the moment the
    # human's fate is sealed (bust or heads-up win). Multi-table games
    # (tournament_multi_table) run their own boundary later in the inter-hand
    # step; cash games have no session and skip this entirely.
    _st_session = game_data.get('tournament_session')
    if _st_session is not None and not game_data.get('tournament_multi_table'):
        from flask_app.handlers.single_table_tournament import single_table_hand_boundary

        if single_table_hand_boundary(
            game_id, game_data, game_state, winning_player_names, winner_data
        ):
            state_machine.current_phase = PokerPhase.GAME_OVER
            game_data['state_machine'] = state_machine
            game_state_service.set_game(game_id, game_data)
            update_and_emit_game_state(game_id)
            owner_id, owner_name = game_state_service.get_game_owner_info(game_id)
            game_repo.save_game(game_id, state_machine._state_machine, owner_id, owner_name)
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
        socketio.emit(
            'game_error',
            {'error': 'Failed to transition between hands', 'recoverable': True},
            to=game_id,
        )
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
        # Player-prestige hook 4: nudge seated AIs' demeanor by the human's
        # reputation (kill-switched via REPUTATION_DEMEANOR_ENABLED). After
        # refill so fresh arrivals feel it too. Self-protecting (best-effort
        # internally) — never raises into the hand flow.
        _apply_reputation_demeanor(game_data, state_machine)
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
        chip_holders = sum(1 for p in state_machine.game_state.players if p.stack > 0)
        human = next((p for p in state_machine.game_state.players if p.is_human), None)
        human_busted = human is not None and human.stack == 0
        # Pause the table in HAND_OVER (don't deal the next hand) whenever
        # the human can't be dealt back in this instant. Two shapes:
        #   - human busted (stack 0): they're on the rebuy/sponsor modal.
        #     Rebuy is only valid between hands, so dealing on among the
        #     remaining AIs advances the phase out of HAND_OVER and the
        #     rebuy POST gets rejected ("only allowed between hands"). This
        #     holds EVEN WHEN 2+ AIs still have chips — without the pause
        #     the table plays on without the human and there's no
        #     between-hands window for the rebuy to land in.
        #   - fewer than 2 chip-holders: the table can't deal anyway, and
        #     the state machine would loop HAND_OVER → INIT_HAND →
        #     SHOWDOWN → HAND_OVER, hit the 50-iteration cap, and pin
        #     progress_game's lock — blocking /api/cash/leave for the user
        #     staring at the bust modal.
        # Either way, rebuy or leave unsticks it (rebuy calls progress_game).
        if human_busted or chip_holders < 2:
            # Distinguish the two dead-table shapes so the frontend shows
            # the right prompt:
            #   - human busted (stack 0): the bust/rebuy modal, already
            #     emitted by _detect_human_cash_bust above.
            #   - human still has chips but every opponent left or busted
            #     without a refill: the "everyone left" solo prompt, which
            #     offers Stay (reseat named AIs) or Leave (cash out).
            # Set the flag HERE — after _refill_cash_seats had its chance
            # — so a normal heads-up win (last opponent at 0 chips for one
            # HAND_OVER frame, about to be refilled) never trips it.
            paused_players = state_machine.game_state.players
            human = next((p for p in paused_players if p.is_human), None)
            others_have_chips = any(p.stack > 0 and not p.is_human for p in paused_players)
            if human is not None and human.stack > 0 and not others_have_chips:
                candidates = []
                try:
                    candidates = select_rejoin_candidates(
                        game_data, state_machine.game_state, limit=2
                    )
                except Exception as e:
                    logger.error(
                        "[CASH] rejoin-candidate selection failed for %r: %s",
                        game_id,
                        e,
                        exc_info=True,
                    )
                game_data['cash_solo_paused'] = True
                game_data['cash_rejoin_candidates'] = candidates
                logger.info(
                    "[CASH] Solo table game_id=%r — all opponents gone, "
                    "human has chips; offering %d rejoin candidate(s)",
                    game_id,
                    len(candidates),
                )
            else:
                game_data['cash_solo_paused'] = False
                game_data['cash_rejoin_candidates'] = []
            game_data['state_machine'] = state_machine
            game_state_service.set_game(game_id, game_data)
            update_and_emit_game_state(game_id)
            owner_id, owner_name = game_state_service.get_game_owner_info(game_id)
            game_repo.save_game(game_id, state_machine._state_machine, owner_id, owner_name)
            logger.info(
                "[CASH] Paused game_id=%r in HAND_OVER — human_busted=%s, "
                "%d player(s) with chips; waiting for rebuy or leave",
                game_id,
                human_busted,
                chip_holders,
            )
            return state_machine.game_state, True

        # Quorum is intact — clear any stale solo-pause flag so a table
        # that was just re-seated (via /api/cash/reseat) doesn't re-show
        # the "everyone left" prompt on the next state frame.
        game_data.pop('cash_solo_paused', None)
        game_data.pop('cash_rejoin_candidates', None)

    # Multi-table tournament: the human plays one table; at each hand boundary
    # fold the result into the field, pace the AI tables, settle, and either
    # reconcile this table's roster/blinds for the next hand (continue/relocated)
    # or pause the game (human out / tournament complete). Gated on
    # `tournament_multi_table` (not merely `tournament_session`, which EVERY
    # non-cash game now carries) — single-table tournaments run their own boundary
    # at handle_evaluating_hand_phase above and must NOT also run this one, or
    # they double-advance the session and reset blinds/dealer every hand.
    if game_data.get('tournament_multi_table'):
        try:
            from flask_app.handlers.tournament_game_builder import tournament_hand_boundary

            tournament_stop = tournament_hand_boundary(game_id, game_data, state_machine)
        except Exception as e:
            logger.error(f"[TOURNEY] hand-boundary failed for {game_id}: {e}", exc_info=True)
            tournament_stop = True  # pause rather than risk a runaway loop
            # Recovery: if the field is actually terminal (the boundary may have
            # advanced it before raising, or a re-entry raised on an already-
            # finished event), finalize so the human lands on the standings/win
            # screen instead of a permanently frozen table. finalize_tournament is
            # idempotent and self-guards on terminal state, so this is a no-op
            # otherwise.
            try:
                from flask_app.handlers.tournament_completion import finalize_tournament

                _sess = game_data.get('tournament_session')
                if _sess is not None and (_sess.is_complete() or _sess.human_out):
                    finalize_tournament(game_id, game_data, emit=_sess.is_complete())
            except Exception:
                logger.exception("[TOURNEY] post-failure finalize also failed for %s", game_id)
        if tournament_stop:
            game_data['state_machine'] = state_machine
            game_state_service.set_game(game_id, game_data)
            update_and_emit_game_state(game_id)
            owner_id, owner_name = game_state_service.get_game_owner_info(game_id)
            game_repo.save_game(game_id, state_machine._state_machine, owner_id, owner_name)
            return state_machine.game_state, True

    # Advance to next hand - run until player action needed (deals cards, posts blinds)
    try:
        state_machine.run_until_player_action()
        # Flush any top-up the human staged mid-hand now that the fresh
        # stack exists. Done before set_game/emit so the credited stack is
        # what gets persisted and shown for the new hand.
        _flush_pending_topup(game_id, game_data, state_machine)
        game_data['state_machine'] = state_machine
        game_state_service.set_game(game_id, game_data)
        update_and_emit_game_state(game_id)
    except (ValueError, KeyError, RuntimeError, OSError, ArithmeticError) as e:
        logger.error(f"Failed to advance to next hand for game {game_id}: {e}", exc_info=True)
        # Persist whatever state we have to prevent inconsistency
        game_state_service.set_game(game_id, game_data)
        socketio.emit(
            'game_error', {'error': 'Failed to start new hand', 'recoverable': True}, to=game_id
        )
        return state_machine.game_state, False

    # Start recording new hand AFTER cards are dealt
    if 'memory_manager' in game_data:
        memory_manager = game_data['memory_manager']
        new_hand_number = memory_manager.hand_count + 1
        memory_manager.on_hand_start(
            state_machine.game_state,
            hand_number=new_hand_number,
            deck_seed=state_machine.current_hand_seed,
        )
        memory_manager.record_blinds(state_machine.game_state)

    # Track hand_start_stacks for stack-based pressure events (double_up, crippled, short_stack)
    # Capture after blinds are posted but before any betting action
    game_data['hand_start_stacks'] = {p.name: p.stack for p in state_machine.game_state.players}

    # Initialize short_stack_players tracking if not exists
    if 'short_stack_players' not in game_data:
        big_blind = (
            state_machine.game_state.current_ante
            if hasattr(state_machine.game_state, 'current_ante')
            else 100
        )
        game_data['short_stack_players'] = {
            p.name
            for p in state_machine.game_state.players
            if p.stack < 10 * big_blind and p.stack > 0
        }

    # Save state after hand evaluation completes (now in stable phase). The
    # tournament session (eliminations/standings) is persisted separately by the
    # hand boundary above.
    owner_id, owner_name = game_state_service.get_game_owner_info(game_id)
    game_repo.save_game(game_id, state_machine._state_machine, owner_id, owner_name)

    limit_reached = _track_guest_hand(game_id, game_data)
    if limit_reached:
        return state_machine.game_state, True

    return state_machine.game_state, False


def _flush_pending_topup(game_id: str, game_data: dict, state_machine) -> None:
    """Apply a cash top-up the human staged mid-hand, at the next deal.

    A human who clicks "Top up" mid-hand (folded or still in the pot)
    can't have their live stack touched, so ``/api/cash/topup`` parks the
    amount in ``game_data['pending_topup']`` instead of racing the
    auto-dealt next hand (which used to make the request hang on the game
    lock and then 400). This runs right after the next hand is dealt: it
    debits the bankroll and credits the fresh stack.

    Debiting *here* — not at request time — is deliberate: until the
    chips actually land on the stack no money has left the bankroll, so a
    leave / bust / session-drop before this point can't strand committed
    chips. The bankroll debit is persisted before the stack credit so a
    failure can't mint chips; on any error the stage is left intact and
    retried at the following deal. Best-effort: never raises into the
    hand-progression flow.
    """
    try:
        pending = int(game_data.get('pending_topup', 0) or 0)
    except (TypeError, ValueError):
        pending = 0
    if pending <= 0:
        return

    human_idx = next(
        (i for i, p in enumerate(state_machine.game_state.players) if p.is_human),
        None,
    )
    if human_idx is None:
        # Human isn't seated this deal (e.g. just left). Nothing was
        # debited, so no chips are at risk — leave the stage parked.
        return

    try:
        from cash_mode.bankroll import PlayerBankrollState
        from flask_app.extensions import bankroll_repo

        if bankroll_repo is None:
            return
        owner_id, _owner_name = game_state_service.get_game_owner_info(game_id)
        bankroll = bankroll_repo.load_player_bankroll(owner_id)
        if bankroll is None or bankroll.chips <= 0:
            # No bankroll to draw from — drop the stale stage so it can't
            # silently apply later.
            game_data.pop('pending_topup', None)
            return

        applied = min(pending, bankroll.chips)
        # Debit the persistent side first; only credit the live stack once
        # the debit is committed.
        bankroll_repo.save_player_bankroll(
            PlayerBankrollState(
                player_id=bankroll.player_id,
                chips=bankroll.chips - applied,
                starting_bankroll=bankroll.starting_bankroll,
            )
        )
        new_stack = state_machine.game_state.players[human_idx].stack + applied
        state_machine.game_state = state_machine.game_state.update_player(
            human_idx, stack=new_stack
        )
        game_data.pop('pending_topup', None)
    except Exception as e:
        logger.error(
            "[CASH] Failed to flush staged top-up game_id=%r: %s",
            game_id,
            e,
            exc_info=True,
        )
        return

    # Mirror the immediate top-up path: count it toward leave-time P&L and
    # emit the paired buy-in ledger row. Best-effort — the chips are
    # already on the stack, so accounting hiccups must not unwind them.
    try:
        from flask_app.routes.cash_routes import _increment_cash_session_buy_in

        _increment_cash_session_buy_in(game_id, applied)
    except Exception as e:
        logger.error(
            "[CASH] Staged top-up buy-in accounting failed game_id=%r: %s",
            game_id,
            e,
            exc_info=True,
        )

    logger.info(
        "[CASH] Flushed staged top-up game_id=%r applied=%d new_stack=%d",
        game_id,
        applied,
        new_stack,
    )
    send_message(game_id, "Table", f"You topped up ${applied:,}.", "table")


def handle_human_turn(game_id: str, game_data: dict, game_state) -> None:
    """Handle when it's a human player's turn."""
    cost_to_call = game_state.highest_bet - game_state.current_player.bet
    player_options = (
        list(game_state.current_player_options) if game_state.current_player_options else []
    )
    socketio.emit(
        'player_turn_start',
        {'current_player_options': player_options, 'cost_to_call': cost_to_call},
        to=game_id,
    )

    # Emit elasticity update for UI display
    if 'ai_controllers' in game_data:
        elasticity_data = format_elasticity_data(game_data['ai_controllers'])
        socketio.emit('elasticity_update', elasticity_data, to=game_id)

    # Prefetch the proactive coach tip now, off the critical path, so the LLM
    # call overlaps the player's thinking time instead of starting only after
    # the client round-trips. No-op unless coach mode is 'proactive'; the /ask
    # path serves this cached result (one LLM call per decision). Best-effort.
    try:
        from flask_app.services.coach_prefetch import prefetch_proactive_tip

        socketio.start_background_task(prefetch_proactive_tip, game_id)
    except Exception as e:
        logger.debug(f"[COACH_PREFETCH] failed to schedule for {game_id}: {e}")


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
            awaiting_action=False,
            run_it_out=False,
        )
        state_machine._state_machine = state_machine._state_machine.with_game_state(
            cleared
        ).with_phase(next_phase)

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

        # Rehydrate cash-mode metadata when a background hand advance (world
        # ticker / socket) cold-loaded this game without passing through the
        # /api/game-state restore block. Otherwise the hand-end cash flow
        # (refill, bust-detect, lobby refresh + table-binding self-heal) is
        # gated off by the dropped `cash_mode` flag and the human seat
        # orphans — the cold-load ghost-seat split-brain.
        _ensure_cash_mode(game_id, current_game_data)

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
            if current_phase != last_announced_phase and current_phase in [
                PokerPhase.FLOP,
                PokerPhase.TURN,
                PokerPhase.RIVER,
            ]:
                handle_phase_cards_dealt(game_id, state_machine, game_state, current_game_data)
                current_game_data['last_announced_phase'] = current_phase
                game_state_service.set_game(game_id, current_game_data)

            # Handle "run it out" scenario - auto-advance with delays
            if game_state.run_it_out:
                # Reveal hole cards once before first run-out (dramatic showdown reveal)
                if not game_state.has_revealed_cards:
                    emit_hole_cards_reveal(game_id, game_state)
                    game_state = game_state.update(has_revealed_cards=True)
                    state_machine._state_machine = state_machine._state_machine.with_game_state(
                        game_state
                    )
                    current_game_data['state_machine'] = state_machine
                    game_state_service.set_game(game_id, current_game_data)

                    # Pre-compute run-out reactions while players view hole cards
                    reaction_schedule = compute_runout_reactions(
                        game_state, current_game_data.get('ai_controllers', {})
                    )
                    current_game_data['runout_reaction_schedule'] = reaction_schedule

                    # Emit the per-card schedule once, for the mobile run-out
                    # director (Phase 2). Carries reactions + per-card timing
                    # only — no board cards — so future-street cards never reach
                    # the client ahead of reveal. The director reads each
                    # street's faces from the per-street state push it already
                    # gets. Desktop ignores this event and stays on the
                    # backend-paced per-street emits below (one backend path,
                    # no mobile/desktop branching). Emitted alongside the reveal
                    # so the client has the timeline before the board moves.
                    if reaction_schedule.steps:
                        socketio.emit(
                            'runout_schedule',
                            runout_schedule_payload(reaction_schedule),
                            to=game_id,
                        )
                        # Pre-warm every emotion image the schedule will use, now,
                        # at the reveal — generation takes ~5-7s, and the whole
                        # run-out plays over a similar window, so an emotion first
                        # requested at its beat (the old on-demand path) only
                        # finishes after that beat has passed (it then pops in on
                        # the winner screen). Firing here gives each the maximum
                        # head start. Thread-safe + skips already-cached/in-flight
                        # emotions, so this is a cheap fire-and-forget.
                        prewarmed = {
                            (r.player_name, r.emotion)
                            for step in reaction_schedule.steps
                            for r in step.reactions
                        }
                        for player_name, emotion in prewarmed:
                            start_single_emotion_generation(game_id, player_name, emotion)

                    # The INITIAL (hole-card) reactions are the players' read on
                    # the matchup. We deliberately do NOT emit them here, at the
                    # same instant as the reveal — they land as their own beat
                    # AFTER the cards have settled (the PRE_FLOP per-street emit
                    # below maps to the INITIAL schedule). Start with no overrides
                    # so the reveal cascade plays on its own first.
                    current_game_data['runout_emotion_overrides'] = {}
                    game_state_service.set_game(game_id, current_game_data)

                    # Brief pause for players to register the all-in matchup
                    # before the board runs out (see config.RUNOUT_REVEAL_HOLD).
                    delay = config.RUNOUT_REVEAL_HOLD * config.ANIMATION_SPEED
                    if delay > 0:
                        _ff_aware_sleep(game_id, delay)

                # Wait for card animation to finish, then emit reactions,
                # then hold so the player can absorb before next street.
                # Flop (3 cards): ~2.825s animation (2s stagger + 0.825s)
                # Turn/River (1 card): ~0.825s animation
                # PRE_FLOP is the reveal step — no community card is dealt, so
                # there's no animation to wait for. Skipping it removes a dead
                # ~1s before the board ran out; the reveal cascade already plays
                # during RUNOUT_REVEAL_HOLD above, and the reaction_hold below
                # now shows the preflop (INITIAL) reactions.
                if current_phase == PokerPhase.FLOP:
                    animation_sleep = 3
                elif current_phase in (PokerPhase.TURN, PokerPhase.RIVER):
                    animation_sleep = 1
                else:
                    animation_sleep = 0
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
                    # The PRE_FLOP reveal step shows the INITIAL (hole-card)
                    # reactions — the players' read on the matchup — now as their
                    # own beat, after the reveal has settled.
                    phase_name = (
                        'INITIAL' if current_phase == PokerPhase.PRE_FLOP else current_phase.name
                    )
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
                            for reaction in reaction_schedule.reactions_by_phase.get(
                                'SHOWDOWN', []
                            ):
                                if overrides.get(reaction.player_name) == reaction.emotion:
                                    continue
                                overrides[reaction.player_name] = reaction.emotion
                                _emit_avatar_reaction(
                                    game_id, reaction.player_name, reaction.emotion
                                )
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
                state_machine._state_machine = state_machine._state_machine.with_game_state(
                    new_game_state
                ).with_phase(next_phase)
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
                p.is_all_in
                for p in game_state.players
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


def _resolve_human_bio(current_game_data: dict) -> str:
    """Resolve the human owner's AI-visible bio, cached per game.

    Read once per game (lazily). The bio is set-and-forget in /profile, so a
    mid-game edit not reflecting until the game reloads is acceptable, and this
    avoids a DB hit on every AI decision and commentary pass.
    """
    if 'human_bio' in current_game_data:
        return current_game_data['human_bio']
    owner_id = current_game_data.get('owner_id')
    if not owner_id:
        current_game_data['human_bio'] = ""
        return ""
    try:
        from ..extensions import user_prefs_repo

        bio = user_prefs_repo.get_bio(owner_id) if user_prefs_repo else ""
    except Exception as e:
        # Don't cache on failure — a transient DB error shouldn't suppress the
        # bio for the rest of the session; retry on the next decision.
        logger.debug(f"Could not resolve human bio for {owner_id}: {e}")
        return ""
    current_game_data['human_bio'] = bio
    return bio


def _resolve_game_speed(current_game_data: dict) -> str:
    """Resolve the owner's game-speed preference, cached per game.

    'standard' / 'after_fold' / 'always'. Read once per game (lazily) — it's a
    set-and-forget user preference, so a mid-game change not taking effect until
    the game reloads is acceptable, and this avoids a DB hit on every AI turn.
    Mirrors `_resolve_human_bio`.
    """
    if 'game_speed' in current_game_data:
        return current_game_data['game_speed']
    owner_id = current_game_data.get('owner_id')
    if not owner_id:
        current_game_data['game_speed'] = 'standard'
        return 'standard'
    try:
        from ..extensions import user_prefs_repo

        value = user_prefs_repo.get_game_speed(owner_id) if user_prefs_repo else 'standard'
    except Exception as e:
        # Don't cache on failure — retry on the next turn.
        logger.debug(f"Could not resolve game_speed for {owner_id}: {e}")
        return 'standard'
    current_game_data['game_speed'] = value
    return value


def maybe_engage_fast_forward_on_fold(game_id: str, action: str) -> None:
    """If the human just folded and their speed is 'after_fold', fast-forward.

    Sets the one-orbit `fast_forward` flag so `handle_ai_action` swaps the
    remaining AIs to no-LLM tiered controllers; it auto-clears when action
    returns to the human next hand. ('always' doesn't need this — handle_ai_action
    fast-forwards every turn on its own.) Call after applying the human's action,
    before progress_game.
    """
    if action != 'fold':
        return
    current_game_data = game_state_service.get_game(game_id)
    if not current_game_data or current_game_data.get('fast_forward'):
        return
    if _resolve_game_speed(current_game_data) == 'after_fold':
        current_game_data['fast_forward'] = True
        game_state_service.set_game(game_id, current_game_data)
        logger.info(f"[FF] game={game_id} fast-forward engaged after human fold")


def stamp_coach_default_mode(game_id: str, owner_id: Optional[str]) -> None:
    """Seed a new game's coach mode from the owner's default preference.

    The user's "default coaching mode" is a sticky per-user pref; applying it to
    each new game at creation is what makes it carry across devices (the games
    table is the source of truth the in-game coach reads). The in-game coach
    panel still overrides per game. No-op for guests, or when the default is the
    column default ('off') so we don't write redundantly.
    """
    if not owner_id:
        return
    try:
        from ..extensions import user_prefs_repo

        mode = user_prefs_repo.get_coach_default_mode(owner_id) if user_prefs_repo else 'off'
        if mode and mode != 'off':
            game_repo.save_coach_mode(game_id, mode)
    except Exception as e:
        logger.debug(f"[Coach] failed to stamp default mode for game {game_id}: {e}")


def _resolve_human_reputation_tone(current_game_data: dict) -> str:
    """Resolve a table-talk tone hint from the human's reputation, cached per game.

    Hook 3 of the prestige system: the human's room-level reputation quadrant
    colors how AIs address them in their table talk — warm/deferential for a
    Beloved Legend, needling/hostile for an Infamous Villain, and silent for
    low-renown players the room doesn't react to yet (see
    `cash_mode.prestige.reputation_chat_tone`). Read-only FLAVOR: the hint only
    reaches the ExpressionGenerator's prompt suffix, never the action math.

    Cached on `game_data` like `human_bio`: reputation drifts slowly (the world
    ticker recomputes it on the order of minutes, and renown ratchets), so a
    per-game read is fresh enough and avoids a DB hit on every AI decision. A
    quadrant change mid-session won't reflect until the game reloads — an
    accepted tradeoff matching the bio precedent. Tournament games (no
    sandbox/owner) and pre-first-capture sessions resolve to "".
    """
    if 'human_reputation_tone' in current_game_data:
        return current_game_data['human_reputation_tone']
    owner_id = current_game_data.get('owner_id')
    sandbox_id = _sandbox_id_for(current_game_data)
    if not owner_id or not sandbox_id:
        current_game_data['human_reputation_tone'] = ""
        return ""
    try:
        from cash_mode.prestige import reputation_chat_tone

        from ..extensions import prestige_snapshots_repo

        tone = ""
        if prestige_snapshots_repo is not None:
            snap = prestige_snapshots_repo.load_latest(sandbox_id, owner_id)
            if snap is not None:
                tone = reputation_chat_tone(snap['quadrant'])
    except Exception as e:
        # Don't cache on failure — a transient DB error shouldn't suppress the
        # tone for the rest of the session; retry on the next decision.
        logger.debug(f"Could not resolve human reputation tone for {owner_id}: {e}")
        return ""
    current_game_data['human_reputation_tone'] = tone
    return tone


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
    # no LLM expression) so the turn resolves quickly. Engaged by the one-orbit
    # `fast_forward` flag (manual FF button / after-fold trigger) OR permanently
    # when the owner's game speed is 'always'. Per-game FF controllers are
    # cached in `ff_controllers` to avoid rebuilding the strategy tables.
    if current_game_data.get('fast_forward') or _resolve_game_speed(current_game_data) == 'always':
        controller = _get_or_build_ff_controller(
            current_game_data,
            current_player.name,
            state_machine,
            game_id,
        )
    else:
        controller = ai_controllers[current_player.name]

    # Set current hand number for tracking
    if 'memory_manager' in current_game_data:
        controller.current_hand_number = current_game_data['memory_manager'].hand_count

    # Surface the human's self-description so the AI can needle them about it in
    # its table talk (dramatic_sequence). Runtime context, not config.
    controller.human_bio = _resolve_human_bio(current_game_data)
    # Surface the human's room-level reputation so the AI's table talk skews by
    # it (warm for a Beloved Legend, needling for an Infamous Villain). Flavor
    # only — it reaches the narration prompt, never the action math.
    controller.human_reputation_tone = _resolve_human_reputation_tone(current_game_data)

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
                max_raise=max_raise,
            )
            action = fallback_result['action']
            amount = fallback_result['raise_to']
            full_message = ''
        else:
            player_response_dict = controller.decide_action(
                game_messages[-AI_MESSAGE_CONTEXT_LIMIT:]
            )

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
            f"[AI_ACTION] Critical error getting AI decision for " f"{current_player.name}: {e}",
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
            max_raise=max_raise,
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
            game_id,
            current_player.name,
            full_message,
            "ai",
            sleep=1,
            addressing=response_addressing,
        )

    if action == 'fold':
        detect_and_apply_pressure(game_id, 'fold', player_name=current_player.name)
    elif action in ['raise', 'all_in'] and amount > 0:
        detect_and_apply_pressure(
            game_id, 'big_bet', player_name=current_player.name, bet_size=amount
        )

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
            logger.debug(
                f"[Energy] Applied action events for {current_player.name}: {[e[0] for e in action_events]}"
            )

    # Save pre-action state for decision analysis
    pre_action_state = state_machine.game_state

    game_state = play_turn(state_machine.game_state, action, amount)

    # Analyze decision quality (for all AI players including RuleBots)
    from flask_app.routes.game_routes import analyze_player_decision

    memory_manager = current_game_data.get('memory_manager')
    hand_number = memory_manager.hand_count if memory_manager else None
    analyze_player_decision(
        game_id,
        current_player.name,
        action,
        amount,
        state_machine,
        pre_action_state,
        hand_number,
        memory_manager,
        ai_controllers=current_game_data.get('ai_controllers'),
    )

    # Normalize the recorded amount for calls: the LLM/UI passes raise_to=0 for
    # calls since they're not raising, but downstream consumers (opponent model,
    # c-bet detector, hand recap, decision analysis) expect the actual call
    # cost. Compute it from the pre-action state.
    record_amount = amount
    if action == 'call':
        record_amount = max(
            0, min(pre_action_state.highest_bet - current_player.bet, current_player.stack)
        )
    record_action_in_memory(
        current_game_data, current_player.name, action, record_amount, game_state, state_machine
    )
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
            'attitude': getattr(controller.ai_player, 'attitude', 'Neutral'),
        }
        game_repo.save_ai_player_state(
            game_id,
            current_player.name,
            controller.assistant.memory.get_history(),
            personality_state,
        )

        # Save unified psychology state and prompt config
        psychology_dict = controller.psychology.to_dict()
        prompt_config_dict = (
            controller.prompt_config.to_dict() if hasattr(controller, 'prompt_config') else None
        )
        game_repo.save_controller_state(
            game_id,
            current_player.name,
            psychology=psychology_dict,
            prompt_config=prompt_config_dict,
        )

    update_and_emit_game_state(game_id)
