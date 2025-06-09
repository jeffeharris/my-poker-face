# My Poker Face - Technical Release Plan v1.0

## Executive Summary

This document outlines the technical requirements, infrastructure plan, and development roadmap for releasing My Poker Face as a web application. The focus is on creating a cost-effective, scalable solution with built-in analytics and user feedback capabilities.

## Current State Analysis

### Critical Issues to Fix

#### 🔴 Security (Must fix before any public release)
1. **Hardcoded Secret Key** - `flask_app/ui_web.py:34`
   - Replace with environment variable
   - Generate secure random key
   
2. **No Authentication** 
   - Anyone can join any game
   - No user accounts or profiles
   
3. **Exposed API Endpoints**
   - No rate limiting
   - No input validation
   - No CORS configuration

#### 🟡 Stability Issues
1. **In-Memory Game Storage** - Games lost on restart
2. **No WebSocket Error Handling** - Disconnections crash games
3. **Hardcoded Player Names** - Poor UX
4. **Missing Tests** - Core game logic untested

#### 🟢 Feature Gaps
1. **No Multiplayer** - Can't play human vs human
2. **No Game Rooms** - No private games
3. **No Mobile Support** - UI not responsive
4. **No Analytics** - Can't track usage or errors

## Hosting Strategy

### Option 1: Serverless (Recommended for v1.0)
**Platform**: Vercel + Supabase
- **Frontend**: React app on Vercel (free tier)
- **Backend**: Vercel Functions for API
- **Database**: Supabase PostgreSQL (free tier)
- **WebSockets**: Supabase Realtime
- **Cost**: $0-20/month for moderate usage

**Pros**:
- Zero cost to start
- Auto-scaling
- Built-in analytics
- Easy deployment

**Cons**:
- WebSocket limitations
- Cold starts
- Vendor lock-in

### Option 2: Container-Based
**Platform**: Railway.app or Render.com
- **Stack**: Docker container with Flask + React
- **Database**: Managed PostgreSQL
- **WebSockets**: Native Socket.IO
- **Cost**: $5-20/month

**Pros**:
- Full control
- Better WebSocket support
- Easier local development

**Cons**:
- Fixed monthly cost
- Manual scaling
- More complex setup

### Option 3: Traditional VPS
**Platform**: DigitalOcean Droplet or AWS Lightsail
- **Stack**: Nginx + Gunicorn + Flask
- **Database**: PostgreSQL on same server
- **Cost**: $5-10/month

**Pros**:
- Complete control
- Predictable costs
- Can run multiple apps

**Cons**:
- Manual everything
- No auto-scaling
- Requires DevOps knowledge

## Development Roadmap

### Phase 1: Security & Stability (Week 1)

```python
# 1. Environment-based configuration
# config.py
import os
from datetime import timedelta

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or os.urandom(32)
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(hours=24)
    
    # Database
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///poker.db')
    
    # Redis for game state
    REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379')
    
    # Rate limiting
    RATELIMIT_STORAGE_URL = os.environ.get('REDIS_URL', 'memory://')
```

```python
# 2. Add rate limiting ✅ COMPLETED
# Rate limits are now configurable via environment variables
# See docs/RATE_LIMITING.md for full configuration details
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri=os.environ.get('REDIS_URL')
)

@app.route('/api/game/action', methods=['POST'])
@limiter.limit(os.environ.get('RATE_LIMIT_GAME_ACTION', '60 per minute'))
def game_action():
    # Game action logic
```

```python
# 3. Replace in-memory storage with Redis
import redis
import json

class RedisGameStore:
    def __init__(self, redis_url):
        self.redis = redis.from_url(redis_url)
        
    def save_game(self, game_id, game_state):
        self.redis.setex(
            f"game:{game_id}", 
            86400,  # 24 hour TTL
            json.dumps(game_state)
        )
        
    def load_game(self, game_id):
        data = self.redis.get(f"game:{game_id}")
        return json.loads(data) if data else None
```

### Phase 2: User System (Week 2) 🚧 IN PROGRESS

Basic authentication system implemented:
- ✅ Guest login with session management
- ✅ JWT token generation for API auth
- ✅ Game ownership tracking
- ✅ Protected endpoints for user's games
- ✅ React authentication hook and UI
- 🔄 Google OAuth integration (prepared but not active)

See `docs/AUTHENTICATION.md` for full details.

```python
# Current implementation in poker/auth.py
from poker.auth import AuthManager

auth_manager = AuthManager(app, persistence)

# Guest login endpoint
@app.route('/api/auth/login', methods=['POST'])
def login():
    # Handles guest and future OAuth login
    
# Protected endpoint example
@app.route('/api/my-games')
@auth_manager.require_auth
def my_games():
    user = auth_manager.get_current_user()
    # Returns games owned by authenticated user
```

### Phase 3: Analytics Integration (Week 3)

```javascript
// 1. PostHog for product analytics (free tier)
// React component
import posthog from 'posthog-js'

posthog.init('YOUR_API_KEY', {
    api_host: 'https://app.posthog.com',
    autocapture: false  // Control what we track
})

// Track game events
const trackGameEvent = (event, properties) => {
    posthog.capture(event, {
        ...properties,
        game_id: gameId,
        session_id: sessionId,
        timestamp: new Date().toISOString()
    })
}

// Example events to track
trackGameEvent('game_started', { 
    num_players: 4, 
    ai_personalities: ['Gordon', 'Bob Ross'] 
})
trackGameEvent('hand_completed', { 
    winner: 'player1', 
    pot_size: 500 
})
trackGameEvent('player_action', { 
    action: 'raise', 
    amount: 100 
})
```

```python
# 2. Sentry for error tracking (free tier)
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration

sentry_sdk.init(
    dsn="YOUR_SENTRY_DSN",
    integrations=[FlaskIntegration()],
    traces_sample_rate=0.1,
    environment="production"
)

# Automatic error tracking + custom events
@app.errorhandler(Exception)
def handle_exception(e):
    sentry_sdk.capture_exception(e)
    return jsonify(error="Internal server error"), 500
```

### Phase 4: In-App Feedback (Week 3)

```javascript
// React feedback widget
const FeedbackWidget = () => {
    const [isOpen, setIsOpen] = useState(false)
    const [feedback, setFeedback] = useState('')
    const [type, setType] = useState('bug')
    
    const submitFeedback = async () => {
        await fetch('/api/feedback', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({
                type,
                message: feedback,
                game_state: getCurrentGameState(),
                user_agent: navigator.userAgent,
                timestamp: new Date().toISOString()
            })
        })
        
        // Track in analytics
        posthog.capture('feedback_submitted', { type })
        
        toast.success('Thanks for your feedback!')
        setIsOpen(false)
    }
    
    return (
        <>
            <button 
                className="feedback-button"
                onClick={() => setIsOpen(true)}
            >
                Feedback
            </button>
            
            {isOpen && (
                <div className="feedback-modal">
                    <select value={type} onChange={e => setType(e.target.value)}>
                        <option value="bug">Bug Report</option>
                        <option value="feature">Feature Request</option>
                        <option value="praise">Something Good</option>
                    </select>
                    <textarea 
                        value={feedback}
                        onChange={e => setFeedback(e.target.value)}
                        placeholder="Tell us what's on your mind..."
                    />
                    <button onClick={submitFeedback}>Send</button>
                </div>
            )}
        </>
    )
}
```

### Phase 5: Deployment Pipeline (Week 4)

```yaml
# GitHub Actions deployment
name: Deploy to Production

on:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Run tests
        run: |
          pip install -r requirements.txt
          python -m pytest
          
  deploy:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - name: Deploy to Vercel
        run: vercel --prod --token=${{ secrets.VERCEL_TOKEN }}
```

## Cost Analysis

### Monthly Cost Breakdown (100 daily active users)

**Serverless Option (Vercel + Supabase)**
- Vercel: $0 (free tier)
- Supabase: $0 (free tier)
- PostHog: $0 (free tier)
- Sentry: $0 (free tier)
- **Total: $0/month**

**Container Option (Railway)**
- Railway: $5 (starter)
- PostgreSQL: $5
- Redis: $5
- **Total: $15/month**

**Scale Considerations**
- 1,000 users/day: ~$20-50/month
- 10,000 users/day: ~$100-300/month
- OpenAI API costs: ~$0.10-0.50 per game

## MVP Feature Set

### Must Have (v1.0)
- [ ] Secure authentication (guest accounts)
- [ ] Game rooms with invite links
- [ ] Basic analytics (games played, errors)
- [ ] In-app feedback button
- [ ] Mobile responsive UI
- [ ] Error recovery & reconnection

### Nice to Have (v1.1)
- [ ] User profiles & stats
- [ ] Leaderboards
- [ ] Tournament mode
- [ ] Social login (Google/GitHub)
- [ ] Replay system
- [ ] Custom personalities

### Future (v2.0)
- [ ] Multiplayer (human vs human)
- [ ] Spectator mode
- [ ] Mobile apps
- [ ] Premium features
- [ ] API for third-party clients

## Timeline

**Week 1**: Security fixes, testing, Redis integration
**Week 2**: Authentication, game rooms
**Week 3**: Analytics, feedback widget
**Week 4**: Deployment, monitoring
**Week 5**: Bug fixes, performance tuning
**Week 6**: Launch to friends & family

## Success Metrics

### Technical
- Page load time < 2s
- Game action latency < 500ms
- 99.9% uptime
- Zero critical security issues

### User Engagement
- 50% D1 retention
- 20% D7 retention
- Average session > 15 minutes
- 10% of users submit feedback

### Growth
- 100 users in first week
- 500 users in first month
- < $50/month hosting costs
- Positive user feedback score

## Next Steps

1. **Immediate** (Today)
   - Fix hardcoded secret key
   - Set up Sentry account
   - Create staging environment

2. **This Week**
   - Implement Redis game storage
   - Add basic rate limiting
   - Set up CI/CD pipeline

3. **Next Week**
   - Build authentication system
   - Create feedback widget
   - Deploy to staging

The key is to start small with the serverless option, validate the game with real users, then scale up infrastructure as needed. Analytics and feedback collection should be built in from day one to guide development priorities.