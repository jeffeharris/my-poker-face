"""
Authentication module for My Poker Face.

Provides session-based authentication with optional Google OAuth support.
"""
import os
import secrets
from datetime import datetime, timedelta
from functools import wraps
from typing import Optional, Dict, Any

from flask import session, request, jsonify, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
import jwt

# JWT configuration
JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', secrets.token_hex(32))
JWT_ALGORITHM = 'HS256'
JWT_EXPIRATION_DELTA = timedelta(days=7)


class AuthManager:
    """Manages authentication for the poker application."""
    
    def __init__(self, app=None, persistence=None):
        self.app = app
        self.persistence = persistence
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
                return jsonify({
                    'success': True,
                    'user': user_data,
                    'token': self.generate_token(user_data)
                })
            
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
            session.pop('user', None)
            return jsonify({'success': True})
        
        @self.app.route('/api/auth/me', methods=['GET'])
        def get_current_user():
            """Get the current authenticated user."""
            user = self.get_current_user()
            if user:
                return jsonify({'user': user})
            return jsonify({'user': None})
        
        @self.app.route('/api/auth/google/callback', methods=['GET', 'POST'])
        def google_callback():
            """Handle Google OAuth callback."""
            # This would be implemented with a proper OAuth library
            # For now, return a placeholder response
            return jsonify({
                'success': False,
                'error': 'Google OAuth not yet implemented'
            }), 501
    
    def create_guest_user(self, name: str) -> Dict[str, Any]:
        """Create a guest user session."""
        user_data = {
            'id': f'guest_{secrets.token_hex(8)}',
            'name': name,
            'is_guest': True,
            'created_at': datetime.utcnow().isoformat()
        }
        
        session['user'] = user_data
        session.permanent = True
        
        return user_data
    
    def get_current_user(self) -> Optional[Dict[str, Any]]:
        """Get the current user from session or JWT token."""
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


class BasicAuthProvider:
    """Basic username/password authentication provider."""
    
    def __init__(self, persistence):
        self.persistence = persistence
    
    def create_user(self, username: str, password: str, email: Optional[str] = None) -> Dict[str, Any]:
        """Create a new user with hashed password."""
        # This would store in database in production
        password_hash = generate_password_hash(password)
        user_data = {
            'id': f'user_{secrets.token_hex(8)}',
            'username': username,
            'email': email,
            'password_hash': password_hash,
            'created_at': datetime.utcnow().isoformat()
        }
        # TODO: Save to database
        return user_data
    
    def verify_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """Verify username and password."""
        # TODO: Load from database and check password hash
        # For now, return None (not implemented)
        return None


class GoogleOAuthProvider:
    """Google OAuth authentication provider."""
    
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
    
    def get_auth_url(self, redirect_uri: str) -> str:
        """Get the Google OAuth authorization URL."""
        # This would use a proper OAuth library in production
        # For now, return a placeholder
        return f"https://accounts.google.com/oauth2/auth?client_id={self.client_id}&redirect_uri={redirect_uri}"
    
    def handle_callback(self, code: str) -> Optional[Dict[str, Any]]:
        """Handle the OAuth callback and return user data."""
        # TODO: Implement actual OAuth flow
        return None