"""Quick view: who's on a side hustle and who's idle in cash mode.

Reads `ai_side_hustle_state` (AIs off-grid earning) and `cash_idle_pool`
(AIs between tables) and prints them with names + timing, grouped by
sandbox. Read-only.

Usage (in backend container):
    python3 /app/scripts/cash_who.py                 # all live sandboxes
    python3 /app/scripts/cash_who.py <sandbox_id>    # one sandbox
    python3 /app/scripts/cash_who.py guest_jeff      # resolve by owner_id

Or from the host:
    docker compose exec backend python3 /app/scripts/cash_who.py
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime

DB = "/app/data/poker_games.db"


def _parse(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _ago(ts, now):
    """Human 'Nm Ns' since/until a timestamp, or '?'."""
    dt = _parse(ts)
    if dt is None:
        return "?"
    secs = int(abs((now - dt).total_seconds()))
    if secs >= 3600:
        return f"{secs // 3600}h{(secs % 3600) // 60}m"
    if secs >= 60:
        return f"{secs // 60}m{secs % 60}s"
    return f"{secs}s"


def _resolve_sandboxes(conn, arg):
    """Return [(sandbox_id, label)]. arg may be a sandbox_id, an owner_id,
    or None (all live sandboxes)."""
    if arg:
        # owner_id match first (friendlier), else treat as sandbox_id.
        rows = conn.execute(
            "SELECT sandbox_id, owner_id, name FROM sandboxes "
            "WHERE owner_id = ? AND archived_at IS NULL", (arg,),
        ).fetchall()
        if not rows:
            rows = conn.execute(
                "SELECT sandbox_id, owner_id, name FROM sandboxes "
                "WHERE sandbox_id = ?", (arg,),
            ).fetchall()
        return [(r["sandbox_id"], f"{r['owner_id']} / {r['name']}") for r in rows]
    rows = conn.execute(
        "SELECT sandbox_id, owner_id, name FROM sandboxes "
        "WHERE archived_at IS NULL ORDER BY created_at"
    ).fetchall()
    return [(r["sandbox_id"], f"{r['owner_id']} / {r['name']}") for r in rows]


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    now = datetime.utcnow()

    names = {
        r["personality_id"]: r["name"]
        for r in conn.execute("SELECT personality_id, name FROM personalities")
        if r["personality_id"]
    }

    def nm(pid):
        return names.get(pid, pid)

    sandboxes = _resolve_sandboxes(conn, arg)
    if not sandboxes:
        print(f"No sandbox matched {arg!r}")
        return 1

    for sandbox_id, label in sandboxes:
        hustle = conn.execute(
            "SELECT personality_id, started_at, ends_at, amount "
            "FROM ai_side_hustle_state WHERE sandbox_id = ? "
            "ORDER BY ends_at", (sandbox_id,),
        ).fetchall()
        idle = conn.execute(
            "SELECT personality_id, left_at, reason, target_stake "
            "FROM cash_idle_pool WHERE sandbox_id = ? "
            "ORDER BY left_at", (sandbox_id,),
        ).fetchall()

        if not hustle and not idle:
            continue  # quiet for empty sandboxes when listing all

        print(f"\n=== {label}")
        print(f"    {sandbox_id}")

        print(f"  SIDE HUSTLE ({len(hustle)}):")
        for r in hustle or []:
            done = _parse(r["ends_at"])
            when = (f"returns in {_ago(r['ends_at'], now)}"
                    if done and done > now else "DUE (ready to return)")
            print(f"    {nm(r['personality_id']):28} target={r['amount'] or 0:>7}  "
                  f"on hustle {_ago(r['started_at'], now)}  {when}")
        if not hustle:
            print("    (none)")

        print(f"  IDLE ({len(idle)}):")
        for r in idle or []:
            tgt = f" → {r['target_stake']}" if r["target_stake"] else ""
            print(f"    {nm(r['personality_id']):28} {r['reason'] or '?':14} "
                  f"idle {_ago(r['left_at'], now)}{tgt}")
        if not idle:
            print("    (none)")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
