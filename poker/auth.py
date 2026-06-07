"""
Authentication module for My Poker Face.

Provides session-based authentication with optional Google OAuth support.
"""

import hashlib
import hmac
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
# Mirror flask_app.config's SECRET_KEY handling: in production a missing OR
# empty JWT_SECRET_KEY is fatal. An empty secret signs forgeable tokens (full
# auth bypass), and `docker-compose.prod.yml` interpolates an unset
# `${JWT_SECRET_KEY}` to "" — a present-but-empty value that os.environ.get
# would otherwise hand back unchecked. JWT_SECRET_KEY is also the fallback
# secret for the signed guest cookies below, so an empty value poisons those too.
# In development, fall back to an ephemeral random secret.
_jwt_is_development = (
    os.environ.get('FLASK_ENV', 'production') == 'development'
    or os.environ.get('FLASK_DEBUG', '0') == '1'
)
JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY')
if not JWT_SECRET_KEY:
    if _jwt_is_development:
        JWT_SECRET_KEY = secrets.token_hex(32)
    else:
        raise RuntimeError(
            "JWT_SECRET_KEY environment variable is required in production. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )

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
# PRH-26: the guest_tracking_id cookie (the hand-quota key) is signed too, so a
# client can't mint a fresh quota by forging/clearing it — an unverifiable
# cookie falls back to the IP-derived stable id (see resolve_guest_tracking_id).
_GUEST_TRACKING_SIGNER_SALT = 'guest-tracking-v1'
JWT_ALGORITHM = 'HS256'
JWT_EXPIRATION_DELTA = timedelta(days=7)

# OAuth state expiration (10 minutes)
OAUTH_STATE_EXPIRATION = timedelta(minutes=10)
GUEST_ID_PATTERN = re.compile(r'^guest_[a-f0-9]{32}$')

# Native Google sign-in (mobile) token verification.
# Mobile apps (iOS/Android) sign in with the native Google SDK, which hands the
# app a Google ID token rather than running the server-side redirect flow. The
# /api/auth/google/native endpoint verifies that token directly. We validate the
# RS256 signature against Google's published JWKS and check issuer/audience
# ourselves so the same code path works for every platform's client ID.
GOOGLE_CERTS_URL = 'https://www.googleapis.com/oauth2/v3/certs'
GOOGLE_ISSUERS = frozenset({'https://accounts.google.com', 'accounts.google.com'})

# Lazily-built, self-caching JWKS client (PyJWT caches signing keys internally
# for `lifespan` seconds, so we don't refetch Google's certs on every sign-in).
_google_jwks_client: Optional['jwt.PyJWKClient'] = None


def _get_google_jwks_client() -> 'jwt.PyJWKClient':
    """Return a process-wide cached PyJWKClient for Google's signing keys."""
    global _google_jwks_client
    if _google_jwks_client is None:
        _google_jwks_client = jwt.PyJWKClient(
            GOOGLE_CERTS_URL, cache_keys=True, lifespan=3600
        )
    return _google_jwks_client


def verify_google_id_token(id_token: str, allowed_audiences) -> Dict[str, Any]:
    """Verify a Google-issued ID token from a native mobile sign-in.

    Validates the RS256 signature against Google's JWKS and enforces issuer,
    audience, expiry, and a verified email. ``allowed_audiences`` is the list of
    accepted OAuth client IDs (one per platform) — the token is accepted if its
    ``aud`` matches any of them.

    Returns the decoded claims on success. Raises ValueError on any failure
    (kept narrow so callers translate it to a single 401).
    """
    if not allowed_audiences:
        raise ValueError("No allowed Google audiences configured")
    if not id_token:
        raise ValueError("Missing id_token")

    try:
        signing_key = _get_google_jwks_client().get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=['RS256'],
            audience=list(allowed_audiences),
            options={'require': ['exp', 'iat', 'aud', 'iss', 'sub']},
        )
    except jwt.PyJWTError as e:
        raise ValueError(f"ID token validation failed: {e}") from e

    if claims.get('iss') not in GOOGLE_ISSUERS:
        raise ValueError(f"Untrusted issuer: {claims.get('iss')}")
    if not claims.get('email'):
        raise ValueError("ID token missing email")
    # Google sets email_verified=False for unverified addresses; reject those so
    # an attacker can't claim someone else's email via an unverified account.
    if claims.get('email_verified') is False:
        raise ValueError("Google email not verified")

    return claims


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

    def _guest_minting_request(self) -> bool:
        """True when this request would mint a brand-new guest (PRH-26).

        A guest login carrying a valid signed `guest_id` cookie is a returning
        guest, not a mint; a non-guest (password) login isn't a mint either.
        Used as the rate-limit's exempt condition so the cap bites only scripted
        minting, never legit users (incl. many distinct users behind one
        shared/CGNAT IP, who each carry their own cookie).
        """
        data = request.get_json(silent=True) or {}
        if not data.get('guest'):
            return False
        existing = self._unsign_guest_id(request.cookies.get('guest_id'))
        return not self._is_valid_guest_id(existing)

    def _guest_login_limit(self):
        """Return a decorator that rate-limits fresh guest minting per IP.

        No-op if the limiter can't be imported. Exempts returning guests and
        password logins via `_guest_minting_request`.
        """
        try:
            from flask_app.config import RATE_LIMIT_GUEST_LOGIN
            from flask_app.extensions import limiter
        except ImportError:
            return lambda f: f
        if not limiter:
            return lambda f: f
        return limiter.limit(
            RATE_LIMIT_GUEST_LOGIN,
            exempt_when=lambda: not self._guest_minting_request(),
        )

    def _register_routes(self):
        """Register authentication routes."""

        @self.app.route('/api/auth/login', methods=['POST'])
        @self._guest_login_limit()
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

                # Resolve the hand-quota tracking id (PRH-7 + PRH-26): a
                # tracking cookie WE signed (proves it's ours, not a forged
                # reset) takes precedence; otherwise the IP-derived stable id,
                # so clearing or forging the cookie can't mint a fresh quota.
                # Only when even the IP is unavailable do we mint a random id.
                tracking_id = self.resolve_guest_tracking_id() or str(uuid.uuid4())
                user_data['tracking_id'] = tracking_id

                # Auth rides the HttpOnly session + signed guest cookies set
                # below; we no longer hand the browser a bearer token to stash
                # in localStorage (PRH-37 — shrinks the XSS blast radius). The
                # server still *accepts* a Bearer header in get_current_user for
                # any non-browser/API client, but browser flows are cookie-only.
                response = jsonify({'success': True, 'user': user_data})

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

                # Set tracking cookie for hand counting (30 days). Signed
                # (PRH-26) so a forged/cleared cookie can't mint a fresh quota
                # — resolve_guest_tracking_id rejects unverifiable values and
                # falls back to the IP-derived id.
                response.set_cookie(
                    'guest_tracking_id',
                    self._sign_tracking_id(tracking_id),
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

            # Username/password login is not implemented. The previous stub
            # accepted ANY credentials and minted a non-guest (guest-limit-free)
            # session — an abuse hole (PRH-26). Refuse until real account auth
            # exists; sign-in goes through Google OAuth or guest login.
            return jsonify(
                {
                    'success': False,
                    'error': 'Password login is not available. Use Google sign-in or continue as a guest.',
                    'code': 'PASSWORD_LOGIN_UNAVAILABLE',
                }
            ), 501

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

                # Get guest_id if this was a linking attempt, then resolve
                # (find-or-create) the user. Shared with the native sign-in path
                # so the two flows can't drift apart.
                guest_id = session.pop('oauth_guest_id', None)
                user_data = self._resolve_google_user(
                    google_sub=google_sub,
                    email=email,
                    name=name,
                    picture=picture,
                    guest_id=guest_id,
                )
                if not user_data:
                    return redirect(
                        f"{config.FRONTEND_URL}/?auth=error&message=user_creation_failed"
                    )

                # Security: Regenerate session before setting authenticated user
                session.clear()

                # Set session
                session['user'] = self._google_session_user(user_data)
                session.permanent = True

                logger.info(f"User {user_data['id']} logged in via Google OAuth")

                # Redirect to frontend with success
                return redirect(f"{config.FRONTEND_URL}/?auth=success")

            except Exception as e:
                logger.exception(f"Google OAuth callback error: {e}")
                return redirect(f"{config.FRONTEND_URL}/?auth=error&message=oauth_failed")

        @self.app.route('/api/auth/google/native', methods=['POST'])
        def google_native_login():
            """Sign in a native (mobile) client from a Google ID token.

            Mobile apps run Google sign-in with the native SDK and POST the
            resulting ID token here. We verify it server-side, find-or-create the
            user (sharing logic with the web callback), and return a JWT the app
            stores in the Keychain/Keystore and sends as ``Authorization: Bearer``
            (also as the Socket.IO connect auth). This path is stateless: no
            session cookie is set, so it works identically across iOS/Android
            without any cookie-jar bridging.

            Body: ``{"id_token": "...", "guest_id": "guest_..."?}``
            (guest_id is optional and only used to migrate a guest's games.)
            """
            from flask_app import config

            data = request.get_json(silent=True) or {}
            id_token = data.get('id_token')
            if not id_token:
                return jsonify({'success': False, 'error': 'id_token is required'}), 400

            allowed_audiences = getattr(config, 'GOOGLE_ALLOWED_AUDIENCES', None) or []
            if not allowed_audiences:
                return (
                    jsonify(
                        {
                            'success': False,
                            'error': 'Native Google sign-in is not configured',
                        }
                    ),
                    503,
                )

            try:
                claims = verify_google_id_token(id_token, allowed_audiences)
            except ValueError as e:
                logger.warning(f"Native Google ID token rejected: {e}")
                return jsonify({'success': False, 'error': 'Invalid Google token'}), 401

            # verify_google_id_token guarantees 'sub' (required) and 'email'
            # (explicitly checked), so these are present here.
            email = claims['email']
            google_sub = claims['sub']
            name = claims.get('name') or (
                email.split('@')[0] if '@' in email else 'User'
            )
            picture = claims.get('picture')

            # The app passes the guest_id it currently holds so an anonymous
            # session's games migrate on first real sign-in. Ignore anything that
            # isn't a well-formed guest id.
            guest_id = data.get('guest_id')
            if guest_id is not None and not self._is_valid_guest_id(guest_id):
                guest_id = None

            user_data = self._resolve_google_user(
                google_sub=google_sub,
                email=email,
                name=name,
                picture=picture,
                guest_id=guest_id,
            )
            if not user_data:
                return (
                    jsonify({'success': False, 'error': 'Could not resolve user'}),
                    500,
                )

            session_user = self._google_session_user(user_data)
            token = self.generate_token(session_user)
            logger.info(f"User {user_data['id']} logged in via Google native sign-in")
            return jsonify({'success': True, 'user': session_user, 'token': token})

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

    def _ip_derived_tracking_id(self) -> Optional[str]:
        """Derive a stable guest tracking id from the client IP (PRH-7).

        Used only when the `guest_tracking_id` cookie is absent. Keying the
        fallback on IP (HMAC'd with the app secret so it isn't guessable or
        cross-correlatable) means clearing cookies no longer mints a fresh
        quota bucket — a cookie-cleared guest maps back to the same id. The
        cookie takes precedence when present, so distinct browsers behind one
        IP stay distinct once issued a cookie.

        Returns None when no client IP is available (caller falls back to a
        random id — no worse than the prior behavior). `request.remote_addr`
        is the real client IP in production (ProxyFix trusts X-Forwarded-For).
        """
        try:
            ip = request.remote_addr
        except Exception:
            ip = None
        if not ip:
            return None
        secret_key = self.app.config.get('SECRET_KEY') if self.app else None
        if not secret_key:
            secret_key = JWT_SECRET_KEY
        if isinstance(secret_key, str):
            secret_key = secret_key.encode('utf-8')
        digest = hmac.new(secret_key, ip.encode('utf-8'), hashlib.sha256).hexdigest()
        return f"ipguest_{digest[:32]}"

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

    def _get_tracking_id_signer(self) -> URLSafeTimedSerializer:
        """Signer for the guest_tracking_id cookie (PRH-26), keyed on SECRET_KEY."""
        secret_key = self.app.config.get('SECRET_KEY') if self.app else None
        if not secret_key:
            secret_key = JWT_SECRET_KEY
        return URLSafeTimedSerializer(secret_key, salt=_GUEST_TRACKING_SIGNER_SALT)

    def _sign_tracking_id(self, tracking_id: str) -> str:
        """Produce a signed cookie value for a guest tracking id."""
        return self._get_tracking_id_signer().dumps(tracking_id)

    def _unsign_tracking_id(
        self,
        signed_value: Optional[str],
        max_age_seconds: int = 30 * 24 * 60 * 60,
    ) -> Optional[str]:
        """Verify a signed guest_tracking_id cookie and return the raw id.

        Returns None for a missing / forged / expired / unsigned value, so the
        caller falls back to the IP-derived id rather than honoring an
        unverifiable cookie (the quota-reset hole). Dev accepts legacy unsigned
        cookies for local convenience (mirrors `_unsign_guest_id`).
        """
        if not signed_value:
            return None
        try:
            return self._get_tracking_id_signer().loads(signed_value, max_age=max_age_seconds)
        except SignatureExpired:
            return None
        except BadSignature:
            if os.environ.get('FLASK_ENV') != 'production':
                return signed_value  # dev: honor legacy unsigned tracking cookie
            return None

    def resolve_guest_tracking_id(self) -> Optional[str]:
        """Resolve the stable hand-quota tracking id for the current request.

        Order (PRH-26): a tracking cookie we signed (proves it's ours, not a
        forged reset) -> the IP-derived stable id (so clearing/forging the
        cookie can't mint a fresh quota) -> None. Login mints a random id only
        when even the IP is unavailable.
        """
        signed = self._unsign_tracking_id(request.cookies.get('guest_tracking_id'))
        if signed:
            return signed
        return self._ip_derived_tracking_id()

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

        # Attach the resolved tracking id for guests (shallow copy to avoid
        # mutating the session dict). resolve_guest_tracking_id (PRH-26) honors
        # only a signed cookie, else the IP-derived stable id — so a forged or
        # cleared cookie can't mint a fresh hand quota.
        if user and user.get('is_guest'):
            tracking_id = self.resolve_guest_tracking_id()
            if tracking_id:
                user = {**user, 'tracking_id': tracking_id}

        return user

    def _resolve_google_user(
        self,
        *,
        google_sub: str,
        email: str,
        name: str,
        picture: Optional[str],
        guest_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Find-or-create the user behind a verified Google identity.

        Shared by the web OAuth callback and the native sign-in endpoint. When a
        ``guest_id`` is supplied (anonymous session linking into a real account),
        the guest's games are transferred to the resolved user.

        Returns the user record, or ``None`` if creation failed unrecoverably
        (callers translate that to their own error response).
        """
        existing_user = self.user_repo.get_user_by_email(email)

        if existing_user:
            if guest_id and not existing_user.get('linked_guest_id'):
                # Guest linking into an existing Google account: move their games.
                games_transferred = self.user_repo.transfer_game_ownership(
                    guest_id, existing_user['id'], existing_user['name']
                )
                if games_transferred > 0:
                    logger.info(
                        f"Transferred {games_transferred} games from {guest_id} to {existing_user['id']}"
                    )
            self.user_repo.update_user_last_login(existing_user['id'])
            return existing_user

        try:
            user_data = self.user_repo.create_google_user(
                google_sub=google_sub,
                email=email,
                name=name,
                picture=picture,
                linked_guest_id=guest_id,
            )
            if guest_id:
                games_transferred = self.user_repo.transfer_game_ownership(
                    guest_id, user_data['id'], user_data['name']
                )
                if games_transferred > 0:
                    logger.info(
                        f"Transferred {games_transferred} games from {guest_id} to {user_data['id']}"
                    )
            return user_data
        except sqlite3.IntegrityError as e:
            # Lost a create race against a concurrent sign-in — fall back to the
            # row the winner inserted.
            logger.warning(f"Race condition creating user: {e}")
            return self.user_repo.get_user_by_email(email)

    @staticmethod
    def _google_session_user(user_data: Dict[str, Any]) -> Dict[str, Any]:
        """Canonical authenticated-user dict for a Google account.

        Used both for the web session and as the JWT payload's ``user`` so the
        cookie and bearer-token paths carry identical identity. Permissions are
        resolved per-request from the user id (see ``/api/auth/me``), not baked
        in here.
        """
        return {
            'id': user_data['id'],
            'email': user_data.get('email'),
            'name': user_data['name'],
            'picture': user_data.get('picture'),
            'is_guest': False,
            'created_at': user_data.get('created_at', datetime.utcnow().isoformat()),
        }

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
