"""Double-submit-cookie CSRF protection (PRH-36).

Cookie-authenticated state changes (session + signed guest cookies, all
`SameSite=Lax`) get a first-class CSRF control on top of SameSite:

- ``after_request`` issues a non-HttpOnly ``csrf_token`` cookie (so the SPA's
  JS can read it) whenever one is absent.
- ``before_request`` requires the ``X-CSRF-Token`` header to match that cookie
  on every state-changing (`POST`/`PUT`/`PATCH`/`DELETE`) ``/api/*`` request,
  rejecting a mismatch/absence with ``403 CSRF_FAILED``.

The frontend echoes the cookie value in the header via a global ``fetch``
wrapper (``react/.../utils/csrf.ts``), so all call sites are covered without
per-request plumbing.

Enforcement is gated by ``CSRF_PROTECTION_ENABLED`` (read live from app config
so tests can flip it). It defaults ON in production — where the SPA and API are
same-origin and the cookie is therefore JS-readable — and OFF in development
(cross-origin :5173↔:5000, cookie not readable) and under the test suite. See
``flask_app/config.py``.

Exemptions: CORS preflight (`OPTIONS`), and the auth-bootstrap / OAuth-callback
routes (``/api/auth/login``, ``/api/auth/google/...``) which either run before a
cookie can be established or are external top-level navigations carrying their
own ``state`` token. Socket.IO is a separate transport guarded by the CORS
origin allowlist, not this gate.
"""

from __future__ import annotations

import hmac
import logging
import secrets

from flask import Flask, current_app, jsonify, request

from . import config

logger = logging.getLogger(__name__)

_MUTATING_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})

# Paths exempt from the header check (still receive a token cookie). Kept tiny
# and explicit: only auth bootstrap + the external OAuth callback; the bearer-only
# native token refresh (no cookie auth, so CSRF doesn't apply — native clients have
# no CSRF cookie to send); the Sentry tunnel relay (the browser Sentry SDK's
# transport posts envelopes here and cannot carry our CSRF token — an allowlisted
# forward-only proxy to Sentry ingest, see routes/sentry_relay_routes.py); and the
# public marketing form.
_EXEMPT_EXACT = frozenset(
    {
        '/api/auth/login',
        '/api/auth/token/refresh',
        '/api/event-relay',
        # Public marketing form (static Astro site, no CSRF cookie/SPA wrapper).
        '/api/character-requests',
    }
)
_EXEMPT_PREFIXES = ('/api/auth/google/',)


def _is_protected_request() -> bool:
    """True when the current request must carry a valid CSRF token."""
    if request.method not in _MUTATING_METHODS:
        return False
    path = request.path
    if not path.startswith('/api/'):
        return False
    if path in _EXEMPT_EXACT:
        return False
    return not any(path.startswith(p) for p in _EXEMPT_PREFIXES)


def init_csrf(app: Flask) -> None:
    """Wire the double-submit CSRF before/after-request hooks onto ``app``."""

    app.config.setdefault('CSRF_PROTECTION_ENABLED', config.CSRF_PROTECTION_ENABLED)
    app.config.setdefault('CSRF_COOKIE_NAME', config.CSRF_COOKIE_NAME)
    app.config.setdefault('CSRF_HEADER_NAME', config.CSRF_HEADER_NAME)

    @app.before_request
    def _csrf_protect():
        # Read the flag live (not captured) so tests can toggle it per-app.
        if not current_app.config.get('CSRF_PROTECTION_ENABLED'):
            return None
        if not _is_protected_request():
            return None

        cookie_name = current_app.config['CSRF_COOKIE_NAME']
        header_name = current_app.config['CSRF_HEADER_NAME']
        cookie_token = request.cookies.get(cookie_name)
        header_token = request.headers.get(header_name)

        # Both must be present and equal (constant-time compare). A missing
        # cookie means the client never loaded the SPA bootstrap that mints it,
        # so a forged cross-site request can't satisfy the header either.
        if (
            not cookie_token
            or not header_token
            or not hmac.compare_digest(str(cookie_token), str(header_token))
        ):
            logger.warning(
                "[CSRF] rejected %s %s (cookie=%s header=%s)",
                request.method,
                request.path,
                'present' if cookie_token else 'missing',
                'present' if header_token else 'missing',
            )
            return jsonify(
                {
                    'success': False,
                    'error': 'CSRF validation failed. Refresh the page and try again.',
                    'code': 'CSRF_FAILED',
                }
            ), 403
        return None

    @app.after_request
    def _csrf_set_cookie(response):
        # Always make sure the browser holds a token cookie so the SPA can echo
        # it. Set only when absent (don't rotate every response). Non-HttpOnly
        # by design (JS must read it); SameSite=Lax + Secure in prod mirror the
        # session/guest cookies.
        cookie_name = current_app.config.get('CSRF_COOKIE_NAME', config.CSRF_COOKIE_NAME)
        if not request.cookies.get(cookie_name):
            response.set_cookie(
                cookie_name,
                secrets.token_urlsafe(32),
                max_age=30 * 24 * 60 * 60,  # 30 days, matches the guest cookies
                httponly=False,  # the SPA must read this to echo it in the header
                secure=not config.is_development,
                samesite='Lax',
                path='/',
            )
        return response
