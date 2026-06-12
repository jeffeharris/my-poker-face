"""Async-friends lifecycle routes: create / invite / join / list.

"Poker by mail" — friends share one game and act turn-by-turn over days. These
endpoints sit alongside the regular game routes; once a friend has claimed a
seat they play through the SAME socket + REST action paths as everyone else
(authorized as a member, gated to their own turn — see membership_service).

Model: an async game is created as the owner (one human seat) plus AI fill. A
friend who joins via an invite code claims the first still-AI seat, which
becomes their human seat. AI fill is simply the seats nobody has claimed yet.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from flask_app import config, extensions
from flask_app.game_adapter import StateMachineAdapter
from flask_app.routes.game_routes import build_and_persist_game
from flask_app.services import async_game_service, game_state_service, membership_service
from poker.utils import get_celebrities

logger = logging.getLogger(__name__)

async_game_bp = Blueprint('async_game', __name__)


def _current_user():
    return extensions.auth_manager.get_current_user() if extensions.auth_manager else None


def _default_llm_config() -> dict:
    """The configured system default model (used for any LLM-driven AI seats)."""
    from core.llm.settings import get_default_model, get_default_provider

    return {'provider': get_default_provider(), 'model': get_default_model()}


def refresh_turn_state(game_id: str, game_state, *, previous_turn_user=None) -> str | None:
    """Mirror the live turn onto the games row for the lobby + notify layer.

    Advances the turn clock (and re-arms notifications) only when the actor has
    actually changed, so an incidental refresh doesn't move the deadline. Returns
    the resolved turn user (or None when no human is on the clock).
    """
    turn_user = membership_service.resolve_turn_user(game_state)
    advanced = turn_user is not None and turn_user != previous_turn_user
    try:
        extensions.game_repo.set_turn_state(game_id, turn_user, advance_turn_clock=advanced)
    except Exception as e:  # pragma: no cover - defensive, never block play on a write
        logger.debug("[ASYNC] turn-state refresh failed for %s: %s", game_id, e)
    return turn_user


@async_game_bp.route('/api/async-game/new', methods=['POST'])
def api_async_new_game():
    """Create an async game (owner + AI fill) and seed the owner's membership."""
    data = request.json or {}
    current_user = _current_user()
    if not current_user or not current_user.get('id'):
        return jsonify({'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}), 401

    owner_id = current_user['id']
    owner_name = current_user.get('name')
    player_name = data.get('playerName', current_user.get('name', 'Player'))

    personalities = data.get('personalities')
    if isinstance(personalities, list) and personalities:
        ai_player_names = [str(p) for p in personalities][:9]
    else:
        opponent_count = max(1, min(9, int(data.get('opponent_count', 3))))
        ai_player_names = get_celebrities(shuffled=True)[:opponent_count]

    if player_name.lower() in [n.lower() for n in ai_player_names]:
        return jsonify(
            {'error': 'An opponent has your name; pick another.', 'code': 'DUPLICATE_PLAYER_NAME'}
        ), 400

    blind_config = {
        'growth': data.get('blind_growth', 1.5),
        'hands_per_level': data.get('blinds_increase', 6),
        'max_blind': data.get('max_blind', 1000),
    }

    game_id, game_data = build_and_persist_game(
        player_name=player_name,
        owner_id=owner_id,
        owner_name=owner_name,
        ai_player_names=ai_player_names,
        player_llm_configs={},
        player_prompt_configs={},
        default_llm_config=_default_llm_config(),
        starting_stack=int(data.get('starting_stack', 5000)),
        big_blind=int(data.get('big_blind', 100)),
        blind_config=blind_config,
        game_mode=data.get('game_mode', 'casual').lower(),
        ai_chat=bool(data.get('ai_chat', True)),
        bot_types={},
        guest_tracking_id=None,
        enable_avatars=False,
    )

    # Flag async (drives the per-turn auth gate + background orbit + notify) and
    # seed the owner's membership at their human seat.
    game_data['is_async'] = True
    game_state_service.set_game(game_id, game_data)
    extensions.game_repo.set_async_flag(game_id, True)

    players = game_data['state_machine'].game_state.players
    owner_seat = next((i for i, p in enumerate(players) if p.is_human), 0)
    extensions.membership_repo.add_member(
        game_id, owner_id, seat_index=owner_seat, role='owner', status='joined',
        display_name=player_name,
    )

    refresh_turn_state(game_id, game_data['state_machine'].game_state)

    return jsonify({'game_id': game_id, 'is_async': True})


@async_game_bp.route('/api/async-game/<game_id>/invite', methods=['POST'])
def api_async_invite(game_id):
    """Create a share code for an async game (members only)."""
    current_user = _current_user()
    user_id = current_user.get('id') if current_user else None
    if not user_id:
        return jsonify({'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}), 401
    if not membership_service.is_member(game_id, user_id):
        return jsonify({'error': 'Not authorized for this game', 'code': 'NOT_MEMBER'}), 403

    code = extensions.membership_repo.create_invite(game_id, created_by=user_id)
    join_url = f"{config.FRONTEND_URL.rstrip('/')}/join/{code}"
    return jsonify({'code': code, 'join_url': join_url})


@async_game_bp.route('/api/async-game/join', methods=['POST'])
def api_async_join():
    """Claim an open seat in an async game via an invite code."""
    data = request.json or {}
    current_user = _current_user()
    user_id = current_user.get('id') if current_user else None
    if not user_id:
        return jsonify({'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}), 401

    code = data.get('code') or data.get('invite_code')
    if not code:
        return jsonify({'error': 'Missing invite code', 'code': 'MISSING_CODE'}), 400

    invite = extensions.membership_repo.get_invite(code)
    if not invite:
        return jsonify({'error': 'Invalid invite code', 'code': 'INVALID_CODE'}), 404
    max_uses = invite.get('max_uses') or 0
    if max_uses and invite.get('used_count', 0) >= max_uses:
        return jsonify({'error': 'Invite already used', 'code': 'INVITE_USED'}), 409

    game_id = invite['game_id']
    display_name = data.get('playerName', current_user.get('name', 'Player'))

    # Already seated? Idempotent — just point them at the game.
    if membership_service.is_member(game_id, user_id):
        return jsonify({'game_id': game_id, 'already_member': True})

    # Serialize the claim against concurrent joins / the background orbit.
    lock = game_state_service.get_game_lock(game_id)
    with lock:
        game_data = game_state_service.get_game(game_id)
        if game_data is not None:
            adapter = game_data['state_machine']
        else:
            loaded = extensions.game_repo.load_game(game_id)
            if loaded is None:
                return jsonify({'error': 'Game not found'}), 404
            adapter = StateMachineAdapter(loaded)

        try:
            new_state, seat_index, prev_ai_name = async_game_service.claim_open_seat(
                adapter.game_state._game_state, user_id, display_name
            )
        except ValueError:
            return jsonify({'error': 'Table is full', 'code': 'TABLE_FULL'}), 409

        adapter.game_state = new_state

        owner_info = extensions.game_repo.get_game_owner_info(game_id) or {}
        if game_data is not None:
            # Retire the AI controller whose seat was claimed; the seat is human now.
            game_data.get('ai_controllers', {}).pop(prev_ai_name, None)
            game_state_service.set_game(game_id, game_data)
        extensions.game_repo.save_game(
            game_id, adapter._state_machine, owner_info.get('owner_id'),
            owner_info.get('owner_name'),
        )

        extensions.membership_repo.claim_seat(
            game_id, user_id, seat_index=seat_index, display_name=display_name
        )
        extensions.membership_repo.consume_invite(code)
        refresh_turn_state(game_id, adapter.game_state._game_state)

    return jsonify({'game_id': game_id, 'seat_index': seat_index})


@async_game_bp.route('/api/async-game/mine', methods=['GET'])
def api_async_mine():
    """List the async games the current user belongs to, with whose-turn flags."""
    current_user = _current_user()
    user_id = current_user.get('id') if current_user else None
    if not user_id:
        return jsonify({'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}), 401

    games = []
    for game_id in extensions.membership_repo.list_user_games(user_id):
        meta = extensions.game_repo.get_async_meta(game_id) or {}
        if not meta.get('is_async'):
            continue
        current_turn_user = meta.get('current_turn_user_id')
        games.append(
            {
                'game_id': game_id,
                'current_turn_user_id': current_turn_user,
                'is_my_turn': current_turn_user == user_id,
                'turn_started_at': meta.get('turn_started_at'),
            }
        )
    return jsonify({'games': games})
