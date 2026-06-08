---
purpose: WebSocket review follow-up — server-side hardening (frame-version guard, async-mode checks, socket rate-limit, default error handler)
type: guide
created: 2026-06-07
last_updated: 2026-06-07
---

# WebSocket hardening (2026-06-07)

Follow-up to the WebSocket review and the `bug-fix-tournament` two-hand-flicker
fix. That branch removed the *client-side* socket leak (the flicker's source) and
diagnosed the prod WS 1002 storm. This change adds the server-side defense-in-depth
the review flagged, plus the startup checks that would have surfaced the 1002 misconfig.

## What shipped

### 1. State-version guard (defense-in-depth for the flicker class)
`flask_app/state_version.py` — a process-global, strictly-increasing counter
(`next_state_version()`, atomic via `itertools.count`). Stamped onto:
- the socket `update_game_state` push (`handlers/game_handler.py`), and
- the REST `/api/game-state` cold-load (`routes/game_routes.py`).

Client (`gameStore.applyGameState`) drops any **socket** frame whose `state_version`
is `<=` the last applied one — so a stale frame from a leaked/orphaned socket or a
late-draining sequencer beat can't regress the table to an earlier hand. An
**authoritative** REST refresh (`applyGameState(data, true)` in `usePokerGame`)
bypasses the guard and *resets* the baseline, so a server restart (which resets the
global counter) can never wedge a client into dropping every frame. Frames without a
version (older server) are never dropped (back-compat). The leak fix removes the
*source*; this removes the *symptom* regardless of how many sockets leak.

### 2. Startup async-mode checks (`__init__.py:_log_async_runtime`)
The existing PRH-24 check only verified gevent monkey-patching was *active* — it
logged `active=True` under the gevent-websocket worker and stayed silent while the
1002 storm raged (its blind spot). Added:
- **Transport-pairing check:** ERROR when running under the gevent-websocket gunicorn
  worker with `async_mode=threading` (the simple_websocket vs geventwebsocket mismatch
  that throws 1002 on close/upgrade). Fix: `SOCKETIO_ASYNC_MODE=gevent`.
- **Multi-worker check:** ERROR when gunicorn `-w > 1` in prod, because the socket
  rate limiter, cash presence registry, and world ticker are all single-process and
  no Socket.IO `message_queue` is configured (room emits from background tasks won't
  fan out across workers).

`_detect_gunicorn_runtime()` parses worker class + count from `sys.argv` (gunicorn
leaves it intact in workers). Logging-only — no behavior change.

### 3. Socket rate-limit hardening (`socket_rate_limit.py`)
- **Per-connection anonymous keying:** unauthenticated sockets now bucket on
  `request.sid` (`sid:<id>`) instead of collapsing into one shared `'anonymous'`
  key — that shared bucket let one bad actor rate-limit *all* anonymous users. Authed
  users/guests still key on their stable id.
- **Idle-key pruning:** an opportunistic sweep (`_maybe_sweep`, every 5 min) drops
  keys whose newest timestamp is older than 1 h. The per-key hot-path prune only freed
  keys that were hit again, so a `(event, caller)` pair fired once leaked forever.

### 4. Default socket error handler (`register_socket_events`)
`@sio.on_error_default` — logs any unhandled exception in a socket handler (with sid +
event for correlation) and emits a recoverable `game_error` to the offending client so
it re-syncs immediately, instead of the action silently stalling until the client's 30s
`aiThinking` safety-net refresh.

## Not done (deliberately)
- Connection-level throttling / bounded reconnect attempts (review #5) — best handled
  at Caddy, not app code.
- `player_action` admin-override consistency (review #7) — the omission is intentional
  (an admin shouldn't take game actions on a user's behalf).

## Validation
- TS: `tsc --noEmit` clean; full vitest 271 passed / 2 skipped (incl. new
  `gameStore.version.test.ts`); eslint clean.
- Python: new `tests/test_state_version.py` + `tests/test_socket_rate_limit.py`;
  `state_version` monotonicity/uniqueness validated directly; ruff clean on new files.
  (Full flask suite runs in Docker — run `python3 scripts/test.py test_socket_rate_limit
  test_state_version` before merge.)
