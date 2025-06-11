# Rate Limiting Configuration

## Overview

My Poker Face includes built-in rate limiting to protect the API from abuse and control costs associated with OpenAI API usage. Rate limiting is implemented using Flask-Limiter with Redis as the storage backend.

## Configuration

Rate limits can be configured via environment variables in your `.env` file:

```env
# Default rate limits for all endpoints
RATE_LIMIT_DEFAULT="200 per day, 50 per hour"

# Specific endpoint limits
RATE_LIMIT_NEW_GAME="10 per hour"
RATE_LIMIT_GAME_ACTION="60 per minute"
RATE_LIMIT_CHAT_SUGGESTIONS="100 per hour"
RATE_LIMIT_GENERATE_PERSONALITY="15 per hour"
```

### Rate Limit Format

Rate limits use the format: `"number per period"` where period can be:
- `second`
- `minute`
- `hour`
- `day`

Multiple limits can be specified by separating them with commas:
```env
RATE_LIMIT_DEFAULT="200 per day, 50 per hour, 10 per minute"
```

## Protected Endpoints

The following endpoints have specific rate limits:

| Endpoint | Default Limit | Purpose |
|----------|---------------|---------|
| `POST /api/new-game` | 10 per hour | Prevents game spam |
| `POST /api/game/<id>/action` | 60 per minute | Prevents action flooding |
| `POST /api/game/<id>/chat-suggestions` | 100 per hour | Controls AI chat costs |
| `POST /api/generate_personality` | 15 per hour | Controls AI generation costs |

All other endpoints use the default rate limits.

## Redis Configuration

### Docker Environment

When running with Docker Compose, Redis is automatically configured and connected. No additional setup is required.

### Local Development

For local development without Docker:

1. Install Redis locally:
   ```bash
   # macOS
   brew install redis
   brew services start redis

   # Ubuntu/Debian
   sudo apt-get install redis-server
   sudo systemctl start redis
   ```

2. Configure the Redis URL in your `.env`:
   ```env
   REDIS_URL=redis://localhost:6379
   ```

### Custom Redis Port

If you need to use a custom Redis port (e.g., to avoid conflicts):

```env
REDIS_PORT=6380
REDIS_URL=redis://localhost:6380
```

## Rate Limit Responses

When a rate limit is exceeded, the API returns a 429 status code with details:

```json
{
  "error": "Rate limit exceeded",
  "message": "10 per hour limit exceeded",
  "retry_after": 3600
}
```

The `retry_after` field indicates how many seconds to wait before the limit resets.

## Monitoring Rate Limits

Rate limit headers are included in API responses:

- `X-RateLimit-Limit`: The rate limit for this endpoint
- `X-RateLimit-Remaining`: Number of requests remaining
- `X-RateLimit-Reset`: Unix timestamp when the limit resets

## Disabling Rate Limiting

For development or testing, you can effectively disable rate limiting by setting very high limits:

```env
RATE_LIMIT_DEFAULT="100000 per day"
RATE_LIMIT_NEW_GAME="10000 per hour"
RATE_LIMIT_GAME_ACTION="10000 per minute"
```

## Cost Control

The rate limits help control OpenAI API costs by limiting:

1. **Game Creation**: Prevents rapid game creation which initializes AI players
2. **Chat Suggestions**: Limits AI-generated chat suggestions
3. **Personality Generation**: Controls AI personality generation requests

## Troubleshooting

### Redis Connection Errors

If you see Redis connection errors:

1. **Docker**: Ensure Redis service is running:
   ```bash
   docker compose ps redis
   docker compose logs redis
   ```

2. **Local**: Check Redis is running:
   ```bash
   redis-cli ping
   # Should return: PONG
   ```

3. **Port Conflicts**: If port 6379 is in use, configure a different port in `.env`

### Rate Limit Not Working

1. Check Redis connection:
   ```bash
   redis-cli
   > KEYS *
   ```

2. Verify environment variables are loaded:
   ```bash
   docker exec poker-backend env | grep RATE_LIMIT
   ```

3. Check Flask logs for rate limiter initialization messages