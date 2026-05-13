"""Shared utilities for Flask route blueprints."""

from flask import Blueprint, request
from poker.authorization import require_permission


def register_admin_guard(bp: Blueprint) -> None:
    """Register a before_request hook that requires admin permission.

    Skips CORS preflight (OPTIONS) requests so Flask-CORS can handle them.
    """
    admin_check = require_permission('can_access_admin_tools')

    @bp.before_request
    def _enforce_admin_access():
        if request.method == 'OPTIONS':
            return None
        result = admin_check(lambda: None)()
        if result is not None:
            return result
