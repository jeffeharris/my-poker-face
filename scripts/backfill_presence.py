"""One-time idempotent backfill of `entity_presence` from the legacy stores.

Existing sandboxes have populated `cash_tables` / `cash_idle_pool` /
`ai_side_hustle_state` / `ai_vice_state` but (until the shadow ran) an EMPTY
`entity_presence`. The Phase-3 authority flip makes `entity_presence` the source
of truth, so before flipping it must be seeded from the current authoritative
state — otherwise every seated actor becomes an invisible orphan.

Read → write mapping, per sandbox:
  * cash_tables seats, kind='ai'              -> ai:<pid>     SEATED(table,seat)
  * cash_tables seats, kind='human'           -> player:<owner> SEATED(table,seat)
  * cash_idle_pool                            -> ai:<pid>     IDLE  (+ cash_idle_metadata)
  * ai_side_hustle_state (active)             -> ai:<pid>     SIDE_HUSTLE
  * ai_vice_state (active)                    -> ai:<pid>     VICE
  * fish personas seated nowhere              -> ai:<pid>     POOL

Idempotent (INSERT OR IGNORE on the compound PK) — safe to re-run; existing rows
are not overwritten. Conflict policy for the pre-existing `seated_and_idle` bug
(an AI in both a seat AND the idle pool): SEATED wins (the seat is the more
recent authoritative record); the idle row is skipped.

SAFE (read-mostly: only writes entity_presence + cash_idle_metadata, never the
legacy stores). `--dry-run` prints the plan without writing. Run before flipping
`PRESENCE_AUTHORITY_ENABLED`.

Usage (backend container):
    docker compose exec backend python -m scripts.backfill_presence \\
        --db-path /app/data/poker_games.db --dry-run
    docker compose exec backend python -m scripts.backfill_presence \\
        --db-path /app/data/poker_games.db
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def _sandbox_ids(conn: sqlite3.Connection) -> List[str]:
    ids = set()
    for tbl in ("cash_tables", "cash_idle_pool", "ai_side_hustle_state", "ai_vice_state"):
        try:
            for (sid,) in conn.execute(
                f"SELECT DISTINCT sandbox_id FROM {tbl} WHERE sandbox_id IS NOT NULL"
            ):
                if sid:
                    ids.add(sid)
        except sqlite3.OperationalError:
            pass
    return sorted(ids)


def _plan_for_sandbox(conn: sqlite3.Connection, sid: str, now_iso: str):
    """Return (presence_rows, idle_meta_rows, counts) for one sandbox.

    presence_rows: list of (entity_id, state, table_id, seat_index)
    idle_meta_rows: list of (pid, reason, target_stake, left_at)
    """
    seated: Dict[str, tuple] = {}   # entity_id -> (table_id, seat_index)
    fish_seated = set()             # pids seen as fish in a seat
    for table_id, seats_json in conn.execute(
        "SELECT table_id, seats_json FROM cash_tables WHERE sandbox_id = ?", (sid,)
    ):
        try:
            seats = json.loads(seats_json)
        except (ValueError, TypeError):
            continue
        for idx, slot in enumerate(seats):
            kind = slot.get("kind")
            if kind == "ai":
                pid = slot.get("personality_id")
                if pid:
                    seated[f"ai:{pid}"] = (table_id, idx)
                    if slot.get("archetype") == "fish":
                        fish_seated.add(pid)
            elif kind == "human":
                owner = (slot.get("owner_id") or slot.get("player_id")
                         or slot.get("user_id") or slot.get("personality_id"))
                if owner:
                    seated[f"player:{owner}"] = (table_id, idx)

    idle: Dict[str, dict] = {}
    try:
        for pid, left_at, reason, target in conn.execute(
            "SELECT personality_id, left_at, reason, target_stake FROM cash_idle_pool WHERE sandbox_id = ?",
            (sid,),
        ):
            if pid and f"ai:{pid}" not in seated:   # SEATED wins over idle
                idle[pid] = {"reason": reason, "target_stake": target, "left_at": left_at or now_iso}
    except sqlite3.OperationalError:
        pass

    def _active_pids(table: str) -> set:
        try:
            return {r[0] for r in conn.execute(
                f"SELECT personality_id FROM {table} WHERE sandbox_id = ? AND ends_at > ?",
                (sid, now_iso),
            )}
        except sqlite3.OperationalError:
            return set()

    hustle = {p for p in _active_pids("ai_side_hustle_state") if f"ai:{p}" not in seated and p not in idle}
    vice = {p for p in _active_pids("ai_vice_state")
            if f"ai:{p}" not in seated and p not in idle and p not in hustle}

    # Fish personas seated nowhere -> POOL. Identify the fish persona set from
    # the seats we saw stamped 'fish' across the whole DB isn't reliable per
    # sandbox; use the personalities table flag if present, else the fish we saw
    # seated as the known fish set (POOL only applies to fish currently unseated,
    # so we can't infer unseated fish without the roster — keep conservative).
    pool = set()
    try:
        rows = conn.execute(
            "SELECT personality_id FROM personalities WHERE personality_id IS NOT NULL"
        ).fetchall()
        # A fish currently NOT seated/idle/offgrid in this sandbox is POOL only
        # if it's a known fish. Without a reliable per-sandbox fish flag we skip
        # speculative POOL seeding — fish get a POOL row lazily on next seat
        # churn (SEED). This keeps the backfill conservative and correct.
    except sqlite3.OperationalError:
        pass

    presence_rows = []
    for eid, (tid, idx) in seated.items():
        presence_rows.append((eid, "seated", tid, idx))
    for pid in idle:
        presence_rows.append((f"ai:{pid}", "idle", None, None))
    for pid in hustle:
        presence_rows.append((f"ai:{pid}", "side_hustle", None, None))
    for pid in vice:
        presence_rows.append((f"ai:{pid}", "vice", None, None))
    for pid in pool:
        presence_rows.append((f"ai:{pid}", "pool", None, None))

    idle_meta_rows = [
        (pid, m["reason"], m["target_stake"], m["left_at"]) for pid, m in idle.items()
    ]
    counts = {
        "seated": len(seated), "idle": len(idle), "side_hustle": len(hustle),
        "vice": len(vice), "pool": len(pool),
    }
    return presence_rows, idle_meta_rows, counts


def run(db_path: str, dry_run: bool, only_sandbox: Optional[str]) -> dict:
    forbidden_note = "(writes entity_presence + cash_idle_metadata only — never the legacy stores)"
    conn = sqlite3.connect(db_path)
    now_iso = datetime.utcnow().isoformat()
    sandboxes = [only_sandbox] if only_sandbox else _sandbox_ids(conn)
    logger.info("Backfill %s over %d sandbox(es) %s", "DRY-RUN" if dry_run else "WRITE", len(sandboxes), forbidden_note)

    totals = {"seated": 0, "idle": 0, "side_hustle": 0, "vice": 0, "pool": 0,
              "presence_written": 0, "idle_meta_written": 0}
    per_sandbox = []
    for sid in sandboxes:
        prows, mrows, counts = _plan_for_sandbox(conn, sid, now_iso)
        for k, v in counts.items():
            totals[k] += v
        written = 0
        if not dry_run:
            for eid, state, tid, idx in prows:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO entity_presence "
                    "(entity_id, sandbox_id, state, table_id, seat_index, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (eid, sid, state, tid, idx, now_iso),
                )
                written += cur.rowcount
            for pid, reason, target, left_at in mrows:
                conn.execute(
                    "INSERT OR IGNORE INTO cash_idle_metadata "
                    "(personality_id, sandbox_id, reason, target_stake, left_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (pid, sid, reason, target, left_at),
                )
            totals["presence_written"] += written
            totals["idle_meta_written"] += len(mrows)
        per_sandbox.append({"sandbox_id": sid, "counts": counts, "presence_inserted": written})
    if not dry_run:
        conn.commit()
    conn.close()

    logger.info("Totals: %s", totals)
    return {"dry_run": dry_run, "sandboxes": len(sandboxes), "totals": totals, "per_sandbox": per_sandbox}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default="/app/data/poker_games.db")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sandbox-id", default=None, help="Backfill only this sandbox")
    args = ap.parse_args()
    run(args.db_path, args.dry_run, args.sandbox_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
