"""Admin chart-opportunity census route — serves the preflop chart-coverage
census artifact for the admin dashboard.

Read-only. The artifact (`data/chart_census.json`) is generated offline by the
census sim + analysis (see docs/technical/CHART_OPPORTUNITY_CENSUS.md):

    docker compose exec backend python3 scripts/chart_census_sim.py --db /tmp/census.db --jobs 6
    docker compose exec backend python3 scripts/chart_census.py /tmp/census.db --json data/chart_census.json --quiet

This endpoint just hands the pre-computed JSON to the frontend, gated behind the
same `can_access_admin_tools` permission as the rest of the admin surface.
"""

from __future__ import annotations

import json
import logging
import os

from flask import Blueprint, jsonify, request

from core.llm import CallType, LLMClient
from poker.authorization import require_permission

from .. import config

logger = logging.getLogger(__name__)

# Conversation guardrails for the analyst chat.
_MAX_TURNS = 20
_MAX_CHARS = 6000

chart_census_bp = Blueprint('chart_census', __name__)

_admin_required = require_permission('can_access_admin_tools')

# data/ is the bind-mounted writable dir (host ./data, container /app/data); the
# census writes the artifact there via `--json`.
_ARTIFACT_CANDIDATES = ('data/chart_census.json', '/app/data/chart_census.json')


def _artifact_path() -> str | None:
    for p in _ARTIFACT_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


@chart_census_bp.route('/api/admin/chart-census')
@_admin_required
def chart_census():
    """Return the latest census payload, or 404 if none has been generated."""
    path = _artifact_path()
    if path is None:
        return jsonify(
            {
                'error': 'no_artifact',
                'message': (
                    'No census artifact yet. Generate it with '
                    'scripts/chart_census_sim.py then scripts/chart_census.py '
                    '--json data/chart_census.json'
                ),
            }
        ), 404
    try:
        with open(path) as f:
            payload = json.load(f)
        payload['_generated_at'] = os.path.getmtime(path)
        return jsonify(payload)
    except Exception as e:
        logger.error("chart-census artifact read failed: %s", e, exc_info=True)
        return jsonify({'error': 'read_failed'}), 500


def _census_system_prompt(payload: dict) -> str:
    """System prompt that grounds the analyst on the census payload."""
    view = {k: v for k, v in payload.items() if not k.startswith('_')}
    return (
        "You are a poker strategy analyst helping a developer interpret a "
        '"chart opportunity census" for an AI poker bot\'s preflop solver charts.\n\n'
        "CENSUS DATA (JSON):\n" + json.dumps(view) + "\n\n"
        "WHAT IT MEASURES: where preflop decisions land across the bot's charts, how "
        "much money (big blinds) rides on each spot, which opponent archetype field "
        "they arise against, and where decisions FALL THROUGH to a conservative "
        "fold/check default or a miscalibrated deep-stack chart instead of a "
        "specialized chart.\n"
        "PRIORITY MODEL: priority = decision frequency x EV impact per decision x "
        "confidence gap x archetype relevance.\n"
        "GLOSSARY: rfi = first-in (open or fold); vs_open = facing a single raise; "
        "vs_3bet / vs_4bet / vs_squeeze = reraise pots; chart_source "
        "(push_fold | facing_all_in_veto | chart_hit | chart_fallback) = which layer "
        "produced the action; a fall-through = the bot wanted specialized-chart "
        "behavior but got a fallback; risk_bb = big blinds the decision commits "
        "(fold/check = 0).\n\n"
        "HOW TO INTERPRET — do NOT confuse a working layer with a leak:\n"
        "  - chart_hit, push_fold, and facing_all_in_veto are the bot's specialized "
        "layers WORKING AS INTENDED (a chart, the Nash push/fold table, or the "
        "pot-odds veto served the decision). High volume in any of these is neither a "
        "problem nor an opportunity. In particular, push_fold routing at short stacks "
        "is correct behavior, not a leak to 'reduce'.\n"
        "  - The ONLY chart-coverage gaps are fallthrough_audit.classes (and "
        "chart_source = chart_fallback). Treat fallthrough_audit.classes as the "
        "AUTHORITATIVE opportunity list: rank opportunities by their count and risk_bb "
        "there, cross-referenced with the archetype matrix — never by the raw volume "
        "of a healthy chart_source.\n\n"
        "RULES: Answer ONLY with facts and numbers present in the JSON above. If the "
        "data does not contain an answer, say so plainly and never invent numbers. Be "
        "concise and concrete, cite the specific figures, and lean toward actionable "
        "prioritization. This is a sim census (homogeneous archetype fields, a TAG "
        "hero), so it is directional for prioritization, not live prod distribution."
    )


@chart_census_bp.route('/api/admin/chart-census/ask', methods=['POST'])
@_admin_required
def chart_census_ask():
    """Conversational analyst grounded on the census payload. The full message
    history is passed in the request body (stateless server)."""
    path = _artifact_path()
    if path is None:
        return jsonify({'error': 'no_artifact', 'message': 'Generate the census first.'}), 404

    body = request.get_json(silent=True) or {}
    history = body.get('messages')
    if not isinstance(history, list) or not history:
        return jsonify({'error': 'bad_request', 'message': 'messages[] required'}), 400

    turns = []
    for m in history[-_MAX_TURNS:]:
        if not isinstance(m, dict):
            continue
        role = m.get('role')
        content = (m.get('content') or '').strip()[:_MAX_CHARS]
        if role in ('user', 'assistant') and content:
            turns.append({'role': role, 'content': content})
    if not turns or turns[-1]['role'] != 'user':
        return jsonify({'error': 'bad_request', 'message': 'last message must be a user turn'}), 400

    try:
        with open(path) as f:
            payload = json.load(f)
        messages = [{'role': 'system', 'content': _census_system_prompt(payload)}] + turns
        client = LLMClient(
            model=config.get_assistant_model(), provider=config.get_assistant_provider()
        )
        resp = client.complete(
            messages=messages,
            call_type=CallType.EXPERIMENT_ANALYSIS,
            game_id='chart_census',
        )
        return jsonify({'answer': resp.content, 'model': client.model})
    except Exception as e:
        logger.error("chart-census ask failed: %s", e, exc_info=True)
        return jsonify({'error': 'llm_failed', 'message': str(e)}), 500
