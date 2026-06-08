"""Decision label management routes.

CRUD endpoints for managing labels/tags on decisions. Labels are keyed on the
decision spine (`player_decision_analysis`), so every decision — human, tiered,
rule, or LLM — is taggable.

Two surfaces are exposed:

* ``/api/decisions/<decision_id>/labels`` — native decision-id space, used by
  the Decision Analyzer (covers all player types).
* ``/api/captures/<capture_id>/labels`` and ``/api/captures/...`` — capture-id
  space, used by the Prompt Playground / replay experiments. These translate
  through ``player_decision_analysis.capture_id``; a capture with no decision
  row cannot be tagged.
"""

import logging

from flask import Blueprint, jsonify, request

from .. import extensions
from ..route_utils import register_admin_guard

logger = logging.getLogger(__name__)

capture_label_bp = Blueprint('capture_labels', __name__)
register_admin_guard(capture_label_bp)


@capture_label_bp.route('/api/capture-labels', methods=['GET'])
def list_capture_labels():
    """List all unique labels with counts.

    Query params:
        label_type: Optional filter by label type ('user' or 'auto')

    Returns:
        {
            "success": true,
            "labels": [{"name": "mistake", "count": 42, "label_type": "user"}, ...]
        }
    """
    try:
        label_type = request.args.get('label_type')

        labels = extensions.capture_label_repo.list_all_labels(label_type=label_type)

        return jsonify({'success': True, 'labels': labels})
    except Exception as e:
        logger.error(f"Error listing capture labels: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ----------------------------------------------------------------------
# Decision-id space (Decision Analyzer)
# ----------------------------------------------------------------------


@capture_label_bp.route('/api/decisions/<int:decision_id>/labels', methods=['GET'])
def get_decision_labels(decision_id: int):
    """Get all labels for a decision."""
    try:
        labels = extensions.capture_label_repo.get_labels(decision_id)
        return jsonify({'success': True, 'labels': labels})
    except Exception as e:
        logger.error(f"Error getting labels for decision {decision_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@capture_label_bp.route('/api/decisions/<int:decision_id>/labels', methods=['POST'])
def update_decision_labels(decision_id: int):
    """Add or remove labels from a decision.

    Request body: {"add": ["mistake"], "remove": ["ignore"]}  (both optional)
    """
    try:
        data = request.json or {}
        labels_to_add = data.get('add', [])
        labels_to_remove = data.get('remove', [])

        if not labels_to_add and not labels_to_remove:
            return jsonify({'success': False, 'error': 'No labels to add or remove specified'}), 400

        added = []
        removed = 0

        if labels_to_add:
            added = extensions.capture_label_repo.add_labels(decision_id, labels_to_add)
        if labels_to_remove:
            removed = extensions.capture_label_repo.remove_labels(decision_id, labels_to_remove)

        labels = extensions.capture_label_repo.get_labels(decision_id)
        return jsonify({'success': True, 'labels': labels, 'added': added, 'removed': removed})
    except Exception as e:
        logger.error(f"Error updating labels for decision {decision_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ----------------------------------------------------------------------
# Capture-id space (Prompt Playground) — bridges to the decision row
# ----------------------------------------------------------------------


@capture_label_bp.route('/api/captures/<int:capture_id>/labels', methods=['GET'])
def get_capture_labels(capture_id: int):
    """Get labels for the decision linked to a capture."""
    try:
        labels = extensions.capture_label_repo.get_labels_by_capture(capture_id)
        return jsonify({'success': True, 'labels': labels})
    except Exception as e:
        logger.error(f"Error getting labels for capture {capture_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@capture_label_bp.route('/api/captures/<int:capture_id>/labels', methods=['POST'])
def update_capture_labels(capture_id: int):
    """Add or remove labels on the decision linked to a capture.

    Request body: {"add": [...], "remove": [...]}  (both optional)
    """
    try:
        data = request.json or {}
        labels_to_add = data.get('add', [])
        labels_to_remove = data.get('remove', [])

        if not labels_to_add and not labels_to_remove:
            return jsonify({'success': False, 'error': 'No labels to add or remove specified'}), 400

        decision_id = extensions.capture_label_repo.decision_id_for_capture(capture_id)
        if decision_id is None:
            return jsonify(
                {'success': False, 'error': 'No decision is linked to this capture; cannot label'}
            ), 404

        added = []
        removed = 0
        if labels_to_add:
            added = extensions.capture_label_repo.add_labels(decision_id, labels_to_add)
        if labels_to_remove:
            removed = extensions.capture_label_repo.remove_labels(decision_id, labels_to_remove)

        labels = extensions.capture_label_repo.get_labels(decision_id)
        return jsonify({'success': True, 'labels': labels, 'added': added, 'removed': removed})
    except Exception as e:
        logger.error(f"Error updating labels for capture {capture_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@capture_label_bp.route('/api/captures/search', methods=['GET'])
def search_captures():
    """Search captures with label and filter support.

    Query params:
        labels: Comma-separated list of labels to filter by
        match_all: If 'true', require all labels (default: any label)
        game_id, player_name, action, phase: Optional filters
        min_pot_odds, max_pot_odds: Optional pot odds range
        error_type, has_error, is_correction: Resilience filters
        limit, offset: Pagination

    Returns:
        {"success": true, "captures": [...], "total": 123}
    """
    try:
        # Parse labels from comma-separated string
        labels_str = request.args.get('labels', '')
        labels = [l.strip() for l in labels_str.split(',') if l.strip()]

        match_all = request.args.get('match_all', 'false').lower() == 'true'
        game_id = request.args.get('game_id')
        player_name = request.args.get('player_name')
        action = request.args.get('action')
        phase = request.args.get('phase')
        min_pot_odds = request.args.get('min_pot_odds', type=float)
        max_pot_odds = request.args.get('max_pot_odds', type=float)
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)

        # Parse error/correction filters
        error_type = request.args.get('error_type')
        has_error_str = request.args.get('has_error')
        has_error = None
        if has_error_str == 'true':
            has_error = True
        elif has_error_str == 'false':
            has_error = False

        is_correction_str = request.args.get('is_correction')
        is_correction = None
        if is_correction_str == 'true':
            is_correction = True
        elif is_correction_str == 'false':
            is_correction = False

        if labels:
            result = extensions.capture_label_repo.search_captures_with_labels(
                labels=labels,
                match_all=match_all,
                game_id=game_id,
                player_name=player_name,
                action=action,
                phase=phase,
                min_pot_odds=min_pot_odds,
                max_pot_odds=max_pot_odds,
                error_type=error_type,
                has_error=has_error,
                is_correction=is_correction,
                limit=limit,
                offset=offset,
            )
        else:
            # No labels, use regular listing
            result = extensions.prompt_capture_repo.list_prompt_captures(
                game_id=game_id,
                player_name=player_name,
                action=action,
                phase=phase,
                min_pot_odds=min_pot_odds,
                max_pot_odds=max_pot_odds,
                error_type=error_type,
                has_error=has_error,
                is_correction=is_correction,
                limit=limit,
                offset=offset,
            )

        return jsonify({'success': True, 'captures': result['captures'], 'total': result['total']})
    except Exception as e:
        logger.error(f"Error searching captures: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@capture_label_bp.route('/api/captures/bulk-labels', methods=['POST'])
def bulk_update_labels():
    """Add or remove labels from multiple captures at once.

    Request body:
        {"capture_ids": [1, 2, 3], "add": ["mistake"], "remove": ["ignore"]}

    Capture ids are resolved to their decision rows; captures with no decision
    are skipped.
    """
    try:
        data = request.json or {}
        capture_ids = data.get('capture_ids', [])
        labels_to_add = data.get('add', [])
        labels_to_remove = data.get('remove', [])

        if not capture_ids:
            return jsonify({'success': False, 'error': 'No capture_ids specified'}), 400

        if not labels_to_add and not labels_to_remove:
            return jsonify({'success': False, 'error': 'No labels to add or remove specified'}), 400

        # Translate capture ids -> decision ids (drop captures without a decision)
        repo = extensions.capture_label_repo
        decision_ids = [
            did
            for did in (repo.decision_id_for_capture(cid) for cid in capture_ids)
            if did is not None
        ]

        added_result = {'captures_affected': 0, 'labels_added': 0}
        removed_result = {'captures_affected': 0, 'labels_removed': 0}

        if decision_ids:
            if labels_to_add:
                added_result = repo.bulk_add_labels(decision_ids, labels_to_add)
            if labels_to_remove:
                removed_result = repo.bulk_remove_labels(decision_ids, labels_to_remove)

        return jsonify({'success': True, 'added': added_result, 'removed': removed_result})
    except Exception as e:
        logger.error(f"Error bulk updating labels: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
