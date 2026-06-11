"""Admin cost-analytics routes — LLM + image-gen spend breakdowns.

Read-only aggregation over the `api_usage` table, gated behind the same
`can_access_admin_tools` permission as the rest of the admin surface.
Cost comes from the pre-computed `estimated_cost` column (USD), written at
insert time by UsageTracker — these endpoints never re-derive pricing.

Three drill levels, mirroring the dashboard:
  1. overview  — KPIs + by-owner + by-call-type + by-model + time-series
  2. owner/<id> — one owner's call-type / model / time-series breakdown
  3. calls      — raw individual rows, filtered by owner / call_type

Range param maps to a SQLite date modifier via the shared admin helper.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from poker.authorization import require_permission

from .. import extensions
from .admin_dashboard_routes import _get_date_modifier

logger = logging.getLogger(__name__)

cost_analytics_bp = Blueprint('cost_analytics', __name__)

_admin_required = require_permission('can_access_admin_tools')


def _range_arg() -> str:
    """Read the `range` query param (24h / 7d / 30d / all), default 7d."""
    return request.args.get('range', '7d')


def _bucket_for_range(range_param: str) -> str:
    """Hourly buckets for the 24h view; daily otherwise.

    Keeps the time-series chart readable: 24 hourly points for a day,
    one point per day for the longer windows.
    """
    return 'hour' if range_param == '24h' else 'day'


@cost_analytics_bp.route('/api/admin/cost-analytics/overview')
@_admin_required
def cost_overview():
    """Top-level rollup: KPIs + breakdowns by owner, call_type, model, time."""
    range_param = _range_arg()
    date_modifier = _get_date_modifier(range_param)
    repo = extensions.llm_repo
    try:
        summary = repo.get_usage_summary(date_modifier)
        by_owner = repo.get_cost_by_owner(date_modifier)
        by_call_type = repo.get_cost_by_call_type(date_modifier)
        by_model = repo.get_cost_by_model(date_modifier)
        by_game = repo.get_cost_by_game(date_modifier, limit=50)
        uncosted = repo.get_uncosted_calls(date_modifier)
        timeseries = repo.get_cost_timeseries(date_modifier, bucket=_bucket_for_range(range_param))
        return jsonify(
            {
                'range': range_param,
                'summary': summary,
                'by_owner': by_owner,
                'by_call_type': by_call_type,
                'by_model': by_model,
                'by_game': by_game,
                'uncosted': uncosted,
                'timeseries': timeseries,
            }
        )
    except Exception as e:
        logger.error("cost-analytics overview failed: %s", e, exc_info=True)
        return jsonify({'error': 'Cost analytics query failed'}), 500


@cost_analytics_bp.route('/api/admin/cost-analytics/owner/<path:owner_id>')
@_admin_required
def cost_owner_detail(owner_id: str):
    """One owner's spend, broken down by call_type, model, and over time."""
    range_param = _range_arg()
    date_modifier = _get_date_modifier(range_param)
    repo = extensions.llm_repo
    try:
        by_call_type = repo.get_cost_by_call_type(date_modifier, owner_id=owner_id)
        by_model = repo.get_cost_by_model(date_modifier, owner_id=owner_id)
        by_game = repo.get_cost_by_game(date_modifier, owner_id=owner_id, limit=50)
        timeseries = repo.get_cost_timeseries(
            date_modifier, owner_id=owner_id, bucket=_bucket_for_range(range_param)
        )
        total_cost = sum(row['total_cost'] for row in by_call_type)
        total_calls = sum(row['total_calls'] for row in by_call_type)
        return jsonify(
            {
                'range': range_param,
                'owner_id': owner_id,
                'total_cost': total_cost,
                'total_calls': total_calls,
                'by_call_type': by_call_type,
                'by_model': by_model,
                'by_game': by_game,
                'timeseries': timeseries,
            }
        )
    except Exception as e:
        logger.error("cost-analytics owner detail failed: %s", e, exc_info=True)
        return jsonify({'error': 'Cost analytics query failed'}), 500


@cost_analytics_bp.route('/api/admin/cost-analytics/calls')
@_admin_required
def cost_calls():
    """Raw api_usage rows for drill-down, filtered by owner / call_type."""
    range_param = _range_arg()
    date_modifier = _get_date_modifier(range_param)
    owner_id = request.args.get('owner_id') or None
    call_type = request.args.get('call_type') or None
    game_id = request.args.get('game_id') or None
    try:
        limit = min(int(request.args.get('limit', 100)), 500)
    except (TypeError, ValueError):
        limit = 100
    try:
        rows = extensions.llm_repo.get_recent_calls(
            date_modifier,
            owner_id=owner_id,
            call_type=call_type,
            game_id=game_id,
            limit=limit,
        )
        return jsonify({'range': range_param, 'count': len(rows), 'calls': rows})
    except Exception as e:
        logger.error("cost-analytics calls failed: %s", e, exc_info=True)
        return jsonify({'error': 'Cost analytics query failed'}), 500
