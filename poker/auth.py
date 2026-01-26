"""
Authentication module for My Poker Face.

Provides session-based authentication with optional Google OAuth support.
"""
import os
import secrets
import logging
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from typing import Optional, Dict, Any

from flask import session, request, jsonify, redirect, url_for
import jwt

logger = logging.getLogger(__name__)

# JWT configuration
JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', secrets.token_hex(32))
JWT_ALGORITHM = 'HS256'
JWT_EXPIRATION_DELTA = timedelta(days=7)

# OAuth state expiration (10 minutes)
OAUTH_STATE_EXPIRATION = timedelta(minutes=10)


class AuthManager:
    """Manages authentication for the poker application."""

    def __init__(self, app=None, persistence=None, oauth=None):
        self.app = app
        self.persistence = persistence
        self.oauth = oauth
        if app:
            self.init_app(app)
    
    def init_app(self, app):
        """Initialize the auth manager with a Flask app."""
        self.app = app
        
        # Configure session
        app.config['SESSION_TYPE'] = 'filesystem'
        app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
        
        # Add auth endpoints
        self._register_routes()
    
    def _register_routes(self):
        """Register authentication routes."""
        @self.app.route('/api/auth/login', methods=['POST'])
        def login():
            """Login with username/password or as guest."""
            data = request.json or {}
            
            if data.get('guest'):
                # Guest login
                guest_name = data.get('name', 'Guest')
                user_data = self.create_guest_user(guest_name)
                
                response = jsonify({
                    'success': True,
                    'user': user_data,
                    'token': self.generate_token(user_data)
                })
                
                # Set a long-lived cookie for guest ID (30 days)
                is_prod = os.environ.get('FLASK_ENV') == 'production'
                response.set_cookie(
                    'guest_id',
                    user_data['id'],
                    max_age=30*24*60*60,  # 30 days
                    httponly=True,
                    secure=is_prod,  # Required for HTTPS
                    samesite='Lax'  # Lax works for same-site requests
                )
                
                return response
            
            # Username/password login (for future implementation)
            username = data.get('username')
            password = data.get('password')
            
            if not username or not password:
                return jsonify({
                    'success': False,
                    'error': 'Username and password required'
                }), 400
            
            # For now, just create a user session
            # In production, this would validate against a user database
            user_data = {
                'id': f'user_{secrets.token_hex(8)}',
                'username': username,
                'name': username,
                'is_guest': False,
                'created_at': datetime.utcnow().isoformat()
            }
            
            session['user'] = user_data
            session.permanent = True
            
            return jsonify({
                'success': True,
                'user': user_data,
                'token': self.generate_token(user_data)
            })
        
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
            
            return response
        
        @self.app.route('/api/auth/me', methods=['GET'])
        def get_current_user_route():
            """Get the current authenticated user with permissions."""
            user = self.get_current_user()
            if user:
                # Enrich user with permissions from database
                user_id = user.get('id')
                if user_id and self.persistence:
                    permissions = self.persistence.get_user_permissions(user_id)
                    user['permissions'] = list(permissions)
                else:
                    user['permissions'] = []
                return jsonify({'user': user})
            return jsonify({'user': None})
        
        @self.app.route('/api/auth/google/login', methods=['GET'])
        def google_login():
            """Initiate Google OAuth flow."""
            # Check if OAuth is configured
            if not self.oauth or not hasattr(self.oauth, 'google'):
                return jsonify({
                    'success': False,
                    'error': 'Google OAuth not configured'
                }), 503

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
                name = user_info.get('name', email.split('@')[0] if email and '@' in email else 'User')
                picture = user_info.get('picture')

                if not google_sub or not email:
                    logger.error("Missing required user info from Google")
                    return redirect(f"{config.FRONTEND_URL}/?auth=error&message=missing_user_info")

                # Check if user already exists by email
                existing_user = self.persistence.get_user_by_email(email)

                # Get guest_id if this was a linking attempt
                guest_id = session.pop('oauth_guest_id', None)

                if existing_user:
                    # User already exists
                    if guest_id and not existing_user.get('linked_guest_id'):
                        # Guest trying to link to existing Google account
                        # Check if guest has games to transfer
                        games_transferred = self.persistence.transfer_game_ownership(
                            guest_id, existing_user['id'], existing_user['name']
                        )
                        if games_transferred > 0:
                            logger.info(f"Transferred {games_transferred} games from {guest_id} to {existing_user['id']}")

                    # Update last login
                    self.persistence.update_user_last_login(existing_user['id'])
                    user_data = existing_user

                else:
                    # Create new user
                    try:
                        user_data = self.persistence.create_google_user(
                            google_sub=google_sub,
                            email=email,
                            name=name,
                            picture=picture,
                            linked_guest_id=guest_id
                        )

                        # Transfer games from guest if linking
                        if guest_id:
                            games_transferred = self.persistence.transfer_game_ownership(
                                guest_id, user_data['id'], user_data['name']
                            )
                            if games_transferred > 0:
                                logger.info(f"Transferred {games_transferred} games from {guest_id} to {user_data['id']}")

                    except sqlite3.IntegrityError as e:
                        # Email already exists (race condition)
                        logger.warning(f"Race condition creating user: {e}")
                        existing_user = self.persistence.get_user_by_email(email)
                        if existing_user:
                            user_data = existing_user
                        else:
                            return redirect(f"{config.FRONTEND_URL}/?auth=error&message=user_creation_failed")

                # Security: Regenerate session before setting authenticated user
                session.clear()

                # Set session
                session['user'] = {
                    'id': user_data['id'],
                    'email': user_data.get('email'),
                    'name': user_data['name'],
                    'picture': user_data.get('picture'),
                    'is_guest': False,
                    'created_at': user_data.get('created_at', datetime.utcnow().isoformat())
                }
                session.permanent = True

                logger.info(f"User {user_data['id']} logged in via Google OAuth")

                # Redirect to frontend with success
                return redirect(f"{config.FRONTEND_URL}/?auth=success")

            except Exception as e:
                logger.exception(f"Google OAuth callback error: {e}")
                return redirect(f"{config.FRONTEND_URL}/?auth=error&message=oauth_failed")
    
    def create_guest_user(self, name: str) -> Dict[str, Any]:
        """Create a guest user session based on name."""
        import re
        # Create a deterministic guest ID from the name (lowercase, alphanumeric only)
        sanitized_name = re.sub(r'[^a-z0-9]', '', name.lower())
        if not sanitized_name:
            sanitized_name = 'guest'
        guest_id = f'guest_{sanitized_name}'

        user_data = {
            'id': guest_id,
            'name': name,
            'is_guest': True,
            'created_at': datetime.utcnow().isoformat()
        }

        session['user'] = user_data
        session.permanent = True

        return user_data
    
    def get_current_user(self) -> Optional[Dict[str, Any]]:
        """Get the current user from session, JWT token, or guest cookie."""
        # Check session first
        if 'user' in session:
            return session['user']

        # Check for JWT token in Authorization header
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
            try:
                payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
                return payload.get('user')
            except jwt.InvalidTokenError:
                pass

        # Check for guest_id cookie and restore session if valid
        guest_id = request.cookies.get('guest_id')
        if guest_id and guest_id.startswith('guest_'):
            # Extract name from guest_id (e.g., 'guest_jeff' -> 'Jeff')
            name_part = guest_id[6:]  # Remove 'guest_' prefix
            display_name = name_part.capitalize() if name_part else 'Guest'
            user = {
                'id': guest_id,
                'name': display_name,
                'is_guest': True,
                'created_at': datetime.utcnow().isoformat()
            }
            session['user'] = user
            session.permanent = True
            return user

        return None
    
    def generate_token(self, user_data: Dict[str, Any]) -> str:
        """Generate a JWT token for the user."""
        payload = {
            'user': user_data,
            'exp': datetime.utcnow() + JWT_EXPIRATION_DELTA
        }
        return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    
    def require_auth(self, f):
        """Decorator to require authentication for a route."""
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = self.get_current_user()
            if not user:
                return jsonify({
                    'error': 'Authentication required',
                    'code': 'AUTH_REQUIRED'
                }), 401
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