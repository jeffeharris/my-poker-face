# Guest Login Flow and Game Persistence Analysis

## Overview
After examining the codebase, I've identified how guest login and game persistence work, as well as potential issues with returning guest users.

## Guest Login Flow

### Frontend (React)
1. **LoginForm Component** (`/react/react/src/components/auth/LoginForm.tsx`)
   - Guest login is handled by calling `onLogin(playerName, true)` where the second parameter indicates guest status
   - The form displays a "Play as Guest" button

2. **App Component** (`/react/react/src/App.tsx`)
   - Uses `useAuth` hook to manage authentication state
   - Stores game state in localStorage: `pokerGameState` with structure:
     ```javascript
     {
       currentView,
       gameId,
       playerName,
       timestamp
     }
     ```
   - When authenticated, sets `playerName` from user data

3. **useAuth Hook** (`/react/react/src/hooks/useAuth.ts`)
   - Stores authentication data in localStorage:
     - `currentUser`: User object with id, name, is_guest, created_at
     - `authToken`: JWT token for authentication
   - On mount, checks localStorage first before checking backend
   - Guest login endpoint: `POST /api/auth/login` with `{ guest: true, name: "Guest Name" }`

### Backend (Flask)
1. **AuthManager** (`/poker/auth.py`)
   - Guest users are created with:
     - ID format: `guest_{random_hex}`
     - `is_guest: true` flag
     - Session-based authentication with `session.permanent = True`
     - JWT token generation for API authentication
   - Session lifetime: 7 days (`PERMANENT_SESSION_LIFETIME`)

2. **Game Persistence** (`/poker/persistence.py`)
   - Games are saved with `owner_id` and `owner_name`
   - Guest users have a limit of 1 saved game
   - Games are filtered by `owner_id` when listing

## Key Issues with Guest Game Persistence

### 1. Guest User Identity Loss
When a guest user closes the browser and returns:
- If localStorage is cleared, the guest user identity is lost
- A new guest login creates a NEW guest ID (e.g., `guest_abc123` → `guest_xyz789`)
- Previous games are inaccessible because they're tied to the old guest ID

### 2. Session-Based Authentication Limitations
- Flask sessions are stored in the filesystem (`flask_session/` directory)
- Sessions expire after 7 days
- If the session cookie is lost, the user cannot recover their guest account

### 3. Game Loading Authorization
From `/flask_app/ui_web.py`:
```python
# Line 236: Check if the game exists and belongs to the current user
current_user = auth_manager.get_current_user()
saved_games = persistence.list_games(owner_id=current_user.get('id') if current_user else None, limit=50)
```
- Games are strictly filtered by owner_id
- No mechanism to recover games from a lost guest session

### 4. LocalStorage Reliability
- The app stores `currentUser` and `authToken` in localStorage
- If localStorage is cleared (privacy mode, browser settings, etc.), authentication is lost
- The `pokerGameState` in localStorage can reference a `gameId` that the user no longer has access to

## Potential Solutions

### 1. Guest User Recovery Mechanism
- Store a recovery token or code with each guest account
- Allow guests to enter this code to recover their account
- Display the code prominently when a guest creates an account

### 2. Cookie-Based Guest Tracking
- Use a long-lived cookie (separate from session) to track guest users
- Reconnect guest users to their account if the cookie is present

### 3. Game Access Tokens
- Generate shareable links for games that include an access token
- Allow anyone with the link to continue the game

### 4. Email-Based Guest Upgrade
- Allow guests to add an email to "upgrade" their account
- This would persist their games even if they lose their session

### 5. Device Fingerprinting
- Use browser fingerprinting to reconnect returning guests
- Less reliable but could work as a fallback

## Current Workarounds

For now, guest users can maintain access by:
1. Not clearing browser data (localStorage and cookies)
2. Keeping the browser tab open
3. Bookmarking the game URL (though this won't help if session is lost)

## Recommendations

The most user-friendly solution would be a combination of:
1. Display a recovery code when guests create accounts
2. Implement cookie-based tracking as a fallback
3. Add an optional email field for guests who want to secure their games