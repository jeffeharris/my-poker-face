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