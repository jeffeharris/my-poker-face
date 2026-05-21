#!/usr/bin/env python3
"""One-shot migration: copy `lender_profile` → `staker_profile` in config_json.

Idempotent. Leaves `lender_profile` in place for the alias window.

Usage:
    docker compose exec backend python scripts/migrate_lender_to_staker.py
"""
import json
import os
import sqlite3

DB_PATH = os.environ.get(
    "POKER_DB_PATH",
    "/app/data/poker_games.db" if os.path.exists("/app/data") else "poker_games.db",
)


def main() -> None:
    print(f"Connecting to {DB_PATH!r}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT personality_id, config_json FROM personalities").fetchall()
        updated, skipped = 0, 0
        for row in rows:
            pid = row["personality_id"]
            try:
                cfg = json.loads(row["config_json"] or "{}")
            except (TypeError, ValueError):
                print(f"  SKIP {pid!r}: malformed config_json")
                skipped += 1
                continue
            if "staker_profile" in cfg or "lender_profile" not in cfg:
                skipped += 1
                continue
            cfg["staker_profile"] = cfg["lender_profile"]
            conn.execute(
                "UPDATE personalities SET config_json = ? WHERE personality_id = ?",
                (json.dumps(cfg), pid),
            )
            updated += 1
            print(f"  UPDATED {pid!r}")
        conn.commit()
        print(f"\nDone. {updated} updated, {skipped} skipped.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
