"""In-memory rate limiting for Socket.IO event handlers.

Flask-Limiter doesn't support Socket.IO, so this provides a simple
decorator that tracks per-user call timestamps and drops events that
exceed the configured limit, emitting feedback to the client.
"""

import functools
import logging
import threading
import time
from collections import defaultdict

from flask import has_request_context, request
from flask_socketio import emit

from . import extensions

logger = logging.getLogger(__name__)

# Storage: {(event_name, caller_id): [timestamp, ...]}
_call_log: dict[tuple[str, str], list[float]] = defaultdict(list)
_lock = threading.Lock()

# Opportunistic pruning of idle keys (the per-key prune on the hot path only
# frees a key that's hit again — a (event, caller) pair fired once and never
# repeated would leak forever). A key whose newest timestamp is older than
# _SWEEP_MAX_AGE is definitely past every window (the largest configured window
# is seconds, this is an hour), so dropping it can never evict an active limiter.
_SWEEP_INTERVAL_SECONDS = 300.0
_SWEEP_MAX_AGE_SECONDS = 3600.0
_last_sweep = 0.0


def _resolve_caller_id() -> str:
    """Identify the caller for rate-limit bucketing.

    A real (OAuth) account or guest carries a stable id — bucket on it so caps
    bind per-user. An unauthenticated socket has none; bucket those on the
    Socket.IO session id (`request.sid`) rather than collapsing every anonymous
    socket into one shared "anonymous" key, which would let a single bad actor
    rate-limit *all* anonymous users (a shared-bucket false positive) while
    being trivially evadable. Falls back to the literal 'anonymous' only when
    even the sid is unavailable.
    """
    auth_manager = extensions.auth_manager
    user = auth_manager.get_current_user() if auth_manager else None
    if isinstance(user, dict) and user.get('id'):
        return f"user:{user['id']}"
    sid = getattr(request, 'sid', None)
    return f"sid:{sid}" if sid else 'anonymous'


def _maybe_sweep(now: float) -> None:
    """Drop limiter keys gone idle past `_SWEEP_MAX_AGE_SECONDS`. Caller holds `_lock`."""
    global _last_sweep
    if now - _last_sweep < _SWEEP_INTERVAL_SECONDS:
        return
    _last_sweep = now
    cutoff = now - _SWEEP_MAX_AGE_SECONDS
    stale = [key for key, timestamps in _call_log.items() if not timestamps or timestamps[-1] < cutoff]
    for key in stale:
        del _call_log[key]


def socket_rate_limit(max_calls: int, window_seconds: int):
    """Rate-limit a Socket.IO event handler.

    Args:
        max_calls: Maximum number of allowed calls within the window.
        window_seconds: Rolling window duration in seconds.

    Drops events that exceed the limit, logs a warning, and emits
    a 'rate_limited' event back to the client.
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not has_request_context():
                # Outside request context (e.g. in tests) — skip rate limiting
                return fn(*args, **kwargs)

            caller_id = _resolve_caller_id()
            event_name = fn.__name__
            key = (event_name, caller_id)

            now = time.monotonic()
            cutoff = now - window_seconds

            with _lock:
                _maybe_sweep(now)

                # Prune expired entries
                timestamps = _call_log[key]
                active = [t for t in timestamps if t > cutoff]

                if not active:
                    del _call_log[key]

                if len(active) >= max_calls:
                    _call_log[key] = active
                    logger.warning(
                        "Socket rate limit exceeded: event=%s caller=%s (%d/%d in %ds)",
                        event_name,
                        caller_id,
                        len(active),
                        max_calls,
                        window_seconds,
                    )
                    emit(
                        'rate_limited',
                        {
                            'event': event_name,
                            'message': 'Too many requests, please wait a moment.',
                        },
                    )
                    return None

                active.append(now)
                _call_log[key] = active

            return fn(*args, **kwargs)

        return wrapper

    return decorator
