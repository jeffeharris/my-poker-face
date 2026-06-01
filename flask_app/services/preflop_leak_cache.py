"""Process-local cache for the computed preflop-leak report.

The report (VPIP bars + chart leaks + recent diff + trend) is recomputed from
the player's whole history on every panel open. The load is fast (~70ms after
the query fix) but the multi-pass grading adds up, and repeat opens are pure
waste. This memoizes the response, keyed by ``(owner, depth, window)`` and
gated by the owner's PRE_FLOP decision count.

Self-invalidating: the count is part of the staleness check, so a new hand →
count changes → cache miss → recompute. No explicit invalidation needed.

Process-local is correct here: prod runs a single gunicorn worker and dev is one
threaded process (see docs explorer note), so one module dict is shared across
all concurrent requests. Mirrors the lock + double-check pattern in
``sandbox_resolver.py``.
"""

from __future__ import annotations

import threading
from typing import Callable, Hashable

# key -> (decision_count, computed_report)
_cache: dict[Hashable, tuple[int, dict]] = {}
_lock = threading.Lock()


def get_or_compute(key: Hashable, count: int, compute: Callable[[], dict]) -> dict:
    """Return the cached report for ``key`` if its stored count matches ``count``,
    else compute it under the lock and store ``(count, report)``."""
    hit = _cache.get(key)
    if hit is not None and hit[0] == count:
        return hit[1]
    with _lock:
        hit = _cache.get(key)  # re-check: another thread may have filled it
        if hit is not None and hit[0] == count:
            return hit[1]
        report = compute()
        _cache[key] = (count, report)
        return report


def clear() -> None:
    """Drop all entries (tests / manual reset)."""
    with _lock:
        _cache.clear()
