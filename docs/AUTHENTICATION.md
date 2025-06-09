# Authentication System

## Overview

My Poker Face includes a flexible authentication system that supports both guest users and authenticated accounts. The system is designed to be simple for casual players while providing the foundation for more advanced features.

## Current Features

### Guest Authentication
- Quick and easy - just enter your name
- No registration required
- Session persists across page refreshes
- Games are tied to browser session

### Session Management
- Server-side session storage using Flask sessions
- JWT tokens for API authentication
- Automatic session restoration on page reload
- Secure cookie-based session handling

## Architecture

### Backend (`poker/auth.py`)

The `AuthManager` class provides:
- Session-based authentication
- JWT token generation and validation
- User session management
- Authentication decorators for protected routes

Key endpoints:
- `POST /api/auth/login` - Login as guest or with credentials
- `POST /api/auth/logout` - Clear current session
- `GET /api/auth/me` - Get current user info

### Frontend (`react/src/hooks/useAuth.ts`)

The `useAuth` hook provides:
- Authentication state management
- Login/logout functions
- Automatic token handling
- Session persistence

## Usage

### Guest Login

```typescript
// In React component
const { login } = useAuth();

const handleGuestLogin = async (name: string) => {
  const result = await login(name, true); // true = guest
  if (result.success) {
    // User is now logged in as guest
  }
};
```

### Protected API Endpoints

```python
# In Flask routes
@app.route('/api/my-games')
@auth_manager.require_auth
def my_games():
    user = auth_manager.get_current_user()
    # Only authenticated users can access this
```

### Optional Authentication

```python
# Allow both guests and authenticated users
@app.route('/api/new-game')
def new_game():
    user = auth_manager.get_current_user()  # May be None
    if user:
        # Use authenticated user's name
        player_name = user.get('name')
    else:
        # Use provided name or default
        player_name = request.json.get('playerName', 'Player')
```

## Game Ownership

Games track their owner via `owner_id`:
- Authenticated users can see their game history
- Guest games are temporary and tied to session
- `/api/my-games` endpoint lists user's games

## Security Considerations

1. **Session Security**
   - Use secure, httpOnly cookies in production
   - Generate strong secret keys
   - Set appropriate session timeouts

2. **JWT Tokens**
   - Tokens expire after 7 days by default
   - Use strong JWT secret key
   - Tokens are stored in localStorage

3. **CORS Configuration**
   - Currently allows all origins for development
   - Should be restricted in production

## Future Enhancements

### Google OAuth Integration
The foundation is in place for Google OAuth:
- `GoogleOAuthProvider` class ready for implementation
- UI shows "Sign in with Google" button (disabled)
- Callback endpoint prepared

### User Accounts
Infrastructure exists for full user accounts:
- Password hashing utilities
- User creation methods
- Database schema ready

### Enhanced Features
With full authentication, these become possible:
- Persistent game history
- Player statistics
- Achievements
- Friend lists
- Private games

## Configuration

### Environment Variables

```env
# Session secret key (required)
SECRET_KEY=your-secret-key-here

# JWT secret (optional, auto-generated if not set)
JWT_SECRET_KEY=your-jwt-secret

# Google OAuth (future)
GOOGLE_CLIENT_ID=your-client-id
GOOGLE_CLIENT_SECRET=your-client-secret
```

### Session Configuration

```python
# Flask session settings
app.config['SESSION_TYPE'] = 'filesystem'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
```

## Testing Authentication

1. **Guest Login Flow**
   ```bash
   # Start the app
   docker compose up
   
   # Visit http://localhost:5173
   # Enter any name and click "Play as Guest"
   ```

2. **Check Authentication Status**
   ```bash
   curl http://localhost:5000/api/auth/me
   ```

3. **Protected Endpoint**
   ```bash
   # Without auth - returns 401
   curl http://localhost:5000/api/my-games
   
   # With auth - returns user's games
   curl http://localhost:5000/api/my-games \
     -H "Cookie: session=..."
   ```

## Troubleshooting

### Session Not Persisting
- Check SECRET_KEY is set in environment
- Verify cookies are enabled in browser
- Check Flask session configuration

### JWT Token Issues
- Verify JWT_SECRET_KEY is consistent
- Check token expiration
- Ensure Authorization header format: `Bearer <token>`

### CORS Errors
- Verify Flask-CORS is configured
- Check allowed origins match your frontend URL
- Ensure credentials are included in requests