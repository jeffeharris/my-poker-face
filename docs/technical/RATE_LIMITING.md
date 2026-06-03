---
purpose: Rate-limiting configuration for HTTP and Socket.IO endpoints
type: reference
created: 2025-06-08
last_updated: 2026-06-03
---

# Rate Limiting Configuration

## Overview

My Poker Face rate-limits its API to protect against abuse and to cap LLM cost.
Two independent mechanisms exist because they cover different transports:

| Mechanism | Covers | Backed by | Storage |
|-----------|--------|-----------|---------|
| **Flask-Limiter** | HTTP routes (`@limiter.limit(...)`) | `flask_app/extensions.py` (`limiter`, `init_limiter`) | Redis in prod, in-memory in dev |
| **`socket_rate_limit`** | Socket.IO event handlers | `flask_app/socket_rate_limit.py` | Per-process in-memory dict |

Flask-Limiter does **not** support Socket.IO, which is why the second mechanism
exists for real-time events.

## Limit values & format

All limits are defined in `flask_app/config.py` and are env-var overridable.

> **Format note:** the `RATE_LIMIT_DEFAULT` env var is split on **`;`**, not
> commas (`config.py:81`). Per-route limit strings use Flask-Limiter's
> `"N per period"` syntax (period = `second` / `minute` / `hour` / `day`).

### Defaults (`flask_app/config.py`)

| Constant | Default | Applied to | Reference |
|----------|---------|------------|-----------|
| `RATE_LIMIT_DEFAULT` | `10000 per day`, `1000 per hour`, `100 per minute` | All routes (global default) | `config.py:83` |
| `RATE_LIMIT_NEW_GAME` | `10 per hour` | `POST /api/new-game` | `config.py:84` |
| `RATE_LIMIT_GAME_ACTION` | `60 per minute` | `POST /api/game/<id>/action` | `config.py:85` |
| `RATE_LIMIT_GUEST_LOGIN` | `60 per hour` | Fresh guest minting (per IP) | `config.py:90` |
| `RATE_LIMIT_POLLING` | `600 per minute` | High-frequency read-only state polls | `config.py:95` |
| `RATE_LIMIT_CHAT_SUGGESTIONS` | `100 per hour` | AI chat-suggestion endpoints | `config.py:96` |
| `RATE_LIMIT_GENERATE_PERSONALITY` | `15 per hour` | Personality generation | `config.py:97` |
| `RATE_LIMIT_GENERATE_THEME` | `10 per hour` | Theme generation | `config.py:98` |
| `RATE_LIMIT_REGENERATE_AVATAR` | `10 per hour` | Avatar regeneration | `config.py:99` |
| `RATE_LIMIT_GENERATE_IMAGES` | `5 per hour` | Image generation | `config.py:100` |

Setting `RATE_LIMIT_DEFAULT` in the environment replaces the list entirely; e.g.
`RATE_LIMIT_DEFAULT="5000 per day;500 per hour;50 per minute"`.

### `RATE_LIMIT_POLLING`

A single generous per-minute window for cheap, client-driven GET polling. It
**overrides the default limits** on the routes it decorates so that long play
sessions aren't punished by the day/hour caps, while the minute cap still blocks
runaway loops. Applied across game and cash state-poll routes:

- `flask_app/routes/game_routes.py:620` (game-state)
- `flask_app/routes/cash_routes.py:3339, 5275, 5959, 6490`

## Keying (who a limit counts against)

`get_rate_limit_key` (`flask_app/extensions.py`) keys by **authenticated user
id** (`user:<id>`) when a non-guest user is present, otherwise falls back to the
client IP. Guests stay IP-keyed because their id is a resettable cookie; fresh
guest minting is separately throttled per IP via `RATE_LIMIT_GUEST_LOGIN`.
OPTIONS (CORS preflight) requests are exempt (`_skip_options_requests`).

## Storage backend & fail-closed-in-prod

`init_limiter(app)` (`flask_app/extensions.py:181`) chooses storage:

- If `REDIS_URL` is set and reachable → **Redis** (`storage_uri = config.REDIS_URL`).
- If `REDIS_URL` is set but **unreachable in production** → startup **fails
  loudly** with a `RuntimeError` (`extensions.py:203`). Rationale: degrading to
  per-worker in-memory limits silently multiplies every per-IP cap by the worker
  count and breaks shared-state assumptions (presence, world-ticker).
- Dev/test (`config.is_development`) fall back to in-memory with a warning.
- If `REDIS_URL` is unset → in-memory (`memory://`).

The `limiter` is a single app-less `Limiter` created at import
(`extensions.py:147`) and bound via `init_app` so that decorators registered at
route-import time stay attached across every `create_app()`.

## Socket.IO rate limiting

`flask_app/socket_rate_limit.py` provides the `socket_rate_limit(max_calls,
window_seconds)` decorator. It tracks per-`(event_name, user_id)` call
timestamps in a process-local dict, drops events over the limit, logs a warning,
and emits a `rate_limited` event back to the client. Outside a request context
(e.g. tests) it is a no-op.

Current socket limits (`flask_app/routes/game_routes.py`):

| Event handler | Limit | Reference |
|---------------|-------|-----------|
| `on_join` | 20 calls / 10s | `:2306` |
| `handle_player_action` | 10 calls / 10s | `:2335` |
| `handle_send_message` | 5 calls / 10s | `:2464` |
| `on_progress_game` | 5 calls / 10s | `:2521` |

> Because storage is per-process, socket limits are **not** shared across
> workers — acceptable for these short anti-spam windows, but not a global cap.

## 429 responses

A global handler (`flask_app/__init__.py:225`) returns HTTP 429 with:

```json
{
  "error": "Rate limit exceeded",
  "message": "<limit description>",
  "retry_after": <seconds or null>
}
```

Some routes return their own tailored 429 bodies instead — e.g. guest chat
(`code: "GUEST_CHAT_LIMIT"`, `game_routes.py:2069`) and the cash forgiveness ask
(`retry_after_seconds`, `cash_routes.py:3079`).

## Redis setup

### Docker

Redis is wired up automatically by Docker Compose; no extra setup.

### Local development

```bash
# macOS
brew install redis && brew services start redis
# Ubuntu/Debian
sudo apt-get install redis-server && sudo systemctl start redis
```

Then set the URL (in-memory is the default if unset):

```env
REDIS_URL=redis://localhost:6379
```

For a custom port:

```env
REDIS_PORT=6380
REDIS_URL=redis://localhost:6380
```

## Effectively disabling limits (dev/test)

Set very high values (remember the `;` delimiter for the default):

```env
RATE_LIMIT_DEFAULT="100000 per day;100000 per hour;100000 per minute"
RATE_LIMIT_NEW_GAME="10000 per hour"
RATE_LIMIT_GAME_ACTION="10000 per minute"
```

## Troubleshooting

### Redis connection errors

- **Docker:** `docker compose ps redis` / `docker compose logs redis`
- **Local:** `redis-cli ping` → should return `PONG`
- **Prod startup failure:** if the app refuses to start citing an unreachable
  `REDIS_URL`, that's the fail-closed guard (`extensions.py:203`) — fix Redis or
  unset `REDIS_URL` to opt into in-memory explicitly.

### Limits not applying

1. Confirm env vars are loaded: `docker exec poker-backend env | grep RATE_LIMIT`
2. Check the startup log line: `Rate limiter initialized with <Redis|in-memory> storage`
3. Inspect Redis keys: `redis-cli KEYS *`
