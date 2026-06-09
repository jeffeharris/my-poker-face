"""Out-of-band decision-analysis worker.

Drains the Redis decision-analysis queue and runs the per-decision equity Monte
Carlo + persistence off the gameplay hot path. Intended to run as its own
container (on the box's otherwise-idle second core, or a separate box later), so
the single gevent gameplay worker is freed of the ~70% analytics CPU the
2026-06-09 load test measured. Processing is delayed-OK and batched.

Run:  python -m poker.decision_analysis_worker
Env:  REDIS_URL, DECISION_ANALYSIS_ITERATIONS, DECISION_ANALYSIS_WORKER_BATCH,
      DECISION_ANALYSIS_DB_PATH (defaults to the app's DB).
"""

import logging
import os
import signal
import sys
import time

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("decision_analysis_worker")

_stop = False


def _handle_signal(signum, _frame):
    global _stop
    logger.info("received signal %s — finishing current batch then exiting", signum)
    _stop = True


def main() -> int:
    from poker.db_utils import get_default_db_path
    from poker.decision_analysis_queue import dequeue_batch, queue_depth
    from poker.decision_analyzer import run_decision_analysis_job
    from poker.repositories import create_repos

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    db_path = os.environ.get("DECISION_ANALYSIS_DB_PATH") or get_default_db_path()
    batch = int(os.environ.get("DECISION_ANALYSIS_WORKER_BATCH", "50"))
    repos = create_repos(db_path)
    analysis_repo = repos["decision_analysis_repo"]
    capture_label_repo = repos["capture_label_repo"]

    logger.info(
        "decision-analysis worker started (db=%s, batch=%d, iterations=%s)",
        db_path,
        batch,
        os.environ.get("DECISION_ANALYSIS_ITERATIONS", "2000"),
    )

    processed = 0
    backoff = 1.0  # seconds; grows on repeated dequeue failure (Redis down), resets on success
    while not _stop:
        try:
            jobs = dequeue_batch(batch, timeout=5)
            backoff = 1.0
        except Exception:
            # Redis down/misconfigured: dequeue_batch raises immediately, so without
            # a sleep this loop would spin (peg CPU + flood logs). Back off, capped.
            logger.warning("dequeue failed; backing off %.0fs", backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
            continue
        if not jobs:
            continue
        ok = 0
        for job in jobs:
            try:
                run_decision_analysis_job(job, analysis_repo, capture_label_repo)
                ok += 1
            except Exception:
                logger.exception("decision-analysis job failed (dropped)")
        processed += ok
        logger.info(
            "processed batch=%d ok=%d total=%d depth=%s",
            len(jobs),
            ok,
            processed,
            queue_depth(),
        )

    logger.info("decision-analysis worker stopped (total processed=%d)", processed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
