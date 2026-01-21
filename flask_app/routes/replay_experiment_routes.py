"""Replay Experiment API Routes.

CRUD endpoints for replay experiments that re-run captured AI decisions
with different variants (models, prompts, guidance) to test prompt effectiveness.
"""

import json
import logging
import threading
from typing import Dict, Any

from flask import Blueprint, jsonify, request

from ..extensions import persistence

logger = logging.getLogger(__name__)

replay_experiment_bp = Blueprint('replay_experiments', __name__)

# Store active replay experiment threads
_active_replay_threads: Dict[int, threading.Thread] = {}


@replay_experiment_bp.route('/api/replay-experiments', methods=['GET'])
def list_replay_experiments():
    """List all replay experiments.

    Query params:
        status: Optional filter by status (pending, running, completed, failed)
        limit: Max results (default 50)
        offset: Pagination offset (default 0)

    Returns:
        {
            "success": true,
            "experiments": [...],
            "total": 123
        }
    """
    try:
        status = request.args.get('status')
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)

        result = persistence.list_replay_experiments(
            status=status,
            limit=limit,
            offset=offset
        )

        return jsonify({
            'success': True,
            'experiments': result['experiments'],
            'total': result['total']
        })
    except Exception as e:
        logger.error(f"Error listing replay experiments: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@replay_experiment_bp.route('/api/replay-experiments', methods=['POST'])
def create_replay_experiment():
    """Create a new replay experiment.

    Request body:
        {
            "name": "test_claude_on_mistakes",
            "description": "Testing if Claude does better on known mistakes",
            "hypothesis": "Claude with guidance will make fewer mistakes",
            "capture_selection": {
                "mode": "labels",  // or "ids", "filters"
                "labels": ["mistake"],
                "ids": [1, 2, 3],  // if mode is "ids"
                "filters": {"phase": "FLOP"}  // if mode is "filters"
            },
            "variants": [
                {"label": "Control"},
                {"label": "Claude", "model": "claude-sonnet-4-20250514", "provider": "anthropic"}
            ]
        }

    Returns:
        {
            "success": true,
            "experiment_id": 123,
            "capture_count": 50
        }
    """
    try:
        data = request.json or {}

        # Validate required fields
        name = data.get('name')
        if not name:
            return jsonify({'success': False, 'error': 'name is required'}), 400

        capture_selection = data.get('capture_selection', {})
        variants = data.get('variants', [])

        if not variants:
            return jsonify({'success': False, 'error': 'At least one variant is required'}), 400

        # Resolve capture IDs based on selection mode
        capture_ids = _resolve_capture_ids(capture_selection)

        if not capture_ids:
            return jsonify({
                'success': False,
                'error': 'No captures match the selection criteria'
            }), 400

        # Create the experiment
        experiment_id = persistence.create_replay_experiment(
            name=name,
            capture_ids=capture_ids,
            variants=variants,
            description=data.get('description'),
            hypothesis=data.get('hypothesis'),
            tags=data.get('tags')
        )

        return jsonify({
            'success': True,
            'experiment_id': experiment_id,
            'capture_count': len(capture_ids)
        })

    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error creating replay experiment: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@replay_experiment_bp.route('/api/replay-experiments/<int:experiment_id>', methods=['GET'])
def get_replay_experiment(experiment_id: int):
    """Get a replay experiment with progress info.

    Returns:
        {
            "success": true,
            "experiment": {...},
            "progress": {
                "completed": 45,
                "total": 100,
                "percent": 45
            }
        }
    """
    try:
        experiment = persistence.get_replay_experiment(experiment_id)
        if not experiment:
            return jsonify({'success': False, 'error': 'Experiment not found'}), 404

        # Calculate progress
        completed = experiment.get('results_completed', 0)
        total = experiment.get('results_total', 0)
        progress = {
            'completed': completed,
            'total': total,
            'percent': int(completed / total * 100) if total > 0 else 0
        }

        return jsonify({
            'success': True,
            'experiment': experiment,
            'progress': progress
        })
    except Exception as e:
        logger.error(f"Error getting replay experiment {experiment_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@replay_experiment_bp.route('/api/replay-experiments/<int:experiment_id>/launch', methods=['POST'])
def launch_replay_experiment(experiment_id: int):
    """Launch a replay experiment.

    Starts the experiment running in a background thread.

    Query params:
        parallel: If 'false', run sequentially (default: true)
        max_workers: Max concurrent workers (default: 3)

    Returns:
        {
            "success": true,
            "message": "Experiment launched"
        }
    """
    try:
        experiment = persistence.get_replay_experiment(experiment_id)
        if not experiment:
            return jsonify({'success': False, 'error': 'Experiment not found'}), 404

        if experiment.get('status') == 'running':
            return jsonify({'success': False, 'error': 'Experiment is already running'}), 400

        # Get options
        parallel = request.args.get('parallel', 'true').lower() != 'false'
        max_workers = request.args.get('max_workers', 3, type=int)

        # Import runner
        from experiments.run_replay_experiment import run_replay_experiment_async

        # Start the experiment
        thread = run_replay_experiment_async(
            experiment_id=experiment_id,
            persistence=persistence,
            parallel=parallel,
            max_workers=max_workers
        )

        _active_replay_threads[experiment_id] = thread

        return jsonify({
            'success': True,
            'message': 'Experiment launched'
        })
    except Exception as e:
        logger.error(f"Error launching replay experiment {experiment_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@replay_experiment_bp.route('/api/replay-experiments/<int:experiment_id>/results', methods=['GET'])
def get_replay_results(experiment_id: int):
    """Get results for a replay experiment.

    Query params:
        variant: Optional filter by variant label
        quality_change: Optional filter by quality change ('improved', 'degraded', 'unchanged')
        limit: Max results (default 100)
        offset: Pagination offset (default 0)

    Returns:
        {
            "success": true,
            "results": [...],
            "total": 50
        }
    """
    try:
        variant = request.args.get('variant')
        quality_change = request.args.get('quality_change')
        limit = request.args.get('limit', 100, type=int)
        offset = request.args.get('offset', 0, type=int)

        result = persistence.get_replay_results(
            experiment_id=experiment_id,
            variant=variant,
            quality_change=quality_change,
            limit=limit,
            offset=offset
        )

        return jsonify({
            'success': True,
            'results': result['results'],
            'total': result['total']
        })
    except Exception as e:
        logger.error(f"Error getting replay results for {experiment_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@replay_experiment_bp.route('/api/replay-experiments/<int:experiment_id>/summary', methods=['GET'])
def get_replay_summary(experiment_id: int):
    """Get summary statistics for a replay experiment.

    Returns:
        {
            "success": true,
            "summary": {
                "overall": {...},
                "by_variant": {...}
            }
        }
    """
    try:
        summary = persistence.get_replay_results_summary(experiment_id)

        return jsonify({
            'success': True,
            'summary': summary
        })
    except Exception as e:
        logger.error(f"Error getting replay summary for {experiment_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@replay_experiment_bp.route('/api/replay-experiments/<int:experiment_id>/captures', methods=['GET'])
def get_replay_captures(experiment_id: int):
    """Get the captures linked to a replay experiment.

    Returns:
        {
            "success": true,
            "captures": [...]
        }
    """
    try:
        captures = persistence.get_replay_experiment_captures(experiment_id)

        return jsonify({
            'success': True,
            'captures': captures
        })
    except Exception as e:
        logger.error(f"Error getting replay captures for {experiment_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@replay_experiment_bp.route('/api/replay-experiments/<int:experiment_id>/captures/<int:capture_id>', methods=['GET'])
def get_capture_replay_comparison(experiment_id: int, capture_id: int):
    """Get side-by-side comparison of original vs variant results for a capture.

    Returns:
        {
            "success": true,
            "original": {...},
            "variants": [...]
        }
    """
    try:
        # Get original capture info
        captures = persistence.get_replay_experiment_captures(experiment_id)
        original = None
        for c in captures:
            if c['capture_id'] == capture_id:
                original = c
                break

        if not original:
            return jsonify({'success': False, 'error': 'Capture not found in this experiment'}), 404

        # Get variant results for this capture
        results = persistence.get_replay_results(
            experiment_id=experiment_id,
            limit=100,
            offset=0
        )

        # Filter to this capture
        capture_results = [r for r in results['results'] if r['capture_id'] == capture_id]

        return jsonify({
            'success': True,
            'original': original,
            'variants': capture_results
        })
    except Exception as e:
        logger.error(f"Error getting capture comparison for {experiment_id}/{capture_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def _resolve_capture_ids(capture_selection: Dict[str, Any]) -> list:
    """Resolve capture IDs from selection criteria.

    Args:
        capture_selection: Dict with 'mode' and mode-specific fields

    Returns:
        List of capture IDs
    """
    mode = capture_selection.get('mode', 'ids')

    if mode == 'ids':
        # Direct IDs provided
        return capture_selection.get('ids', [])

    elif mode == 'labels':
        # Search by labels
        labels = capture_selection.get('labels', [])
        filters = capture_selection.get('filters', {})

        if not labels:
            return []

        result = persistence.search_captures_with_labels(
            labels=labels,
            match_all=capture_selection.get('match_all', False),
            phase=filters.get('phase'),
            action=filters.get('action'),
            min_pot_odds=filters.get('min_pot_odds'),
            max_pot_odds=filters.get('max_pot_odds'),
            limit=1000  # Cap at 1000 captures per experiment
        )
        return [c['id'] for c in result.get('captures', [])]

    elif mode == 'filters':
        # Search by filters only
        filters = capture_selection.get('filters', {})

        result = persistence.list_prompt_captures(
            phase=filters.get('phase'),
            action=filters.get('action'),
            min_pot_odds=filters.get('min_pot_odds'),
            max_pot_odds=filters.get('max_pot_odds'),
            limit=1000
        )
        return [c['id'] for c in result.get('captures', [])]

    return []
