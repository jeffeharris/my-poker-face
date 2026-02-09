"""Prompt preset management routes.

CRUD endpoints for managing reusable prompt configurations that can be
applied to tournament variants or replay experiments for A/B testing.
"""

import logging
from flask import Blueprint, jsonify, request

from ..extensions import prompt_preset_repo, auth_manager
from poker.authorization import get_authorization_service

logger = logging.getLogger(__name__)

prompt_preset_bp = Blueprint('prompt_preset', __name__)


def _is_admin(user_id: str) -> bool:
    """Check whether a user has admin tools permission."""
    auth_service = get_authorization_service()
    return bool(auth_service and auth_service.has_permission(user_id, 'can_access_admin_tools'))


def _can_access_preset(current_user: dict | None, preset: dict) -> bool:
    """Check whether current user can read/write a preset."""
    owner_id = preset.get('owner_id')
    if owner_id is None:
        # System/shared presets are readable by authenticated users.
        return current_user is not None

    if not current_user:
        return False

    user_id = current_user.get('id')
    if user_id == owner_id:
        return True

    return _is_admin(user_id)


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

        presets = prompt_preset_repo.list_prompt_presets(owner_id=owner_id, limit=limit)

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
        current_user = auth_manager.get_current_user() if auth_manager else None
        if not current_user or not current_user.get('id'):
            return jsonify({'success': False, 'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}), 401

        data = request.json or {}
        name = data.get('name', '').strip()

        if not name:
            return jsonify({
                'success': False,
                'error': 'Name is required'
            }), 400

        # Get current user for ownership
        owner_id = current_user['id']

        preset_id = prompt_preset_repo.create_prompt_preset(
            name=name,
            description=data.get('description'),
            prompt_config=data.get('prompt_config'),
            guidance_injection=data.get('guidance_injection'),
            owner_id=owner_id
        )

        preset = prompt_preset_repo.get_prompt_preset(preset_id)

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
        current_user = auth_manager.get_current_user()
        if not current_user:
            return jsonify({'success': False, 'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}), 401

        preset = prompt_preset_repo.get_prompt_preset(preset_id)

        if not preset:
            return jsonify({
                'success': False,
                'error': f'Preset with ID {preset_id} not found'
            }), 404

        if not _can_access_preset(current_user, preset):
            return jsonify({'success': False, 'error': 'Permission denied'}), 403

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
        current_user = auth_manager.get_current_user() if auth_manager else None
        if not current_user or not current_user.get('id'):
            return jsonify({'success': False, 'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}), 401
        user_id = current_user['id']
        is_admin = _is_admin(user_id)

        data = request.json or {}

        # Check if preset exists
        existing = prompt_preset_repo.get_prompt_preset(preset_id)
        if not existing:
            return jsonify({
                'success': False,
                'error': f'Preset with ID {preset_id} not found'
            }), 404

        if not _can_access_preset(current_user, existing):
            return jsonify({'success': False, 'error': 'Permission denied'}), 403

        # System presets are managed by YAML config and cannot be edited
        if existing.get('is_system'):
            return jsonify({
                'success': False,
                'error': 'System presets cannot be edited (managed by config/game_modes.yaml)'
            }), 403

        # Enforce ownership unless admin
        if not is_admin and existing.get('owner_id') != user_id:
            return jsonify({
                'success': False,
                'error': 'Permission denied'
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

        if is_admin:
            updated = prompt_preset_repo.update_prompt_preset(preset_id, **update_kwargs)
        else:
            updated = prompt_preset_repo.update_prompt_preset_for_owner(preset_id, user_id, **update_kwargs)

        if not updated:
            return jsonify({
                'success': False,
                'error': 'Failed to update preset'
            }), 500

        preset = prompt_preset_repo.get_prompt_preset(preset_id)

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
        current_user = auth_manager.get_current_user() if auth_manager else None
        if not current_user or not current_user.get('id'):
            return jsonify({'success': False, 'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}), 401
        user_id = current_user['id']
        is_admin = _is_admin(user_id)

        # Check if preset exists first
        existing = prompt_preset_repo.get_prompt_preset(preset_id)
        if not existing:
            return jsonify({
                'success': False,
                'error': f'Preset with ID {preset_id} not found'
            }), 404

        if not _can_access_preset(current_user, existing):
            return jsonify({'success': False, 'error': 'Permission denied'}), 403

        # System presets are managed by YAML config and cannot be deleted
        if existing.get('is_system'):
            return jsonify({
                'success': False,
                'error': 'System presets cannot be deleted (managed by config/game_modes.yaml)'
            }), 403

        # Enforce ownership unless admin
        if not is_admin and existing.get('owner_id') != user_id:
            return jsonify({
                'success': False,
                'error': 'Permission denied'
            }), 403

        if is_admin:
            deleted = prompt_preset_repo.delete_prompt_preset(preset_id)
        else:
            deleted = prompt_preset_repo.delete_prompt_preset_for_owner(preset_id, user_id)

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
