"""Push-device registration for async-friends notifications.

A client (the iOS app today) registers its APNs device token here after the OS
grants push permission, so the dispatcher can reach the user when it's their
turn and the app is closed. Auth is the same bearer/session as the rest of the
API — a device always belongs to the authenticated user.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from flask_app import extensions

logger = logging.getLogger(__name__)

device_bp = Blueprint('device', __name__)

_VALID_PLATFORMS = {'ios', 'android', 'web'}


@device_bp.route('/api/devices/register', methods=['POST'])
def register_device():
    """Register (or refresh) a push token for the current user."""
    current_user = extensions.auth_manager.get_current_user() if extensions.auth_manager else None
    user_id = current_user.get('id') if current_user else None
    if not user_id:
        return jsonify({'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}), 401

    data = request.json or {}
    token = (data.get('token') or '').strip()
    platform = (data.get('platform') or 'ios').lower()
    if not token:
        return jsonify({'error': 'Missing device token', 'code': 'MISSING_TOKEN'}), 400
    if platform not in _VALID_PLATFORMS:
        return jsonify(
            {'error': f'Invalid platform: {platform}', 'valid': sorted(_VALID_PLATFORMS)}
        ), 400

    extensions.device_repo.register(user_id, platform, token)
    return jsonify({'ok': True})


@device_bp.route('/api/devices/unregister', methods=['POST'])
def unregister_device():
    """Drop a push token (e.g. on logout / permission revoked)."""
    current_user = extensions.auth_manager.get_current_user() if extensions.auth_manager else None
    user_id = current_user.get('id') if current_user else None
    if not user_id:
        return jsonify({'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}), 401

    token = ((request.json or {}).get('token') or '').strip()
    if not token:
        return jsonify({'error': 'Missing device token', 'code': 'MISSING_TOKEN'}), 400
    extensions.device_repo.remove(user_id, token)
    return jsonify({'ok': True})
