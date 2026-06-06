"""Admin chip-ledger routes — audit + recent entries.

Wraps `flask_app.services.chip_ledger_audit.compute_audit` behind
the same `can_access_admin_tools` permission that gates the rest of
the admin surface. v0 is read-only; the ledger is append-only and
there's nothing to mutate from the API.

Spec: docs/plans/CASH_MODE_CHIP_LEDGER_HANDOFF.md §"Audit endpoint".
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from poker.authorization import require_permission

from .. import extensions
from ..services import game_state_service
from ..services.chip_ledger_audit import compute_audit
from ..services.holdings_view import (
    compute_holdings_history,
    compute_holdings_snapshot,
    record_holdings_snapshot,
)

logger = logging.getLogger(__name__)

chip_ledger_bp = Blueprint('chip_ledger', __name__)

_admin_required = require_permission('can_access_admin_tools')


def _sandbox_arg() -> str | None:
    """Read the optional `sandbox_id` query param.

    Empty string → None (the admin / cross-sandbox view). Treating
    `?sandbox_id=` as cross-sandbox keeps the frontend simple: the
    "All sandboxes" dropdown option can submit an empty value.
    """
    raw = request.args.get('sandbox_id')
    if raw is None or raw == '':
        return None
    return raw


@chip_ledger_bp.route('/api/admin/chip-ledger/audit')
@_admin_required
def chip_ledger_audit():
    """Return the v0 audit payload — ledger view, actual view, drift.

    `?sandbox_id=<uuid>` scopes per-sandbox AI runtime aggregates;
    cross-cutting surfaces (player_bankrolls, active_loans_principal,
    live_session_ai_stacks) stay global by design — see
    `compute_audit`'s docstring.
    """
    try:
        sandbox_id = _sandbox_arg()
        data = compute_audit(
            ledger_repo=extensions.chip_ledger_repo,
            bankroll_repo=extensions.bankroll_repo,
            cash_table_repo=extensions.cash_table_repo,
            stake_repo=extensions.stake_repo,
            db_path=extensions.persistence_db_path,
            list_game_ids_fn=game_state_service.list_game_ids,
            get_game_fn=game_state_service.get_game,
            sandbox_id=sandbox_id,
        )
        # World-tick count (economy maturity) for the selected sandbox, or
        # summed across all when unscoped. Cheap KV read; never fails the audit.
        try:
            from flask_app.services.ticker_service import world_tick_count

            data['world_ticks'] = world_tick_count(sandbox_id)
        except Exception:  # noqa: BLE001 — telemetry, not load-bearing
            data['world_ticks'] = None
        return jsonify(data)
    except Exception as e:
        logger.error("chip-ledger audit failed: %s", e, exc_info=True)
        return jsonify({'error': 'Audit computation failed'}), 500


@chip_ledger_bp.route('/api/admin/chip-ledger/recent')
@_admin_required
def chip_ledger_recent():
    """Return the most recent ledger entries (default limit=100).

    Useful for spot-checking the audit numbers — see what events
    landed and in what order. Honors a `limit` query param,
    clamped to [1, 500], and an optional `sandbox_id` scope.
    """
    try:
        limit = int(request.args.get('limit', 100))
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(500, limit))

    try:
        entries = extensions.chip_ledger_repo.recent_entries(
            limit=limit,
            sandbox_id=_sandbox_arg(),
        )
        return jsonify({'entries': entries})
    except Exception as e:
        logger.error("chip-ledger recent failed: %s", e, exc_info=True)
        return jsonify({'error': 'Recent-entries lookup failed'}), 500


@chip_ledger_bp.route('/api/admin/chip-ledger/holdings')
@_admin_required
def chip_ledger_holdings():
    """Return the per-entity net-worth table for the admin "Holdings" section.

    Lists every AI personality in scope and every human player with a
    bankroll row. When `?sandbox_id=` selects a sandbox, each row carries
    net worth (chips + stakes receivable − stakes outstanding) plus vice
    spent / side-hustle earned. In the cross-sandbox "All sandboxes" view
    net worth is omitted (chips only) — stakes are global per entity, so
    attributing them across per-sandbox chip rows isn't meaningful.
    """
    try:
        data = compute_holdings_snapshot(
            bankroll_repo=extensions.bankroll_repo,
            personality_repo=extensions.personality_repo,
            user_repo=extensions.user_repo,
            stake_repo=extensions.stake_repo,
            cash_table_repo=extensions.cash_table_repo,
            db_path=extensions.persistence_db_path,
            sandbox_id=_sandbox_arg(),
        )
        return jsonify(data)
    except Exception as e:
        logger.error("chip-ledger holdings failed: %s", e, exc_info=True)
        return jsonify({'error': 'Holdings snapshot failed'}), 500


@chip_ledger_bp.route('/api/admin/chip-ledger/lifecycle')
@_admin_required
def chip_ledger_lifecycle():
    """Session-lifecycle telemetry for the admin Chip Economy tab (Tier 4.3).

    Aggregates the `cash_session_events` stream (Tier 3.3) over a window
    plus the current `session_state` distribution, so an operator can see
    at a glance: how many sessions started / left cleanly / were swept as
    orphans, and whether any `broken` sessions are outstanding (cleanup
    that couldn't converge — the wedge class this whole plan targets).

    `?window_hours=<int>` (default 24, clamped [1, 720]) bounds the event
    counts; `?sandbox_id=` scopes both event + state counts.
    """
    from datetime import datetime, timedelta

    try:
        window_hours = int(request.args.get('window_hours', 24))
    except (TypeError, ValueError):
        window_hours = 24
    window_hours = max(1, min(720, window_hours))
    since = datetime.utcnow() - timedelta(hours=window_hours)
    sandbox_id = _sandbox_arg()

    try:
        events = extensions.cash_session_repo.event_counts(since=since, sandbox_id=sandbox_id)
        states = extensions.cash_session_repo.state_counts(sandbox_id=sandbox_id)
        return jsonify(
            {
                'window_hours': window_hours,
                'events': events,
                'states': states,
                # Convenience headline: cleanup that couldn't converge.
                'outstanding_broken': int(states.get('broken', 0)),
            }
        )
    except Exception as e:
        logger.error("chip-ledger lifecycle failed: %s", e, exc_info=True)
        return jsonify({'error': 'Lifecycle stats failed'}), 500


@chip_ledger_bp.route('/api/admin/chip-ledger/holdings/history')
@_admin_required
def chip_ledger_holdings_history():
    """Return per-entity net worth over time for the selected sandbox.

    Drives the time-series chart in the Holdings section, read from the
    `holdings_snapshots` the world ticker records. Net worth requires a
    sandbox (`requires_sandbox=true` and an empty series otherwise). On
    first view of a sandbox with no snapshots yet, seed one so the chart
    isn't blank. `?days=N` clamps to [1, 365], default 30.
    """
    try:
        days = int(request.args.get('days', 30))
    except (TypeError, ValueError):
        days = 30
    sandbox_id = _sandbox_arg()
    try:
        # First-view seed: if a sandbox is selected but nothing has been
        # recorded yet (ticker hasn't fired, or fresh table), capture one
        # point now so the curve has something to draw.
        if sandbox_id is not None and extensions.holdings_snapshots_repo is not None:
            if extensions.holdings_snapshots_repo.latest_captured_at(sandbox_id) is None:
                record_holdings_snapshot(
                    snapshots_repo=extensions.holdings_snapshots_repo,
                    bankroll_repo=extensions.bankroll_repo,
                    personality_repo=extensions.personality_repo,
                    user_repo=extensions.user_repo,
                    stake_repo=extensions.stake_repo,
                    cash_table_repo=extensions.cash_table_repo,
                    db_path=extensions.persistence_db_path,
                    sandbox_id=sandbox_id,
                )
        data = compute_holdings_history(
            snapshots_repo=extensions.holdings_snapshots_repo,
            personality_repo=extensions.personality_repo,
            user_repo=extensions.user_repo,
            days=days,
            sandbox_id=sandbox_id,
        )
        return jsonify(data)
    except Exception as e:
        logger.error("chip-ledger holdings history failed: %s", e, exc_info=True)
        return jsonify({'error': 'Holdings history failed'}), 500


@chip_ledger_bp.route('/api/admin/sandboxes')
@_admin_required
def list_sandboxes():
    """List all (live) sandboxes for the admin chip-ledger dropdown.

    Returns `{'sandboxes': [{sandbox_id, owner_id, name, created_at}, ...]}`.
    Archived sandboxes are excluded by default — admins driving the
    chip-ledger view want live save-files, not history.
    """
    try:
        sandboxes = extensions.sandbox_repo.list_all()
        # Order by freshest net-worth snapshot, then newest — so the admin
        # panel can default to a sandbox that actually has a chart (the one
        # the ticker is actively recording) rather than a dormant/empty one.
        latest: dict = {}
        if extensions.holdings_snapshots_repo is not None:
            for s in sandboxes:
                try:
                    latest[s.sandbox_id] = (
                        extensions.holdings_snapshots_repo.latest_captured_at(s.sandbox_id) or ''
                    )
                except Exception:
                    latest[s.sandbox_id] = ''
        sandboxes = sorted(
            sandboxes,
            key=lambda s: (latest.get(s.sandbox_id, ''), s.created_at.isoformat()),
            reverse=True,
        )
        return jsonify(
            {
                'sandboxes': [
                    {
                        'sandbox_id': s.sandbox_id,
                        'owner_id': s.owner_id,
                        'name': s.name,
                        'created_at': s.created_at.isoformat(),
                    }
                    for s in sandboxes
                ],
            }
        )
    except Exception as e:
        logger.error("admin sandbox list failed: %s", e, exc_info=True)
        return jsonify({'error': 'Sandbox list failed'}), 500


@chip_ledger_bp.route('/api/admin/cash/whereabouts')
@_admin_required
def cash_whereabouts():
    """Unfiltered world-state + stuck tripwire for the admin panel.

    Lists every AI that's somewhere trackable — seated, idle, on a side
    hustle, or on a vice — with its location, timing, and any invariant
    `stuck` flags (double-seat, seated-and-idle split-brain, overdue
    return, stale idle, orphan). This is the live debug surface for the
    ghost-seat / cold-load bug classes that keep recurring.

    `?sandbox_id=<uuid>` scopes to one save; `?sandbox_id=` (empty, the
    "All sandboxes" dropdown option) scans every live sandbox and tags
    each person with their sandbox so a bug in any world surfaces here.
    Unlike the player route this keeps `stuck` and does NOT filter to
    "met" — admins need the whole picture.
    """
    from datetime import datetime

    from cash_mode.whereabouts import build_whereabouts

    def _for_sandbox(sb_id: str, owner_id: str) -> list:
        data = build_whereabouts(
            sandbox_id=sb_id,
            owner_id=owner_id,
            now=datetime.utcnow(),
            cash_table_repo=extensions.cash_table_repo,
            side_hustle_repo=extensions.side_hustle_state_repo,
            vice_repo=extensions.vice_state_repo,
            relationship_repo=extensions.relationship_repo,
            bankroll_repo=extensions.bankroll_repo,
            personality_repo=extensions.personality_repo,
            tournament_session_repo=getattr(extensions, 'tournament_session_repo', None),
            tournament_invite_repo=getattr(extensions, 'tournament_invite_repo', None),
        )
        people = data['people']
        for person in people:
            person['sandbox_id'] = sb_id
            person['sandbox_owner_id'] = owner_id
        return people

    try:
        sandbox_id = _sandbox_arg()
        if sandbox_id is not None:
            # Single sandbox. Met-stats are computed from the sandbox
            # owner's POV so the "met"/PnL annotation is meaningful.
            owner_id = ''
            try:
                sb = extensions.sandbox_repo.load(sandbox_id)
                owner_id = sb.owner_id if sb is not None else ''
            except Exception:
                owner_id = ''
            people = _for_sandbox(sandbox_id, owner_id)
        else:
            # All live sandboxes — a cross-world stuck scan.
            people = []
            for sb in extensions.sandbox_repo.list_all():
                people.extend(_for_sandbox(sb.sandbox_id, sb.owner_id))
            # Re-sort the merged list: stuck first, then by sandbox.
            people.sort(
                key=lambda r: (
                    0 if r.get('stuck') else 1,
                    r.get('sandbox_id') or '',
                    (r.get('name') or '').lower(),
                )
            )

        stuck_count = sum(1 for p in people if p.get('stuck'))
        return jsonify(
            {
                'people': people,
                'stuck_count': stuck_count,
                'total': len(people),
                'sandbox_id': sandbox_id,
            }
        )
    except Exception as e:
        logger.error("cash whereabouts failed: %s", e, exc_info=True)
        return jsonify({'error': 'Whereabouts computation failed'}), 500
