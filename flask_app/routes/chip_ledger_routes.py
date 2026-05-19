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

from ..extensions import (
    bankroll_repo,
    cash_table_repo,
    chip_ledger_repo,
    limiter,
    persistence_db_path,
)
from ..services import game_state_service
from ..services.chip_ledger_audit import compute_audit
from poker.authorization import require_permission

logger = logging.getLogger(__name__)

chip_ledger_bp = Blueprint('chip_ledger', __name__)

_admin_required = require_permission('can_access_admin_tools')


@chip_ledger_bp.route('/api/admin/chip-ledger/audit')
@_admin_required
def chip_ledger_audit():
    """Return the v0 audit payload — ledger view, actual view, drift."""
    try:
        data = compute_audit(
            ledger_repo=chip_ledger_repo,
            bankroll_repo=bankroll_repo,
            cash_table_repo=cash_table_repo,
            db_path=persistence_db_path,
            list_game_ids_fn=game_state_service.list_game_ids,
            get_game_fn=game_state_service.get_game,
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
    clamped to [1, 500].
    """
    try:
        limit = int(request.args.get('limit', 100))
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(500, limit))

    try:
        entries = chip_ledger_repo.recent_entries(limit=limit)
        return jsonify({'entries': entries})
    except Exception as e:
        logger.error("chip-ledger recent failed: %s", e, exc_info=True)
        return jsonify({'error': 'Recent-entries lookup failed'}), 500
