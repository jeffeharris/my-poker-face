"""Coach routes — REST endpoints for the poker coaching feature."""

import json
import logging
from typing import Optional

from flask import Blueprint, jsonify, request

from flask_app.utils.hand_context import (
    build_hand_context_from_recorded_hand,
    format_hand_context_for_prompt,
)
from poker.authorization import get_authorization_service, require_permission

from .. import extensions
from ..extensions import limiter
from ..services import game_state_service
from ..services.coach_assistant import get_or_create_coach_with_mode
from ..services.coach_engine import compute_coaching_data_with_progression
from ..services.coach_progression import CoachProgressionService, restore_session_memory
from ..services.skill_definitions import ALL_GATES, ALL_SKILLS

logger = logging.getLogger(__name__)

coach_bp = Blueprint('coach', __name__)

# RBAC decorator — requires 'can_access_coach' permission (user + admin groups)
_coach_required = require_permission('can_access_coach')


def _get_human_player_name(game_data: dict) -> Optional[str]:
    """Return the human player's name, or None."""
    game_state = game_data['state_machine'].game_state
    for player in game_state.players:
        if player.is_human:
            return player.name
    return None


def _get_current_user_id() -> str:
    """Get the current authenticated user's ID, or empty string."""
    if not extensions.auth_manager:
        return ''
    user = extensions.auth_manager.get_current_user()
    if not user:
        return ''
    if isinstance(user, dict):
        return user.get('id', '')
    return getattr(user, 'id', '')


def _is_admin(user_id: str) -> bool:
    """Check whether a user has admin tools permission."""
    auth_service = get_authorization_service()
    return bool(auth_service and auth_service.has_permission(user_id, 'can_access_admin_tools'))


def _require_game_owner(game_id: str, game_data: dict):
    """Reject access if the caller doesn't own ``game_id`` and isn't an admin.

    Mirrors the deny semantics of game_routes._authorize_game_access:
    a NULL ``owner_id`` is treated as "not owned by this user" and
    rejected with 403 unless the caller is an admin. Returns a Flask
    response tuple on rejection, or ``None`` to continue.
    """
    user_id = _get_current_user_id()
    if not user_id:
        return jsonify({'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}), 401

    owner_id = (game_data or {}).get('owner_id')
    if owner_id is None:
        owner_info = extensions.game_repo.get_game_owner_info(game_id)
        if owner_info is not None:
            owner_id = owner_info.get('owner_id')
            if game_data is not None and owner_id is not None:
                game_data['owner_id'] = owner_id
                game_data.setdefault('owner_name', owner_info.get('owner_name'))

    if owner_id != user_id and not _is_admin(user_id):
        return jsonify({'error': 'Permission denied'}), 403
    return None


@coach_bp.route('/api/coach/<game_id>/stats')
@limiter.limit("30/minute")
@_coach_required
def coach_stats(game_id: str):
    """Return pre-computed coaching statistics for the human player."""
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return jsonify({'error': 'Game not found'}), 404

    forbidden = _require_game_owner(game_id, game_data)
    if forbidden:
        return forbidden

    player_name = _get_human_player_name(game_data)
    if not player_name:
        return jsonify({'error': 'No human player found'}), 400

    user_id = _get_current_user_id()
    data = compute_coaching_data_with_progression(
        game_id,
        player_name,
        user_id=user_id,
        game_data=game_data,
        coach_repo=extensions.coach_repo,
    )
    if data is None:
        return jsonify({'error': 'Could not compute stats'}), 500

    return jsonify(data)


def _record_proactive_tip(game_id: str, game_data: dict, player_name: str, payload: dict) -> None:
    """Best-effort log of a proactive tip that was served, for measuring the
    coach's effect on play (joins to player_decision_analysis later). Never raises."""
    try:
        if not getattr(extensions, 'coach_repo', None):
            return
        stats = payload.get('stats') or {}
        leak = stats.get('known_preflop_leak') or {}
        mm = game_data.get('memory_manager')
        rng = stats.get('player_range_analysis') or {}
        extensions.coach_repo.record_tip(
            {
                'game_id': game_id,
                'owner_id': game_data.get('owner_id'),
                'player_name': player_name,
                'hand_number': getattr(mm, 'hand_count', None) if mm else None,
                'phase': stats.get('phase'),
                'tip_text': payload.get('answer'),
                'leak_fired': bool(leak),
                'leak_scenario': leak.get('scenario'),
                'leak_position': leak.get('position'),
                'leak_kind': leak.get('kind'),
                'leak_status': leak.get('status'),
                'leak_granularity': leak.get('granularity'),
                'player_hand_canonical': rng.get('canonical_hand'),
                'player_position': stats.get('position'),
            }
        )
    except Exception as e:
        logger.debug(f"coach tip capture skipped: {e}")


@coach_bp.route('/api/coach/<game_id>/ask', methods=['POST'])
@limiter.limit("10/minute")
@_coach_required
def coach_ask(game_id: str):
    """Answer a coaching question (or generate a proactive tip)."""
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return jsonify({'error': 'Game not found'}), 404

    forbidden = _require_game_owner(game_id, game_data)
    if forbidden:
        return forbidden

    player_name = _get_human_player_name(game_data)
    if not player_name:
        return jsonify({'error': 'No human player found'}), 400

    body = request.get_json(silent=True) or {}
    request_type = body.get('type', '')
    question = body.get('question', '').strip()
    request_player_name = body.get('playerName', '')

    if request_type != 'proactive_tip' and not question:
        return jsonify({'error': 'No question provided'}), 400

    # Serve a prefetched proactive tip if one is ready/in-flight for this exact
    # decision (fired at turn-start in handle_human_turn). Guarantees a single
    # coach LLM call per decision and hides the round-trip + start latency.
    if request_type == 'proactive_tip':
        from ..services.coach_prefetch import take_cached_tip

        cached = take_cached_tip(game_data)
        if cached is not None:
            _record_proactive_tip(game_id, game_data, player_name, cached)
            return jsonify(cached)

    # Compute current stats with progression context
    user_id = _get_current_user_id()
    stats = compute_coaching_data_with_progression(
        game_id,
        player_name,
        user_id=user_id,
        game_data=game_data,
        coach_repo=extensions.coach_repo,
    )

    # Use mode-aware coach if progression data is available
    progression = (stats or {}).get('progression', {})
    coaching_mode = progression.get('coaching_mode', '')
    coaching_prompt = progression.get('coaching_prompt', '')

    coach = get_or_create_coach_with_mode(
        game_data,
        game_id,
        player_name=request_player_name or player_name,
        mode=coaching_mode,
        skill_context=coaching_prompt,
    )

    try:
        if request_type == 'proactive_tip':
            result = coach.get_proactive_tip(stats or {})
        else:
            result = coach.ask(question, stats or {})
    except json.JSONDecodeError as e:
        logger.error(f"Coach response parse failed: {e}", exc_info=True)
        return jsonify({'error': 'Coach response error'}), 500
    except TimeoutError as e:
        logger.error(f"Coach request timed out: {e}", exc_info=True)
        return jsonify({'error': 'Coach is taking too long, please try again'}), 504
    except Exception as e:
        logger.error(f"Coach ask failed: {e}", exc_info=True)
        return jsonify({'error': 'Coach unavailable'}), 503

    # Extract structured response fields
    answer = result.get('advice', '')
    coach_action = result.get('action')
    coach_raise_to = result.get('raise_to')

    # Point the recommendation highlight at the coach's pick (env-gated).
    from flask_app.services.coach_assistant import apply_coach_highlight
    apply_coach_highlight(stats, coach_action, coach_raise_to)

    payload = {
        'answer': answer,
        'coach_action': coach_action,
        'coach_raise_to': coach_raise_to,
        'stats': stats,
    }
    if request_type == 'proactive_tip':
        _record_proactive_tip(game_id, game_data, player_name, payload)
    return jsonify(payload)


@coach_bp.route('/api/coach/preflop-leaks', methods=['GET'])
@limiter.limit("20/minute")
@_coach_required
def coach_preflop_leaks():
    """Your preflop range vs a reference — the leak-finder, across your real games.

    User-scoped (not game-scoped): aggregates the caller's OWN preflop decisions.
    Returns per-position context (your VPIP next to an opening-range reference —
    context only, since VPIP includes calls/defense) plus the actionable signal:
    specific below-range hands you keep voluntarily playing. The too-tight
    direction is deliberately not graded (can't tell opens from correct folds to
    a raise). See flask_app/services/coach_leaks for the scope caveats.
    """
    from ..services import preflop_leak_cache
    from ..services.coach_chart_data import load_owner_chart_decisions
    from ..services.coach_chart_leaks import (
        DEEP_FLOOR_BB,
        compute_chart_leaks,
        compute_leak_trend,
        compute_slice_diff,
        depth_slice,
        recent_slice,
    )
    from ..services.coach_leaks import (
        compute_preflop_leaks,
        count_owner_preflop_decisions,
        load_owner_preflop_decisions,
    )
    from poker.strategy.preflop_reference import reference_strategy

    owner_id = _get_current_user_id()
    if not owner_id:
        return jsonify({'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}), 401

    # Below this, the signal is too thin to act on — the UI shows "keep playing".
    min_for_signal = 50
    # "Recent" = the player's last N hands (volume-stable; ?window_hands= override).
    try:
        recent_hands = max(50, min(5000, int(request.args.get('window_hands', 500))))
    except (TypeError, ValueError):
        recent_hands = 500
    # Depth slice: 'all' | 'deep' (≥35bb) | 'short' (<35bb).
    depth = request.args.get('depth', 'all')
    if depth not in ('all', 'deep', 'short'):
        depth = 'all'
    db_path = extensions.persistence_db_path
    _insufficient = {'n': 0, 'gap': None, 'status': None, 'trend': 'insufficient'}

    def _build() -> dict:
        # VPIP-by-position bars: orientation only (always all-time; depth scopes
        # the chart leaks, not these context bars).
        vpip_report = compute_preflop_leaks(load_owner_preflop_decisions(db_path, owner_id))
        # Load chart decisions ONCE; depth-slice, then reuse for every pass.
        all_decisions = load_owner_chart_decisions(db_path, owner_id)
        deep_n = sum(1 for d in all_decisions if (d.get('effective_stack_bb') or 0) >= DEEP_FLOOR_BB)
        short_n = sum(
            1 for d in all_decisions if 0 < (d.get('effective_stack_bb') or 0) < DEEP_FLOOR_BB
        )
        decisions = depth_slice(all_decisions, depth)
        chart_report = compute_chart_leaks(decisions, reference_strategy, group_by='position')
        recent = recent_slice(decisions, n_hands=recent_hands)
        trends, emerging = compute_slice_diff(
            decisions, recent, reference_strategy, group_by='position'
        )
        trend_map = compute_leak_trend(decisions, reference_strategy, group_by='position')

        by_position = [
            {'position': g, **vpip_report.by_position_summary[g]}
            for g in ('early', 'middle', 'late', 'blind')
            if g in vpip_report.by_position_summary
        ]
        leaks = [
            {
                'scenario': lk.scenario,
                'position': lk.position,
                'hand': lk.hand,
                'kind': lk.kind,
                'your_freq': lk.your_freq,
                'chart_freq': lk.chart_freq,
                'gap': lk.gap,
                'times_seen': lk.n,
                'status': lk.status,
                'recent': trends.get((lk.scenario, lk.position), _insufficient),
                # Gap trajectory (oldest→newest, null where a block was too thin).
                'trend': {'series': trend_map.get((lk.scenario, lk.position), [])},
            }
            for lk in chart_report.leaks
        ][:15]
        emerging_payload = [
            {
                'scenario': m['scenario'],
                'position': m['position'],
                'hand': m['hand'],
                'kind': m['kind'],
                'your_freq': m['your_freq'],
                'chart_freq': m['chart_freq'],
                'gap': m['gap'],
                'times_seen': m['n'],
                'status': m['status'],
            }
            for m in emerging
        ][:10]
        return {
            'total_decisions': vpip_report.total_decisions,
            'enough_data': vpip_report.total_decisions >= min_for_signal,
            'min_for_signal': min_for_signal,
            'by_position': by_position,
            'leaks': leaks,
            'emerging': emerging_payload,
            'recent_window': {'unit': 'hands', 'n': recent_hands, 'decisions': len(recent)},
            'depth': {'band': depth, 'deep': deep_n, 'short': short_n},
            'graded': chart_report.graded,
            'eligible_groups': chart_report.eligible_groups,
            'skipped': chart_report.skipped,
        }

    try:
        # Cache the computed report per (owner, depth, window); the owner's
        # PRE_FLOP count gates staleness, so a new hand self-invalidates it.
        count = count_owner_preflop_decisions(db_path, owner_id)
        report = preflop_leak_cache.get_or_compute(
            (owner_id, depth, recent_hands), count, _build
        )
    except Exception as e:
        logger.error(f"preflop-leaks failed for {owner_id}: {e}", exc_info=True)
        return jsonify({'error': 'Could not compute leaks'}), 500

    return jsonify(report)


@coach_bp.route('/api/coach/opponent-tells', methods=['GET'])
@limiter.limit("20/minute")
@_coach_required
def coach_opponent_tells():
    """How readable an opponent's bet SIZING is, with a stability trend.

    Surface B of docs/plans/SIZING_COACH_SURFACES.md. User-scoped: grades the
    named opponent's postflop bets (in the caller's games) into a size→strength
    `sizing_polarization_score` over time-blocks, so the dossier shows whether the
    tell is holding (`stable`) or the opponent is starting to mix (`mixing`). The
    `stability` axis is the kill-switch signal for the bot's Phase B sizing-defense.
    """
    from ..services.coach_sizing_tells import (
        CONFIRM_MIN_BETS,
        FACE_UP_THRESHOLD,
        compute_opponent_sizing_tell,
        load_opponent_bet_decisions,
        sizing_label,
    )

    owner_id = _get_current_user_id()
    if not owner_id:
        return jsonify({'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}), 401
    opponent = (request.args.get('opponent') or '').strip()
    if not opponent:
        return jsonify({'error': 'opponent query param required', 'code': 'BAD_REQUEST'}), 400

    db_path = extensions.persistence_db_path
    try:
        decisions = load_opponent_bet_decisions(db_path, owner_id, opponent)
        tell = compute_opponent_sizing_tell(decisions)
    except Exception as e:
        logger.error(f"opponent-tells failed for {owner_id}/{opponent}: {e}", exc_info=True)
        return jsonify({'error': 'Could not compute opponent tells'}), 500

    payload = {
        'opponent': opponent,
        'face_up_threshold': FACE_UP_THRESHOLD,
        'confirm_min_bets': CONFIRM_MIN_BETS,
        'tells': [],
    }
    if tell.confidence == 'insufficient':
        payload['message'] = (
            f"Not enough of {opponent}'s big bets seen yet to read their sizing — "
            "keep playing them."
        )
        return jsonify(payload)

    payload['tells'].append(
        {
            'axis': 'sizing',
            'label': sizing_label(tell.verdict),
            'verdict': tell.verdict,
            'score': tell.score,
            'big_eq': tell.big_eq,
            'small_eq': tell.small_eq,
            'confidence': tell.confidence,
            'stability': tell.stability,
            'n_bets': tell.n_bets,
            'n_big': tell.n_big,
            'n_small': tell.n_small,
            'exploit': tell.exploit,
            'trend': {'series': tell.series},
        }
    )
    return jsonify(payload)


@coach_bp.route('/api/coach/sizing-readability', methods=['GET'])
@limiter.limit("20/minute")
@_coach_required
def coach_sizing_readability():
    """How readable YOUR OWN bet sizing is, over time — Surface A.

    Self-coaching twin of /opponent-tells: grades the caller's own postflop bets
    into a size→strength score (do your big bets always mean strength? → face-up →
    opponents fold for free) with the same stability trend. Reuses the Surface B
    grading core (compute_opponent_sizing_tell) pointed at the owner seat.
    """
    from ..services.coach_sizing_tells import (
        CONFIRM_MIN_BETS,
        FACE_UP_THRESHOLD,
        compute_opponent_sizing_tell,
        load_owner_bet_decisions,
        self_advice,
        self_label,
    )

    owner_id = _get_current_user_id()
    if not owner_id:
        return jsonify({'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}), 401

    db_path = extensions.persistence_db_path
    try:
        decisions = load_owner_bet_decisions(db_path, owner_id)
        tell = compute_opponent_sizing_tell(decisions)
    except Exception as e:
        logger.error(f"sizing-readability failed for {owner_id}: {e}", exc_info=True)
        return jsonify({'error': 'Could not compute sizing readability'}), 500

    payload = {
        'face_up_threshold': FACE_UP_THRESHOLD,
        'confirm_min_bets': CONFIRM_MIN_BETS,
        'readability': None,
    }
    if tell.confidence == 'insufficient':
        payload['message'] = (
            "Not enough of your own big bets yet to read your sizing — keep playing."
        )
        return jsonify(payload)

    payload['readability'] = {
        'label': self_label(tell.verdict),
        'verdict': tell.verdict,
        'score': tell.score,
        'big_eq': tell.big_eq,
        'small_eq': tell.small_eq,
        'confidence': tell.confidence,
        'stability': tell.stability,
        'n_bets': tell.n_bets,
        'n_big': tell.n_big,
        'n_small': tell.n_small,
        'advice': self_advice(tell.verdict),
        'trend': {'series': tell.series},
    }
    return jsonify(payload)


@coach_bp.route('/api/coach/preflop-leaks/feedback', methods=['POST'])
@limiter.limit("10/minute")
@_coach_required
def coach_preflop_leaks_feedback():
    """Have the coach interpret the player's preflop profile into feedback.

    Recomputes the profile server-side (never trusts client-sent data) and feeds
    the text description to the coach (Assistant tier). The coach explains real,
    computed data — it can't invent leaks. User-initiated (one LLM call/click).
    """
    from ..services.coach_assistant import CoachAssistant
    from ..services.coach_chart_data import load_owner_chart_decisions
    from ..services.coach_chart_leaks import compute_chart_leaks, format_chart_leaks_for_prompt
    from poker.strategy.preflop_reference import reference_strategy

    owner_id = _get_current_user_id()
    if not owner_id:
        return jsonify({'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}), 401

    try:
        report = compute_chart_leaks(
            load_owner_chart_decisions(extensions.persistence_db_path, owner_id),
            reference_strategy,
            group_by='position',
        )
        if report.graded == 0:
            return jsonify({'feedback': "Play some hands first — there's nothing to review yet."})
        profile_text = format_chart_leaks_for_prompt(report)
        coach = CoachAssistant(game_id=f'preflop-leaks-{owner_id}', owner_id=owner_id, mode='review')
        feedback = coach.review_preflop_leaks(profile_text)
    except TimeoutError:
        return jsonify({'error': 'Coach is taking too long, please try again'}), 504
    except Exception as e:
        logger.error(f"preflop-leaks feedback failed for {owner_id}: {e}", exc_info=True)
        return jsonify({'error': 'Coach unavailable'}), 503

    return jsonify({'feedback': feedback})


@coach_bp.route('/api/coach/tip-effectiveness', methods=['GET'])
@limiter.limit("30/minute")
@_coach_required
def coach_tip_effectiveness():
    """How often the CURRENT player took the solver line after a leak nudge,
    vs their baseline rate in the same leak spots.

    Self-scoped follow-through on the review panel — "is the coach helping me?".
    Nudged side is empty until the coach has nudged this player; the baseline is
    their overall play in those leak spots (correlational, not a clean A/B).
    """
    from ..services.coach_chart_data import get_owner_chart_leak_set, load_owner_chart_decisions
    from ..services.coach_chart_leaks import compute_baseline_follow_rates, merge_effectiveness

    owner_id = _get_current_user_id()
    if not owner_id:
        return jsonify({'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}), 401
    db = extensions.persistence_db_path
    try:
        nudged = extensions.coach_repo.get_tip_effectiveness(owner_id)
        baseline = compute_baseline_follow_rates(
            load_owner_chart_decisions(db, owner_id),
            get_owner_chart_leak_set(db, owner_id, recent_hands=None),  # all-time leak spots
        )
        return jsonify(merge_effectiveness(nudged, baseline))
    except Exception as e:
        logger.error(f"tip-effectiveness failed for {owner_id}: {e}", exc_info=True)
        return jsonify({'error': 'Could not load tip effectiveness'}), 500


@coach_bp.route('/api/coach/drill', methods=['GET'])
@limiter.limit("30/minute")
@_coach_required
def coach_drill():
    """Build a preflop drill from the player's leak (the practice half of the loop).

    Drills an explicit ?scenario=&position= when given, else the player's top
    CONFIRMED chart leak. Returns {leak, spots} or {enough_data: false} when
    there's nothing confirmed to practice yet.
    """
    from ..services.coach_chart_data import get_owner_chart_leak_set
    from ..services.coach_drill import sample_drill_spots

    owner_id = _get_current_user_id()
    if not owner_id:
        return jsonify({'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}), 401

    scenario = (request.args.get('scenario') or '').strip()
    position = (request.args.get('position') or '').strip()
    kind = None
    try:
        if not (scenario and position):
            from ..services.coach_drill import pick_drill_leak

            leak = pick_drill_leak(
                get_owner_chart_leak_set(extensions.persistence_db_path, owner_id)
            )
            if not leak:
                return jsonify({'enough_data': False})
            scenario, position, kind = leak['scenario'], leak['position'], leak['kind']
        spots = sample_drill_spots(scenario, position, n=10)
    except Exception as e:
        logger.error(f"drill build failed for {owner_id}: {e}", exc_info=True)
        return jsonify({'error': 'Could not build drill'}), 500

    if not spots:
        return jsonify({'enough_data': False})
    return jsonify(
        {
            'enough_data': True,
            'leak': {'scenario': scenario, 'position': position, 'kind': kind},
            'spots': spots,
        }
    )


@coach_bp.route('/api/coach/drill/answer', methods=['POST'])
@limiter.limit("120/minute")
@_coach_required
def coach_drill_answer():
    """Grade one drill answer against the solver chart (recomputed server-side)."""
    from ..services.coach_drill import grade_drill_answer

    body = request.get_json(silent=True) or {}
    result = grade_drill_answer(
        body.get('scenario', ''),
        body.get('position', ''),
        body.get('hand', ''),
        body.get('action', ''),
    )
    if result is None:
        return jsonify({'error': 'Not a gradeable spot'}), 400
    return jsonify(result)


@coach_bp.route('/api/coach/<game_id>/config', methods=['GET'])
@limiter.limit("30/minute")
@_coach_required
def coach_config_get(game_id: str):
    """Load coach mode preference for the game."""
    game_data = game_state_service.get_game(game_id)
    forbidden = _require_game_owner(game_id, game_data)
    if forbidden:
        return forbidden

    if game_data:
        config = game_data.get('coach_config', {})
        mode = config.get('mode')
        if mode:
            return jsonify({'mode': mode})

    mode = extensions.game_repo.load_coach_mode(game_id)
    return jsonify({'mode': mode})


@coach_bp.route('/api/coach/<game_id>/config', methods=['POST'])
@limiter.limit("30/minute")
@_coach_required
def coach_config(game_id: str):
    """Store coach mode preference for the game."""
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return jsonify({'error': 'Game not found'}), 404

    forbidden = _require_game_owner(game_id, game_data)
    if forbidden:
        return forbidden

    body = request.get_json(silent=True) or {}
    mode = body.get('mode')
    if mode not in ('proactive', 'reactive', 'off'):
        return jsonify({'error': 'Invalid mode'}), 400

    game_data['coach_config'] = {'mode': mode}
    extensions.game_repo.save_coach_mode(game_id, mode)
    return jsonify({'status': 'ok', 'mode': mode})


@coach_bp.route('/api/coach/<game_id>/hand-review', methods=['POST'])
@limiter.limit("10/minute")
@_coach_required
def coach_hand_review(game_id: str):
    """Generate a post-hand review of the most recently completed hand."""
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return jsonify({'error': 'Game not found'}), 404

    forbidden = _require_game_owner(game_id, game_data)
    if forbidden:
        return forbidden

    player_name = _get_human_player_name(game_data)
    if not player_name:
        return jsonify({'error': 'No human player found'}), 400

    # Get the last completed hand from the memory manager
    memory_manager = game_data.get('memory_manager')
    completed_hands = (
        memory_manager.hand_recorder.completed_hands
        if memory_manager and hasattr(memory_manager, 'hand_recorder')
        else []
    )

    if not completed_hands:
        return jsonify({'error': 'No completed hands found'}), 404

    body = request.get_json(silent=True) or {}
    request_player_name = body.get('playerName', '')
    explanation = body.get('explanation', '').strip()

    hand = completed_hands[-1]

    # Build context and format for LLM
    context = build_hand_context_from_recorded_hand(hand, player_name)
    # Use the rich narrator-based format (matches hybrid-bot decision prompts:
    # street-by-street action with "You" substitution + per-card hand breakdown).
    big_blind = None
    state_machine = game_data.get('state_machine')
    if state_machine is not None:
        live_state = getattr(state_machine, 'game_state', None)
        if live_state is not None:
            big_blind = getattr(live_state, 'current_ante', None)
    hand_text = format_hand_context_for_prompt(
        context,
        player_name,
        recorded_hand=hand,
        big_blind=big_blind,
    )

    # Append skill evaluations from SessionMemory (if available).
    # PRH-15: restore persisted history on a memory miss (cold-load / restart)
    # so a returning player's hand review still carries its skill evaluations.
    session_memory = restore_session_memory(game_id, game_data, extensions.coach_repo)
    hand_number = getattr(hand, 'hand_number', None)
    if session_memory and hand_number is not None:
        evaluations = session_memory.get_hand_evaluations(hand_number)
        if evaluations:
            skill_eval_text = "\n\nSKILL EVALUATIONS FOR THIS HAND:\n"
            for ev in evaluations:
                skill_eval_text += f"- {ev.skill_id}: {ev.evaluation} — {ev.reasoning}\n"
            hand_text += skill_eval_text

    # Append player explanation
    if explanation:
        hand_text += f"\n\nPlayer's explanation: {explanation}"

    # Use mode-aware coach with REVIEW mode
    coach = get_or_create_coach_with_mode(
        game_data,
        game_id,
        player_name=request_player_name or player_name,
        mode='review',
        skill_context='',
    )

    try:
        review = coach.review_hand(hand_text)
    except Exception as e:
        logger.error(f"Coach hand review failed: {e}", exc_info=True)
        return jsonify({'error': 'Coach unavailable'}), 503

    return jsonify(
        {
            'review': review,
            'hand_number': hand_number,
        }
    )


@coach_bp.route('/api/coach/<game_id>/progression')
@limiter.limit("30/minute")
@_coach_required
def coach_progression(game_id: str):
    """Return the player's skill progression state."""
    game_data = game_state_service.get_game(game_id)
    forbidden = _require_game_owner(game_id, game_data)
    if forbidden:
        return forbidden

    user_id = _get_current_user_id()

    try:
        service = CoachProgressionService(extensions.coach_repo)
        state = service.get_or_initialize_player(user_id)

        return jsonify(
            {
                'skill_states': {
                    sid: {
                        'state': ss.state.value,
                        'total_opportunities': ss.total_opportunities,
                        'total_correct': ss.total_correct,
                        'window_accuracy': round(ss.window_accuracy, 2),
                        'streak_correct': ss.streak_correct,
                        'name': ALL_SKILLS[sid].name if sid in ALL_SKILLS else sid,
                        'description': ALL_SKILLS[sid].description if sid in ALL_SKILLS else '',
                        'gate': ALL_SKILLS[sid].gate if sid in ALL_SKILLS else 0,
                    }
                    for sid, ss in state['skill_states'].items()
                },
                'gate_progress': {
                    str(gn): {
                        'unlocked': state['gate_progress'][gn].unlocked
                        if gn in state['gate_progress']
                        else False,
                        'unlocked_at': state['gate_progress'][gn].unlocked_at
                        if gn in state['gate_progress']
                        else None,
                        'name': gate_def.name,
                        'description': gate_def.description,
                    }
                    for gn, gate_def in ALL_GATES.items()
                },
                'profile': state['profile'],
            }
        )
    except Exception as e:
        logger.error(f"Coach progression failed: {e}", exc_info=True)
        return jsonify({'error': 'Could not load progression'}), 500


@coach_bp.route('/api/coach/<game_id>/onboarding', methods=['POST'])
@limiter.limit("5/minute")
@_coach_required
def coach_onboarding(game_id: str):
    """Initialize or update the player's coaching profile.

    If the player has no existing profile, initializes from scratch.
    If the player already has a profile (with accumulated stats),
    only updates the level and unlocks new gates without wiping stats.
    """
    game_data = game_state_service.get_game(game_id)
    forbidden = _require_game_owner(game_id, game_data)
    if forbidden:
        return forbidden

    user_id = _get_current_user_id()

    body = request.get_json(silent=True) or {}
    level = body.get('level', 'beginner')
    if level not in ('beginner', 'intermediate', 'experienced'):
        return jsonify({'error': 'Invalid level'}), 400

    try:
        service = CoachProgressionService(extensions.coach_repo)

        # Check if player already has a profile with accumulated stats
        existing_state = service.get_player_state(user_id)
        if existing_state['profile']:
            # Player exists - update level without wiping stats
            state = service.update_player_level(user_id, level=level)
            logger.info(f"Updated existing player {user_id} to level {level}")
        else:
            # New player - full initialization
            state = service.initialize_player(user_id, level=level)
            logger.info(f"Initialized new player {user_id} at level {level}")

        return jsonify(
            {
                'status': 'ok',
                'profile': state['profile'],
            }
        )
    except Exception as e:
        logger.error(f"Coach onboarding failed: {e}", exc_info=True)
        return jsonify({'error': 'Onboarding failed'}), 500


# --- Admin-only metrics endpoints ---

_admin_required = require_permission('can_access_admin_tools')


@coach_bp.route('/api/coach/metrics/overview')
@limiter.limit("30/minute")
@_admin_required
def coach_metrics_overview():
    """Aggregate overview of coach progression usage."""
    try:
        stats = extensions.coach_repo.get_profile_stats()
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Coach metrics overview failed: {e}", exc_info=True)
        return jsonify({'error': 'Could not load metrics'}), 500


@coach_bp.route('/api/coach/metrics/skills')
@limiter.limit("30/minute")
@_admin_required
def coach_metrics_skills():
    """Per-skill distribution and advancement stats."""
    try:
        stats = extensions.coach_repo.get_skill_distribution()
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Coach metrics skills failed: {e}", exc_info=True)
        return jsonify({'error': 'Could not load skill metrics'}), 500


@coach_bp.route('/api/coach/metrics/advancement')
@limiter.limit("30/minute")
@_admin_required
def coach_metrics_advancement():
    """Skill advancement timing and difficulty analysis."""
    try:
        stats = extensions.coach_repo.get_skill_advancement_stats()
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Coach metrics advancement failed: {e}", exc_info=True)
        return jsonify({'error': 'Could not load advancement metrics'}), 500


@coach_bp.route('/api/coach/metrics/tip-effectiveness')
@limiter.limit("30/minute")
@_admin_required
def coach_metrics_tip_effectiveness():
    """After a leak nudge fired, did the player's next decision follow the solver?

    Aggregates across all players (global). ``?owner=<id>`` scopes to one. Reads
    only the instrumentation tables (coach_tips ⋈ player_decision_analysis) —
    measures whether the live coach is helping vs. noise.
    """
    try:
        owner = request.args.get('owner') or None
        return jsonify(extensions.coach_repo.get_tip_effectiveness(owner))
    except Exception as e:
        logger.error(f"Coach tip effectiveness failed: {e}", exc_info=True)
        return jsonify({'error': 'Could not load tip effectiveness'}), 500
