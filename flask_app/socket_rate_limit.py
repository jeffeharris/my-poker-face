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

from flask import has_request_context
from flask_socketio import emit

from .extensions import auth_manager

logger = logging.getLogger(__name__)

# Storage: {(event_name, user_id): [timestamp, ...]}
_call_log: dict[tuple[str, str], list[float]] = defaultdict(list)
_lock = threading.Lock()


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

            user = auth_manager.get_current_user() if auth_manager else None
            user_id = user.get('id', 'anonymous') if isinstance(user, dict) else 'anonymous'
            event_name = fn.__name__
            key = (event_name, user_id)

            now = time.monotonic()
            cutoff = now - window_seconds

            with _lock:
                # Prune expired entries
                timestamps = _call_log[key]
                active = [t for t in timestamps if t > cutoff]

                if not active:
                    del _call_log[key]

                if len(active) >= max_calls:
                    _call_log[key] = active
                    logger.warning(
                        "Socket rate limit exceeded: event=%s user=%s (%d/%d in %ds)",
                        event_name, user_id, len(active), max_calls, window_seconds
                    )
                    emit('rate_limited', {
                        'event': event_name,
                        'message': 'Too many requests, please wait a moment.',
                    })
                    return None

                active.append(now)
                _call_log[key] = active

            return fn(*args, **kwargs)
        return wrapper
    return decorator
