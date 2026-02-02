"""In-memory rate limiting for Socket.IO event handlers.

Flask-Limiter doesn't support Socket.IO, so this provides a simple
decorator that tracks per-user call timestamps and silently drops
events that exceed the configured limit.
"""

import functools
import logging
import time
from collections import defaultdict

from .extensions import auth_manager

logger = logging.getLogger(__name__)

# Storage: {(event_name, user_id): [timestamp, ...]}
_call_log: dict[tuple[str, str], list[float]] = defaultdict(list)


def socket_rate_limit(max_calls: int, window_seconds: int):
    """Rate-limit a Socket.IO event handler.

    Args:
        max_calls: Maximum number of allowed calls within the window.
        window_seconds: Rolling window duration in seconds.

    Silently drops events that exceed the limit and logs a warning.
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                user = auth_manager.get_current_user() if auth_manager else None
            except RuntimeError:
                # Outside request context (e.g. in tests) — skip rate limiting
                return fn(*args, **kwargs)
            user_id = user.get('id') if user else 'anonymous'
            event_name = fn.__name__
            key = (event_name, user_id)

            now = time.monotonic()
            cutoff = now - window_seconds

            # Prune expired entries
            timestamps = _call_log[key]
            _call_log[key] = [t for t in timestamps if t > cutoff]

            if len(_call_log[key]) >= max_calls:
                logger.warning(
                    "Socket rate limit exceeded: event=%s user=%s (%d/%d in %ds)",
                    event_name, user_id, len(_call_log[key]), max_calls, window_seconds
                )
                return None

            _call_log[key].append(now)
            return fn(*args, **kwargs)
        return wrapper
    return decorator
