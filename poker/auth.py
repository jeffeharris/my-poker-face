"""
Authentication module for My Poker Face.

Provides session-based authentication with optional Google OAuth support.
"""

import logging
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Dict, Optional

import jwt
from flask import jsonify, redirect, request, session
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

logger = logging.getLogger(__name__)

# JWT configuration
JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', secrets.token_hex(32))

# Signed-cookie support for guest_id (T1-26).
# The raw guest_id cookie was previously format-checked only — anyone
# who knew or guessed a valid-format guest_id could set their own
# cookie to that value and impersonate the target. Signing with the
# app SECRET_KEY makes the cookie value unforgeable: an attacker who
# doesn't have the secret can't produce a valid signature for an
# arbitrary guest_id.
#
# The signed cookie value is `<guest_id>.<signature>`. Verification
# happens via `_unsign_guest_id`; if signature is missing or invalid,
# the cookie is rejected and a fresh guest session is created. This
# is acceptable because:
#   - Zero production users today, so no migration burden
#   - The guest session is recreatable (it's a guest!) — worst case
#     a returning user loses their guest_id and gets a new one
_GUEST_ID_SIGNER_SALT = 'guest-id-v1'
JWT_ALGORITHM = 'HS256'
JWT_EXPIRATION_DELTA = timedelta(days=7)

# OAuth state expiration (10 minutes)
OAUTH_STATE_EXPIRATION = timedelta(minutes=10)
GUEST_ID_PATTERN = re.compile(r'^guest_[a-f0-9]{32}$')


class AuthManager:
    """Manages authentication for the poker application."""

    def __init__(self, app=None, user_repo=None, oauth=None):
        self.app = app
        self.user_repo = user_repo
        self.oauth = oauth
        if app:
            self.init_app(app)

    def init_app(self, app):
        """Initialize the auth manager with a Flask app."""
        self.app = app

        # Configure session
        app.config['SESSION_TYPE'] = 'filesystem'
        app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
        app.config['SESSION_COOKIE_HTTPONLY'] = True
        app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
        app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') == 'production'

        # Add auth endpoints
        self._register_routes()

    @staticmethod
    def _exempt_from_rate_limit(f):
        """Mark a route as exempt from rate limiting."""
        try:
            from flask_app.extensions import limiter

            if limiter:
                return limiter.exempt(f)
        except ImportError:
            logger.warning("Could not import limiter for rate limit exemption")
        return f

    def _register_routes(self):
        """Register authentication routes."""

        @self.app.route('/api/auth/login', methods=['POST'])
        def login():
            """Login with username/password or as guest."""
            data = request.json or {}

            if data.get('guest'):
                # Guest login
                guest_name = data.get('name', 'Guest')
                guest_name = re.sub(r'[\x00-\x1f\x7f]', '', str(guest_name)).strip()[:50] or 'Guest'

                # Read and verify the existing guest_id cookie (signed since T1-26 fix).
                # Unsigning returns None for forged / expired / unsigned values;
                # callers fall through to create_guest_user generating a fresh ID.
                existing_guest_id = self._unsign_guest_id(request.cookies.get('guest_id'))
                guest_id = existing_guest_id if self._is_valid_guest_id(existing_guest_id) else None
                user_data = self.create_guest_user(guest_name, guest_id=guest_id)

                # Check for existing tracking cookie, generate if needed
                tracking_id = request.cookies.get('guest_tracking_id')
                if not tracking_id:
                    tracking_id = str(uuid.uuid4())
                user_data['tracking_id'] = tracking_id

                response = jsonify(
                    {'success': True, 'user': user_data, 'token': self.generate_token(user_data)}
                )

                # Set a long-lived cookie for guest ID (30 days), signed
                # with the app SECRET_KEY so the value can't be forged by
                # an attacker who knows the format (T1-26 fix).
                is_prod = os.environ.get('FLASK_ENV') == 'production'
                response.set_cookie(
                    'guest_id',
                    self._sign_guest_id(user_data['id']),
                    max_age=30 * 24 * 60 * 60,  # 30 days
                    httponly=True,
                    secure=is_prod,
                    samesite='Lax',
                )

                # Set tracking cookie for hand counting (30 days)
                response.set_cookie(
                    'guest_tracking_id',
                    tracking_id,
                    max_age=30 * 24 * 60 * 60,
                    httponly=True,
                    secure=is_prod,
                    samesite='Lax',
                )

                response.set_cookie(
                    'guest_name',
                    guest_name,
                    max_age=30 * 24 * 60 * 60,
                    httponly=True,
                    secure=is_prod,
                    samesite='Lax',
                )

                return response

            # Username/password login (for future implementation)
            username = data.get('username')
            password = data.get('password')

            if not username or not password:
                return jsonify({'success': False, 'error': 'Username and password required'}), 400

            # For now, just create a user session
            # In production, this would validate against a user database
            user_data = {
                'id': f'user_{secrets.token_hex(8)}',
                'username': username,
                'name': username,
                'is_guest': False,
                'created_at': datetime.utcnow().isoformat(),
            }

            session['user'] = user_data
            session.permanent = True

            return jsonify(
                {'success': True, 'user': user_data, 'token': self.generate_token(user_data)}
            )

        @self.app.route('/api/auth/logout', methods=['POST'])
        def logout():
            """Logout the current user."""
            # Get user before removing from session
            user = session.get('user')
            session.pop('user', None)

            response = jsonify({'success': True})

            # Clear guest_id cookie if logging out a guest
            if user and user.get('is_guest'):
                response.set_cookie('guest_id', '', expires=0)
                response.set_cookie('guest_tracking_id', '', expires=0)
                response.set_cookie('guest_name', '', expires=0)

            return response

        @self.app.route('/api/auth/me', methods=['GET'])
        @self._exempt_from_rate_limit
        def get_current_user_route():
            """Get the current authenticated user with permissions."""
            user = self.get_current_user()
            if user:
                # Enrich user with permissions from database
                user_id = user.get('id')
                if user_id and self.user_repo:
                    permissions = self.user_repo.get_user_permissions(user_id)
                    user['permissions'] = list(permissions)
                else:
                    user['permissions'] = []
                # Enrich with the user's custom avatar + AI-visible bio (both
                # work for guests too). The frontend prefers avatar_url over the
                # Google `picture` when rendering the identity chip.
                if user_id:
                    try:
                        from flask_app.extensions import (
                            user_avatar_service,
                            user_prefs_repo,
                        )

                        if user_avatar_service:
                            avatar_url = user_avatar_service.get_avatar_url(user_id)
                            if avatar_url:
                                user['avatar_url'] = avatar_url
                        if user_prefs_repo:
                            user['bio'] = user_prefs_repo.get_bio(user_id)
                    except Exception as e:
                        logger.debug(f"Could not enrich user with profile data: {e}")
                return jsonify({'user': user})
            return jsonify({'user': None})

        @self.app.route('/api/auth/google/login', methods=['GET'])
        def google_login():
            """Initiate Google OAuth flow."""
            # Check if OAuth is configured
            if not self.oauth or not hasattr(self.oauth, 'google'):
                return jsonify({'success': False, 'error': 'Google OAuth not configured'}), 503

            # Store current guest_id if user is a guest (for linking)
            current_user = self.get_current_user()
            if current_user and current_user.get('is_guest'):
                session['oauth_guest_id'] = current_user['id']

            # Generate CSRF state token with expiration
            state = secrets.token_urlsafe(32)
            session['oauth_state'] = state
            session['oauth_state_created'] = datetime.utcnow().isoformat()

            # Build redirect URI from FRONTEND_URL to ensure correct domain
            # (url_for may not have correct host behind reverse proxy)
            from flask_app import config

            frontend_url = config.FRONTEND_URL.rstrip('/')
            # Replace frontend URL with backend callback path
            # FRONTEND_URL is like https://mypokerfacegame.com
            # We need https://mypokerfacegame.com/api/auth/google/callback
            redirect_uri = f"{frontend_url}/api/auth/google/callback"

            return self.oauth.google.authorize_redirect(redirect_uri, state=state)

        @self.app.route('/api/auth/google/callback', methods=['GET', 'POST'])
        def google_callback():
            """Handle Google OAuth callback."""
            from flask_app import config

            # Check if OAuth is configured
            if not self.oauth or not hasattr(self.oauth, 'google'):
                return redirect(f"{config.FRONTEND_URL}/?auth=error&message=oauth_not_configured")

            # Verify CSRF state
            state = request.args.get('state')
            stored_state = session.pop('oauth_state', None)
            state_created = session.pop('oauth_state_created', None)

            if not state or not stored_state or state != stored_state:
                logger.warning("OAuth state mismatch - possible CSRF attack")
                return redirect(f"{config.FRONTEND_URL}/?auth=error&message=invalid_state")

            # Check state expiration
            if state_created:
                created_time = datetime.fromisoformat(state_created)
                if datetime.utcnow() - created_time > OAUTH_STATE_EXPIRATION:
                    logger.warning("OAuth state expired")
                    return redirect(f"{config.FRONTEND_URL}/?auth=error&message=state_expired")

            try:
                # Exchange code for token
                token = self.oauth.google.authorize_access_token()
                user_info = token.get('userinfo')

                if not user_info:
                    # Fetch user info if not in token
                    user_info = self.oauth.google.userinfo()

                google_sub = user_info.get('sub')
                email = user_info.get('email')
                name = user_info.get(
                    'name', email.split('@')[0] if email and '@' in email else 'User'
                )
                picture = user_info.get('picture')

                if not google_sub or not email:
                    logger.error("Missing required user info from Google")
                    return redirect(f"{config.FRONTEND_URL}/?auth=error&message=missing_user_info")

                # Check if user already exists by email
                existing_user = self.user_repo.get_user_by_email(email)

                # Get guest_id if this was a linking attempt
                guest_id = session.pop('oauth_guest_id', None)

                if existing_user:
                    # User already exists
                    if guest_id and not existing_user.get('linked_guest_id'):
                        # Guest trying to link to existing Google account
                        # Check if guest has games to transfer
                        games_transferred = self.user_repo.transfer_game_ownership(
                            guest_id, existing_user['id'], existing_user['name']
                        )
                        if games_transferred > 0:
                            logger.info(
                                f"Transferred {games_transferred} games from {guest_id} to {existing_user['id']}"
                            )

                    # Update last login
                    self.user_repo.update_user_last_login(existing_user['id'])
                    user_data = existing_user

                else:
                    # Create new user
                    try:
                        user_data = self.user_repo.create_google_user(
                            google_sub=google_sub,
                            email=email,
                            name=name,
                            picture=picture,
                            linked_guest_id=guest_id,
                        )

                        # Transfer games from guest if linking
                        if guest_id:
                            games_transferred = self.user_repo.transfer_game_ownership(
                                guest_id, user_data['id'], user_data['name']
                            )
                            if games_transferred > 0:
                                logger.info(
                                    f"Transferred {games_transferred} games from {guest_id} to {user_data['id']}"
                                )

                    except sqlite3.IntegrityError as e:
                        # Email already exists (race condition)
                        logger.warning(f"Race condition creating user: {e}")
                        existing_user = self.user_repo.get_user_by_email(email)
                        if existing_user:
                            user_data = existing_user
                        else:
                            return redirect(
                                f"{config.FRONTEND_URL}/?auth=error&message=user_creation_failed"
                            )

                # Security: Regenerate session before setting authenticated user
                session.clear()

                # Set session
                session['user'] = {
                    'id': user_data['id'],
                    'email': user_data.get('email'),
                    'name': user_data['name'],
                    'picture': user_data.get('picture'),
                    'is_guest': False,
                    'created_at': user_data.get('created_at', datetime.utcnow().isoformat()),
                }
                session.permanent = True

                logger.info(f"User {user_data['id']} logged in via Google OAuth")

                # Redirect to frontend with success
                return redirect(f"{config.FRONTEND_URL}/?auth=success")

            except Exception as e:
                logger.exception(f"Google OAuth callback error: {e}")
                return redirect(f"{config.FRONTEND_URL}/?auth=error&message=oauth_failed")

    @staticmethod
    def _is_valid_guest_id(guest_id: Optional[str]) -> bool:
        """Validate guest ID format."""
        if not guest_id:
            return False
        if GUEST_ID_PATTERN.match(guest_id):
            return True
        # Dev mode: accept old-style deterministic IDs (e.g. guest_jeff)
        if os.environ.get('FLASK_ENV') != 'production':
            return bool(re.match(r'^guest_[a-z0-9]+$', guest_id))
        return False

    def _get_guest_id_signer(self) -> URLSafeTimedSerializer:
        """Build a signer keyed on the app SECRET_KEY at call time.

        Read at call time (not class-init) so the app's SECRET_KEY is
        whatever the active Flask app is configured with — keeps the
        helper compatible with test fixtures that rotate SECRET_KEY.
        """
        secret_key = self.app.config.get('SECRET_KEY') if self.app else None
        if not secret_key:
            # Fall back to JWT_SECRET_KEY if SECRET_KEY isn't set.
            secret_key = JWT_SECRET_KEY
        return URLSafeTimedSerializer(secret_key, salt=_GUEST_ID_SIGNER_SALT)

    def _sign_guest_id(self, guest_id: str) -> str:
        """Produce a signed cookie value for a guest_id.

        The signed payload is `<guest_id>.<timestamp>.<signature>`; the
        signature commits to all three components plus the app
        SECRET_KEY, so an attacker who doesn't have the secret can't
        forge a valid cookie for an arbitrary guest_id.
        """
        return self._get_guest_id_signer().dumps(guest_id)

    def _unsign_guest_id(
        self,
        signed_value: Optional[str],
        max_age_seconds: int = 30 * 24 * 60 * 60,
    ) -> Optional[str]:
        """Verify a signed guest_id cookie and return the raw id.

        Returns None when the signature is missing or invalid, or when
        the cookie is older than `max_age_seconds`. Callers should treat
        None as "no valid guest session" and fall through to creating
        a fresh guest.

        Backwards compatibility: in dev mode, accepts unsigned cookies
        that match the legacy format. This lets local development
        workflows that pre-date the signing change continue working
        without forcing a re-login. In production, only signed cookies
        are accepted.
        """
        if not signed_value:
            return None
        try:
            return self._get_guest_id_signer().loads(
                signed_value,
                max_age=max_age_seconds,
            )
        except SignatureExpired:
            logger.debug("Guest ID cookie expired")
            return None
        except BadSignature:
            # In dev, fall through to legacy format check below — old
            # cookies from before this change are unsigned and would
            # never pass the signature check.
            if os.environ.get('FLASK_ENV') != 'production':
                if self._is_valid_guest_id(signed_value):
                    return signed_value
            logger.debug("Guest ID cookie has invalid signature")
            return None

    def create_guest_user(self, name: str, guest_id: Optional[str] = None) -> Dict[str, Any]:
        """Create a guest user session based on name."""
        is_prod = os.environ.get('FLASK_ENV') == 'production'
        if not self._is_valid_guest_id(guest_id):
            if is_prod:
                guest_id = f'guest_{uuid.uuid4().hex}'
            else:
                # Dev mode: deterministic IDs for stable local admin access
                sanitized = re.sub(r'[^a-z0-9]', '', name.lower()) or 'guest'
                guest_id = f'guest_{sanitized}'

        user_data = {
            'id': guest_id,
            'name': name,
            'is_guest': True,
            'created_at': datetime.utcnow().isoformat(),
        }

        session['user'] = user_data
        session.permanent = True

        return user_data

    def get_current_user(self) -> Optional[Dict[str, Any]]:
        """Get the current user from session, JWT token, or guest cookie."""
        user = None

        # Check session first
        if 'user' in session:
            user = session['user']
        else:
            # Check for JWT token in Authorization header
            auth_header = request.headers.get('Authorization')
            if auth_header and auth_header.startswith('Bearer '):
                token = auth_header.split(' ')[1]
                try:
                    payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
                    user = payload.get('user')
                except jwt.InvalidTokenError as e:
                    logger.debug(f"Invalid JWT token: {e}")

                # Check for guest_id cookie and restore session if valid.
                # The cookie is signed (T1-26); _unsign_guest_id returns
                # None for forged / expired / unsigned values, and the
                # is_valid_guest_id check still runs as a defense-in-depth
                # format check after unsigning.
            if not user:
                guest_id = self._unsign_guest_id(request.cookies.get('guest_id'))
                if self._is_valid_guest_id(guest_id):
                    display_name = request.cookies.get('guest_name', 'Guest')
                    display_name = (
                        re.sub(r'[\x00-\x1f\x7f]', '', str(display_name)).strip()[:50] or 'Guest'
                    )
                    user = {
                        'id': guest_id,
                        'name': display_name,
                        'is_guest': True,
                        'created_at': datetime.utcnow().isoformat(),
                    }
                    session['user'] = user
                    session.permanent = True

        # Attach tracking_id from cookie for guest users (shallow copy to avoid mutating session dict)
        if user and user.get('is_guest'):
            tracking_id = request.cookies.get('guest_tracking_id')
            if tracking_id:
                try:
                    uuid.UUID(tracking_id)
                    user = {**user, 'tracking_id': tracking_id}
                except ValueError:
                    logger.warning("Invalid guest_tracking_id cookie value")

        return user

    def generate_token(self, user_data: Dict[str, Any]) -> str:
        """Generate a JWT token for the user."""
        payload = {'user': user_data, 'exp': datetime.utcnow() + JWT_EXPIRATION_DELTA}
        return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

    def require_auth(self, f):
        """Decorator to require authentication for a route."""

        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = self.get_current_user()
            if not user:
                return jsonify({'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}), 401
            return f(*args, **kwargs)

        return decorated_function

    def optional_auth(self, f):
        """Decorator to optionally check authentication."""

        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Just ensure user info is available if authenticated
            # but don't require it
            return f(*args, **kwargs)

        return decorated_function
