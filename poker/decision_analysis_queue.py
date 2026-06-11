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
# Two clients on purpose:
#  - producer: short connect/socket timeout. Used on the gameplay hot path (LPUSH)
#    and for LLEN, so a slow/blackholed Redis fails fast (~0.25s) and the caller's
#    inline fallback runs promptly instead of the action blocking on the OS timeout.
#  - consumer: NO socket_timeout, because dequeue_batch's blocking BRPOP governs its
#    own idle wait; a socket_timeout <= the BRPOP timeout would abort it every cycle.
_producer = None
_consumer = None
ENQUEUE_TIMEOUT_S = float(os.environ.get("DECISION_ANALYSIS_ENQUEUE_TIMEOUT", "0.25"))


def _get_producer():
    global _producer
    if _producer is None:
        import redis  # local import — only needed when the queue is enabled

        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        _producer = redis.from_url(
            url, socket_connect_timeout=ENQUEUE_TIMEOUT_S, socket_timeout=ENQUEUE_TIMEOUT_S
        )
    return _producer


def _get_consumer():
    global _consumer
    if _consumer is None:
        import redis

        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        _consumer = redis.from_url(url)
    return _consumer


def enqueue_decision_analysis_job(job: dict) -> None:
    """Push one analysis job onto the queue (LPUSH) via the short-timeout producer,
    so a Redis stall fails fast on the gameplay hot path (caller falls back inline)."""
    _get_producer().lpush(QUEUE_KEY, json.dumps(job))


def dequeue_batch(max_items: int = 50, timeout: int = 5) -> List[dict]:
    """Block up to ``timeout`` s for the first job, then drain up to ``max_items``
    more without blocking. Returns [] on idle timeout."""
    r = _get_consumer()
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
        return _get_producer().llen(QUEUE_KEY)
    except Exception:
        return None
