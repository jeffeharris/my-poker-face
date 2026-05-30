"""REST routes for multi-table tournaments (Phase 2a — API against the
TournamentSession contract).

These expose the tournament meta-layer: register a tournament, read field-wide
standings, and advance/fast-forward the world. The actual single-table poker
game is reused from the existing machinery; this layer only manages seating,
chip movement between tables, standings, and the blind clock (via
`tournament.TournamentSession`).

Route patterns mirror `cash_routes.py` (owner_id from auth_manager, JSON in/out,
in-memory registry keyed by id). The deep bridge that drives the human's hands
through the live `game_handler` is Phase 2c; until then `advance` / `play-out`
resolve the human's table with the same resolver as the AI tables, so the
standings UI has real, evolving data to render.
"""

from __future__ import annotations

import logging
from datetime import datetime

from flask import Blueprint, jsonify, request

from flask_app.services import tournament_registry as registry
from tournament.beats import build_beats, level_up_beat
from tournament.config import DEFAULT_FIELD_ARCHETYPES, TournamentConfig
from tournament.director import FakeHandResolver, build_initial_state
from tournament.session import TournamentSession, paid_places_for

logger = logging.getLogger(__name__)

tournament_bp = Blueprint('tournament', __name__)

MAX_FIELD_SIZE = 200
MAX_TABLE_SIZE = 10


def _resolve_owner_id() -> str:
    """Stable id for the current user — same path cash routes use."""
    from flask_app.extensions import auth_manager

    user = auth_manager.get_current_user() if auth_manager else None
    if user and user.get('id'):
        return user['id']
    raise ValueError("No owner_id resolvable from request")


def _resolve_player_name() -> str:
    from flask_app.extensions import auth_manager

    user = auth_manager.get_current_user() if auth_manager else None
    if user and user.get('name'):
        return user['name']
    return 'You'


def _build_resolver(kind: str, entries: dict[str, str]):
    if kind == 'engine':
        from tournament.engine_resolver import EngineHandResolver

        return EngineHandResolver(entries)
    return FakeHandResolver()


def _emit_update(
    owner_id: str, tournament_id: str, standings: dict, beats: list | None = None
) -> None:
    """Best-effort push to the owner's lobby room (already joined on connect)."""
    try:
        from flask_app.extensions import socketio
        from flask_app.services import presence

        if socketio is not None:
            socketio.emit(
                'mtt_update',
                {
                    'tournament_id': tournament_id,
                    'standings': standings,
                    'beats': beats or [],
                },
                to=presence.lobby_room_name(owner_id),
            )
    except Exception as exc:  # noqa: BLE001 — emit is best-effort observability
        logger.debug("mtt_update emit failed: %s", exc)


def _beats_for(session, reports, remaining_before: int, level_before: int) -> list:
    """Translate a burst of round reports into activity beats, appending a
    level-up beat when the blind clock crossed a level during the burst."""
    beats = build_beats(
        reports,
        paid_places=paid_places_for(session.field.field_size),
        table_size=session.config.table_size,
        human_id=session.human_id,
        remaining_before=remaining_before,
    )
    level_after = session.current_level()
    if level_after.level > level_before:
        # Key the level-up off the last round in this burst (session.rounds has
        # already advanced past it), so its de-dup key sits with that round.
        round_idx = reports[-1].round_index if reports else session.rounds
        beats.append(level_up_beat(level_after, round_index=round_idx))
    return beats


def _owned_record(tournament_id: str):
    """Return (record, owner_id) if the current user owns the tournament, else
    (None, owner_id). Raises ValueError if no user."""
    owner_id = _resolve_owner_id()
    rec = registry.get(tournament_id)
    if rec is None or rec.get('owner_id') != owner_id:
        return None, owner_id
    return rec, owner_id


@tournament_bp.route('/api/tournament/lobby', methods=['GET'])
def get_lobby():
    try:
        owner_id = _resolve_owner_id()
    except ValueError:
        return jsonify({'error': 'unauthorized'}), 401

    active_tid = registry.find_active_for_owner(owner_id)
    active = None
    if active_tid:
        rec = registry.get(active_tid)
        active = {
            'tournament_id': active_tid,
            'created_at': rec['created_at'],
            'standings': rec['session'].standings_view(),
        }
    return jsonify(
        {
            'has_active': active_tid is not None,
            'active': active,
            'defaults': {'field_size': 18, 'table_size': 6, 'starting_stack': 10_000},
        }
    )


@tournament_bp.route('/api/tournament/register', methods=['POST'])
def register_tournament():
    try:
        owner_id = _resolve_owner_id()
    except ValueError:
        return jsonify({'error': 'unauthorized'}), 401

    existing = registry.find_active_for_owner(owner_id)
    if existing:
        return jsonify({'error': 'already_registered', 'tournament_id': existing}), 409

    body = request.get_json(silent=True) or {}
    try:
        field_size = int(body.get('field_size', 18))
        table_size = int(body.get('table_size', 6))
        starting_stack = int(body.get('starting_stack', 10_000))
        seed = int(body.get('seed', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'field_size/table_size/starting_stack/seed must be integers'}), 400

    if not 2 <= field_size <= MAX_FIELD_SIZE:
        return jsonify({'error': f'field_size must be between 2 and {MAX_FIELD_SIZE}'}), 400
    if not 2 <= table_size <= MAX_TABLE_SIZE:
        return jsonify({'error': f'table_size must be between 2 and {MAX_TABLE_SIZE}'}), 400
    if starting_stack < 1:
        return jsonify({'error': 'starting_stack must be >= 1'}), 400

    resolver_kind = body.get('resolver', 'fake')
    if resolver_kind not in ('fake', 'engine'):
        return jsonify({'error': "resolver must be 'fake' or 'engine'"}), 400

    archetypes = body.get('archetypes') or list(DEFAULT_FIELD_ARCHETYPES)
    try:
        config = TournamentConfig(
            field_size=field_size,
            table_size=table_size,
            starting_stack=starting_stack,
            seed=seed,
            field_archetypes=tuple(archetypes),
        )
        player_ids, entries, _field, _seating = build_initial_state(config)
        resolver = _build_resolver(resolver_kind, entries)
        session = TournamentSession(config, ai_resolver=resolver, human_id=player_ids[0])
    except (ValueError, KeyError) as exc:
        return jsonify({'error': str(exc)}), 400

    tournament_id = registry.new_tournament_id()
    registry.put(
        tournament_id,
        {
            'session': session,
            'owner_id': owner_id,
            'created_at': datetime.utcnow().isoformat(),
            'resolver': resolver,
            'resolver_kind': resolver_kind,
            'game_id': None,
        },
    )
    registry.persist(tournament_id)  # durable from the moment it's registered
    return jsonify({'tournament_id': tournament_id, 'standings': session.standings_view()}), 201


@tournament_bp.route('/api/tournament/<tournament_id>/standings', methods=['GET'])
def get_standings(tournament_id):
    try:
        rec, _owner_id = _owned_record(tournament_id)
    except ValueError:
        return jsonify({'error': 'unauthorized'}), 401
    if rec is None:
        return jsonify({'error': 'not_found'}), 404
    return jsonify(rec['session'].standings_view())


@tournament_bp.route('/api/tournament/<tournament_id>/sit', methods=['POST'])
def sit_tournament(tournament_id):
    """Build (or return) the human's LIVE single-table game for this tournament,
    so they can play it through the normal game UI/action API. The boundary hook
    in game_handler advances the field after each of the human's hands."""
    try:
        rec, owner_id = _owned_record(tournament_id)
    except ValueError:
        return jsonify({'error': 'unauthorized'}), 401
    if rec is None:
        return jsonify({'error': 'not_found'}), 404

    session = rec['session']
    if session.is_complete() or session.human_out:
        return jsonify({'error': 'tournament is not joinable'}), 409

    from flask_app.services import game_state_service

    existing = rec.get('game_id')
    if existing and game_state_service.get_game(existing) is not None:
        return jsonify({'game_id': existing}), 200

    from flask_app.handlers.tournament_game_builder import build_tournament_game

    owner_name = _resolve_player_name()
    with registry.get_lock(tournament_id):
        game_id = build_tournament_game(
            session,
            tournament_id=tournament_id,
            owner_id=owner_id,
            owner_name=owner_name,
            resolver_kind=rec.get('resolver_kind', 'fake'),
        )
        rec['game_id'] = game_id
        registry.persist(tournament_id)  # record the live game_id
    return jsonify({'game_id': game_id}), 201


@tournament_bp.route('/api/tournament/<tournament_id>/advance', methods=['POST'])
def advance(tournament_id):
    """Advance one round. Until the live-game bridge (Phase 2c), the human's hand
    is resolved with the same resolver as the AI tables."""
    try:
        rec, owner_id = _owned_record(tournament_id)
    except ValueError:
        return jsonify({'error': 'unauthorized'}), 401
    if rec is None:
        return jsonify({'error': 'not_found'}), 404

    session: TournamentSession = rec['session']
    with registry.get_lock(tournament_id):
        remaining_before = session.field.active_count
        level_before = session.current_level().level
        reports: list = []
        if not session.is_complete():
            if session.human_out:
                reports = session.play_out()
            else:
                reports = [session.play_round(rec['resolver'].resolve)]
        standings = session.standings_view()
        beats = _beats_for(session, reports, remaining_before, level_before)
        registry.persist(tournament_id)
    _emit_update(owner_id, tournament_id, standings, beats)
    return jsonify(standings)


@tournament_bp.route('/api/tournament/<tournament_id>/play-out', methods=['POST'])
def play_out(tournament_id):
    """Fast-forward the whole field to completion (auto-playing remaining hands).
    Useful for spectating after a bust, or demoing the final standings."""
    try:
        rec, owner_id = _owned_record(tournament_id)
    except ValueError:
        return jsonify({'error': 'unauthorized'}), 401
    if rec is None:
        return jsonify({'error': 'not_found'}), 404

    session: TournamentSession = rec['session']
    with registry.get_lock(tournament_id):
        remaining_before = session.field.active_count
        level_before = session.current_level().level
        reports = session.play_out()
        standings = session.standings_view()
        beats = _beats_for(session, reports, remaining_before, level_before)
        registry.persist(tournament_id)
    _emit_update(owner_id, tournament_id, standings, beats)
    return jsonify(standings)


@tournament_bp.route('/api/tournament/<tournament_id>', methods=['DELETE'])
def leave_tournament(tournament_id):
    try:
        rec, _owner_id = _owned_record(tournament_id)
    except ValueError:
        return jsonify({'error': 'unauthorized'}), 401
    if rec is None:
        return jsonify({'error': 'not_found'}), 404
    registry.delete(tournament_id)
    return jsonify({'ok': True})
