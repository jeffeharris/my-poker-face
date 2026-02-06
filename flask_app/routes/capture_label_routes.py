"""Capture label management routes.

CRUD endpoints for managing labels/tags on captured AI decisions,
enabling filtering and selection for replay experiments.
"""

import logging
from flask import Blueprint, jsonify, request

from poker.authorization import require_permission
from ..extensions import capture_label_repo, prompt_capture_repo

logger = logging.getLogger(__name__)

capture_label_bp = Blueprint('capture_labels', __name__)
_admin_required = require_permission('can_access_admin_tools')


@capture_label_bp.before_request
@_admin_required
def _require_admin_access():
    """Require admin permission for capture label APIs."""
    return None


@capture_label_bp.route('/api/capture-labels', methods=['GET'])
def list_capture_labels():
    """List all unique capture labels with counts.

    Query params:
        label_type: Optional filter by label type ('user' or 'smart')

    Returns:
        {
            "success": true,
            "labels": [{"name": "mistake", "count": 42, "label_type": "user"}, ...]
        }
    """
    try:
        label_type = request.args.get('label_type')

        labels = capture_label_repo.list_all_labels(label_type=label_type)

        return jsonify({
            'success': True,
            'labels': labels
        })
    except Exception as e:
        logger.error(f"Error listing capture labels: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@capture_label_bp.route('/api/captures/<int:capture_id>/labels', methods=['GET'])
def get_capture_labels(capture_id: int):
    """Get all labels for a specific capture.

    Returns:
        {
            "success": true,
            "labels": [{"label": "mistake", "label_type": "user", "created_at": "..."}, ...]
        }
    """
    try:
        labels = capture_label_repo.get_capture_labels(capture_id)

        return jsonify({
            'success': True,
            'labels': labels
        })
    except Exception as e:
        logger.error(f"Error getting labels for capture {capture_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@capture_label_bp.route('/api/captures/<int:capture_id>/labels', methods=['POST'])
def update_capture_labels(capture_id: int):
    """Add or remove labels from a capture.

    Request body:
        {
            "add": ["mistake", "high-stakes"],  // optional
            "remove": ["ignore"]  // optional
        }

    Returns:
        {
            "success": true,
            "labels": [{"label": "...", "label_type": "...", "created_at": "..."}, ...],
            "added": ["mistake", "high-stakes"],
            "removed": 1
        }
    """
    try:
        data = request.json or {}
        labels_to_add = data.get('add', [])
        labels_to_remove = data.get('remove', [])

        if not labels_to_add and not labels_to_remove:
            return jsonify({
                'success': False,
                'error': 'No labels to add or remove specified'
            }), 400

        added = []
        removed = 0

        if labels_to_add:
            added = capture_label_repo.add_capture_labels(capture_id, labels_to_add)

        if labels_to_remove:
            removed = capture_label_repo.remove_capture_labels(capture_id, labels_to_remove)

        # Get current labels after changes
        labels = capture_label_repo.get_capture_labels(capture_id)

        return jsonify({
            'success': True,
            'labels': labels,
            'added': added,
            'removed': removed
        })
    except Exception as e:
        logger.error(f"Error updating labels for capture {capture_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@capture_label_bp.route('/api/captures/search', methods=['GET'])
def search_captures():
    """Search captures with label and filter support.

    Query params:
        labels: Comma-separated list of labels to filter by
        match_all: If 'true', require all labels (default: any label)
        game_id: Optional filter by game
        player_name: Optional filter by player
        action: Optional filter by action (fold, check, call, raise)
        phase: Optional filter by phase (PRE_FLOP, FLOP, TURN, RIVER)
        min_pot_odds: Optional minimum pot odds
        max_pot_odds: Optional maximum pot odds
        error_type: Optional filter by specific error type
        has_error: Optional filter for captures with errors ('true') or without ('false')
        is_correction: Optional filter for correction attempts ('true') or originals only ('false')
        limit: Max results (default 50)
        offset: Pagination offset (default 0)

    Returns:
        {
            "success": true,
            "captures": [...],
            "total": 123
        }
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
            result = capture_label_repo.search_captures_with_labels(
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
                offset=offset
            )
        else:
            # No labels, use regular listing
            result = prompt_capture_repo.list_prompt_captures(
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
                offset=offset
            )

        return jsonify({
            'success': True,
            'captures': result['captures'],
            'total': result['total']
        })
    except Exception as e:
        logger.error(f"Error searching captures: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@capture_label_bp.route('/api/captures/bulk-labels', methods=['POST'])
def bulk_update_labels():
    """Add or remove labels from multiple captures at once.

    Request body:
        {
            "capture_ids": [1, 2, 3],
            "add": ["mistake"],  // optional
            "remove": ["ignore"]  // optional
        }

    Returns:
        {
            "success": true,
            "added": {"captures_affected": 3, "labels_added": 3},
            "removed": {"captures_affected": 3, "labels_removed": 1}
        }
    """
    try:
        data = request.json or {}
        capture_ids = data.get('capture_ids', [])
        labels_to_add = data.get('add', [])
        labels_to_remove = data.get('remove', [])

        if not capture_ids:
            return jsonify({
                'success': False,
                'error': 'No capture_ids specified'
            }), 400

        if not labels_to_add and not labels_to_remove:
            return jsonify({
                'success': False,
                'error': 'No labels to add or remove specified'
            }), 400

        added_result = {'captures_affected': 0, 'labels_added': 0}
        removed_result = {'captures_affected': 0, 'labels_removed': 0}

        if labels_to_add:
            added_result = capture_label_repo.bulk_add_capture_labels(capture_ids, labels_to_add)

        if labels_to_remove:
            removed_result = capture_label_repo.bulk_remove_capture_labels(capture_ids, labels_to_remove)

        return jsonify({
            'success': True,
            'added': added_result,
            'removed': removed_result
        })
    except Exception as e:
        logger.error(f"Error bulk updating labels: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
