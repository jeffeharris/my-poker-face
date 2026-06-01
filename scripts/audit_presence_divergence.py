"""READ-ONLY Presence divergence audit against a LIVE database.

Unlike `validate_presence_shadow.py` (which seeds a fresh sandbox and drives a
sim), this tool touches NOTHING: it just compares the `entity_presence` shadow
rows against the authoritative stores (`cash_tables` seat map, `cash_idle_pool`,
`ai_side_hustle_state`, `ai_vice_state`) across every sandbox that has presence
or cash activity, and classifies divergences (benign vs unexpected) using the
same taxonomy as the validator. Use it to audit the shadow on live dev traffic
after enabling `PRESENCE_SHADOW_WRITE_ENABLED`.

It does NOT flip the flag and does NOT write — safe to run against the live dev
DB while the app is up. Writes a JSON report so the verdict is read from a file.

Usage (backend container, live dev DB):
    docker compose exec backend python -m scripts.audit_presence_divergence \\
        --db-path /app/data/poker_games.db --out /tmp/live_presence_audit.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import List

_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from scripts.validate_presence_shadow import BENIGN, _audit_once  # reuse taxonomy

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def _sandbox_ids(db_path: str) -> List[str]:
    """Every sandbox with presence rows OR cash-table rows (the shadow only
    writes where there's cash activity, so the union is the audit set)."""
    ids = set()
    with sqlite3.connect(db_path) as conn:
        for tbl in ("entity_presence", "cash_tables"):
            try:
                for (sid,) in conn.execute(
                    f"SELECT DISTINCT sandbox_id FROM {tbl} WHERE sandbox_id IS NOT NULL"
                ):
                    if sid:
                        ids.add(sid)
            except sqlite3.OperationalError:
                pass
    return sorted(ids)


def run(db_path: str, out_path: str) -> dict:
    from poker.repositories import create_repos

    repos = create_repos(db_path)
    now = datetime.utcnow()

    sandboxes = _sandbox_ids(db_path)
    logger.info("Auditing %d sandbox(es) in %s", len(sandboxes), db_path)

    per_sandbox = []
    agg = {}
    total_unexpected = 0
    classes_seen = set()
    sandboxes_with_presence = 0

    from cash_mode.presence_consistency import check_presence_seat_consistency

    total_consistency_violations = 0
    for sid in sandboxes:
        snap = _audit_once(repos, sid, now)
        if snap["n_presence_rows"] > 0:
            sandboxes_with_presence += 1
        # R1: the presence ⇔ seat-map invariant (the read-side "projection"
        # deliverable). Read-only; uses its own connection.
        with sqlite3.connect(db_path) as _conn:
            violations = check_presence_seat_consistency(_conn, sid)
        snap["seat_consistency_violations"] = violations
        total_consistency_violations += len(violations)
        snap["sandbox_id"] = sid
        per_sandbox.append(snap)
        for k, v in snap["classification_counts"].items():
            agg[k] = agg.get(k, 0) + v
            classes_seen.add(k)
        total_unexpected += snap["n_unexpected"]

    unexpected_classes = sorted(classes_seen - BENIGN - {"MATCH"})
    report = {
        "db_path": db_path,
        "audited_at": now.isoformat(),
        "n_sandboxes": len(sandboxes),
        "n_sandboxes_with_presence_rows": sandboxes_with_presence,
        "aggregate_classification_counts": dict(sorted(agg.items())),
        "classes_ever_seen": sorted(classes_seen),
        "unexpected_classes_seen": unexpected_classes,
        "n_unexpected_total": total_unexpected,
        # R1 presence ⇔ seat-map invariant (double-read to filter ticker races).
        "n_seat_consistency_violations": total_consistency_violations,
        "sandboxes_with_seat_inconsistency": [
            {"sandbox_id": s["sandbox_id"], "violations": s["seat_consistency_violations"]}
            for s in per_sandbox if s.get("seat_consistency_violations")
        ],
        # only sandboxes that actually diverged unexpectedly, for triage
        "sandboxes_with_unexpected": [
            s for s in per_sandbox if s["n_unexpected"] > 0
        ],
        "verdict": (
            "PASS — only benign divergences" if total_unexpected == 0
            else f"REVIEW — {total_unexpected} unexpected divergence(s) "
                 f"in classes {unexpected_classes}"
        ),
        "note": (
            "Benign classes are expected during the shadow phase (idle adds not "
            "wired, off-grid START from non-IDLE, POOL with no old-store "
            "analogue). Unexpected classes (SEAT_MISMATCH/MISSING_SEAT/"
            "OFFGRID_STALE/OTHER) are real wiring gaps to investigate before the "
            "authority flip."
        ),
    }

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(report, indent=2))
    logger.info("Wrote report -> %s", out_path)
    logger.info("VERDICT: %s", report["verdict"])
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default="/app/data/poker_games.db",
                    help="Live DB to audit (read-only). Default: Docker dev DB.")
    ap.add_argument("--out", default="/tmp/live_presence_audit.json")
    args = ap.parse_args()
    report = run(args.db_path, args.out)
    return 0 if report["n_unexpected_total"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
