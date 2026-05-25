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

from ..extensions import (
    bankroll_repo,
    cash_table_repo,
    chip_ledger_repo,
    persistence_db_path,
    personality_repo,
    relationship_repo,
    sandbox_repo,
    stake_repo,
    user_repo,
)
from ..services import game_state_service
from ..services.chip_ledger_audit import compute_audit
from ..services.holdings_view import (
    compute_holdings_history,
    compute_holdings_snapshot,
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
        data = compute_audit(
            ledger_repo=chip_ledger_repo,
            bankroll_repo=bankroll_repo,
            cash_table_repo=cash_table_repo,
            stake_repo=stake_repo,
            db_path=persistence_db_path,
            list_game_ids_fn=game_state_service.list_game_ids,
            get_game_fn=game_state_service.get_game,
            sandbox_id=_sandbox_arg(),
        )
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
        entries = chip_ledger_repo.recent_entries(
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
    """Return the per-player holdings table for the admin "Holdings" section.

    Lists every AI personality in scope and every human player with a
    bankroll row, with both stored and projected chip counts. AI scope
    honors `?sandbox_id=`; human rows come from the global
    `player_bankroll_state` regardless of sandbox (humans aren't
    sandbox-scoped in v1).
    """
    try:
        data = compute_holdings_snapshot(
            bankroll_repo=bankroll_repo,
            personality_repo=personality_repo,
            user_repo=user_repo,
            relationship_repo=relationship_repo,
            db_path=persistence_db_path,
            sandbox_id=_sandbox_arg(),
        )
        return jsonify(data)
    except Exception as e:
        logger.error("chip-ledger holdings failed: %s", e, exc_info=True)
        return jsonify({'error': 'Holdings snapshot failed'}), 500


@chip_ledger_bp.route('/api/admin/chip-ledger/holdings/history')
@_admin_required
def chip_ledger_holdings_history():
    """Return per-entity cumulative chip flow into/out of the central bank.

    Drives the time-series chart in the Holdings section. The series
    value is "net chips received from the central bank to date" — NOT
    actual entity balance, since the ledger doesn't observe seat-to-
    seat (intra-table) chip flows. `?days=N` clamps to [1, 365],
    default 30.
    """
    try:
        days = int(request.args.get('days', 30))
    except (TypeError, ValueError):
        days = 30
    try:
        data = compute_holdings_history(
            ledger_repo=chip_ledger_repo,
            bankroll_repo=bankroll_repo,
            personality_repo=personality_repo,
            user_repo=user_repo,
            db_path=persistence_db_path,
            days=days,
            sandbox_id=_sandbox_arg(),
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
        sandboxes = sandbox_repo.list_all()
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
