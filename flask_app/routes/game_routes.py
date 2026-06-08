"""Game-related routes and socket events."""

import json
import logging
import secrets
import time
from datetime import datetime
from typing import Dict, Optional

from flask import Blueprint, jsonify, redirect, request, send_from_directory
from flask_socketio import emit, join_room

from core.llm import AVAILABLE_PROVIDERS, PROVIDER_MODELS
from core.moderation import moderate_text
from flask_app.handlers.avatar_handler import get_avatar_url_with_fallback
from poker.authorization import get_authorization_service, require_permission
from poker.betting_context import BettingContext

# TiltState removed - now using ComposureState from player_psychology
from poker.guest_limits import (
    GUEST_FREE_CHAT_ENABLED,
    GUEST_LIMITS_ENABLED,
    GUEST_MAX_ACTIVE_GAMES,
    GUEST_MAX_HANDS,
    GUEST_MAX_OPPONENTS,
    check_guest_free_chat,
    check_guest_game_limit,
    check_guest_hands_limit,
    check_guest_message_limit,
    is_guest,
    validate_guest_opponent_count,
)
from poker.memory import AIMemoryManager
from poker.memory.chat_intent import map_tone
from poker.memory.opponent_model import OpponentModelManager
from poker.poker_game import advance_to_next_active_player, initialize_game_state, play_turn
from poker.poker_state_machine import PokerPhase, PokerStateMachine
from poker.pressure_detector import PressureEventDetector
from poker.pressure_stats import PressureStatsTracker
from poker.prompt_config import PromptConfig
from poker.utils import get_celebrities

from .. import config, extensions
from ..extensions import limiter, socketio
from ..game_adapter import StateMachineAdapter
from ..handlers.avatar_handler import start_background_avatar_generation
from ..handlers.chat_relationship import dispatch_chat_relationship_event
from ..handlers.game_handler import (
    maybe_engage_fast_forward_on_fold,
    progress_game,
    recover_stuck_runout,
    restore_ai_controllers,
    stamp_coach_default_mode,
    update_and_emit_game_state,
)
from ..handlers.message_handler import (
    format_action_message,
    format_messages_for_api,
    record_action_in_memory,
    send_message,
)
from ..services import game_state_service
from ..services.elasticity_service import format_elasticity_data
from ..socket_rate_limit import socket_rate_limit
from ..state_version import next_state_version
from ..validation import validate_player_action

logger = logging.getLogger(__name__)

game_bp = Blueprint('game', __name__)


def _is_admin(user_id: str) -> bool:
    """Check whether a user has admin tools permission."""
    auth_service = get_authorization_service()
    return bool(auth_service and auth_service.has_permission(user_id, 'can_access_admin_tools'))


def _sync_cached_owner_from_db(game_id: str, current_game_data: dict, owner_info: dict) -> None:
    """Keep cached game owner metadata aligned with persisted ownership."""
    if not current_game_data or not owner_info:
        return

    persistent_owner_id = owner_info.get('owner_id')
    persistent_owner_name = owner_info.get('owner_name')
    if (
        current_game_data.get('owner_id') == persistent_owner_id
        and current_game_data.get('owner_name') == persistent_owner_name
    ):
        return

    current_game_data['owner_id'] = persistent_owner_id
    current_game_data['owner_name'] = persistent_owner_name
    game_state_service.set_game(game_id, current_game_data)


def _authorize_game_access(game_id: str, current_game_data: dict = None):
    """Authorize access to a game using owner-or-admin checks."""
    current_user = extensions.auth_manager.get_current_user() if extensions.auth_manager else None
    if not current_user or not current_user.get('id'):
        return (
            None,
            None,
            None,
            (
                jsonify({'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}),
                401,
            ),
        )

    user_id = current_user['id']
    is_admin = _is_admin(user_id)

    game_exists = bool(current_game_data)
    owner_id = current_game_data.get('owner_id') if current_game_data else None
    owner_info = None

    if owner_id is None:
        owner_info = extensions.game_repo.get_game_owner_info(game_id)
        if owner_info is not None:
            game_exists = True
            owner_id = owner_info.get('owner_id')
            _sync_cached_owner_from_db(game_id, current_game_data, owner_info)

    if not game_exists:
        return current_user, is_admin, owner_id, (jsonify({'error': 'Game not found'}), 404)

    if not is_admin and owner_id != user_id:
        # Cached owner data can be stale after guest->user ownership transfer.
        # Re-check persistence before denying access.
        if owner_info is None:
            owner_info = extensions.game_repo.get_game_owner_info(game_id)
        if owner_info is not None:
            owner_id = owner_info.get('owner_id')
            _sync_cached_owner_from_db(game_id, current_game_data, owner_info)
        if owner_id != user_id:
            return current_user, is_admin, owner_id, (jsonify({'error': 'Permission denied'}), 403)

    return current_user, is_admin, owner_id, None


def _emit_reload_if_persisted(game_id: str) -> None:
    """PRH-12: a socket handler missed the game in memory. If it's actually
    persisted (evicted by TTL/restart, not deleted) and the caller is its
    owner/admin, emit `reload_required` so the client re-fetches state — GET
    /api/game-state cold-loads it — and retries, instead of silently dropping.

    Mirrors `_authorize_game_access`'s DB owner lookup so we never tell a
    non-owner to reload, and never fire for a genuinely gone game. Best-effort:
    any failure just degrades to the prior silent no-op.
    """
    try:
        owner_info = extensions.game_repo.get_game_owner_info(game_id)
        if owner_info is None:
            return  # truly gone — nothing to reload
        user = extensions.auth_manager.get_current_user() if extensions.auth_manager else None
        user_id = user.get('id') if user else None
        if user_id and (user_id == owner_info.get('owner_id') or _is_admin(user_id)):
            emit('reload_required', {'game_id': game_id, 'code': 'RELOAD_REQUIRED'})
    except Exception as e:
        logger.debug("[SOCKET] reload signal skipped for %s: %s", game_id, e)


def _reattach_mtt_session(current_game_data: dict, mtt_session_row, game_id: str) -> None:
    """Re-attach the multi-table tournament session on cold load. The games row
    only persists the single table's state machine; the field / seating / blinds
    live in the TournamentSession (tournaments table). Without this, an evicted or
    restarted MTT table loses game_data['tournament_session'], the hand-boundary
    hook stops advancing the field, and the event silently decays into a lone
    single table. Rehydrate via the registry and re-point it at this game.

    `mtt_session_row` is None for cash / single-table games (the caller only sets
    it for a row whose `resolver_kind != 'single'`), so this is a no-op there.
    """
    if mtt_session_row is None:
        return
    try:
        from flask_app.services import tournament_registry

        _rec = tournament_registry.get(mtt_session_row['tournament_id'])
        _sess = _rec.get('session') if _rec else None
        if _sess is None:
            return
        _ht = _sess.human_table
        # The rehydrated session carries `is_multi_table=True`, which is what the
        # hand-boundary dispatch keys on — so simply restoring the session here is
        # enough to route this cold-loaded game back through the multi-table
        # boundary (the only path that escalates the live table's blinds). No
        # separate game_data flag to re-stamp (and thus none to drop).
        current_game_data['tournament_session'] = _sess
        current_game_data['tournament_id'] = mtt_session_row['tournament_id']
        current_game_data['tournament_human_id'] = _sess.human_id
        current_game_data['tournament_table_id'] = _ht.table_id if _ht is not None else None
        current_game_data['tournament_resolver_kind'] = _rec.get('resolver_kind', 'fake')
        if _rec.get('game_id') != game_id:
            _rec['game_id'] = game_id
    except Exception:
        logger.error(
            "[LOAD] failed to re-attach tournament session for %s",
            game_id,
            exc_info=True,
        )


def load_game_mode_preset(game_mode: str) -> PromptConfig:
    """Load a game mode as a preset from the database.

    Game modes (casual, standard, pro) are stored as system presets
    in the prompt_presets table, unifying them with user-defined presets.

    Legacy 'competitive' mode is auto-mapped to 'pro' with a warning to
    keep older stored games loadable. We normalize before the DB lookup
    so we don't end up warning twice through `PromptConfig.from_mode_name`
    on the fallback path.

    Args:
        game_mode: The game mode name ('casual', 'standard', 'pro')

    Returns:
        PromptConfig with the preset's settings applied
    """
    if game_mode == 'competitive':
        logger.warning("Game mode 'competitive' is deprecated; mapping to 'pro'.")
        game_mode = 'pro'

    preset = extensions.prompt_preset_repo.get_prompt_preset_by_name(game_mode)
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
    memory_manager=None,
    ai_controllers=None,
) -> None:
    """Analyze a player decision (human or AI) and save to database.

    This tracks decision quality for ALL players, not just AI.

    `ai_controllers`: optional dict {player_name -> controller}. When the
    acting player is a sharp/tiered bot whose narration was skipped, the
    controller-side analysis hook never fires, so this function is the only
    chance to capture the intervention trace and pipeline snapshot. Pulled
    here from the controller's `_last_*` accumulators if available.
    """
    try:
        from poker.decision_analyzer import get_analyzer

        # Skip when the acting player's controller already persisted the
        # richer controller-side row for THIS decision from inside its own
        # decision path. Self-saving controllers (chaos/standard/lean/tiered)
        # write the row with the capture_id, psychology snapshot, menu
        # compliance, and — critically — the FRESH, in-call pipeline snapshot
        # that this handler cannot reconstruct. We discriminate on a static
        # capability flag (+ a repo actually being wired) rather than the old
        # in-memory `_last_analyzed_decision` stamp: the stamp was an
        # order-of-operations handshake that, on controller-instance
        # divergence (cold-load), silently fell through here and grafted a
        # STALE snapshot off the controller. RuleBot/casebot does not
        # self-save (WRITES_OWN_DECISION_ANALYSIS = False) and still falls
        # through to the basic row below.
        controller = (ai_controllers or {}).get(player_name)
        if (
            controller is not None
            and getattr(controller, 'WRITES_OWN_DECISION_ANALYSIS', False)
            and getattr(controller, '_decision_analysis_repo', None) is not None
        ):
            return

        player = game_state.current_player
        if player.name != player_name:
            # Find the player who acted (may have moved to next player already)
            player = next((p for p in game_state.players if p.name == player_name), None)
            if not player:
                return

        # Get cards in format equity calculator understands
        from poker.card_utils import card_to_string

        community_cards = (
            [card_to_string(c) for c in game_state.community_cards]
            if game_state.community_cards
            else []
        )
        player_hand = [card_to_string(c) for c in player.hand] if player.hand else []

        # Count opponents still in hand
        opponents_in_hand = [
            p for p in game_state.players if not p.is_folded and p.name != player_name
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
        opponent_model_manager = (
            memory_manager.get_opponent_model_manager() if memory_manager else None
        )

        for opp in opponents_in_hand:
            opp_position = position_by_name.get(opp.name, "button")

            # Get observed stats from opponent model manager
            opp_model_data = None
            if opponent_model_manager:
                opp_model = opponent_model_manager.get_model(player_name, opp.name)
                if opp_model and opp_model.tendencies:
                    opp_model_data = opp_model.tendencies.to_dict()

            opponent_infos.append(
                build_opponent_info(
                    name=opp.name,
                    position=opp_position,
                    opponent_model=opp_model_data,
                )
            )

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

        # NOTE: this path intentionally does NOT attach a pipeline snapshot or
        # intervention trace. Those belong to the bot's decision pipeline and
        # are persisted — fresh, in the same decision call — by the controller's
        # own `_analyze_decision` (see WRITES_OWN_DECISION_ANALYSIS, skipped
        # above). The rows reaching here are humans and RuleBot/casebot, which
        # have no pipeline snapshot. Reading the controller's `_last_*`
        # accumulators here was the source of stale "resolved RAISE next to an
        # actual FOLD" rows when a self-saving bot fell through (cold-load
        # instance divergence): the accumulators held the player's PREVIOUS
        # decision. A snapshot-less row is honest; a grafted one lied.

        # Capture the EXACT solver-chart node (scenario|position|opener|hand) from
        # the pre-action state, so chart-graded coach leaks can grade against the
        # precise spot (exact opener, vs_3bet) instead of backfill reconstruction.
        # PRE_FLOP only; best-effort — old rows / failures fall back to reconstruction.
        phase_name = state_machine.current_phase.name if state_machine.current_phase else None
        if phase_name == 'PRE_FLOP' and analysis.player_hand_canonical:
            try:
                from poker.strategy.preflop_classifier import build_preflop_node

                player_idx = game_state.players.index(player)
                node = build_preflop_node(game_state, player_idx, analysis.player_hand_canonical)
                analysis.preflop_node_key = node.key
            except Exception:
                logger.debug("preflop node capture skipped", exc_info=True)

        decision_id = extensions.decision_analysis_repo.save_decision_analysis(analysis)
        equity_str = f"{analysis.equity:.2f}" if analysis.equity is not None else "N/A"
        logger.debug(
            f"[DECISION_ANALYSIS] {player_name}: {analysis.decision_quality} "
            f"(equity={equity_str}, ev_lost={analysis.ev_lost:.0f})"
        )

        # Auto-label the decision (humans + RuleBot reach here; self-saving bots
        # auto-label from their own decision path). No drama context on this
        # path — those labels are an LLM-prompt artifact — but the fold/pot-odds
        # mistake labels apply to every player type.
        if extensions.capture_label_repo and decision_id:
            big_blind = game_state.current_ante or 100
            label_data = {
                'action_taken': action,
                'pot_odds': (game_state.pot.get('total', 0) / cost_to_call)
                if cost_to_call > 0
                else None,
                'stack_bb': (player.stack / big_blind) if big_blind > 0 else None,
                'already_bet_bb': (player.bet / big_blind) if big_blind > 0 else None,
            }
            extensions.capture_label_repo.compute_and_store_auto_labels(decision_id, label_data)
    except Exception as e:
        logger.warning(f"[DECISION_ANALYSIS] Failed to analyze decision for {player_name}: {e}")


def _evaluate_coach_progression(
    game_id: str, player_name: str, action: str, amount: int, game_data: dict, pre_action_state
) -> Optional[dict]:
    """Post-action hook: evaluate the human player's action against skill targets.

    Uses a broad try/except intentionally: this entire function is a non-critical
    post-action hook. Any failure must not disrupt the game flow. The phases
    (data loading, classification/evaluation, feedback prompt generation) are kept
    in one block to avoid partial state from early failures.

    Returns a serialized "inline feedback" dict for the primary evaluated skill
    ({skill_id, skill_name, verdict, reasoning, confidence}) when there's a
    coachable verdict, else None. Training mode surfaces this in the action
    response; other modes ignore the return value. Never raises.
    """
    feedback: Optional[dict] = None
    try:
        from flask_app.services.coach_engine import compute_coaching_data
        from flask_app.services.coach_progression import (
            CoachProgressionService,
            restore_session_memory,
        )
        from flask_app.services.situation_classifier import SituationClassifier

        user_id = game_data.get('owner_id', '')
        if not user_id:
            logger.debug("[COACH_PROGRESSION] Skipped: no owner_id for game=%s", game_id)
            return

        # Compute coaching data from the pre-action state for accurate evaluation
        coaching_data = compute_coaching_data(
            game_id,
            player_name,
            game_data=game_data,
            game_state_override=pre_action_state,
        )
        if not coaching_data:
            logger.debug(
                "[COACH_PROGRESSION] Skipped: no coaching_data for game=%s player=%s",
                game_id,
                player_name,
            )
            return

        # Inject current action's bet sizing (not available from hand_actions
        # because the current action hasn't been recorded yet)
        if action in ('raise', 'bet', 'all_in') and amount > 0:
            pot_total = coaching_data.get('pot_total', 0)
            ratio = amount / pot_total if pot_total > 0 else 0
            coaching_data = {**coaching_data, 'bet_to_pot_ratio': ratio}

        service = CoachProgressionService(extensions.coach_repo)
        player_state = service.get_or_initialize_player(user_id)

        # Get range targets from player profile
        profile = player_state.get('profile', {})
        range_targets = profile.get('range_targets') if profile else None

        classifier = SituationClassifier()
        unlocked = [g for g, gp in player_state['gate_progress'].items() if gp.unlocked]
        classification = classifier.classify(
            coaching_data, unlocked, player_state['skill_states'], range_targets=range_targets
        )

        if classification.relevant_skills:
            evaluations = service.evaluate_and_update(
                user_id, action, coaching_data, classification, range_targets=range_targets
            )
            if evaluations:
                logger.debug(
                    f"[COACH_PROGRESSION] {player_name}: evaluated {len(evaluations)} skills, "
                    f"primary={classification.primary_skill}"
                )

                # Record evaluations in session memory for hand review.
                # PRH-15: restore persisted history on a memory miss (cold-load /
                # restart) instead of starting blank — otherwise the first
                # post-restart eval would overwrite the saved blob with one row.
                session_memory = restore_session_memory(game_id, game_data, extensions.coach_repo)

                memory_manager = game_data.get('memory_manager')
                if memory_manager and hasattr(memory_manager, 'hand_recorder'):
                    hand_number = getattr(memory_manager.hand_recorder, 'hand_count', 0)
                else:
                    hand_number = 0
                    logger.debug(
                        "[COACH_PROGRESSION] No memory_manager; recording under hand_number=0"
                    )

                for ev in evaluations:
                    session_memory.record_hand_evaluation(hand_number, ev)

                # Build inline feedback for the primary skill (the one the
                # classifier prioritized), falling back to the first eval.
                # Only surface a coachable verdict — skip not_applicable so the
                # training UI doesn't flash a badge on every irrelevant action.
                from flask_app.services.skill_definitions import get_skill_by_id

                primary = classification.primary_skill
                chosen = next((e for e in evaluations if e.skill_id == primary), evaluations[0])
                if chosen.evaluation in ('correct', 'incorrect', 'marginal'):
                    sd = get_skill_by_id(chosen.skill_id)
                    feedback = {
                        'skill_id': chosen.skill_id,
                        'skill_name': sd.name if sd else chosen.skill_id,
                        'verdict': chosen.evaluation,
                        'reasoning': chosen.reasoning,
                        'confidence': chosen.confidence,
                    }

                # PRH-15: persist the per-hand evaluations so the review
                # history survives a restart / TTL-eviction. Best-effort —
                # a persistence hiccup must never break the action path.
                try:
                    extensions.coach_repo.save_session_evaluations(
                        game_id, user_id, session_memory.to_evaluations_json()
                    )
                except Exception as persist_err:
                    logger.debug(
                        "[COACH_PROGRESSION] persist session evals failed for %s: %s",
                        game_id,
                        persist_err,
                    )
    except Exception as e:
        logger.error(
            f"[COACH_PROGRESSION] Failed for game={game_id} player={player_name}: {e}",
            exc_info=True,
        )
    return feedback


def generate_game_id() -> str:
    """Generate a unique, unpredictable game ID."""
    return secrets.token_urlsafe(16)


@game_bp.route('/api/usage-stats')
def get_usage_stats():
    """Get guest usage stats (hands played, limits)."""
    current_user = extensions.auth_manager.get_current_user()
    guest = is_guest(current_user) if current_user else True

    hands_played = 0
    if guest:
        # PRH-26: resolve via the signed-cookie / IP-derived path (same as the
        # quota writer) so a forged cookie can't report a fresh 0-hand bucket.
        tracking_id = (
            extensions.auth_manager.resolve_guest_tracking_id() if extensions.auth_manager else None
        )
        if tracking_id:
            hands_played = extensions.guest_tracking_repo.get_hands_played(tracking_id)

    hands_limit_reached = guest and GUEST_LIMITS_ENABLED and hands_played >= GUEST_MAX_HANDS
    # PRH-27: whether free-text chat is sign-in-gated for this user. Lets the
    # client disable the keyboard input + steer to "sign in to chat" without
    # guessing the server policy.
    free_chat_locked = guest and GUEST_LIMITS_ENABLED and not GUEST_FREE_CHAT_ENABLED

    return jsonify(
        {
            'hands_played': hands_played,
            'hands_limit': GUEST_MAX_HANDS,
            'hands_limit_reached': hands_limit_reached,
            'max_opponents': GUEST_MAX_OPPONENTS if guest else 9,
            'max_active_games': GUEST_MAX_ACTIVE_GAMES if guest else 10,
            'is_guest': guest,
            'free_chat_locked': free_chat_locked,
        }
    )


@game_bp.route('/api/games')
def list_games():
    """List games for the current user."""
    current_user = extensions.auth_manager.get_current_user()

    try:
        limit = int(request.args.get('limit', 20))
        offset = int(request.args.get('offset', 0))
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid pagination parameters'}), 400
    limit = max(0, min(limit, config.GAME_LIST_MAX_LIMIT))
    offset = max(0, offset)

    if current_user:
        saved_games = extensions.game_repo.list_games(
            owner_id=current_user.get('id'), limit=limit, offset=offset
        )
    else:
        saved_games = []

    # Filter out games that own a dedicated resume surface, so they don't
    # also appear as standalone "continue games":
    #   - cash games ("cash-"): session-only, resumed from the cash lobby.
    #   - training games ("train-"): session-only, resumed from the training UI.
    #   - multi-table tournament tables ("tourney-"): the human's live table is
    #     an internal child of a TournamentSession, resumed only through the
    #     tournament lobby/standings (never loaded standalone). Listing it here
    #     would let a player reopen one table detached from its field.
    saved_games = [
        g
        for g in saved_games
        if not (
            g.game_id.startswith("cash-")
            or g.game_id.startswith("tourney-")
            or g.game_id.startswith("train-")
        )
    ]

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

        games_data.append(
            {
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
                'big_blind': big_blind,
            }
        )

    return jsonify({'games': games_data})


@game_bp.route('/api/game-state/<game_id>')
@limiter.limit(config.RATE_LIMIT_POLLING)
def api_game_state(game_id):
    """API endpoint to get current game state for React app."""
    current_game_data = game_state_service.get_game(game_id)
    current_user, _, _, auth_error = _authorize_game_access(game_id, current_game_data)
    if auth_error:
        return auth_error

    # Auto-advance cached games that are stuck in non-action phases
    if current_game_data:
        state_machine = current_game_data['state_machine']
        if not state_machine.game_state.awaiting_action and not current_game_data.get(
            'game_started', False
        ):
            logger.debug(
                f"[CACHE] Auto-advancing cached game {game_id}, phase: {state_machine.current_phase}"
            )
            current_game_data['game_started'] = True
            progress_game(game_id)

    # Cold-load path. Concurrent GETs for the same game_id (tab reloads,
    # React Strict-Mode double effects, socket reconnect storms, two open
    # tabs) can each observe `get_game() → None` and race to load,
    # rebuild controllers, run recovery on a separate state machine, and
    # clobber each other in `set_game`. Acquire the per-game lock and
    # re-check the cache so only one thread does the load. The lock is
    # released before `progress_game` is called because progress_game
    # acquires the same lock with blocking=False.
    _post_load_should_advance = False
    _post_load_advance_reason = ''
    if not current_game_data:
        load_lock = game_state_service.get_game_lock(game_id)
        with load_lock:
            current_game_data = game_state_service.get_game(game_id)
            if current_game_data is None:
                try:
                    owner_info = extensions.game_repo.get_game_owner_info(game_id) or {}
                    owner_id = owner_info.get('owner_id')
                    owner_name = owner_info.get('owner_name')

                    base_state_machine = extensions.game_repo.load_game(game_id)
                    if base_state_machine:
                        state_machine = StateMachineAdapter(base_state_machine)
                        # Load per-player LLM configs for proper provider restoration
                        llm_configs = extensions.game_repo.load_llm_configs(game_id) or {}
                        # Multi-table tournament tables ("tourney-") persist their
                        # per-seat intent (P3.9c): a SYNTHETIC field saves
                        # `ai_chat=False` + all-`sharp` (zero-LLM, no table talk);
                        # a PERSONA field saves `ai_chat=True` + per-seat
                        # `player_llm_configs` (real provider/model) so each
                        # persona seat rebuilds WITH table talk and a valid config.
                        # Honor the persisted flag, but DEFAULT FALSE for tourney-
                        # rows that saved none (legacy zero-LLM games / a synthetic
                        # field) — a True default would rebuild seats with the
                        # expression layer on an empty config and 404 the narration
                        # call per decision (see tiered_factory).
                        _restore_ai_chat = (
                            bool(llm_configs.get('ai_chat', False))
                            if game_id.startswith("tourney-")
                            else llm_configs.get('ai_chat', True)
                        )
                        ai_controllers = restore_ai_controllers(
                            game_id,
                            state_machine,
                            extensions.game_repo,
                            owner_id=owner_id,
                            player_llm_configs=llm_configs.get('player_llm_configs'),
                            default_llm_config=llm_configs.get('default_llm_config'),
                            capture_label_repo=extensions.capture_label_repo,
                            decision_analysis_repo=extensions.decision_analysis_repo,
                            bot_types=llm_configs.get('bot_types'),
                            ai_chat=_restore_ai_chat,
                        )
                        db_messages = extensions.game_repo.load_messages(game_id)

                        # Wire pressure_stats to the DB so past events are loaded and
                        # new events persist — matches the new-game route. Without
                        # game_id + event_repository, restored games silently lose
                        # both: past stats start empty and new events no-op.
                        from poker.repositories.sqlite_repositories import PressureEventRepository

                        event_repository = PressureEventRepository(config.DB_PATH)
                        pressure_detector = PressureEventDetector()
                        pressure_stats = PressureStatsTracker(game_id, event_repository)

                        # Cash games are tagged with a `cash-` game_id
                        # prefix in /api/cash/start. The cold-load path
                        # has to set cash_mode flags by hand — without
                        # them, the cash HUD won't render, _refill_cash_seats
                        # never fires, and /api/cash/{leave,topup,rebuy}
                        # can't locate the session. (The metadata isn't
                        # stored in the saved JSON; we reconstruct it
                        # from the game_id prefix + current_ante.)
                        is_cash_game = game_id.startswith("cash-")
                        # Training games (train- prefix) are a non-counting
                        # sibling like cash. game_data flags aren't persisted,
                        # so mode is re-derived from the prefix here. A training
                        # game must NOT wire a relationship repo (relationship_states
                        # is not cash_mode-gated) and must NOT get a tournament
                        # tracker — otherwise an evicted train- game would leak
                        # relationship rows and rebuild as a tournament. See
                        # docs/plans/TRAINING_MODE.md and training_routes.py.
                        is_training_game = game_id.startswith("train-")
                        # Multi-table tournament tables (tourney- prefix) are a
                        # third non-cash sibling. P3.9a: they wire the dossier
                        # grind exactly like a Circuit cash game (sandbox_id +
                        # persona registration) but with cash_mode=False — so the
                        # same sandbox resolution that cash uses must also fire on
                        # tournament cold-load, or "Resume the Main Event" silently
                        # drops the sandbox and stops folding observations.
                        is_tournament_game = game_id.startswith("tourney-")

                        # CRITICAL cold-load re-coupling guard: a DECOUPLED
                        # (exhibition) tournament must stay isolated across
                        # reload/resume. Both the sandbox derivation just below and
                        # the relationship-repo wiring further down would otherwise
                        # silently RE-COUPLE it (re-deriving a sandbox restores the
                        # dossier fold + relationship writes the fresh build
                        # deliberately suppressed). Read the persisted `decoupled`
                        # flag off the tournament session row (it lives in
                        # session_json, survives cold-load) and skip both wirings.
                        is_decoupled_tournament = False
                        if is_tournament_game and extensions.tournament_session_repo is not None:
                            try:
                                _drow = extensions.tournament_session_repo.find_by_game_id(game_id)
                                if _drow is not None and _drow.get('session_json'):
                                    is_decoupled_tournament = bool(
                                        (json.loads(_drow['session_json']) or {}).get(
                                            'decoupled', False
                                        )
                                    )
                            except Exception:
                                logger.debug(
                                    "[LOAD] decoupled-flag lookup failed for %s",
                                    game_id,
                                    exc_info=True,
                                )

                        # v109: cash_pair_stats writes need a sandbox_id so the
                        # admin Chip Economy panel can scope Won/Lost/Net. For
                        # cold-loaded cash games the owner's default sandbox is
                        # the right answer — owners are single-sandbox in v1,
                        # and the same resolver feeds /api/cash/start. Tournament
                        # games resolve the same sandbox so the dossier fold lands.
                        # A DECOUPLED tournament resolves NO sandbox (stays isolated).
                        cold_load_sandbox_id: Optional[str] = None
                        if (
                            (is_cash_game or is_tournament_game)
                            and not is_decoupled_tournament
                            and owner_id is not None
                        ):
                            try:
                                from flask_app.extensions import sandbox_repo as _sandbox_repo
                                from flask_app.services.sandbox_resolver import (
                                    resolve_default_sandbox_for,
                                )

                                cold_load_sandbox_id = resolve_default_sandbox_for(
                                    owner_id,
                                    sandbox_repo=_sandbox_repo,
                                )
                            except Exception as e:
                                logger.warning(
                                    "[LOAD] sandbox resolve failed for game "
                                    "%s owner %s: %s — cash_pair_stats / dossier "
                                    "fold writes will be skipped this session",
                                    game_id,
                                    owner_id,
                                    e,
                                )

                        memory_manager = AIMemoryManager(
                            game_id, extensions.persistence_db_path, owner_id=owner_id
                        )
                        memory_manager.set_hand_history_repo(
                            extensions.hand_history_repo
                        )  # Enable hand history saving

                        # Restore hand count from database
                        restored_hand_count = extensions.hand_history_repo.get_hand_count(game_id)
                        if restored_hand_count > 0:
                            memory_manager.hand_count = restored_hand_count
                            logger.info(
                                f"[LOAD] Restored hand count: {restored_hand_count} for game {game_id}"
                            )

                        # Restore opponent models from database. This
                        # swaps `memory_manager.opponent_model_manager`
                        # for a fresh instance, so any wiring on the
                        # old OPM (relationship_repo, etc.) must be
                        # reapplied AFTER this — see set_relationship_repo
                        # below.
                        saved_opponent_models = extensions.game_repo.load_opponent_models(game_id)
                        if saved_opponent_models:
                            memory_manager.opponent_model_manager = OpponentModelManager.from_dict(
                                saved_opponent_models
                            )
                            logger.info(f"[LOAD] Restored opponent models for game {game_id}")

                        # Phase 3: relationship state populates from hand
                        # outcomes. Cash sessions write cash_pair_stats;
                        # tournament sessions skip it. Wire AFTER the OPM
                        # restore above so the wiring lands on the OPM
                        # that record_event actually mutates — and so the
                        # detector's name→id reference re-syncs to the
                        # restored OPM's registry.
                        #
                        # Suppress at casino tables — ephemeral tourists
                        # should never accumulate relationship history.
                        # Detect by loading the cash table and checking
                        # table_type.
                        #
                        # Fail-safe direction: if we CAN'T confirm this
                        # is a lobby table, suppress writes. A false
                        # positive (skipping relationship for a real
                        # lobby table on a transient load failure) loses
                        # a few events for one cold-load. A false
                        # negative (writing tourist pids into dossiers)
                        # permanently corrupts relationship state.
                        suppress_for_casino = False
                        if is_cash_game:
                            # cash_table_id isn't in the saved game JSON, and
                            # current_game_data isn't built yet at this point.
                            # The durable cash_sessions row (v108) is the source
                            # of truth — populated at sit-down by
                            # cash_routes._record_cash_session_start. A failed
                            # lookup leaves cash_table_id None, which falls
                            # through to the fail-safe suppression below.
                            cash_table_id = None
                            try:
                                from flask_app.extensions import (
                                    cash_session_repo as _cash_session_repo,
                                )

                                if _cash_session_repo is not None:
                                    _cs = _cash_session_repo.load(game_id)
                                    if _cs is not None:
                                        cash_table_id = _cs.cash_table_id
                            except Exception as e:
                                logger.warning(
                                    "[LOAD] cash_sessions lookup for relationship "
                                    "suppression failed for %s: %s — suppressing "
                                    "relationship writes (fail-safe).",
                                    game_id,
                                    e,
                                )
                                suppress_for_casino = True
                            if cash_table_id:
                                try:
                                    from flask_app.extensions import (
                                        cash_table_repo as _cash_table_repo,
                                    )

                                    if _cash_table_repo is None:
                                        suppress_for_casino = True
                                        logger.warning(
                                            "[LOAD] cash_table_repo unavailable "
                                            "during cold-load of cash game %s; "
                                            "suppressing relationship writes "
                                            "(fail-safe).",
                                            game_id,
                                        )
                                    else:
                                        _ct = _cash_table_repo.load_table(
                                            cash_table_id,
                                            sandbox_id=cold_load_sandbox_id,
                                        )
                                        if _ct is None:
                                            suppress_for_casino = True
                                            logger.warning(
                                                "[LOAD] cash table %s not "
                                                "found for cold-load of game "
                                                "%s; suppressing relationship "
                                                "writes (fail-safe).",
                                                cash_table_id,
                                                game_id,
                                            )
                                        else:
                                            suppress_for_casino = _ct.table_type == 'casino'
                                except Exception as exc:
                                    suppress_for_casino = True
                                    logger.warning(
                                        "[LOAD] cash table load failed for "
                                        "%s (game %s): %s — suppressing "
                                        "relationship writes (fail-safe).",
                                        cash_table_id,
                                        game_id,
                                        exc,
                                    )
                        # Training games never wire a relationship repo:
                        # relationship_states writes for ANY wired repo (not
                        # just cash_mode), so this is the only safe suppression.
                        # A DECOUPLED (exhibition) tournament likewise stays
                        # isolated — relationship_states is not sandbox-gated, so
                        # the only way to keep its real personas from accumulating
                        # dossier state across a resume is to NOT wire the repo at
                        # all (mirrors the fresh-build sandbox-null lever).
                        if (
                            not suppress_for_casino
                            and not is_training_game
                            and not is_decoupled_tournament
                        ):
                            memory_manager.set_relationship_repo(
                                extensions.relationship_repo,
                                cash_mode=is_cash_game,
                                sandbox_id=cold_load_sandbox_id,
                            )

                        for player in state_machine.game_state.players:
                            # Resolve each player's stable personality_id at
                            # startup so the opponent_model_manager carries
                            # ids on every model it creates. AI seats with
                            # personalities in the DB get a real id; humans
                            # and ad-hoc names get None.
                            try:
                                if is_tournament_game:
                                    # Tournament seat Player.name IS the
                                    # personality_id (MTT bridge), not a display
                                    # name — resolve_name_to_personality_id queries
                                    # by name and would return None (Break B). Use
                                    # it directly, gated on it being a real persona
                                    # (side-effect-free lookup) so synthetic P##
                                    # fields don't register junk lifetime rows.
                                    pid = (
                                        player.name
                                        if extensions.personality_repo.display_names_by_ids(
                                            [player.name]
                                        )
                                        else None
                                    )
                                else:
                                    pid = (
                                        extensions.personality_repo.resolve_name_to_personality_id(
                                            player.name
                                        )
                                    )
                            except Exception:
                                pid = None
                            if not player.is_human and player.name in ai_controllers:
                                memory_manager.initialize_for_player(
                                    player.name, personality_id=pid
                                )
                                controller = ai_controllers[player.name]
                                controller.session_memory = memory_manager.get_session_memory(
                                    player.name
                                )
                                controller.opponent_model_manager = (
                                    memory_manager.get_opponent_model_manager()
                                )
                                controller.memory_manager = memory_manager
                            elif player.is_human:
                                # Register with owner_id (the stable auth id) so
                                # per-hand BIG_WIN/BIG_LOSS events land on
                                # (owner_id, ai_pid) rows — the same key the
                                # cash loan flow and the dossier read use.
                                # Falls back to `pid` (almost always None for
                                # humans) when owner_id isn't on this session,
                                # preserving the legacy display-name fallback.
                                memory_manager.initialize_human_observer(
                                    player.name,
                                    personality_id=owner_id or pid,
                                )

                        memory_manager.on_hand_start(
                            state_machine.game_state,
                            hand_number=memory_manager.hand_count + 1,
                            deck_seed=state_machine.current_hand_seed,
                        )

                        # Tournament tracker drives the elimination / placement
                        # flow. Cash games must NOT have one (PRH-4):
                        # handle_eliminations() keys off its mere presence, so a
                        # stray tracker reroutes a cash bust into the tournament
                        # "Nth place" screen instead of the rebuy/sponsor modal.
                        # New cash games (/api/cash/start) omit it; the cold-load
                        # path used to rebuild it for everyone, which is the bug.
                        # Re-attach this game's tournament wrapper on cold-load.
                        # Every non-cash, non-training game is a tournament: look
                        # up its row in the `tournaments` table by game_id and
                        # classify it —
                        #   - resolver_kind != 'single' → MULTI-table session
                        #     (the human's table inside a field).
                        #   - resolver_kind == 'single' with a real session blob
                        #     → single-table session (the unified one-table game).
                        #   - no row, or a pre-3B lightweight envelope → legacy
                        #     tracker-driven single game.
                        # Training games (train-) are single-table practice, not
                        # tournaments — they skip this path entirely.
                        mtt_session_row = None
                        single_session = None
                        _session_row = None
                        if (
                            not is_cash_game
                            and not is_training_game
                            and extensions.tournament_session_repo is not None
                        ):
                            try:
                                _session_row = extensions.tournament_session_repo.find_by_game_id(
                                    game_id
                                )
                            except Exception:
                                logger.debug(
                                    "[LOAD] tournament lookup failed for %s",
                                    game_id,
                                    exc_info=True,
                                )
                        if _session_row is not None:
                            if _session_row.get('resolver_kind') != 'single':
                                mtt_session_row = _session_row
                            else:
                                try:
                                    _sj = json.loads(_session_row.get('session_json') or '{}')
                                    if 'field' in _sj:  # a real session, not a bare envelope
                                        from tournament.director import FakeHandResolver
                                        from tournament.session import TournamentSession

                                        single_session = TournamentSession.from_dict(
                                            _sj, FakeHandResolver()
                                        )
                                        # `resolver_kind == 'single'` is the
                                        # authoritative persisted signal — force
                                        # it on the rehydrated session so a legacy
                                        # blob (predating the serialized
                                        # `single_table` key, which defaults to
                                        # multi) isn't misrouted to the MTT
                                        # boundary.
                                        single_session.single_table = True
                                except Exception:
                                    logger.error(
                                        "[LOAD] single session rehydrate failed for %s; "
                                        "falling back to tracker",
                                        game_id,
                                        exc_info=True,
                                    )
                                    single_session = None

                        if (
                            not is_cash_game
                            and not is_training_game
                            and mtt_session_row is None
                            and single_session is None
                        ):
                            # Legacy single game (pre-3B): no session row yet.
                            # Seed a fresh single-table session from the live
                            # table so the game becomes session-backed.
                            # TournamentTracker is fully retired (v124) — any
                            # legacy tracker blob is NOT migrated; stacks may have
                            # diverged mid-hand, the next boundary resyncs the field.
                            from flask_app.handlers.single_table_tournament import (
                                build_session_for_new_game,
                            )

                            players = state_machine.game_state.players
                            total = sum(p.stack for p in players)
                            n = len(players)
                            starting_stack = (
                                total // n
                                if n and total % n == 0
                                else (players[0].stack if players else 0)
                            )
                            single_session = build_session_for_new_game(
                                players, starting_stack=starting_stack, seed=0
                            )
                            try:
                                from flask_app.services import tournament_registry

                                tournament_registry.persist_single_session(
                                    game_id=game_id, owner_id=owner_id, session=single_session
                                )
                            except Exception:
                                logger.debug(
                                    "[LOAD] single-session persist failed for %s",
                                    game_id,
                                    exc_info=True,
                                )

                        # Seed hand_start_stacks / short_stack_players from current
                        # stacks. On mid-hand restore we don't know the real hand-start
                        # baseline, so we use the current snapshot — pressure deltas
                        # against this resolve to 0 for the in-progress hand (no false
                        # double_up/crippled fires) and the next on_hand_start will
                        # overwrite both with fresh values.
                        big_blind = state_machine.game_state.current_ante or 100
                        hand_start_stacks = {
                            p.name: p.stack for p in state_machine.game_state.players
                        }
                        short_stack_players = {
                            p.name
                            for p in state_machine.game_state.players
                            if 0 < p.stack < 10 * big_blind
                        }

                        current_game_data = {
                            'state_machine': state_machine,
                            'ai_controllers': ai_controllers,
                            'pressure_detector': pressure_detector,
                            'pressure_stats': pressure_stats,
                            'memory_manager': memory_manager,
                            'owner_id': owner_id,
                            'owner_name': owner_name,
                            'messages': db_messages,
                            'last_announced_phase': None,  # Reset on game load
                            'game_started': True,
                            'guest_tracking_id': current_user.get('tracking_id')
                            if current_user
                            else None,
                            'hand_start_stacks': hand_start_stacks,
                            'short_stack_players': short_stack_players,
                        }
                        # Single-table session game: attach the rehydrated /
                        # migrated session (no multi_table flag → the
                        # single-table boundary drives eliminations/completion).
                        # Cash games get neither (handled below).
                        if single_session is not None:
                            current_game_data['tournament_session'] = single_session

                        # Re-derive the training flag from the prefix (not
                        # persisted in game_data) so the non-counting,
                        # auto-coach session survives eviction. Coach mode is
                        # persisted on the games row, so it reloads on its own.
                        if is_training_game:
                            current_game_data['training_mode'] = True

                        # Cash-mode metadata. STAKES_LADDER is the
                        # source of truth for stake_label ↔ big_blind;
                        # cash_personality_ids feeds _refill_cash_seats
                        # so busted AI seats can be reseated with the
                        # right archetype after a hand.
                        if is_cash_game:
                            from flask_app.routes.cash_routes import STAKES_LADDER

                            stake_label = next(
                                (
                                    label
                                    for label, cfg in STAKES_LADDER.items()
                                    if cfg["big_blind"] == big_blind
                                ),
                                None,
                            )
                            cash_personality_ids: Dict[str, str] = {}
                            for player in state_machine.game_state.players:
                                if player.is_human:
                                    continue
                                try:
                                    pid = (
                                        extensions.personality_repo.resolve_name_to_personality_id(
                                            player.name
                                        )
                                    )
                                except Exception:
                                    pid = None
                                if pid:
                                    cash_personality_ids[player.name] = pid
                            current_game_data['cash_mode'] = True
                            current_game_data['cash_stake_label'] = stake_label
                            current_game_data['cash_personality_ids'] = cash_personality_ids
                            # STACK_DOMINANCE depends on the table cap.
                            # Wire it now that stake_label is resolved —
                            # set_relationship_repo above ran before we
                            # knew the cap. Skip silently for legacy
                            # rows whose big_blind doesn't map to any
                            # current STAKES_LADDER tier.
                            if stake_label is not None:
                                from cash_mode.stakes_ladder import (
                                    table_buy_in_window,
                                )

                                _, _, cold_load_max_buy_in = table_buy_in_window(stake_label)
                                memory_manager.set_table_max_buy_in(cold_load_max_buy_in)
                            # Restore the four buy-in / start-time / seat
                            # fields the cold-load path used to leave at
                            # None. Without this, a leave after a Flask
                            # restart got buy_in=0, duration=0, and
                            # cash_tables seat never freed (ghost seat).
                            # The durable cash_sessions row (v108) is
                            # the source of truth — populated at sit-
                            # down by cash_routes._record_cash_session_start.
                            try:
                                from flask_app.extensions import cash_session_repo

                                if cash_session_repo is not None:
                                    cs = cash_session_repo.load(game_id)
                                    if cs is not None:
                                        current_game_data['cash_buy_in'] = cs.total_buy_in
                                        current_game_data['cash_started_at'] = (
                                            cs.started_at.isoformat() if cs.started_at else None
                                        )
                                        current_game_data['cash_table_id'] = cs.cash_table_id
                                        current_game_data['cash_seat_index'] = cs.cash_seat_index
                                        # Resolve the friendly room name for the
                                        # header chip / arrival toast. One-time
                                        # lookup on cold-load (not the hot
                                        # game-state path). Best-effort: any
                                        # miss leaves the chip off.
                                        try:
                                            from flask_app.extensions import (
                                                cash_table_repo,
                                            )

                                            if (
                                                cash_table_repo is not None
                                                and cs.cash_table_id is not None
                                            ):
                                                _ct = cash_table_repo.load_table(
                                                    cs.cash_table_id,
                                                    sandbox_id=cold_load_sandbox_id,
                                                )
                                                if _ct is not None:
                                                    current_game_data['cash_table_name'] = _ct.name
                                        except Exception:
                                            pass
                            except Exception as e:
                                logger.warning(
                                    "[LOAD] cash_sessions cold-load restore failed for %r: %s",
                                    game_id,
                                    e,
                                )
                        # Recover from games persisted mid-all-in-runout (server
                        # crash while run_it_out=True). Without this, the player
                        # sees a stuck state with no action buttons (the UI
                        # clears options whenever run_it_out is set). Fast-
                        # forwards through the run-out to the next stable point
                        # — usually the showdown completes and a new hand
                        # begins. Re-saves so the recovered state is durable.
                        if recover_stuck_runout(state_machine):
                            extensions.game_repo.save_game(
                                game_id, state_machine._state_machine, owner_id, owner_name
                            )

                        # Re-attach the multi-table tournament session on cold
                        # load (no-op for cash / single-table games).
                        _reattach_mtt_session(current_game_data, mtt_session_row, game_id)

                        game_state_service.set_game(game_id, current_game_data)

                        game_state = state_machine.game_state
                        current_player = game_state.current_player
                        logger.debug(
                            f"[LOAD] Game {game_id} loaded. Phase: {state_machine.current_phase}, "
                            f"awaiting_action: {game_state.awaiting_action}, "
                            f"current_player: {current_player.name} (human: {current_player.is_human})"
                        )

                        # Defer the progress_game calls until after the
                        # load lock is released — progress_game acquires
                        # the same lock with blocking=False and would
                        # otherwise silently no-op.
                        if not game_state.awaiting_action:
                            _post_load_should_advance = True
                            _post_load_advance_reason = "not awaiting action"
                        elif game_state.awaiting_action and not current_player.is_human:
                            _post_load_should_advance = True
                            _post_load_advance_reason = f"AI turn: {current_player.name}"
                    else:
                        return jsonify({'error': 'Game not found'}), 404
                except Exception as e:
                    logger.error(f"[LOAD] Error loading game {game_id}: {str(e)}", exc_info=True)
                    # Tier 3 (3.4): stamp the failure on the cash session so a
                    # wedged session is debuggable without log archaeology.
                    # Best-effort; never let telemetry mask the original 500.
                    if game_id.startswith("cash-"):
                        try:
                            from datetime import datetime as _dt

                            from flask_app.extensions import (
                                cash_session_repo as _csr,
                            )

                            if _csr is not None:
                                _csr.set_last_load_error(
                                    game_id,
                                    f"{type(e).__name__}: {e} @ {_dt.utcnow().isoformat()}",
                                )
                        except Exception:
                            logger.debug("[LOAD] last_load_error stamp failed for %s", game_id)
                    return jsonify(
                        {
                            'error': 'Failed to load game from database',
                            'message': 'An error occurred while loading the game. Please try again or start a new game.',
                            'players': [],
                        }
                    ), 500

    if _post_load_should_advance:
        logger.debug(f"[LOAD] Auto-advancing game {game_id} ({_post_load_advance_reason})")
        progress_game(game_id)

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
            # Live emotion lives on controller.psychology (the standalone
            # `emotional_state` attr was retired); fall back to 'confident'.
            psych = getattr(controller, 'psychology', None)
            avatar_emotion = psych.get_display_emotion() if psych is not None else 'confident'
            avatar_url = get_avatar_url_with_fallback(game_id, player.name, avatar_emotion)

        players.append(
            {
                'name': player.name,
                'stack': player.stack,
                'bet': player.bet,
                'is_folded': player.is_folded,
                'is_all_in': player.is_all_in,
                'is_human': player.is_human,
                'hand': hand,
                'avatar_url': avatar_url,
                'avatar_emotion': avatar_emotion,
            }
        )

    community_cards = [
        card.to_dict() if hasattr(card, 'to_dict') else card for card in game_state.community_cards
    ]
    messages = format_messages_for_api(current_game_data.get('messages', []))

    # Build betting context for current player
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
        'player_options': list(game_state.current_player_options)
        if game_state.current_player_options
        else [],
        'min_raise': game_state.min_raise_amount,
        'big_blind': game_state.current_ante,
        'messages': messages,
        'game_id': game_id,
        'betting_context': betting_context,
        # Authoritative cold-load snapshot: the client treats this as a baseline
        # RESET for its monotonic frame guard (it accepts this version even after
        # a server restart reset the counter), so stale socket frames older than
        # it are dropped. See flask_app.state_version.
        'state_version': next_state_version(),
    }

    # Cash-mode metadata. Included on the cold-load path too so the
    # bankroll pill renders immediately — otherwise the React UI
    # would wait for the first socket `update_game_state` frame
    # before knowing this is a cash session, leaving the pill hidden
    # on initial paint.
    from flask_app.handlers.game_handler import build_cash_mode_payload

    cash_meta = build_cash_mode_payload(current_game_data, game_state)
    if cash_meta is not None:
        response['cash_mode'] = cash_meta

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
    return extensions.llm_repo.get_model_cost_tiers()


@game_bp.route('/api/user-models', methods=['GET'])
def api_user_models():
    """Get LLM providers and models available for user-facing game configuration.

    Returns models where BOTH enabled=1 AND user_enabled=1.
    Use /api/system-models for admin tools that need system-only models.
    """
    from core.llm import (
        AVAILABLE_PROVIDERS,
        PROVIDER_CAPABILITIES,
        PROVIDER_DEFAULT_MODELS,
        PROVIDER_MODELS,
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

        providers.append(
            {
                'id': provider,
                'name': provider.title(),
                'models': models,
                'default_model': default_model,
                'capabilities': PROVIDER_CAPABILITIES.get(provider, {}),
                'model_capabilities': provider_model_caps,
                'model_tiers': model_tiers.get(provider, {}),
            }
        )

    return jsonify(
        {
            'providers': providers,
            'default_provider': 'openai',
        }
    )


@game_bp.route('/api/system-models', methods=['GET'])
@require_permission('can_access_admin_tools')
def api_system_models():
    """Get LLM providers and models available for system/admin features.

    Admin-only (SBP-002): returns system-only models + cost tiers meant for the
    admin tooling below; only the admin-surface hooks (scope='system') call it,
    while user-facing game setup uses the public `/api/user-models`.

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
        PROVIDER_CAPABILITIES,
        PROVIDER_DEFAULT_MODELS,
        PROVIDER_MODELS,
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

        providers.append(
            {
                'id': provider,
                'name': provider.title(),
                'models': models,
                'default_model': default_model,
                'capabilities': PROVIDER_CAPABILITIES.get(provider, {}),
                'model_capabilities': provider_model_caps,
                'model_tiers': model_tiers.get(provider, {}),
            }
        )

    return jsonify(
        {
            'providers': providers,
            'default_provider': 'openai',
        }
    )


def _get_enabled_models_map():
    """Get a map of (provider, model) -> enabled status for user-facing features.

    For game setup and user-facing features, models must have BOTH:
    - enabled = 1 (system enabled)
    - user_enabled = 1 (user enabled)

    Returns empty dict if enabled_models table doesn't exist yet.
    """
    return extensions.llm_repo.get_enabled_models_map()


def _get_system_enabled_models_map():
    """Get a map of (provider, model) -> enabled status for system/admin features.

    For admin tools (experiments, playground, decision analyzer), models only need:
    - enabled = 1 (system enabled)

    This includes "System-only" models (enabled=1, user_enabled=0) that admins
    can use but regular users cannot see in game setup.

    Returns empty dict if enabled_models table doesn't exist yet.
    """
    return extensions.llm_repo.get_system_enabled_models_map()


def _get_model_capabilities_map():
    """Get a map of (provider, model) -> capability flags.

    Returns model-level capabilities (supports_img2img, etc.) from enabled_models table.
    This supplements provider-level capabilities with model-specific flags.

    Returns:
        Dict mapping (provider, model) to dict of capability flags
    """
    return extensions.llm_repo.get_model_capabilities_map()


def _guest_safe_bot_types(bot_types: dict, enforce_guest: bool) -> dict:
    """PRH-26: gate the paid LLM bots behind real auth.

    `chaos`/`standard`/`lean` run an LLM call per decision; a guest must not be
    able to opt into them. When `enforce_guest` is true (a guest with limits
    enabled), every opponent is forced to the LLM-free `sharp` tiered bot
    regardless of what was requested. Signed-in users keep full selection.
    """
    if enforce_guest:
        return {name: 'sharp' for name in bot_types}
    return bot_types


# Server-side cap on player chat (the client input caps lower; this is the
# anti-bloat backstop — every message rides along in subsequent AI prompts).
MAX_PLAYER_CHAT_LEN = 500


def _player_chat_rejection(content: str) -> Optional[dict]:
    """PRH-27: screen authed player chat before it reaches the AI prompt.

    Returns an error payload ({'error', 'code'}) to reject, or None to allow.
    Length-cap first (cheap), then moderation (free OpenAI omni-moderation;
    fail-open on outage — see core.moderation).
    """
    if len(content) > MAX_PLAYER_CHAT_LEN:
        return {
            'error': f'Message too long (max {MAX_PLAYER_CHAT_LEN} characters).',
            'code': 'CHAT_TOO_LONG',
        }
    if moderate_text(content).flagged:
        return {
            'error': 'That message was flagged by our content filter. Please rephrase.',
            'code': 'MODERATION_REJECTED',
        }
    return None


def _human_seat_name(game_data) -> Optional[str]:
    """Return the human player's seat name in this game (PRH-33).

    `sender` is otherwise client-supplied and trusted — a spoofed value enters
    the AI prompt as if another player said it. Callers force `sender` to this
    so the chat line is always attributed to the actual human seat. Returns None
    if it can't be determined (caller keeps its fallback).
    """
    try:
        state_machine = game_data.get('state_machine')
        for player in state_machine.game_state.players:
            if getattr(player, 'is_human', False):
                return player.name
    except Exception:
        return None
    return None


@game_bp.route('/api/new-game', methods=['POST'])
@limiter.limit(config.RATE_LIMIT_NEW_GAME)
def api_new_game():
    """Create a new game and return the game ID."""
    data = request.json or {}

    current_user = extensions.auth_manager.get_current_user() if extensions.auth_manager else None
    if not current_user or not current_user.get('id'):
        return jsonify({'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}), 401

    player_name = data.get('playerName', current_user.get('name', 'Player'))
    owner_id = current_user.get('id')
    owner_name = current_user.get('name')

    game_count = extensions.user_repo.count_user_games(owner_id)

    # Use guest-specific limits if applicable
    if is_guest(current_user):
        allowed, error_msg = check_guest_game_limit(current_user, game_count)
        if not allowed:
            return jsonify({'error': error_msg, 'code': 'GUEST_LIMIT_GAMES'}), 403
    else:
        max_games = 10
        if game_count >= max_games:
            return jsonify(
                {'error': f'Game limit reached. You can have up to {max_games} saved games.'}
            ), 400

    # Prevent duplicate game creation from rapid clicks
    last_created = extensions.user_repo.get_last_game_creation_time(owner_id)
    if last_created is not None and (time.time() - last_created) < 3:
        return jsonify({'error': 'Please wait a moment before creating another game.'}), 429

    requested_personalities = data.get('personalities', [])
    default_llm_config = data.get('llm_config', {})
    # Quick Play / themed games omit llm_config — fall back to the configured
    # system default (groq llama for the tiered bot's narration) instead of the
    # hardcoded 'openai' the controllers would otherwise default to.
    if not default_llm_config:
        from core.llm.settings import get_default_model, get_default_provider

        default_llm_config = {
            'provider': get_default_provider(),
            'model': get_default_model(),
        }
    starting_stack = data.get('starting_stack', 5000)
    big_blind = data.get('big_blind', 100)
    blind_growth = data.get('blind_growth', 1.5)
    blinds_increase = data.get('blinds_increase', 6)
    max_blind = data.get('max_blind', 1000)  # 0 = no limit
    # AI table talk. When off, the tiered ("Solver") bot makes ZERO LLM calls
    # (no narration) so play is instant — surfaced to the UI as
    # game_state['ai_instant'] to hide the now-pointless fast-forward button.
    ai_chat = bool(data.get('ai_chat', True))

    # Validate game mode (if provided)
    game_mode = data.get('game_mode', 'casual').lower()
    # 'competitive' is auto-mapped to 'pro' downstream (kept here for backward compat).
    VALID_GAME_MODES = {'casual', 'standard', 'pro', 'competitive'}
    if game_mode not in VALID_GAME_MODES:
        return jsonify(
            {'error': f'Invalid game_mode: {game_mode}', 'valid_modes': list(VALID_GAME_MODES)}
        ), 400

    # Validate default LLM config if provided
    if default_llm_config:
        default_provider = default_llm_config.get('provider', 'openai').lower()
        if default_provider not in AVAILABLE_PROVIDERS:
            return jsonify({'error': f'Invalid default provider: {default_provider}'}), 400
        default_model = default_llm_config.get('model')
        if default_model and default_model not in PROVIDER_MODELS.get(default_provider, []):
            return jsonify(
                {'error': f'Invalid default model {default_model} for provider {default_provider}'}
            ), 400

    # Note: UI warns if starting stack < 10x big blind, but we allow it

    # Per-player controller selection (defaults to 'sharp' = the tiered solver
    # bot, the core engine; LLM-driven bots are opt-in via Custom Game).
    # Legacy values 'hybrid' / 'tiered' are accepted on input but auto-mapped to
    # 'standard' / 'sharp' before storage. They are NOT advertised in error
    # responses so new clients don't pick them up as legitimate choices.
    VALID_BOT_TYPES = {
        'chaos',
        'standard',
        'lean',
        'sharp',
        'casebot',
        'regplus',
        'gto_lite',
        'baseline_solver',
    }
    _BOT_TYPE_ALIASES = {'hybrid': 'standard', 'tiered': 'sharp'}
    _ACCEPTED_BOT_TYPES = VALID_BOT_TYPES | set(_BOT_TYPE_ALIASES)
    bot_types = data.get('bot_types', {}) or {}
    if not isinstance(bot_types, dict):
        return jsonify(
            {'error': 'bot_types must be an object mapping player name to bot type'}
        ), 400
    for _name, _bt in bot_types.items():
        if not isinstance(_name, str) or not isinstance(_bt, str) or _bt not in _ACCEPTED_BOT_TYPES:
            return jsonify(
                {
                    'error': f'Invalid bot_type for {_name!r}: {_bt!r}',
                    'valid_bot_types': sorted(VALID_BOT_TYPES),
                }
            ), 400

    # Normalize legacy aliases (hybrid → standard, tiered → sharp).
    # Done after validation so callers can still send legacy values during the transition.
    bot_types = {n: _BOT_TYPE_ALIASES.get(bt, bt) for n, bt in bot_types.items()}

    # PRH-26: guests can't opt into the paid LLM bots — force them to 'sharp'.
    # (Dev mode leaves selection open for testing via GUEST_LIMITS_ENABLED.)
    bot_types = _guest_safe_bot_types(
        bot_types, bool(current_user and is_guest(current_user) and GUEST_LIMITS_ENABLED)
    )

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
                            return jsonify(
                                {'error': f'Invalid model {model} for provider {provider}'}
                            ), 400
                        # Merge with default config (per-player overrides default)
                        player_llm_configs[name] = {**default_llm_config, **p_llm_config}
                    # Handle per-player game_mode override
                    if 'game_mode' in p:
                        p_mode = p['game_mode'].lower()
                        if p_mode not in VALID_GAME_MODES:
                            return jsonify(
                                {
                                    'error': f'Invalid game_mode for {name}: {p_mode}',
                                    'valid_modes': list(VALID_GAME_MODES),
                                }
                            ), 400
                        player_prompt_configs[name] = load_game_mode_preset(p_mode)
    else:
        opponent_count = max(1, min(9, data.get('opponent_count', 3)))
        ai_player_names = get_celebrities(shuffled=True)[:opponent_count]

    # Check for duplicate names (e.g., AI personality matching human player name)
    if player_name.lower() in [n.lower() for n in ai_player_names]:
        return jsonify(
            {
                'error': f'An opponent has the same name as you ("{player_name}"). Please choose a different player name or remove that opponent.',
                'code': 'DUPLICATE_PLAYER_NAME',
            }
        ), 400

    # Enforce guest opponent limit
    if current_user and is_guest(current_user):
        allowed, error_msg = validate_guest_opponent_count(current_user, len(ai_player_names))
        if not allowed:
            return jsonify({'error': error_msg, 'code': 'GUEST_LIMIT_OPPONENTS'}), 403

    game_state = initialize_game_state(
        player_names=ai_player_names,
        human_name=player_name,
        starting_stack=starting_stack,
        big_blind=big_blind,
    )

    # Blind escalation config
    blind_config = {
        'growth': blind_growth,
        'hands_per_level': blinds_increase,
        'max_blind': max_blind,
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
            if not ai_chat:
                # Quiet the LLM-driven bots (Guided/Improv) when chat is off.
                player_prompt_config = player_prompt_config.copy(
                    chattiness=False, dramatic_sequence=False
                )
            bot_type = bot_types.get(player.name, 'sharp')

            from flask_app.handlers.tiered_factory import build_controller

            new_controller = build_controller(
                bot_type=bot_type,
                player_name=player.name,
                state_machine=state_machine,
                llm_config=player_config,
                prompt_config=player_prompt_config,
                game_id=game_id,
                owner_id=owner_id,
                capture_label_repo=extensions.capture_label_repo,
                decision_analysis_repo=extensions.decision_analysis_repo,
                expression_enabled=ai_chat,
            )
            ai_controllers[player.name] = new_controller

    from poker.repositories.sqlite_repositories import PressureEventRepository

    event_repository = PressureEventRepository(config.DB_PATH)
    pressure_detector = PressureEventDetector()
    pressure_stats = PressureStatsTracker(game_id, event_repository)

    memory_manager = AIMemoryManager(game_id, extensions.persistence_db_path, owner_id=owner_id)
    memory_manager.set_hand_history_repo(extensions.hand_history_repo)  # Enable hand history saving
    # Phase 3: relationship state populates from hand outcomes.
    # Tournament mode (the only mode today) → cash_mode=False;
    # cash_pair_stats stays empty.
    memory_manager.set_relationship_repo(
        extensions.relationship_repo,
        cash_mode=False,
    )
    for player in state_machine.game_state.players:
        # Resolve each player's stable personality_id (None for humans)
        try:
            pid = extensions.personality_repo.resolve_name_to_personality_id(player.name)
        except Exception:
            pid = None
        if not player.is_human:
            memory_manager.initialize_for_player(player.name, personality_id=pid)
            controller = ai_controllers[player.name]
            controller.session_memory = memory_manager.get_session_memory(player.name)
            controller.opponent_model_manager = memory_manager.get_opponent_model_manager()
            controller.memory_manager = memory_manager
        else:
            # Register with owner_id (the stable auth id) so per-hand
            # BIG_WIN/BIG_LOSS events write to (owner_id, ai_pid) rows
            # — the same key the dossier read uses. Falls back to `pid`
            # (almost always None for humans) when owner_id isn't set
            # on this session, preserving legacy display-name behavior.
            memory_manager.initialize_human_observer(
                player.name,
                personality_id=owner_id or pid,
            )

    # Advance state machine to deal cards and post blinds before recording hand start,
    # so that hole cards are available when on_hand_start records them.
    state_machine.run_until_player_action()

    memory_manager.on_hand_start(
        state_machine.game_state, hand_number=1, deck_seed=state_machine.current_hand_seed
    )

    # A single-table game is a one-table tournament: build a TournamentSession
    # from the real players. It replaces the legacy TournamentTracker as the
    # elimination/completion authority (one wrapper type for single + multi),
    # feeding the unified completion path. The live state machine still owns play
    # and blinds — the session is a passive field/standings observer here.
    import zlib

    from flask_app.handlers.single_table_tournament import build_session_for_new_game

    tournament_session = build_session_for_new_game(
        state_machine.game_state.players,
        starting_stack=starting_stack,
        seed=zlib.crc32(game_id.encode()),
    )

    game_data = {
        'state_machine': state_machine,
        'ai_controllers': ai_controllers,
        'pressure_detector': pressure_detector,
        'pressure_stats': pressure_stats,
        'memory_manager': memory_manager,
        'tournament_session': tournament_session,
        'owner_id': owner_id,
        'owner_name': owner_name,
        'llm_config': default_llm_config,  # Default config for new players
        'player_llm_configs': player_llm_configs,  # Per-player LLM overrides
        'player_prompt_configs': player_prompt_configs,  # Per-player prompt config overrides
        'default_game_mode': game_mode,  # Game-level mode setting
        'ai_chat': ai_chat,  # Game-level AI table talk toggle (drives ai_instant)
        'last_announced_phase': None,  # Track which phase we've announced cards for
        'guest_tracking_id': current_user.get('tracking_id') if current_user else None,
        'guest_messages_this_action': 0,  # Chat rate limiting for guests
        'messages': [
            {
                'id': '1',
                'sender': 'Table',
                'content': '***   GAME START   ***',
                'timestamp': datetime.now().isoformat(),
                'type': 'table',
            }
        ],
        # Stack tracking for pressure events (double_up, crippled, short_stack)
        'hand_start_stacks': {p.name: p.stack for p in state_machine.game_state.players},
        'short_stack_players': set(),  # No one is short at game start
    }
    game_state_service.set_game(game_id, game_data)

    # Stamp the resolved bot type for every AI player so the saved game is
    # self-describing on restore. The front-end omits bot_types from the
    # request when all opponents are on the default ('sharp'); without
    # this, the dict on disk is empty and restoration can't tell that the
    # tiered path was wanted.
    saved_bot_types = dict(bot_types)
    for player in state_machine.game_state.players:
        if not player.is_human:
            saved_bot_types.setdefault(player.name, 'sharp')

    extensions.game_repo.save_game(
        game_id,
        state_machine._state_machine,
        owner_id,
        owner_name,
        llm_configs={
            'player_llm_configs': player_llm_configs,
            'default_llm_config': default_llm_config,
            'bot_types': saved_bot_types,
            'ai_chat': ai_chat,
        },
    )
    extensions.game_repo.save_opponent_models(game_id, memory_manager.get_opponent_model_manager())
    # Persist the one-table TournamentSession to the durable `tournaments` table
    # (resolver_kind='single'), so all games — single and multi — share one
    # tournament identity and one rehydration path on cold-load. Best-effort;
    # never block game creation.
    try:
        from flask_app.services import tournament_registry

        tournament_registry.persist_single_session(
            game_id=game_id, owner_id=owner_id, session=tournament_session
        )
    except Exception:
        logger.warning("failed to persist single-table tournament session for %s", game_id)
    # New games adopt the owner's default coaching mode (sticky cross-device pref).
    stamp_coach_default_mode(game_id, owner_id)
    if config.ENABLE_AVATAR_GENERATION:
        start_background_avatar_generation(game_id, ai_player_names, owner_id=owner_id)

    # Record game creation timestamp to prevent rapid duplicate creation
    if owner_id:
        extensions.user_repo.update_last_game_creation_time(owner_id, time.time())

    return jsonify({'game_id': game_id})


@game_bp.route('/api/game/<game_id>/action', methods=['POST'])
@limiter.limit(config.RATE_LIMIT_GAME_ACTION)
def api_player_action(game_id):
    """Handle player action via API. Same shape for cash + tournament:
    cash games skip the tournament_tracker work via the no-tracker
    branches inside handle_eliminations / check_tournament_complete
    (they early-return when 'tournament_tracker' isn't in game_data).
    """
    data = request.json or {}
    action = data.get('action')
    amount = data.get('amount', 0)

    current_game_data = game_state_service.get_game(game_id)
    current_user, _, _, auth_error = _authorize_game_access(game_id, current_game_data)
    if auth_error:
        return auth_error

    if not current_game_data:
        # PRH-12: auth passed via the DB owner lookup, so the game is
        # persisted but evicted from memory (TTL/restart) — not deleted.
        # Signal the client to re-fetch state (GET /api/game-state cold-loads
        # it) and retry, instead of a bare 404 that looks like deletion. This
        # makes the action path self-healing rather than GET-order-dependent.
        return jsonify(
            {
                'error': 'Game state not loaded; re-fetch state and retry',
                'code': 'RELOAD_REQUIRED',
            }
        ), 409

    state_machine = current_game_data['state_machine']

    is_valid, error_message = validate_player_action(state_machine.game_state, action, amount)
    if not is_valid:
        return jsonify({'error': error_message}), 400

    if current_user and is_guest(current_user) and GUEST_LIMITS_ENABLED:
        tracking_id = current_game_data.get('guest_tracking_id')
        if tracking_id:
            hands_played = extensions.guest_tracking_repo.get_hands_played(tracking_id)
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

        # Coach progression: evaluate human player actions against skill targets.
        # In training mode the verdict is surfaced inline (see the response below).
        skill_feedback = None
        if current_player.is_human:
            skill_feedback = _evaluate_coach_progression(
                game_id, current_player.name, action, amount, current_game_data, pre_action_state
            )

        # Normalize the recorded amount for calls: callers pass amount=0 since
        # they're not raising. Downstream consumers expect the true call cost.
        record_amount = amount
        if action == 'call':
            record_amount = max(
                0, min(pre_action_state.highest_bet - current_player.bet, current_player.stack)
            )
        record_action_in_memory(
            current_game_data, current_player.name, action, record_amount, game_state, state_machine
        )

        table_message_content = format_action_message(
            current_player.name, action, amount, highest_bet
        )
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
        extensions.game_repo.save_game(game_id, state_machine._state_machine, owner_id, owner_name)
        if 'memory_manager' in current_game_data:
            _mm = current_game_data['memory_manager']
            extensions.game_repo.save_opponent_models(game_id, _mm.get_opponent_model_manager())
            # Circuit scouting memory: fold this game's observation counts
            # into the durable per-sandbox lifetime rows. No-op for
            # non-sandbox games (sandbox_id is None). Isolated + guarded so a
            # fold hiccup can never break the hand flow.
            try:
                extensions.game_repo.fold_observations_into_lifetime(game_id, _mm.sandbox_id)
            except Exception as _fold_exc:  # pragma: no cover - defensive
                logger.warning(
                    "[DOSSIER] observation lifetime fold failed for game %s: %s",
                    game_id,
                    _fold_exc,
                )

        # If the human just folded and opted into "speed through after I fold",
        # fast-forward the rest of the orbit before progressing.
        if current_player.is_human:
            maybe_engage_fast_forward_on_fold(game_id, action)

        progress_game(game_id)

        # Training mode surfaces the coach's per-action skill verdict inline so
        # every decision gets immediate feedback (other modes evaluate silently).
        response_body = {'success': True}
        if current_game_data.get('training_mode') and skill_feedback:
            response_body['skill_evaluation'] = skill_feedback
        return jsonify(response_body)
    except Exception as e:
        logger.error(f"Error processing action for game {game_id}: {e}", exc_info=True)
        return jsonify({'error': 'Failed to process action'}), 500


@game_bp.route('/api/game/<game_id>/fast-forward', methods=['POST'])
def api_fast_forward(game_id):
    """Enable fast-forward: subsequent AI decisions skip the LLM.

    While `fast_forward` is set on game_data, `handle_ai_action` swaps each
    AI's controller for a TieredBotController with `expression_enabled=False`
    — sub-100ms decisions, zero token cost. The flag auto-clears when action
    returns to the human (see `progress_game`'s human-turn branch), so this
    is a one-orbit affordance: tap once, the rest of the cycle resolves
    quickly, then normal personality-aware play resumes on your next turn.

    Body: `{enabled: bool}` — optional, defaults to `true`. POST with
    `enabled=false` to manually cancel before the orbit completes (e.g. you
    changed your mind mid-cycle).

    Returns `{success, fast_forward}` on success, 404 if the game is gone.
    No 'authorized actor' check beyond the standard game-access guard — only
    the seated human can meaningfully trigger FF (it's tied to *their* turn
    cycle).
    """
    data = request.json or {}
    enabled = data.get('enabled', True)
    if not isinstance(enabled, bool):
        return jsonify({'error': 'enabled must be a boolean'}), 400

    current_game_data = game_state_service.get_game(game_id)
    _, _, _, auth_error = _authorize_game_access(game_id, current_game_data)
    if auth_error:
        return auth_error
    if not current_game_data:
        return jsonify({'error': 'Game not found'}), 404

    current_game_data['fast_forward'] = enabled
    game_state_service.set_game(game_id, current_game_data)
    logger.info(f"[FF] game={game_id} fast_forward={enabled}")

    # Kick the progression loop so any AI mid-orbit resolves quickly.
    # progress_game's per-game lock short-circuits if already running, so
    # this is safe even when the loop is already draining the orbit.
    if enabled:
        progress_game(game_id)

    return jsonify({'success': True, 'fast_forward': enabled})


@game_bp.route('/api/game/<game_id>/message', methods=['POST'])
def api_send_message(game_id):
    """Send a chat message in the game."""
    data = request.json or {}
    message = data.get('message', '')
    sender = data.get('sender', 'Player')
    # Optional list of player names this message is directly addressed to.
    # Drives the AI's find_callouts detection so targeted chat reliably
    # reaches the intended opponent regardless of message wording.
    raw_addressing = data.get('addressing')
    addressing = (
        [str(n) for n in raw_addressing if isinstance(n, str)]
        if isinstance(raw_addressing, list)
        else None
    )

    # Quick-chat metadata: when the message originated from a structured
    # tone selector (mid-hand `ChatTone` or post-round `PostRoundTone`),
    # the UI passes the tone string here. Drives the bilateral
    # relationship-axis update via `chat_intent.map_tone` — see the
    # post-send dispatch below.
    tone = data.get('tone')
    intensity = data.get('intensity')

    current_game_data = game_state_service.get_game(game_id)
    current_user, _, _, auth_error = _authorize_game_access(game_id, current_game_data)
    if auth_error:
        return auth_error

    if not current_game_data:
        return jsonify(
            {'success': False, 'error': 'Game not found in memory. Try refreshing the page first.'}
        ), 404

    # PRH-33: never trust the client-supplied `sender` — force it to the human's
    # actual seat so a spoofed name can't be injected into the AI prompt or the
    # relationship-event attribution.
    sender = _human_seat_name(current_game_data) or sender

    is_guest_user = current_user and is_guest(current_user) and GUEST_LIMITS_ENABLED

    if is_guest_user:
        # PRH-27: free-text chat is sign-in-gated for guests. A recognized
        # quick-chat tone marks structured (bounded-vocabulary) chat, which
        # stays allowed; everything else is free text that would reach the AI
        # prompt verbatim.
        has_structured_tone = map_tone(tone, intensity) is not None
        allowed, error_msg = check_guest_free_chat(current_user, has_structured_tone)
        if not allowed:
            return jsonify(
                {'success': False, 'error': error_msg, 'code': 'GUEST_FREE_CHAT_LOCKED'}
            ), 403

        msgs_this_action = current_game_data.get('guest_messages_this_action', 0)
        allowed, error_msg = check_guest_message_limit(current_user, msgs_this_action)
        if not allowed:
            return jsonify({'success': False, 'error': error_msg, 'code': 'GUEST_CHAT_LIMIT'}), 429
        # Increment before sending to close the race window between check and send
        current_game_data['guest_messages_this_action'] = msgs_this_action + 1
        game_state_service.set_game(game_id, current_game_data)

    content = message.strip()
    if content:
        rejection = _player_chat_rejection(content)
        if rejection:
            return jsonify({'success': False, **rejection}), 400
        send_message(game_id, sender, content, 'player', addressing=addressing)
        dispatch_chat_relationship_event(
            current_game_data,
            sender,
            addressing,
            tone,
            intensity,
        )
        return jsonify({'success': True})

    return jsonify({'success': False, 'error': 'Empty message'})


@game_bp.route('/api/game/<game_id>/retry', methods=['POST'])
def api_retry_game(game_id):
    """Force-retry a hung game by re-triggering AI turns."""
    current_game_data = game_state_service.get_game(game_id)
    _, _, _, auth_error = _authorize_game_access(game_id, current_game_data)
    if auth_error:
        return auth_error

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
        return jsonify(
            {
                'status': 'not_stuck',
                'message': 'Game is waiting for human player action',
                'diagnostic': diagnostic,
            }
        ), 200

    if not game_state.awaiting_action:
        return jsonify(
            {
                'status': 'not_stuck',
                'message': 'Game is not awaiting action',
                'diagnostic': diagnostic,
            }
        ), 200

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

    return jsonify(
        {
            'status': 'retried',
            'message': f'Retried AI turn for {current_player.name}',
            'diagnostic': diagnostic,
        }
    ), 200


def _delete_single_tournament_envelope(game_id: str) -> None:
    """Best-effort removal of a game's single-table tournament envelope when the
    game itself is deleted. Only touches the `single-<game_id>` row, so it's a
    no-op for cash games and multi-table tournament tables."""
    try:
        from flask_app.services import tournament_registry

        tournament_registry.delete_single_envelope(game_id)
    except Exception:
        logger.debug("[DELETE] single-envelope cleanup failed for %s", game_id, exc_info=True)


@game_bp.route('/api/game/<game_id>', methods=['DELETE'])
def delete_game(game_id):
    """Delete a saved game."""
    current_game_data = game_state_service.get_game(game_id)
    _, _, _, auth_error = _authorize_game_access(game_id, current_game_data)
    if auth_error:
        return auth_error

    try:
        game_state_service.delete_game(game_id)
        extensions.game_repo.delete_game(game_id)
        _delete_single_tournament_envelope(game_id)

        return jsonify({'message': 'Game deleted successfully'}), 200
    except Exception as e:
        logger.error(f"Error deleting game {game_id}: {e}")
        return jsonify({'error': str(e)}), 500


@game_bp.route('/api/end_game/<game_id>', methods=['POST'])
def end_game(game_id):
    """Clean up game after tournament completes or user exits."""
    current_game_data = game_state_service.get_game(game_id)
    _, _, _, auth_error = _authorize_game_access(game_id, current_game_data)
    if auth_error:
        return auth_error

    game_state_service.delete_game(game_id)

    try:
        extensions.game_repo.delete_game(game_id)
    except Exception as e:
        logger.warning(f"[DELETE] Error deleting game {game_id} from database: {e}")
    _delete_single_tournament_envelope(game_id)

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
    _, _, _, auth_error = _authorize_game_access(game_id, game_data)
    if auth_error:
        return auth_error

    if not game_data:
        return jsonify([])
    return jsonify(game_data.get('messages', []))


@game_bp.route('/api/game/<game_id>/llm-configs', methods=['GET'])
def api_game_llm_configs(game_id):
    """Get LLM configurations for all players in a game (debug endpoint)."""
    current_game_data = game_state_service.get_game(game_id)
    _, _, _, auth_error = _authorize_game_access(game_id, current_game_data)
    if auth_error:
        return auth_error

    if not current_game_data:
        # Try to load from database
        try:
            llm_configs = extensions.game_repo.load_llm_configs(game_id)
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

    return jsonify({'default_llm_config': default_llm_config, 'player_configs': player_configs})


# SocketIO event handlers
def register_socket_events(sio):
    """Register SocketIO event handlers for game events."""

    @sio.on_error_default
    def on_socket_error(e):
        """Catch-all for unhandled exceptions in any socket event handler.

        Without this, an exception inside a handler is logged by Flask-SocketIO
        but the *client* gets no signal — the action silently stalls until the
        30s `aiThinking` safety-net refresh fires. Here we log it (with the sid
        + the event that raised, for correlation) and emit a recoverable
        `game_error` to the offending client so it re-syncs immediately. The
        client's `game_error` handler (recoverable=true) does a throttled
        `refreshGameState`, which cold-loads authoritative state.

        Best-effort: the emit itself is guarded so a failure here can't mask the
        original error or raise out of the error handler.
        """
        event = None
        sid = None
        try:
            event = (getattr(request, 'event', None) or {}).get('message')
            sid = getattr(request, 'sid', None)
        except Exception:
            pass
        logger.error(
            "[SOCKET] unhandled error in event=%s sid=%s: %s",
            event,
            sid,
            e,
            exc_info=True,
        )
        try:
            emit(
                'game_error',
                {'error': 'Something went wrong processing that request.', 'recoverable': True},
            )
        except Exception:
            logger.debug("[SOCKET] failed to emit game_error from default handler", exc_info=True)

    @sio.on('connect')
    def on_connect():
        """Register cash presence + join the per-user lobby room.

        Drives the realtime world ticker: a sandbox is ticked while the
        owner has a live socket (lobby OR game page — both connect here).
        Best-effort and non-fatal: anonymous/guest sockets or any
        resolution failure just skip presence, leaving game sockets
        working exactly as before. We never reject the connection.
        """
        try:
            from flask_app.extensions import sandbox_repo
            from flask_app.services import presence
            from flask_app.services.sandbox_resolver import resolve_default_sandbox_for

            user = extensions.auth_manager.get_current_user() if extensions.auth_manager else None
            owner_id = user.get('id') if user else None
            if not owner_id:
                return  # unauthenticated socket — nothing to track
            sandbox_id = resolve_default_sandbox_for(owner_id, sandbox_repo=sandbox_repo)
            presence.mark_active(owner_id, sandbox_id, request.sid)
            join_room(presence.lobby_room_name(owner_id))
        except Exception as e:
            logger.debug(f"[SOCKET] connect presence skipped: {e}")

    @sio.on('disconnect')
    def on_disconnect(reason=None):
        """Drop the socket from cash presence (TTL grace handles gaps).

        Flask-SocketIO/python-socketio passes a disconnect ``reason`` positional
        arg to the handler; accept it (optional, so older versions that call with
        no args still work) — otherwise every disconnect raised a TypeError and
        the presence cleanup below never ran (orphaned sids).
        """
        try:
            from flask_app.services import presence

            presence.mark_inactive(request.sid)
        except Exception as e:
            logger.debug(f"[SOCKET] disconnect presence skipped: {e}")

    @sio.on('join_game')
    @socket_rate_limit(max_calls=20, window_seconds=10)
    def on_join(game_id):
        game_id_str = str(game_id)
        game_data = game_state_service.get_game(game_id_str)
        if not game_data:
            # PRH-12: persisted-but-evicted? Tell the owner client to re-fetch
            # state (which cold-loads it) instead of silently no-op'ing.
            _emit_reload_if_persisted(game_id_str)
            return

        # Verify the current user is the game owner (or an admin —
        # matches the bypass already in send_message and progress_game).
        user = extensions.auth_manager.get_current_user() if extensions.auth_manager else None
        owner_id = game_data.get('owner_id')
        user_id = user.get('id') if user else None
        if not user_id or (user_id != owner_id and not _is_admin(user_id)):
            emit('auth_error', {'error': 'Not authorized for this game', 'code': 'NOT_OWNER'})
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
            # PRH-12: persisted-but-evicted → tell the owner to re-fetch state
            # (cold-loads it) and retry, rather than silently dropping.
            _emit_reload_if_persisted(game_id)
            return

        # Verify the current user is the game owner
        user = extensions.auth_manager.get_current_user() if extensions.auth_manager else None
        owner_id = current_game_data.get('owner_id')
        if not user or user.get('id') != owner_id:
            logger.debug(
                f"[SOCKET] player_action unauthorized: user={user.get('id') if user else None}, owner={owner_id}"
            )
            emit('auth_error', {'error': 'Not authorized for this game', 'code': 'NOT_OWNER'})
            return

        if user and is_guest(user) and GUEST_LIMITS_ENABLED:
            tracking_id = current_game_data.get('guest_tracking_id')
            if tracking_id:
                hands_played = extensions.guest_tracking_repo.get_hands_played(tracking_id)
                allowed, _ = check_guest_hands_limit(user, hands_played)
                if not allowed:
                    socketio.emit(
                        'guest_limit_reached',
                        {
                            'hands_played': hands_played,
                            'hands_limit': GUEST_MAX_HANDS,
                        },
                        to=game_id,
                    )
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

        # Coach progression: evaluate human player actions against skill targets
        if current_player.is_human:
            _evaluate_coach_progression(
                game_id, current_player.name, action, amount, current_game_data, pre_action_state
            )

        table_message_content = format_action_message(
            current_player.name, action, amount, highest_bet
        )
        send_message(game_id, "Table", table_message_content, "table")

        # Normalize the recorded amount for calls: callers pass amount=0 since
        # they're not raising. Downstream consumers expect the true call cost.
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
        current_game_data['guest_messages_this_action'] = 0
        game_state_service.set_game(game_id, current_game_data)

        owner_id, owner_name = game_state_service.get_game_owner_info(game_id)
        extensions.game_repo.save_game(game_id, state_machine._state_machine, owner_id, owner_name)
        if 'memory_manager' in current_game_data:
            _mm = current_game_data['memory_manager']
            extensions.game_repo.save_opponent_models(game_id, _mm.get_opponent_model_manager())
            # Circuit scouting memory: fold this game's observation counts
            # into the durable per-sandbox lifetime rows. No-op for
            # non-sandbox games (sandbox_id is None). Isolated + guarded so a
            # fold hiccup can never break the hand flow.
            try:
                extensions.game_repo.fold_observations_into_lifetime(game_id, _mm.sandbox_id)
            except Exception as _fold_exc:  # pragma: no cover - defensive
                logger.warning(
                    "[DOSSIER] observation lifetime fold failed for game %s: %s",
                    game_id,
                    _fold_exc,
                )

        # Human opted into "speed through after I fold" — fast-forward the orbit.
        if current_player.is_human:
            maybe_engage_fast_forward_on_fold(game_id, action)

        update_and_emit_game_state(game_id)
        progress_game(game_id)

    @sio.on('send_message')
    @socket_rate_limit(max_calls=5, window_seconds=10)
    def handle_send_message(data):
        game_id = data.get('game_id')
        content = data.get('message')
        sender = data.get('sender', 'Player')
        message_type = data.get('message_type', 'user')
        raw_addressing = data.get('addressing')
        addressing = (
            [str(n) for n in raw_addressing if isinstance(n, str)]
            if isinstance(raw_addressing, list)
            else None
        )

        if not game_id:
            logger.debug("[SOCKET] send_message missing game_id")
            return

        game_data = game_state_service.get_game(game_id)
        if not game_data:
            logger.debug(f"[SOCKET] send_message game not found: {game_id}")
            return

        # Verify the current user is the game owner or an admin
        user = extensions.auth_manager.get_current_user() if extensions.auth_manager else None
        owner_id = game_data.get('owner_id')
        user_id = user.get('id') if user else None
        if not user_id or (user_id != owner_id and not _is_admin(user_id)):
            logger.debug(f"[SOCKET] send_message unauthorized: user={user_id}, owner={owner_id}")
            emit('auth_error', {'error': 'Not authorized for this game', 'code': 'NOT_OWNER'})
            return

        # PRH-33: force sender to the human seat for authored chat (don't trust
        # the client value, which could spoof another player into the AI prompt).
        if message_type in ('player', 'user'):
            sender = _human_seat_name(game_data) or sender

        # PRH-27: gate free-text chat for guests (mirror of api_send_message).
        # This socket path otherwise bypasses every guest chat check.
        if user and is_guest(user) and GUEST_LIMITS_ENABLED:
            has_structured_tone = map_tone(data.get('tone'), data.get('intensity')) is not None
            allowed, error_msg = check_guest_free_chat(user, has_structured_tone)
            if not allowed:
                emit('auth_error', {'error': error_msg, 'code': 'GUEST_FREE_CHAT_LOCKED'})
                return

        # PRH-27: length-cap + moderate authored chat before it reaches the AI
        # prompt (mirror of api_send_message). Table/system messages are server-
        # generated, so only screen player free text.
        if message_type in ('player', 'user') and (content or '').strip():
            rejection = _player_chat_rejection(content.strip())
            if rejection:
                emit('chat_rejected', rejection)
                return

        send_message(game_id, sender, content, message_type, addressing=addressing)

    @sio.on('progress_game')
    @socket_rate_limit(max_calls=5, window_seconds=10)
    def on_progress_game(game_id):
        game_id_str = str(game_id)
        game_data = game_state_service.get_game(game_id_str)
        if not game_data:
            logger.debug(f"[SOCKET] progress_game game not found: {game_id_str}")
            return

        # Verify the current user is the game owner or an admin
        user = extensions.auth_manager.get_current_user() if extensions.auth_manager else None
        owner_id = game_data.get('owner_id')
        user_id = user.get('id') if user else None
        if not user_id or (user_id != owner_id and not _is_admin(user_id)):
            logger.debug(f"[SOCKET] progress_game unauthorized: user={user_id}, owner={owner_id}")
            emit('auth_error', {'error': 'Not authorized for this game', 'code': 'NOT_OWNER'})
            return

        progress_game(game_id_str)
