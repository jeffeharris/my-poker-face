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
from poker.ai_resilience import get_fallback_chat_response, FallbackActionSelector, AIFallbackStrategy
from poker.betting_context import BettingContext
from poker.config import MIN_RAISE, AI_MESSAGE_CONTEXT_LIMIT
from poker.poker_game import determine_winner, play_turn, advance_to_next_active_player, award_pot_winnings
from poker.poker_state_machine import PokerPhase
from poker.hand_evaluator import HandEvaluator
from .avatar_handler import get_avatar_url_with_fallback
from poker.tilt_modifier import TiltState
from poker.elasticity_manager import ElasticPersonality
from poker.emotional_state import EmotionalState
from poker.runout_reactions import compute_runout_reactions
from core.card import Card

from ..extensions import socketio, game_repo, guest_tracking_repo, tournament_repo, hand_history_repo, personality_repo, experiment_repo
from ..services import game_state_service
from ..services.elasticity_service import format_elasticity_data
from ..services.ai_debug_service import get_all_players_llm_stats
from .message_handler import send_message, format_action_message, record_action_in_memory, format_messages_for_api
from .. import config
from poker.guest_limits import GUEST_LIMITS_ENABLED, GUEST_MAX_HANDS

logger = logging.getLogger(__name__)


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
                           experiment_repo=None) -> Dict[str, AIPlayerController]:
    """Restore AI controllers with their saved state.

    Args:
        game_id: The game identifier
        state_machine: The game's state machine
        game_repo: GameRepository for loading AI/controller/emotional states
        owner_id: The owner/user ID for tracking
        player_llm_configs: Per-player LLM configs (provider, model, etc.)
        default_llm_config: Default LLM config for players without specific config
        experiment_repo: ExperimentRepository for AI decision tracking

    Returns:
        Dictionary mapping player names to their AI controllers
    """
    ai_controllers = {}
    ai_states = game_repo.load_ai_player_states(game_id)
    player_llm_configs = player_llm_configs or {}
    default_llm_config = default_llm_config or {}

    controller_states = {}
    emotional_states = {}
    try:
        controller_states = game_repo.load_all_controller_states(game_id)
        emotional_states = game_repo.load_all_emotional_states(game_id)
    except Exception as e:
        logger.warning(f"Could not load controller/emotional states: {e}")

    for player in state_machine.game_state.players:
        if not player.is_human:
            # Get player-specific llm_config or fall back to default
            llm_config = player_llm_configs.get(player.name, default_llm_config)
            controller = AIPlayerController(
                player.name,
                state_machine,
                llm_config=llm_config,
                game_id=game_id,
                owner_id=owner_id,
                experiment_repo=experiment_repo
            )

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
                        controller.psychology.tilt = TiltState.from_dict(ctrl_state['tilt_state'])
                    if ctrl_state.get('elastic_personality'):
                        controller.psychology.elastic = ElasticPersonality.from_dict(ctrl_state['elastic_personality'])
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
            else:
                display_emotion = controller.psychology.get_display_emotion()
            avatar_url = get_avatar_url_with_fallback(game_id, player_name, display_emotion)
            player_dict['avatar_emotion'] = display_emotion
            player_dict['avatar_url'] = avatar_url

            # Add nickname from personality config (for compact UI display)
            nickname = controller.ai_player.personality_config.get('nickname')
            if nickname:
                player_dict['nickname'] = nickname

            # Add psychology data for heads-up mode display
            psych = controller.psychology
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
    game_state_dict['player_options'] = [] if game_state.run_it_out else (list(game_state.current_player_options) if game_state.current_player_options else [])
    game_state_dict['min_raise'] = game_state.min_raise_amount
    game_state_dict['big_blind'] = game_state.current_ante
    game_state_dict['phase'] = str(current_game_data['state_machine'].current_phase).split('.')[-1]
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

    socketio.emit('update_game_state', {'game_state': game_state_dict}, to=game_id)


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


def handle_pressure_events(game_id: str, game_data: dict, game_state,
                           winner_info: dict, winning_player_names: list,
                           pot_size: int) -> None:
    """Apply elasticity pressure events from showdown."""
    if 'pressure_detector' not in game_data:
        return

    pressure_detector = game_data['pressure_detector']
    events = pressure_detector.detect_showdown_events(game_state, winner_info)
    pressure_detector.apply_detected_events(events)

    if not events:
        return

    event_names = [e[0] for e in events]
    send_message(game_id, "System", f"[Debug] Pressure events: {', '.join(event_names)}", "system")

    if 'pressure_stats' not in game_data:
        return

    pressure_stats = game_data['pressure_stats']
    ai_controllers = game_data.get('ai_controllers', {})

    for event_name, affected_players in events:
        details = {
            'pot_size': pot_size,
            'hand_rank': winner_info.get('hand_rank'),
            'hand_name': winner_info.get('hand_name')
        }
        pressure_stats.record_event(event_name, affected_players, details)

        # Update psychology (tilt + elastic) for affected AI players
        for player_name in affected_players:
            if player_name in ai_controllers:
                controller = ai_controllers[player_name]
                opponent = winning_player_names[0] if winning_player_names and player_name not in winning_player_names else None
                controller.psychology.apply_pressure_event(event_name, opponent)

    # Emit elasticity update from psychology state
    if ai_controllers:
        elasticity_data = {}
        for name, controller in ai_controllers.items():
            elasticity_data[name] = {
                'traits': controller.psychology.elastic.to_dict().get('traits', {}),
                'mood': controller.psychology.mood
            }
        socketio.emit('elasticity_update', elasticity_data, to=game_id)


def update_tilt_states(game_id: str, game_data: dict, game_state,
                       winner_info: dict, winning_player_names: list,
                       pot_size: int) -> None:
    """Update psychology state (tilt + emotional) for AI players after hand completes."""
    if 'ai_controllers' not in game_data:
        return

    ai_controllers = game_data['ai_controllers']

    # Calculate winnings per player from pot_breakdown (split-pot support)
    winnings_by_player = {}
    for pot in winner_info.get('pot_breakdown', []):
        for winner in pot['winners']:
            winnings_by_player[winner['name']] = winnings_by_player.get(winner['name'], 0) + winner['amount']

    for player in game_state.players:
        if player.name not in ai_controllers:
            continue

        controller = ai_controllers[player.name]

        player_won = player.name in winning_player_names
        amount = winnings_by_player.get(player.name, 0) if player_won else -pot_size

        was_bad_beat = False
        was_bluff_called = False
        if not player_won and not player.is_folded:
            hand_rank = winner_info.get('hand_rank', 0)
            was_bad_beat = hand_rank >= 2

        nemesis = winning_player_names[0] if not player_won and winning_player_names else None
        outcome = 'won' if player_won else ('folded' if player.is_folded else 'lost')
        key_moment = 'bad_beat' if was_bad_beat else ('bluff_called' if was_bluff_called else None)

        # Get session context for emotional state generation
        session_context = {}
        if 'memory_manager' in game_data:
            mm = game_data['memory_manager']
            if hasattr(mm, 'session_memory') and mm.session_memory:
                ctx = mm.session_memory.get_context(player.name)
                if ctx:
                    session_context = {
                        'net_change': getattr(ctx, 'total_winnings', 0),
                        'streak_type': getattr(ctx, 'current_streak', 'neutral'),
                        'streak_count': getattr(ctx, 'streak_count', 0)
                    }

        # Get big blind for emotional spike scaling (current_ante stores the big blind amount)
        big_blind = game_state.current_ante

        # Single unified call to update all psychology state
        try:
            controller.psychology.on_hand_complete(
                outcome=outcome,
                amount=amount,
                opponent=nemesis,
                was_bad_beat=was_bad_beat,
                was_bluff_called=was_bluff_called,
                session_context=session_context,
                key_moment=key_moment,
                big_blind=big_blind,
            )
            logger.debug(
                f"Psychology update for {player.name}: "
                f"tilt={controller.psychology.tilt_level:.2f} ({controller.psychology.tilt_category}), "
                f"emotional={controller.psychology.emotional.valence_descriptor if controller.psychology.emotional else 'none'}"
            )
        except Exception as e:
            logger.warning(f"Psychology state update failed for {player.name}: {e}")


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

    winner_data = {
        'winners': winning_player_names,
        'pot_breakdown': winner_info.get('pot_breakdown', []),
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

                    players_showdown[player.name] = {
                        'cards': formatted_cards,
                        'hand_name': hand_result.get('hand_name', 'Unknown'),
                        'hand_rank': hand_result.get('hand_rank', 10),
                        'kickers': kicker_names
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
            send_message(game_id, player_name, commentary.table_comment, "ai")

        # Attach decision plans from controller and set hand number
        if player_name in ai_controllers:
            controller = ai_controllers[player_name]
            # Get and clear decision plans for this hand
            plans = controller.clear_decision_plans()
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
            memory_manager.apply_learned_adjustments(
                name,
                controller.psychology.elastic
            )
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


def _run_async_hand_complete_tasks(game_id: str, game_data: dict, game_state,
                                    winner_info: dict, winning_player_names: list,
                                    pot_size_before_award: int,
                                    completion_event: threading.Event = None) -> None:
    """Run async tasks after winner announcement (emotional state, commentary).

    Args:
        game_id: The game identifier
        game_data: Game data dictionary
        game_state: Current game state
        winner_info: Winner information dict
        winning_player_names: List of winner names
        pot_size_before_award: Pot size before awarding
        completion_event: Optional event to signal when all tasks complete
    """
    try:
        # Update tilt/emotional states (LLM calls)
        update_tilt_states(game_id, game_data, game_state, winner_info, winning_player_names, pot_size_before_award)
    except Exception as e:
        logger.warning(f"Async tilt state update failed: {e}")

    try:
        # Generate AI commentary (LLM calls)
        generate_ai_commentary(game_id, game_data)
    except Exception as e:
        logger.warning(f"Async commentary generation failed: {e}")
    finally:
        # Signal completion so the main game loop can proceed
        if completion_event:
            completion_event.set()


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

    # Calculate total pot from pot_breakdown (split-pot support)
    total_pot = sum(pot['total_amount'] for pot in winner_info.get('pot_breakdown', []))

    if is_showdown:
        message_content = (
            f"{winning_players_string} won the pot of ${total_pot} with {winner_info['hand_name']}. "
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
            'pot': total_pot,
            'hand_name': winner_info['hand_name'],
            'winner_cards': winner_hole_cards,
            'community_cards': community_card_strings,
            'winning_combo': winner_info['winning_hand'],
            'is_showdown': True,
        }
    else:
        message_content = f"{winning_players_string} took the pot of ${total_pot}."
        win_result = {
            'winners': winning_players_string,
            'pot': total_pot,
            'is_showdown': False,
        }

    # EMIT WINNER ANNOUNCEMENT IMMEDIATELY
    send_message(game_id, "Table", message_content, "table", 1, win_result=win_result)
    socketio.emit('winner_announcement', winner_data, to=game_id)

    # Create event to track when async commentary tasks complete
    commentary_complete = threading.Event()

    if not config.ENABLE_AI_COMMENTARY:
        commentary_complete.set()
    else:
        # Start async tasks for emotional state and commentary (LLM calls)
        # Commentary runs in parallel for all AI players, but we wait for all to finish
        socketio.start_background_task(
            _run_async_hand_complete_tasks,
            game_id, game_data, game_state, winner_info, winning_player_names, pot_size_before_award,
            commentary_complete
        )

    # Apply pressure events (fast, local calculations)
    handle_pressure_events(game_id, game_data, game_state, winner_info, winning_player_names, pot_size_before_award)

    # Complete hand recording in memory manager (fast, local)
    if 'memory_manager' in game_data:
        memory_manager = game_data['memory_manager']
        ai_controllers = game_data.get('ai_controllers', {})
        ai_players = {name: controller.ai_player for name, controller in ai_controllers.items()}
        try:
            memory_manager.on_hand_complete(
                winner_info=winner_info,
                game_state=game_state,
                ai_players=ai_players,
                skip_commentary=True
            )
        except Exception as e:
            logger.warning(f"Memory manager hand completion failed: {e}")

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
        socketio.sleep(delay)

    # Apply psychology recovery between hands — elastic traits drift toward
    # anchor, tilt naturally decays, emotional state decays toward baseline
    ai_controllers = game_data.get('ai_controllers', {})
    for controller in ai_controllers.values():
        if hasattr(controller, 'psychology') and controller.psychology:
            controller.psychology.recover(recovery_rate=0.1)

    send_message(game_id, "Table", "***   NEW HAND DEALT   ***", "table")

    # Reset card announcement and run-out reaction tracking for new hand
    game_data['last_announced_phase'] = None
    game_data.pop('runout_reaction_schedule', None)
    game_data.pop('runout_emotion_overrides', None)
    # Sync chip updates to state machine before advancing
    state_machine.game_state = game_state
    state_machine.current_phase = PokerPhase.HAND_OVER

    # Advance to next hand - run until player action needed (deals cards, posts blinds)
    state_machine.run_until_player_action()
    game_data['state_machine'] = state_machine
    game_state_service.set_game(game_id, game_data)
    update_and_emit_game_state(game_id)

    # Start recording new hand AFTER cards are dealt
    if 'memory_manager' in game_data:
        memory_manager = game_data['memory_manager']
        new_hand_number = memory_manager.hand_count + 1
        memory_manager.on_hand_start(state_machine.game_state, hand_number=new_hand_number)

    # Save state after hand evaluation completes (now in stable phase)
    owner_id, owner_name = game_state_service.get_game_owner_info(game_id)
    game_repo.save_game(game_id, state_machine._state_machine, owner_id, owner_name)
    if 'tournament_tracker' in game_data:
        game_repo.save_tournament_tracker(game_id, game_data['tournament_tracker'])

    limit_reached = _track_guest_hand(game_id, game_data)
    if limit_reached:
        return game_state, True

    return game_state, False


def handle_human_turn(game_id: str, game_data: dict, game_state) -> None:
    """Handle when it's a human player's turn."""
    cost_to_call = game_state.highest_bet - game_state.current_player.bet
    player_options = list(game_state.current_player_options) if game_state.current_player_options else []
    socketio.emit('player_turn_start', {'current_player_options': player_options, 'cost_to_call': cost_to_call}, to=game_id)

    # Emit elasticity update for UI display
    if 'elasticity_manager' in game_data:
        elasticity_data = format_elasticity_data(game_data['elasticity_manager'])
        socketio.emit('elasticity_update', elasticity_data, to=game_id)


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
                        socketio.sleep(delay)

                # Wait for card animation to finish, then emit reactions,
                # then hold so the player can absorb before next street.
                # Flop (3 cards): ~2.825s animation (2s stagger + 0.825s)
                # Turn/River (1 card): ~0.825s animation
                animation_sleep = 3 if current_phase == PokerPhase.FLOP else 1
                reaction_hold = 1.5
                delay = animation_sleep * config.ANIMATION_SPEED
                if delay > 0:
                    socketio.sleep(delay)

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
                    socketio.sleep(delay)
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
                            socketio.sleep(delay)

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
                handle_human_turn(game_id, current_game_data, game_state)
                break
    finally:
        lock.release()


def detect_and_apply_pressure(game_id: str, event_type: str, **kwargs) -> None:
    """Helper function to detect and apply pressure events."""
    current_game_data = game_state_service.get_game(game_id)
    if not current_game_data or 'pressure_detector' not in current_game_data:
        return

    pressure_detector = current_game_data['pressure_detector']
    elasticity_manager = current_game_data['elasticity_manager']
    game_state = current_game_data['state_machine'].game_state

    events = []

    if event_type == 'fold':
        folding_player = kwargs.get('player_name')
        pot_size = game_state.pot.get('total', 0) if isinstance(game_state.pot, dict) else 0
        if pot_size > 100:
            events.append(('fold_under_pressure', [folding_player]))

    elif event_type == 'big_bet':
        betting_player = kwargs.get('player_name')
        bet_size = kwargs.get('bet_size', 0)
        pot_size = game_state.pot.get('total', 0) if isinstance(game_state.pot, dict) else 0
        if bet_size > pot_size * 0.75:
            events.append(('aggressive_bet', [betting_player]))

    if events:
        pressure_detector.apply_detected_events(events)

        if 'pressure_stats' in current_game_data:
            pressure_stats = current_game_data['pressure_stats']
            for event_name, affected_players in events:
                details = kwargs.copy()
                pressure_stats.record_event(event_name, affected_players, details)

        elasticity_data = format_elasticity_data(elasticity_manager)
        socketio.emit('elasticity_update', elasticity_data, to=game_id)


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
    controller = ai_controllers[current_player.name]

    # Set current hand number for tracking
    if 'memory_manager' in current_game_data:
        controller.current_hand_number = current_game_data['memory_manager'].hand_count

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

    except Exception as e:
        logger.debug(f"[AI_ACTION] Critical error getting AI decision: {e}")

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
        player_message = get_fallback_chat_response(current_player.name)
        player_physical_description = "*pauses momentarily*"
        full_message = f"{player_message} {player_physical_description}".strip()

        send_message(game_id, "Table", f"[{current_player.name} takes a moment to consider]", "table")

    highest_bet = state_machine.game_state.highest_bet
    action_text = format_action_message(current_player.name, action, amount, highest_bet)

    # Send action as Table message (consistent with human actions)
    send_message(game_id, "Table", action_text, "table")

    # Send AI message if player has something to say or show
    if full_message and full_message != '...':
        send_message(game_id, current_player.name, full_message, "ai", sleep=1)

    if action == 'fold':
        detect_and_apply_pressure(game_id, 'fold', player_name=current_player.name)
    elif action in ['raise', 'all_in'] and amount > 0:
        detect_and_apply_pressure(game_id, 'big_bet', player_name=current_player.name, bet_size=amount)

    game_state = play_turn(state_machine.game_state, action, amount)
    record_action_in_memory(current_game_data, current_player.name, action, amount, game_state, state_machine)
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
