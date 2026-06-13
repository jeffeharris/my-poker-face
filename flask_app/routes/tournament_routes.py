"""REST routes for multi-table tournaments (Phase 2a — API against the
TournamentSession contract).

These expose the tournament meta-layer: accept/decline invites, read field-wide
standings, and advance/fast-forward the world. (Human tournaments are created via
the invite → `spawn_human_tournament` path, which builds a real-persona field;
the old `/register` route that minted a synthetic `P01..` field with the human as
`P01` has been removed.) The actual single-table poker
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

from flask import Blueprint, jsonify

from flask_app import config
from flask_app.extensions import limiter
from flask_app.services import tournament_registry as registry
from flask_app.services.tournament_naming import named_standings
from poker.authorization import require_permission
from tournament.beats import build_beats, level_up_beat
from tournament.session import TournamentSession, paid_places_for

logger = logging.getLogger(__name__)

tournament_bp = Blueprint('tournament', __name__)


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
            prestige_snapshots_repo,
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
                # Phase D: grant renown to in-the-money finishers (flag-gated).
                prestige_repo=prestige_snapshots_repo,
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
        # An AUTONOMOUS tournament (declined/expired → AI-only, no `human:<owner>`
        # seat) is not joinable — the human isn't in it. Surfacing it here drives
        # a "Resume Main Event" bar whose /sit 409s ("autonomous tournament is not
        # joinable"). Only a tournament the human is actually seated in counts as
        # their resumable active one. Mirrors the `_is_autonomous_record` guard the
        # /sit, /advance, and /play-out routes already apply.
        if rec is not None and not _is_autonomous_record(rec, owner_id):
            active = {
                'tournament_id': active_tid,
                'created_at': rec['created_at'],
                'standings': named_standings(rec['session']),
            }
    return jsonify(
        {
            'has_active': active is not None,
            'active': active,
            'defaults': {'field_size': 18, 'table_size': 6, 'starting_stack': 10_000},
        }
    )


@tournament_bp.route('/api/tournament/circuit-history', methods=['GET'])
@limiter.limit(config.RATE_LIMIT_POLLING)
def get_circuit_history():
    """The Champions Roll — completed circuit Main Events for the owner, newest
    first, with the winning persona resolved for display. Includes events the
    player declined/expired: the field crowns a champion whether or not they sat
    down (the circuit runs without you). The human seat renders as "You"."""
    try:
        owner_id = _resolve_owner_id()
    except ValueError:
        return jsonify({'error': 'unauthorized'}), 401

    from flask_app import extensions
    from tournament.identity import resolve_display_name

    session_repo = getattr(extensions, 'tournament_session_repo', None)
    personality_repo = getattr(extensions, 'personality_repo', None)
    if session_repo is None:
        return jsonify({'events': []})

    def _winner_label(pid: str | None) -> str | None:
        if not pid:
            return None
        if pid.startswith('human:'):  # the owner's own seat
            return 'You'
        return resolve_display_name(pid, personality_repo=personality_repo)

    rows = session_repo.list_circuit_history_for_owner(owner_id, limit=25)
    events = [
        {
            'tournament_id': r['tournament_id'],
            'winner_name': _winner_label(r['winner_pid']),
            'field_size': r['field_size'],
            'buy_in': r['buy_in'],
            'prize_pool': r['prize_pool'],
            'completed_at': r['completed_at'],
            'played': r['played'],
        }
        for r in rows
    ]
    return jsonify({'events': events})


@tournament_bp.route('/api/tournament/<tournament_id>/standings', methods=['GET'])
def get_standings(tournament_id):
    try:
        rec, _owner_id = _owned_record(tournament_id)
    except ValueError:
        return jsonify({'error': 'unauthorized'}), 401
    if rec is None:
        return jsonify({'error': 'not_found'}), 404
    return jsonify(named_standings(rec['session']))


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
        prestige_repo=getattr(extensions, 'prestige_snapshots_repo', None),
    )


def _invite_view(
    invite: dict | None,
    *,
    prize_pool: int | None = None,
    payouts: list | None = None,
    renown_enabled: bool = False,
) -> dict | None:
    """Trim the invite row to the lobby-card payload.

    `prize_pool`/`payouts`/`renown_enabled` are the (estimated) economy preview for
    the registration card (#2) — computed by the caller from the live bank state,
    so the purse is an ESTIMATE (the actual pool is fixed at register/accept time
    and can drift with the bank). Omitted when not supplied."""
    if invite is None:
        return None
    view = {
        'invite_id': invite['invite_id'],
        'status': invite['status'],
        'buy_in': invite['buy_in'],
        'field_size': invite['field_size'],
        'table_size': invite['table_size'],
        'starting_stack': invite['starting_stack'],
        'expires_at': invite['expires_at'],
    }
    if prize_pool is not None:
        view['prize_pool_estimate'] = int(prize_pool)
        view['payouts'] = payouts or []
        view['renown_enabled'] = bool(renown_enabled)
    return view


def _invite_economy_preview(invite: dict | None, *, ledger_repo, sandbox_id: str):
    """(prize_pool_estimate, payouts, renown_enabled) for the registration card,
    or (None, None, False) when there's no invite. Best-effort: any failure
    degrades to no preview rather than 500-ing the lobby. The purse is an estimate
    — `plan_funding` reads the CURRENT bank, the actual pool is set at accept."""
    if invite is None:
        return None, None, False
    try:
        from cash_mode import economy_flags
        from flask_app.services import tournament_economy_service as econ
        from flask_app.services.tournament_renown import payout_breakdown

        plan = econ.plan_funding(
            ledger_repo=ledger_repo,
            sandbox_id=sandbox_id,
            field_size=invite['field_size'],
            buy_in=invite['buy_in'],
            human_in=True,
        )
        prize_pool = int(plan.prize_pool)
        renown_enabled = bool(economy_flags.TOURNAMENT_DRAW_ENABLED)
        payouts = payout_breakdown(invite['field_size'], prize_pool, with_renown=renown_enabled)
        return prize_pool, payouts, renown_enabled
    except Exception:  # noqa: BLE001 — preview only; never break the lobby card
        logger.exception("invite economy preview failed (sandbox=%s)", sandbox_id)
        return None, None, False


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
                draw_ctx=invites.draw_context(
                    personality_repo=repos['personality_repo'],
                    bankroll_repo=repos['bankroll_repo'],
                    prestige_repo=repos['prestige_repo'],
                    cash_table_repo=repos['cash_table_repo'],
                    ledger_repo=repos['ledger_repo'],
                ),
            )
        except Exception:  # noqa: BLE001 — surfacing is best-effort; never 500 the lobby
            logger.exception("invite offer/expire sweep failed for %s", owner_id)
        invite = invites.active_invite(repos['invite_repo'], owner_id)
    prize_pool, payouts, renown_enabled = _invite_economy_preview(
        invite, ledger_repo=repos['ledger_repo'], sandbox_id=sandbox_id
    )
    return jsonify(
        {
            'invite': _invite_view(
                invite,
                prize_pool=prize_pool,
                payouts=payouts,
                renown_enabled=renown_enabled,
            )
        }
    )


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
                {
                    'error': 'insufficient_funds',
                    'required': exc.required,
                    'available': exc.available,
                }
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
            'standings': named_standings(registry.get(result['tournament_id'])['session'])
            if registry.get(result['tournament_id'])
            else None,
        }
    ), 201


@tournament_bp.route('/api/tournament/spawn', methods=['POST'])
def spawn_tournament():
    """Spawn an on-demand, fully-ISOLATED exhibition ("decoupled") tournament —
    the Tournaments-menu "Main Event" button.

    Modeled on `accept_invite` but WITHOUT an invite: a free-buy-in, real-persona
    field at baseline mood with NO wires to the persistent world (no money,
    persona-mood carry, renown, or escrow). It is exempt from the one-active-per-
    owner guard and never shadows/blocks the cash-circuit Main Event invite.
    Results still count in the shared tournament career stats.

    Body (all optional): field_size, table_size, starting_stack. Returns
    {tournament_id, standings} 201; 409 if the persona pool can't field MIN_FIELD;
    400 on out-of-bounds sizes.
    """
    from flask import request

    try:
        owner_id = _resolve_owner_id()
    except ValueError:
        return jsonify({'error': 'unauthorized'}), 401

    body = request.get_json(silent=True) or {}

    # Validate sizes against sane bounds (defaults mirror the lobby card).
    def _int(value, default, lo, hi):
        try:
            n = int(value)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, n))

    field_size = _int(body.get('field_size'), 18, 2, 100)
    table_size = _int(body.get('table_size'), 6, 2, 10)
    starting_stack = _int(body.get('starting_stack'), 10_000, 500, 10_000_000)
    if table_size > field_size:
        table_size = field_size

    import time

    from flask_app.services import game_state_service, tournament_economy_service as econ
    from flask_app.services.tournament_spawn import DraftScanError, create_human_tournament

    # Human side of the double-presence guard: leave any cash seat first (the
    # same as accept), so the player is in exactly one place.
    _leave_cash_if_seated(owner_id)

    repos = _economy_repos()
    sandbox_id = _resolve_sandbox_id(owner_id)
    seed = int(time.time())
    with game_state_service.get_sandbox_lock(sandbox_id):
        try:
            built = create_human_tournament(
                owner_id=owner_id,
                sandbox_id=sandbox_id,
                personality_repo=repos['personality_repo'],
                bankroll_repo=repos['bankroll_repo'],
                ledger_repo=repos['ledger_repo'],
                session_repo=repos['session_repo'],
                cash_table_repo=repos['cash_table_repo'],
                buy_in=0,
                field_size=field_size,
                table_size=table_size,
                starting_stack=starting_stack,
                seed=seed,
                rng_seed=seed,
                decoupled=True,
            )
        except DraftScanError:
            # Fail-closed exclusion scan (it already logged) — treat as "can't
            # field right now" rather than 500-ing the menu.
            return jsonify({'error': 'cannot_field_tournament'}), 409
        except econ.InsufficientFundsError:
            # Should never happen on a free buy-in, but never 500 the menu.
            return jsonify({'error': 'cannot_field_tournament'}), 409
    if built is None:
        # The persona pool was smaller than MIN_FIELD (or every persona excluded).
        return jsonify({'error': 'cannot_field_tournament'}), 409

    tournament_id = built['tournament_id']
    rec = registry.get(tournament_id)
    return jsonify(
        {
            'tournament_id': tournament_id,
            'standings': named_standings(rec['session']) if rec else None,
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
        standings = named_standings(session)
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
        standings = named_standings(session)
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
    personality_repo = getattr(extensions, 'personality_repo', None)
    sandbox_repo = getattr(extensions, 'sandbox_repo', None)
    if session_repo is None or ledger_repo is None or bankroll_repo is None:
        return jsonify({'error': 'economy not wired'}), 503

    results = tournament_ticker.reconcile_stuck_payouts(
        session_repo=session_repo,
        ledger_repo=ledger_repo,
        bankroll_repo=bankroll_repo,
        personality_repo=personality_repo,
        registry=tournament_registry,
        resolve_sandbox=lambda owner: resolve_default_sandbox_for(owner, sandbox_repo=sandbox_repo),
        get_lock=game_state_service.get_sandbox_lock,
        # No grace window on the manual path — the operator is explicitly asking.
        older_than_iso=None,
    )
    return jsonify(
        {
            'reconciled': sum(1 for r in results if r.get('reconciled')),
            'total_stuck': len(results),
            'results': results,
        }
    )


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
