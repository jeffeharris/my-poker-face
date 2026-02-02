"""User management routes for admin dashboard."""

import logging

from flask import Blueprint, jsonify, request

from ..extensions import user_repo, auth_manager
from poker.authorization import require_permission

logger = logging.getLogger(__name__)

user_bp = Blueprint('user', __name__)


@user_bp.route('/api/admin/users', methods=['GET'])
@require_permission('can_access_admin_tools')
def list_users():
    """List all users with their stats and groups.

    Returns:
        JSON with list of users, each containing:
        - id, email, name, picture, is_guest, created_at, last_login
        - groups: list of group names
        - stats: { total_cost, hands_played, games_completed, last_active }
    """
    try:
        users = user_repo.get_all_users()

        # Enrich with stats
        for user in users:
            user['stats'] = user_repo.get_user_stats(user['id'])

        return jsonify({
            'success': True,
            'users': users
        })
    except Exception as e:
        logger.error(f"Error listing users: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@user_bp.route('/api/admin/users/<user_id>/groups', methods=['POST'])
@require_permission('can_access_admin_tools')
def assign_user_group(user_id: str):
    """Assign a user to a group.

    Request body:
        { "group": "admin" }

    Returns:
        JSON with success status
    """
    try:
        data = request.get_json()
        group_name = data.get('group')

        if not group_name:
            return jsonify({
                'success': False,
                'error': 'Group name is required'
            }), 400

        # Get the current admin user for audit trail
        current_user = auth_manager.get_current_user()
        assigned_by = current_user.get('id') if current_user else None

        success = user_repo.assign_user_to_group(user_id, group_name, assigned_by)

        if success:
            logger.info(f"User {user_id} assigned to group '{group_name}' by {assigned_by}")
            return jsonify({
                'success': True,
                'message': f'User assigned to {group_name} group'
            })
        else:
            return jsonify({
                'success': False,
                'error': f'Group {group_name} not found'
            }), 404

    except ValueError as e:
        # Guest user restriction or other validation error
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400

    except Exception as e:
        logger.error(f"Error assigning user to group: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@user_bp.route('/api/admin/users/<user_id>/groups/<group_name>', methods=['DELETE'])
@require_permission('can_access_admin_tools')
def remove_user_group(user_id: str, group_name: str):
    """Remove a user from a group.

    Returns:
        JSON with success status
    """
    try:
        # Get the current admin user for logging
        current_user = auth_manager.get_current_user()
        current_user_id = current_user.get('id') if current_user else None

        # Prevent admin from removing themselves from admin group
        if user_id == current_user_id and group_name == 'admin':
            return jsonify({
                'success': False,
                'error': 'You cannot remove yourself from the admin group'
            }), 400

        # Prevent removing the last admin from the system
        if group_name == 'admin':
            admin_count = user_repo.count_users_in_group('admin')
            if admin_count <= 1:
                return jsonify({
                    'success': False,
                    'error': 'Cannot remove the last admin from the system'
                }), 400

        success = user_repo.remove_user_from_group(user_id, group_name)

        if success:
            logger.info(f"User {user_id} removed from group '{group_name}' by {current_user_id}")
            return jsonify({
                'success': True,
                'message': f'User removed from {group_name} group'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'User was not in that group'
            }), 404

    except Exception as e:
        logger.error(f"Error removing user from group: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@user_bp.route('/api/groups', methods=['GET'])
@require_permission('can_access_admin_tools')
def list_groups():
    """List all available groups.

    Returns:
        JSON with list of groups, each containing:
        - id, name, description, is_system, created_at
    """
    try:
        groups = user_repo.get_all_groups()
        return jsonify({
            'success': True,
            'groups': groups
        })
    except Exception as e:
        logger.error(f"Error listing groups: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
