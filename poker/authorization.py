"""
Authorization service for My Poker Face.

Provides role-based access control (RBAC) through groups and permissions.
"""
import logging
from functools import wraps
from typing import Optional, Set

from flask import jsonify

logger = logging.getLogger(__name__)


class AuthorizationService:
    """Service for checking user permissions and authorization."""

    def __init__(self, user_repo, auth_manager):
        """Initialize the authorization service.

        Args:
            user_repo: UserRepository instance for database access
            auth_manager: AuthManager instance for getting current user
        """
        self.user_repo = user_repo
        self.auth_manager = auth_manager

    def get_current_user_id(self) -> Optional[str]:
        """Get the current user's ID.

        Returns:
            User ID if authenticated, None otherwise
        """
        user = self.auth_manager.get_current_user()
        return user.get('id') if user else None

    def get_user_permissions(self, user_id: str) -> Set[str]:
        """Get all permissions for a user.

        Args:
            user_id: The user's ID

        Returns:
            Set of permission names the user has
        """
        permissions = self.user_repo.get_user_permissions(user_id)
        return set(permissions)

    def has_permission(self, user_id: str, permission: str) -> bool:
        """Check if a user has a specific permission.

        Args:
            user_id: The user's ID
            permission: The permission name to check

        Returns:
            True if user has the permission, False otherwise
        """
        permissions = self.get_user_permissions(user_id)
        return permission in permissions

    def current_user_has_permission(self, permission: str) -> bool:
        """Check if the current user has a specific permission.

        Args:
            permission: The permission name to check

        Returns:
            True if current user has the permission, False otherwise
        """
        user_id = self.get_current_user_id()
        if not user_id:
            return False
        return self.has_permission(user_id, permission)

    def require_permission(self, permission: str):
        """Decorator to require a specific permission for a route.

        Args:
            permission: The permission name required

        Returns:
            Decorator function
        """
        def decorator(f):
            @wraps(f)
            def decorated_function(*args, **kwargs):
                user = self.auth_manager.get_current_user()
                if not user:
                    return jsonify({
                        'error': 'Authentication required',
                        'code': 'AUTH_REQUIRED'
                    }), 401

                user_id = user.get('id')
                if not user_id:
                    return jsonify({
                        'error': 'Invalid user session',
                        'code': 'INVALID_SESSION'
                    }), 401

                if not self.has_permission(user_id, permission):
                    logger.warning(
                        f"User {user_id} denied access: missing permission '{permission}'"
                    )
                    return jsonify({
                        'error': 'Permission denied',
                        'code': 'PERMISSION_DENIED',
                        'required_permission': permission
                    }), 403

                return f(*args, **kwargs)
            return decorated_function
        return decorator


# Global authorization service instance (set during app initialization)
authorization_service: Optional[AuthorizationService] = None


def init_authorization(user_repo, auth_manager) -> AuthorizationService:
    """Initialize the global authorization service.

    Args:
        user_repo: UserRepository instance
        auth_manager: AuthManager instance

    Returns:
        The initialized AuthorizationService
    """
    global authorization_service
    authorization_service = AuthorizationService(user_repo, auth_manager)
    return authorization_service


def get_authorization_service() -> Optional[AuthorizationService]:
    """Get the global authorization service instance.

    Returns:
        AuthorizationService if initialized, None otherwise
    """
    return authorization_service


def require_permission(permission: str):
    """Convenience decorator for requiring permissions.

    Uses the global authorization service. Checks permissions directly
    instead of creating nested decorators on each request.

    Args:
        permission: The permission name required

    Returns:
        Decorator function
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not authorization_service:
                logger.error("Authorization service not initialized")
                return jsonify({
                    'error': 'Server configuration error',
                    'code': 'AUTH_NOT_CONFIGURED'
                }), 500

            # Check permission directly instead of creating nested decorator
            user = authorization_service.auth_manager.get_current_user()
            if not user:
                return jsonify({
                    'error': 'Authentication required',
                    'code': 'AUTH_REQUIRED'
                }), 401

            user_id = user.get('id')
            if not user_id:
                return jsonify({
                    'error': 'Invalid user session',
                    'code': 'INVALID_SESSION'
                }), 401

            if not authorization_service.has_permission(user_id, permission):
                logger.warning(
                    f"User {user_id} denied access: missing permission '{permission}'"
                )
                return jsonify({
                    'error': 'Permission denied',
                    'code': 'PERMISSION_DENIED',
                    'required_permission': permission
                }), 403

            return f(*args, **kwargs)
        return decorated_function
    return decorator
