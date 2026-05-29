"""Scheduled data-retention sweep (PRH-32).

Two tables grow unbounded and store verbatim user content forever: the LLM
`prompt_captures` (full prompts incl. user chat) and `api_usage` (cost rows).
The cleanup methods existed but nothing called them. This runs a daily sweep
that enforces:

- prompt-capture retention via `get_retention_days()` (DB app-setting → env
  `LLM_PROMPT_RETENTION_DAYS`), and
- `api_usage` retention via env `API_USAGE_RETENTION_DAYS`.

Both treat 0 / unset as "keep everything" (no-op), so this is inert until an
operator sets a finite window — and inert under the test suite. Mirrors the
game-state cleanup timer: a single self-rescheduling daemon `threading.Timer`,
guarded so repeated `create_app()` calls don't stack timers. VACUUM is
intentionally NOT run (it takes a write lock and can stall the single worker;
SQLite reclaims space lazily and WAL checkpoints handle it).
"""

import logging
import os
import threading

logger = logging.getLogger(__name__)

# Daily by default; override for tests/ops.
RETENTION_SWEEP_INTERVAL_SECONDS = int(
    os.environ.get("RETENTION_SWEEP_INTERVAL_SECONDS", str(24 * 60 * 60))
)

_timer = None
_timer_lock = threading.RLock()


def run_retention_sweep() -> dict:
    """Run one retention pass. Returns {'captures': n, 'api_usage': n}. Never raises."""
    result = {"captures": 0, "api_usage": 0}

    # Prompt captures (retention from DB setting → env; 0 = keep all).
    try:
        from core.llm.capture_config import get_retention_days
        from flask_app import extensions

        days = get_retention_days()
        repo = getattr(extensions, "prompt_capture_repo", None)
        if repo is not None and days and days > 0:
            result["captures"] = repo.cleanup_old_captures(days)
    except Exception as e:
        logger.warning("[RETENTION] prompt-capture cleanup failed: %s", e)

    # api_usage cost rows (env-configured; 0 = keep all).
    try:
        from core.llm.tracking import UsageTracker

        days = int(os.environ.get("API_USAGE_RETENTION_DAYS", "0"))
        if days > 0:
            result["api_usage"] = UsageTracker.get_default().prune_old_usage(days)
    except Exception as e:
        logger.warning("[RETENTION] api_usage purge failed: %s", e)

    return result


def _schedule():
    global _timer
    with _timer_lock:
        run_retention_sweep()
        _timer = threading.Timer(RETENTION_SWEEP_INTERVAL_SECONDS, _schedule)
        _timer.daemon = True
        _timer.start()


def start_retention_sweep():
    """Start the background retention sweep (idempotent across create_app())."""
    global _timer
    with _timer_lock:
        if _timer is None:
            _schedule()


def stop_retention_sweep():
    """Stop the background sweep (tests / graceful shutdown)."""
    global _timer
    with _timer_lock:
        if _timer is not None:
            _timer.cancel()
            _timer = None
