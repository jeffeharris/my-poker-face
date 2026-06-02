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

from flask_app import config
from flask_app.extensions import limiter
from flask_app.services import tournament_registry as registry
from poker.authorization import require_permission
from tournament.beats import build_beats, level_up_beat
from tournament.config import DEFAULT_FIELD_ARCHETYPES, TournamentConfig
from tournament.director import FakeHandResolver, build_initial_state
from tournament.session import TournamentSession, paid_places_for

logger = logging.getLogger(__name__)

tournament_bp = Blueprint('tournament', __name__)

MAX_FIELD_SIZE = 200
MAX_TABLE_SIZE = 10
MAX_BUY_IN = 1_000_000  # sanity ceiling on a per-seat buy-in


def _resolve_sandbox_id(owner_id: str) -> str:
    """The owner's default sandbox — the real-chip economy is sandbox-scoped
    (same resolver cash mode uses)."""
    from flask_app.extensions import sandbox_repo
    from flask_app.services.sandbox_resolver import resolve_default_sandbox_for

    return resolve_default_sandbox_for(owner_id, sandbox_repo=sandbox_repo)


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


def _maybe_payout(rec: dict, owner_id: str, tournament_id: str) -> None:
    """If the field has just completed, distribute the prize pool. Idempotent
    (the payout_status guard), so calling it from every completion path —
    advance, play-out, and the live boundary — is safe. Best-effort: a payout
    failure must never break the standings response."""
    session = rec.get('session')
    if session is None or not session.is_complete():
        return
    try:
        from flask_app.extensions import (
            bankroll_repo,
            chip_ledger_repo,
            personality_repo,
            tournament_session_repo,
        )
        from flask_app.services import game_state_service, tournament_economy_service as econ

        sandbox_id = _resolve_sandbox_id(owner_id)
        with game_state_service.get_sandbox_lock(sandbox_id):
            econ.apply_payout_on_complete(
                tournament_id=tournament_id,
                session=session,
                human_owner_id=owner_id,
                sandbox_id=sandbox_id,
                bankroll_repo=bankroll_repo,
                ledger_repo=chip_ledger_repo,
                session_repo=tournament_session_repo,
                # Real personas in the field are credited; synthetic ids sweep to
                # the pool. Without this, a human-played persona field never pays
                # its AIs (the redistribution silently no-ops).
                real_persona_ids=econ.real_persona_ids_for(session, personality_repo),
            )
    except Exception:  # noqa: BLE001 — payout is best-effort observability here
        logger.exception("tournament payout failed for %s", tournament_id)


def _owned_record(tournament_id: str):
    """Return (record, owner_id) if the current user owns the tournament, else
    (None, owner_id). Raises ValueError if no user."""
    owner_id = _resolve_owner_id()
    rec = registry.get(tournament_id)
    if rec is None or rec.get('owner_id') != owner_id:
        return None, owner_id
    return rec, owner_id


def _is_autonomous_record(rec: dict, owner_id: str) -> bool:
    """True if this owned tournament is an AUTONOMOUS (declined/expired, AI-only)
    event. Such tournaments are advanced by the world ticker under the SANDBOX
    lock; a route mutating the same in-memory session under the per-tournament
    lock would race it (a double-settle window past the payout guard) and a
    route-driven settle would misattribute the nominal persona's prize to the
    human bankroll. So the play/sit routes reject them (409). The discriminator is
    the field: an autonomous one has no `human:<owner>` seat."""
    from flask_app.services.tournament_ticker import is_autonomous

    session = rec.get('session')
    return session is not None and is_autonomous(session, owner_id)


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
        buy_in = int(body.get('buy_in', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'field_size/table_size/starting_stack/seed/buy_in must be integers'}), 400

    if not 2 <= field_size <= MAX_FIELD_SIZE:
        return jsonify({'error': f'field_size must be between 2 and {MAX_FIELD_SIZE}'}), 400
    if not 2 <= table_size <= MAX_TABLE_SIZE:
        return jsonify({'error': f'table_size must be between 2 and {MAX_TABLE_SIZE}'}), 400
    if starting_stack < 1:
        return jsonify({'error': 'starting_stack must be >= 1'}), 400
    if not 0 <= buy_in <= MAX_BUY_IN:
        return jsonify({'error': f'buy_in must be between 0 and {MAX_BUY_IN}'}), 400

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

    # --- Real-chip economy (escrow-in) ---------------------------------------
    # Read the economy signal, decide the funding plan, gate affordability, then
    # debit + earmark at the escrow — all under the sandbox lock so the snapshot
    # the plan was computed from is still current when the transfers apply.
    from flask_app.extensions import bankroll_repo, chip_ledger_repo, tournament_session_repo
    from flask_app.services import game_state_service, tournament_economy_service as econ

    sandbox_id = _resolve_sandbox_id(owner_id)
    with game_state_service.get_sandbox_lock(sandbox_id):
        # Re-check under the lock: the pre-lock guard (above) is a fast path, but
        # two concurrent registers can both pass it before either registers. The
        # authoritative check must be inside the lock or both debit the buy-in.
        existing = registry.find_active_for_owner(owner_id)
        if existing:
            return jsonify({'error': 'already_registered', 'tournament_id': existing}), 409
        plan = econ.plan_funding(
            ledger_repo=chip_ledger_repo,
            sandbox_id=sandbox_id,
            field_size=field_size,
            buy_in=buy_in,
            human_in=True,  # registering through this route IS opting in
        )
        # Affordability gate BEFORE creating the tournament (no rollback needed).
        if plan.human_buy_in > 0:
            from flask_app.routes.cash_routes import _load_or_seed_player_bankroll

            bankroll = _load_or_seed_player_bankroll(owner_id, sandbox_id=sandbox_id)
            if bankroll.chips < plan.human_buy_in:
                return jsonify(
                    {
                        'error': 'insufficient_funds',
                        'required': plan.human_buy_in,
                        'available': bankroll.chips,
                    }
                ), 402

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

        try:
            econ.apply_buy_in(
                tournament_id=tournament_id,
                owner_id=owner_id,
                sandbox_id=sandbox_id,
                plan=plan,
                bankroll_repo=bankroll_repo,
                ledger_repo=chip_ledger_repo,
                session_repo=tournament_session_repo,
            )
        except econ.InsufficientFundsError as exc:
            registry.delete(tournament_id)
            return jsonify(
                {'error': 'insufficient_funds', 'required': exc.required, 'available': exc.available}
            ), 402
        except Exception:  # noqa: BLE001 — undo registration on a hard chip failure
            logger.exception("tournament buy-in failed for %s; rolling back", tournament_id)
            registry.delete(tournament_id)
            return jsonify({'error': 'buy_in_failed'}), 500

    return jsonify(
        {
            'tournament_id': tournament_id,
            'standings': session.standings_view(),
            'economy': {
                'buy_in': plan.human_buy_in,
                'bank_overlay': plan.bank_overlay,
                'rake': plan.rake,
                'prize_pool': plan.prize_pool,
                'regime': plan.regime,
            },
        }
    ), 201


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
    if _is_autonomous_record(rec, owner_id):
        return jsonify({'error': 'autonomous tournament is not joinable'}), 409

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


def _economy_repos():
    """The repos the invite lifecycle + economy need, pulled from extensions."""
    from flask_app import extensions

    return dict(
        invite_repo=extensions.tournament_invite_repo,
        session_repo=extensions.tournament_session_repo,
        ledger_repo=extensions.chip_ledger_repo,
        bankroll_repo=extensions.bankroll_repo,
        personality_repo=extensions.personality_repo,
        cash_table_repo=extensions.cash_table_repo,
    )


def _invite_view(invite: dict | None) -> dict | None:
    """Trim the invite row to the lobby-card payload."""
    if invite is None:
        return None
    return {
        'invite_id': invite['invite_id'],
        'status': invite['status'],
        'buy_in': invite['buy_in'],
        'field_size': invite['field_size'],
        'table_size': invite['table_size'],
        'starting_stack': invite['starting_stack'],
        'expires_at': invite['expires_at'],
    }


@tournament_bp.route('/api/tournament/invite', methods=['GET'])
@limiter.limit(config.RATE_LIMIT_POLLING)
def get_invite():
    """The owner's open Main Event invite (the lobby card). Opportunistically
    lets the chairman offer one (FLUSH + cooldown) and sweeps expired invites to
    autonomous play, so the offer surfaces/expires on lobby load without a
    background scheduler."""
    try:
        owner_id = _resolve_owner_id()
    except ValueError:
        return jsonify({'error': 'unauthorized'}), 401
    from flask_app.services import game_state_service, tournament_invites as invites

    repos = _economy_repos()
    sandbox_id = _resolve_sandbox_id(owner_id)
    with game_state_service.get_sandbox_lock(sandbox_id):
        try:
            invites.expire_due(
                invite_repo=repos['invite_repo'],
                personality_repo=repos['personality_repo'],
                bankroll_repo=repos['bankroll_repo'],
                ledger_repo=repos['ledger_repo'],
                session_repo=repos['session_repo'],
                cash_table_repo=repos['cash_table_repo'],
                sandbox_id=sandbox_id,  # only sweep this sandbox (the lock we hold)
            )
            invites.maybe_offer_main_event(
                invite_repo=repos['invite_repo'],
                session_repo=repos['session_repo'],
                ledger_repo=repos['ledger_repo'],
                owner_id=owner_id,
                sandbox_id=sandbox_id,
            )
        except Exception:  # noqa: BLE001 — surfacing is best-effort; never 500 the lobby
            logger.exception("invite offer/expire sweep failed for %s", owner_id)
        invite = invites.active_invite(repos['invite_repo'], owner_id)
    return jsonify({'invite': _invite_view(invite)})


def _leave_cash_if_seated(owner_id: str) -> bool:
    """Stand the human up from any active cash game before they enter a
    tournament — they can't be at a cash table AND in the Main Event at once
    (the human side of the double-presence guard). Cashes out (chips → bankroll,
    via the stake-aware leave path) and frees the seat. Returns True if a leave
    ran. Best-effort; mirrors POST /api/cash/leave's game-lock pattern."""
    from flask_app.routes.cash_routes import _find_active_cash_game_id, _leave_table_locked
    from flask_app.services import game_state_service

    game_id = _find_active_cash_game_id(owner_id)
    if not game_id:
        return False
    pending = game_state_service.get_game(game_id)
    if pending is not None:
        pending['leave_requested'] = True  # cooperative-cancel an in-flight orbit
    with game_state_service.get_game_lock(game_id):
        try:
            _leave_table_locked(owner_id, game_id)  # returns a Response; side effects matter
        except Exception:  # noqa: BLE001 — never block tournament entry on a leave hiccup
            logger.exception("cash leave before tournament accept failed for %s", owner_id)
    return True


@tournament_bp.route('/api/tournament/invite/accept', methods=['POST'])
def accept_invite():
    """Accept the open invite → build the tournament the human plays IN. Returns
    the tournament_id (client then POSTs /sit for the live table). The human is
    first stood up from any cash table (you can't be at cash AND in the Main
    Event) — but only once we've confirmed there's an invite to accept, so a
    misfire never cashes them out for nothing."""
    try:
        owner_id = _resolve_owner_id()
    except ValueError:
        return jsonify({'error': 'unauthorized'}), 401
    from flask_app.services import (
        game_state_service,
        tournament_economy_service as econ,
        tournament_invites as invites,
    )

    repos = _economy_repos()
    # Gate on an open invite BEFORE leaving cash, so a no-op accept doesn't
    # stand the player up for nothing.
    if invites.active_invite(repos['invite_repo'], owner_id) is None:
        return jsonify({'error': 'no_open_invite'}), 404

    # Human side of the double-presence guard: leave the cash seat first, so the
    # cashed-out chips are in bankroll (available toward any buy-in) and the
    # player is in exactly one place.
    _leave_cash_if_seated(owner_id)

    sandbox_id = _resolve_sandbox_id(owner_id)
    with game_state_service.get_sandbox_lock(sandbox_id):
        try:
            result = invites.accept(
                invite_repo=repos['invite_repo'],
                personality_repo=repos['personality_repo'],
                bankroll_repo=repos['bankroll_repo'],
                ledger_repo=repos['ledger_repo'],
                session_repo=repos['session_repo'],
                cash_table_repo=repos['cash_table_repo'],
                owner_id=owner_id,
            )
        except econ.InsufficientFundsError as exc:
            return jsonify(
                {'error': 'insufficient_funds', 'required': exc.required, 'available': exc.available}
            ), 402
        except invites.CannotFieldTournamentError as exc:
            # The invite is still open; the field just couldn't be drafted (e.g.
            # no circulating personas in this sandbox). 409, not a 404 not-found.
            return jsonify({'error': 'cannot_field_tournament', 'message': str(exc)}), 409
    if result is None:
        return jsonify({'error': 'no_open_invite'}), 404
    return jsonify(
        {
            'tournament_id': result['tournament_id'],
            'standings': registry.get(result['tournament_id'])['session'].standings_view()
            if registry.get(result['tournament_id'])
            else None,
        }
    ), 201


@tournament_bp.route('/api/tournament/invite/decline', methods=['POST'])
def decline_invite():
    """Decline the open invite → it starts autonomously (AI-only)."""
    try:
        owner_id = _resolve_owner_id()
    except ValueError:
        return jsonify({'error': 'unauthorized'}), 401
    from flask_app.services import game_state_service, tournament_invites as invites

    repos = _economy_repos()
    sandbox_id = _resolve_sandbox_id(owner_id)
    with game_state_service.get_sandbox_lock(sandbox_id):
        result = invites.decline(
            invite_repo=repos['invite_repo'],
            personality_repo=repos['personality_repo'],
            bankroll_repo=repos['bankroll_repo'],
            ledger_repo=repos['ledger_repo'],
            session_repo=repos['session_repo'],
            cash_table_repo=repos['cash_table_repo'],
            owner_id=owner_id,
        )
    if result is None:
        return jsonify({'error': 'no_open_invite'}), 404
    return jsonify({'ok': True, 'tournament_id': result['tournament_id']})


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
    if _is_autonomous_record(rec, owner_id):
        # The world ticker owns autonomous advancement (under the sandbox lock);
        # a route advancing it here would race that + misattribute the prize.
        return jsonify({'error': 'autonomous tournament advances on the world tick'}), 409

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
        _maybe_payout(rec, owner_id, tournament_id)
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
    if _is_autonomous_record(rec, owner_id):
        return jsonify({'error': 'autonomous tournament advances on the world tick'}), 409

    session: TournamentSession = rec['session']
    with registry.get_lock(tournament_id):
        remaining_before = session.field.active_count
        level_before = session.current_level().level
        reports = session.play_out()
        standings = session.standings_view()
        beats = _beats_for(session, reports, remaining_before, level_before)
        registry.persist(tournament_id)
        _maybe_payout(rec, owner_id, tournament_id)
    _emit_update(owner_id, tournament_id, standings, beats)
    return jsonify(standings)


@tournament_bp.route('/api/tournament/admin/reconcile-payouts', methods=['POST'])
@require_permission('can_access_admin_tools')
def reconcile_payouts():
    """Admin: resume every tournament payout wedged at `payout_status=
    'in_progress'` (a crash mid-distribute). On-demand twin of the ticker's
    payout-reconcile watchdog — pays only the unpaid remainder per finisher from
    the ledger (never a double credit), sweeps the escrow to 0, and stamps
    `complete`. Returns the per-tournament outcomes. Not flag-gated: an operator
    must be able to clear a wedged payout even with the circuit flag off."""
    from flask_app import extensions
    from flask_app.services import game_state_service, tournament_registry, tournament_ticker
    from flask_app.services.sandbox_resolver import resolve_default_sandbox_for

    session_repo = getattr(extensions, 'tournament_session_repo', None)
    ledger_repo = getattr(extensions, 'chip_ledger_repo', None)
    bankroll_repo = getattr(extensions, 'bankroll_repo', None)
    sandbox_repo = getattr(extensions, 'sandbox_repo', None)
    if session_repo is None or ledger_repo is None or bankroll_repo is None:
        return jsonify({'error': 'economy not wired'}), 503

    results = tournament_ticker.reconcile_stuck_payouts(
        session_repo=session_repo,
        ledger_repo=ledger_repo,
        bankroll_repo=bankroll_repo,
        registry=tournament_registry,
        resolve_sandbox=lambda owner: resolve_default_sandbox_for(owner, sandbox_repo=sandbox_repo),
        get_lock=game_state_service.get_sandbox_lock,
        # No grace window on the manual path — the operator is explicitly asking.
        older_than_iso=None,
    )
    return jsonify({
        'reconciled': sum(1 for r in results if r.get('reconciled')),
        'total_stuck': len(results),
        'results': results,
    })


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
