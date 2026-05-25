"""Quick view: how much money fish have fed the population (grinders + human).

By chip conservation, every chip a fish ever held came from the bank pool
(casino_seat_seed / tourist_injection). Player<->player pots aren't
ledgered — only fish<->pool/house flows are — so for each fish:

    net_to_players = ledger_inflow - ledger_outflow - current_holdings

is exactly the chips that fish lost to other players. Positive = the
population net-farmed the fish; negative = the fish is net up. Read-only.

Usage (in backend container):
    python3 /app/scripts/fish_drain.py                 # all live sandboxes
    python3 /app/scripts/fish_drain.py guest_jeff      # by owner_id
    python3 /app/scripts/fish_drain.py <sandbox_id>    # one sandbox

Or from the host:
    docker compose exec backend python3 /app/scripts/fish_drain.py
"""
from __future__ import annotations

import json
import sqlite3
import sys

DB = "/app/data/poker_games.db"


def _resolve_sandboxes(conn, arg):
    """[(sandbox_id, label)] — arg may be sandbox_id, owner_id, or None (all live)."""
    if arg:
        rows = conn.execute(
            "SELECT sandbox_id, owner_id, name FROM sandboxes "
            "WHERE owner_id = ? AND archived_at IS NULL", (arg,),
        ).fetchall()
        if not rows:
            rows = conn.execute(
                "SELECT sandbox_id, owner_id, name FROM sandboxes "
                "WHERE sandbox_id = ?", (arg,),
            ).fetchall()
    else:
        rows = conn.execute(
            "SELECT sandbox_id, owner_id, name FROM sandboxes "
            "WHERE archived_at IS NULL ORDER BY created_at"
        ).fetchall()
    return [(r["sandbox_id"], f"{r['owner_id']} / {r['name']}") for r in rows]


def _fish_drain(conn, sandbox_id, fish_ids, names):
    """Per-fish (name, inflow, outflow, holdings, net) + totals dict."""
    fset = {f"ai:{p}" for p in fish_ids}
    inflow = {p: 0 for p in fish_ids}
    outflow = {p: 0 for p in fish_ids}
    for r in conn.execute(
        "SELECT source, sink, amount FROM chip_ledger_entries WHERE sandbox_id = ?",
        (sandbox_id,),
    ):
        if r["sink"] in fset:
            inflow[r["sink"][3:]] += r["amount"]
        if r["source"] in fset:
            outflow[r["source"][3:]] += r["amount"]

    held = {p: 0 for p in fish_ids}
    for r in conn.execute(
        "SELECT personality_id, chips FROM ai_bankroll_state WHERE sandbox_id = ?",
        (sandbox_id,),
    ):
        if r["personality_id"] in held:
            held[r["personality_id"]] += int(r["chips"] or 0)
    for r in conn.execute(
        "SELECT seats_json FROM cash_tables WHERE sandbox_id = ?", (sandbox_id,),
    ):
        for s in json.loads(r["seats_json"] or "[]"):
            if s.get("archetype") == "fish":
                pid = s.get("personality_id")
                if pid in held:
                    held[pid] += int(s.get("chips", 0))

    rows = []
    for p in fish_ids:
        net = inflow[p] - outflow[p] - held[p]
        if inflow[p] or outflow[p] or held[p]:
            rows.append((names.get(p, p), inflow[p], outflow[p], held[p], net))
    rows.sort(key=lambda x: -x[4])
    totals = {
        "inflow": sum(inflow.values()),
        "outflow": sum(outflow.values()),
        "held": sum(held.values()),
    }
    totals["net"] = totals["inflow"] - totals["outflow"] - totals["held"]
    return rows, totals


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    fish_ids = [
        r[0] for r in conn.execute(
            "SELECT personality_id FROM personalities "
            "WHERE json_extract(config_json,'$.archetype') = 'fish'"
        ) if r[0]
    ]
    names = {
        r["personality_id"]: r["name"]
        for r in conn.execute("SELECT personality_id, name FROM personalities")
        if r["personality_id"]
    }

    sandboxes = _resolve_sandboxes(conn, arg)
    if not sandboxes:
        print(f"No sandbox matched {arg!r}")
        return 1

    for sandbox_id, label in sandboxes:
        rows, t = _fish_drain(conn, sandbox_id, fish_ids, names)
        if not rows:
            continue
        print(f"\n=== {label}")
        print(f"    {sandbox_id}")
        print(f"  {'fish':28} {'in':>9} {'out':>9} {'held':>8} {'net→players':>12}")
        for name, i, o, h, net in rows:
            print(f"  {name:28} {i:>9} {o:>9} {h:>8} {net:>12}")
        print(f"  {'TOTAL':28} {t['inflow']:>9} {t['outflow']:>9} "
              f"{t['held']:>8} {t['net']:>12}")
        print(f"  → {t['net']:,} chips transferred from fish to the population "
              f"(grinders + human)")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
