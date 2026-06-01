#!/usr/bin/env python3
"""Demote leaked sim/test personas out of the auto-seed pool (v123 cleanup).

The v123 migration adds `personalities.circulating` and backfills every
currently-public row to circulating=1 to PRESERVE existing behavior. That
backfill is deliberately behavior-neutral, so it also (correctly) leaves the
already-leaked junk rows circulating. This script is the explicit, one-time
data step that demotes those known placeholder/test/sim-artifact personas:
`circulating = 0` so they drop out of the cash-mode opponent pool while
staying in the DB — still public, still visible/pickable, just not
auto-seated.

Environment-specific by design (prod has different junk than dev), which is
why it's a separate script and not baked into the generic migration.

Idempotent: only flips rows that exist and are still circulating; re-running
is a no-op. Non-destructive: touches only the `circulating` flag.

Run inside the backend container against the Flask DB:

    docker compose exec -T backend python3 scripts/demote_noncirculating_personas.py
    docker compose exec -T backend python3 scripts/demote_noncirculating_personas.py --apply

Without --apply it's a dry run (prints what it WOULD change).
"""
import argparse
import sqlite3
import sys

# The Flask app's DB inside the container (see CLAUDE.md "Database Location").
DEFAULT_DB = "/app/data/poker_games.db"

# Known leaked placeholder / test / sim-artifact personas observed in the
# live pool (heavy times_used = sim seatings, not real play). These pre-date
# the RESERVED_PERSONA_NAMES write-guard or slipped past it (e.g. "Tester",
# "AI 12", "P10", "Fishy" are not exact reserved names).
JUNK_NAMES = [
    "Test Player",
    "Tester",
    "Unknown Celebrity",
    "AI 12",
    "AI 13",
    "AI 14",
    "AI 15",
    "P10",
    "P11",
    "P12",
    "P13",
    "P14",
    "P15",
    "Fishy",
    "Fishy2",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite path (default {DEFAULT_DB})")
    parser.add_argument(
        "--apply", action="store_true", help="Actually write changes (default: dry run)"
    )
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    cols = [r[1] for r in conn.execute("PRAGMA table_info(personalities)")]
    if "circulating" not in cols:
        print(
            "ERROR: personalities.circulating column not found — run the app once "
            "so migration v123 applies, then re-run this script.",
            file=sys.stderr,
        )
        return 1

    placeholders = ",".join("?" * len(JUNK_NAMES))
    rows = conn.execute(
        f"SELECT name, visibility, circulating, times_used "
        f"FROM personalities WHERE name IN ({placeholders}) ORDER BY name",
        JUNK_NAMES,
    ).fetchall()

    found = {r["name"] for r in rows}
    missing = [n for n in JUNK_NAMES if n not in found]
    to_flip = [r for r in rows if r["circulating"]]

    print(f"DB: {args.db}")
    print(f"Junk names targeted: {len(JUNK_NAMES)} | present: {len(found)} | missing: {len(missing)}")
    if missing:
        print(f"  not in this DB (ok): {', '.join(missing)}")
    print()
    print("Currently circulating (would be demoted):")
    if not to_flip:
        print("  (none — already clean)")
    for r in to_flip:
        print(f"  {r['name']:24} visibility={r['visibility']:8} times_used={r['times_used']}")

    if not to_flip:
        print("\nNothing to do.")
        return 0

    if not args.apply:
        print(f"\nDRY RUN — re-run with --apply to demote {len(to_flip)} persona(s).")
        return 0

    names = [r["name"] for r in to_flip]
    ph = ",".join("?" * len(names))
    cur = conn.execute(
        f"UPDATE personalities SET circulating = 0, updated_at = CURRENT_TIMESTAMP "
        f"WHERE name IN ({ph})",
        names,
    )
    conn.commit()
    print(f"\nAPPLIED — demoted {cur.rowcount} persona(s) out of the auto-seed pool.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
