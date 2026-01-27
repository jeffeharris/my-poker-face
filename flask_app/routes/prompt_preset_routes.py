"""Prompt preset management routes.

CRUD endpoints for managing reusable prompt configurations that can be
applied to tournament variants or replay experiments for A/B testing.
"""

import logging
from flask import Blueprint, jsonify, request

from ..extensions import persistence, auth_manager

logger = logging.getLogger(__name__)

prompt_preset_bp = Blueprint('prompt_preset', __name__)


@prompt_preset_bp.route('/api/prompt-presets', methods=['GET'])
def list_prompt_presets():
    """List all prompt presets.

    Query params:
        limit: Maximum number of results (default 100)

    Returns:
        {
            "success": true,
            "presets": [...]
        }
    """
    try:
        limit = request.args.get('limit', 100, type=int)

        # Get current user for owner filtering
        current_user = auth_manager.get_current_user()
        owner_id = current_user.get('id') if current_user else None

        presets = persistence.list_prompt_presets(owner_id=owner_id, limit=limit)

        return jsonify({
            'success': True,
            'presets': presets
        })
    except Exception as e:
        logger.error(f"Error listing prompt presets: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@prompt_preset_bp.route('/api/prompt-presets', methods=['POST'])
def create_prompt_preset():
    """Create a new prompt preset.

    Request body:
        {
            "name": "preset_name",
            "description": "optional description",
            "prompt_config": { ... },
            "guidance_injection": "optional guidance text"
        }

    Returns:
        {
            "success": true,
            "preset": { ... },
            "message": "..."
        }
    """
    try:
        data = request.json or {}
        name = data.get('name', '').strip()

        if not name:
            return jsonify({
                'success': False,
                'error': 'Name is required'
            }), 400

        # Get current user for ownership
        current_user = auth_manager.get_current_user()
        owner_id = current_user.get('id') if current_user else None

        preset_id = persistence.create_prompt_preset(
            name=name,
            description=data.get('description'),
            prompt_config=data.get('prompt_config'),
            guidance_injection=data.get('guidance_injection'),
            owner_id=owner_id
        )

        preset = persistence.get_prompt_preset(preset_id)

        return jsonify({
            'success': True,
            'preset': preset,
            'message': f"Created prompt preset '{name}'"
        })
    except ValueError as e:
        # Duplicate name
        return jsonify({'success': False, 'error': str(e)}), 409
    except Exception as e:
        logger.error(f"Error creating prompt preset: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@prompt_preset_bp.route('/api/prompt-presets/<int:preset_id>', methods=['GET'])
def get_prompt_preset(preset_id: int):
    """Get a specific prompt preset by ID.

    Returns:
        {
            "success": true,
            "preset": { ... }
        }
    """
    try:
        preset = persistence.get_prompt_preset(preset_id)

        if not preset:
            return jsonify({
                'success': False,
                'error': f'Preset with ID {preset_id} not found'
            }), 404

        return jsonify({
            'success': True,
            'preset': preset
        })
    except Exception as e:
        logger.error(f"Error getting prompt preset {preset_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@prompt_preset_bp.route('/api/prompt-presets/<int:preset_id>', methods=['PUT'])
def update_prompt_preset(preset_id: int):
    """Update a prompt preset.

    Request body (all fields optional):
        {
            "name": "new_name",
            "description": "new description",
            "prompt_config": { ... },
            "guidance_injection": "new guidance text"
        }

    Returns:
        {
            "success": true,
            "preset": { ... },
            "message": "..."
        }
    """
    try:
        data = request.json or {}

        # Check if preset exists
        existing = persistence.get_prompt_preset(preset_id)
        if not existing:
            return jsonify({
                'success': False,
                'error': f'Preset with ID {preset_id} not found'
            }), 404

        # System presets are managed by YAML config and cannot be edited
        if existing.get('is_system'):
            return jsonify({
                'success': False,
                'error': 'System presets cannot be edited (managed by config/game_modes.yaml)'
            }), 403

        # Build update kwargs from provided fields
        update_kwargs = {}
        if 'name' in data:
            update_kwargs['name'] = data['name'].strip() if data['name'] else None
        if 'description' in data:
            update_kwargs['description'] = data['description']
        if 'prompt_config' in data:
            update_kwargs['prompt_config'] = data['prompt_config']
        if 'guidance_injection' in data:
            update_kwargs['guidance_injection'] = data['guidance_injection']

        if not update_kwargs:
            return jsonify({
                'success': False,
                'error': 'No fields to update'
            }), 400

        updated = persistence.update_prompt_preset(preset_id, **update_kwargs)

        if not updated:
            return jsonify({
                'success': False,
                'error': 'Failed to update preset'
            }), 500

        preset = persistence.get_prompt_preset(preset_id)

        return jsonify({
            'success': True,
            'preset': preset,
            'message': f"Updated prompt preset '{preset['name']}'"
        })
    except ValueError as e:
        # Duplicate name
        return jsonify({'success': False, 'error': str(e)}), 409
    except Exception as e:
        logger.error(f"Error updating prompt preset {preset_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@prompt_preset_bp.route('/api/prompt-presets/<int:preset_id>', methods=['DELETE'])
def delete_prompt_preset(preset_id: int):
    """Delete a prompt preset.

    Returns:
        {
            "success": true,
            "message": "..."
        }
    """
    try:
        # Check if preset exists first
        existing = persistence.get_prompt_preset(preset_id)
        if not existing:
            return jsonify({
                'success': False,
                'error': f'Preset with ID {preset_id} not found'
            }), 404

        # System presets are managed by YAML config and cannot be deleted
        if existing.get('is_system'):
            return jsonify({
                'success': False,
                'error': 'System presets cannot be deleted (managed by config/game_modes.yaml)'
            }), 403

        deleted = persistence.delete_prompt_preset(preset_id)

        if not deleted:
            return jsonify({
                'success': False,
                'error': 'Failed to delete preset'
            }), 500

        return jsonify({
            'success': True,
            'message': f"Deleted prompt preset '{existing['name']}'"
        })
    except Exception as e:
        logger.error(f"Error deleting prompt preset {preset_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
