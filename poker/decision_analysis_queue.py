"""Redis-backed queue for off-hot-path decision-analysis jobs.

The gameplay worker enqueues a JSON job (see ``controllers._analyze_decision``)
instead of running the equity Monte Carlo inline; the out-of-band
``decision_analysis_worker`` drains the queue on a separate core/box. Keeps the
heavy analytics CPU off the single gevent gameplay core.

The queue is best-effort/lossy by design: analytics are "mostly OK if delayed"
and a dropped job (Redis flush/restart) just means one fewer logged decision —
gameplay never depends on it.
"""
import json
import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)

QUEUE_KEY = "decision_analysis:jobs"
_redis = None


def _get_redis():
    """Lazily build a Redis client from REDIS_URL (shared with the rate limiter)."""
    global _redis
    if _redis is None:
        import redis  # local import — only needed when the queue is enabled

        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        _redis = redis.from_url(url, socket_timeout=5)
    return _redis


def enqueue_decision_analysis_job(job: dict) -> None:
    """Push one analysis job onto the queue (LPUSH). Cheap — JSON-encode + send."""
    _get_redis().lpush(QUEUE_KEY, json.dumps(job))


def dequeue_batch(max_items: int = 50, timeout: int = 5) -> List[dict]:
    """Block up to ``timeout`` s for the first job, then drain up to ``max_items``
    more without blocking. Returns [] on idle timeout."""
    r = _get_redis()
    first = r.brpop(QUEUE_KEY, timeout=timeout)
    if not first:
        return []
    items = [json.loads(first[1])]
    for _ in range(max_items - 1):
        nxt = r.rpop(QUEUE_KEY)
        if not nxt:
            break
        items.append(json.loads(nxt))
    return items


def queue_depth() -> Optional[int]:
    """Current backlog (LLEN), or None if Redis is unreachable."""
    try:
        return _get_redis().llen(QUEUE_KEY)
    except Exception:
        return None
