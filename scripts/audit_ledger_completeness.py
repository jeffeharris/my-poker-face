"""Measure whether the chip ledger is a COMPLETE record of chip movements —
the foundation gate for making the ledger the chip authority (CASH_MODE_STATE_MODEL
invariant I1 / the chip-custody machine).

For every entity account (`player:<id>`, `ai:<pid>`) it derives the balance from
the ledger (Σ amount where sink=account − Σ where source=account) and compares to
the STORED bankroll int. A zero gap means every chip movement for that entity is
ledgered (derivable); a nonzero gap is an UNLEDGERED movement — the exact gap the
chip-custody foundation must close.

Scoping wrinkle (measured, not assumed): `player_bankroll_state` is GLOBAL (no
sandbox_id) while `ai_bankroll_state` + ledger entries are per-sandbox. So:
  * ai:<pid>      → ledger balance WITHIN each sandbox vs ai_bankroll_state(pid,sandbox)
  * player:<id>   → ledger balance summed ACROSS sandboxes vs the global bankroll

READ-ONLY. Usage:
    docker compose exec backend python -m scripts.audit_ledger_completeness \\
        --db-path /app/data/poker_games.db --out /tmp/ledger_completeness.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

_root = str(Path(__file__).parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def run(db_path: str, out_path: str) -> dict:
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row

    # (account, sandbox_id) -> net ledger balance
    bal: dict = defaultdict(int)
    for r in c.execute(
        "SELECT source, sink, amount, sandbox_id FROM chip_ledger_entries"
    ):
        amt = int(r["amount"])
        sb = r["sandbox_id"]
        bal[(r["sink"], sb)] += amt
        bal[(r["source"], sb)] -= amt

    # ledger balance per account, both per-sandbox and summed across sandboxes
    per_sandbox: dict = defaultdict(dict)   # account -> {sandbox: balance}
    across: dict = defaultdict(int)         # account -> summed balance
    for (acct, sb), v in bal.items():
        per_sandbox[acct][sb] = v
        across[acct] += v

    # stored bankrolls
    ai_stored = {
        (r["personality_id"], r["sandbox_id"]): int(r["chips"])
        for r in c.execute("SELECT personality_id, sandbox_id, chips FROM ai_bankroll_state")
    }
    player_stored = {
        r["player_id"]: int(r["chips"])
        for r in c.execute("SELECT player_id, chips FROM player_bankroll_state")
    }

    ai_rows, player_rows = [], []

    # AI: per (pid, sandbox)
    ai_accts = {(a[len("ai:"):], sb) for a in across if a.startswith("ai:")
                for sb in per_sandbox[a]}
    ai_accts |= set(ai_stored.keys())
    for pid, sb in sorted(ai_accts, key=lambda t: (t[0] or "", t[1] or "")):
        derived = per_sandbox.get(f"ai:{pid}", {}).get(sb, 0)
        stored = ai_stored.get((pid, sb))
        gap = (stored - derived) if stored is not None else None
        ai_rows.append({"pid": pid, "sandbox": sb, "derived": derived,
                        "stored": stored, "gap": gap})

    # Player: summed across sandboxes vs global stored
    player_accts = {a[len("player:"):] for a in across if a.startswith("player:")}
    player_accts |= set(player_stored.keys())
    for oid in sorted(player_accts):
        derived = across.get(f"player:{oid}", 0)
        stored = player_stored.get(oid)
        gap = (stored - derived) if stored is not None else None
        player_rows.append({"owner_id": oid, "derived": derived,
                            "stored": stored, "gap": gap})

    # seat: balances (chips currently committed to seats per the ledger)
    seat_bal = {a: across[a] for a in across if a.startswith("seat:") and across[a] != 0}

    def _summ(rows, key):
        have = [r for r in rows if r["gap"] is not None]
        recon = [r for r in have if r["gap"] == 0]
        return {
            "n_accounts": len(rows),
            "n_with_stored_bankroll": len(have),
            "n_reconciled (gap==0)": len(recon),
            "n_unledgered (gap!=0)": len(have) - len(recon),
            "total_abs_gap": sum(abs(r["gap"]) for r in have),
            "worst": sorted(have, key=lambda r: -abs(r["gap"]))[:8],
            "n_ledger_only (no stored row)": len([r for r in rows if r["gap"] is None and (r.get("derived") or 0) != 0]),
        }

    report = {
        "db_path": db_path,
        "total_ledger_entries": c.execute("SELECT COUNT(*) FROM chip_ledger_entries").fetchone()[0],
        "ai": _summ(ai_rows, "pid"),
        "player": _summ(player_rows, "owner_id"),
        "n_seat_accounts_with_balance": len(seat_bal),
        "seat_balance_total": sum(seat_bal.values()),
        "verdict": None,
    }
    air, plr = report["ai"], report["player"]
    ledger_complete = (air["n_unledgered (gap!=0)"] == 0 and plr["n_unledgered (gap!=0)"] == 0)
    report["verdict"] = (
        "LEDGER COMPLETE — every entity's bankroll is derivable; ready to be the chip authority"
        if ledger_complete else
        f"LEDGER INCOMPLETE — {air['n_unledgered (gap!=0)']} AI + {plr['n_unledgered (gap!=0)']} player "
        f"accounts have unledgered movements (total abs gap {air['total_abs_gap']+plr['total_abs_gap']}). "
        f"These are the chip movements the custody foundation must ledger."
    )
    c.close()
    Path(out_path).write_text(json.dumps(report, indent=2, default=str))
    logger.info("VERDICT: %s", report["verdict"])
    logger.info("AI: %s/%s reconciled | Player: %s/%s reconciled",
                air["n_reconciled (gap==0)"], air["n_with_stored_bankroll"],
                plr["n_reconciled (gap==0)"], plr["n_with_stored_bankroll"])
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default="/app/data/poker_games.db")
    ap.add_argument("--out", default="/tmp/ledger_completeness.json")
    args = ap.parse_args()
    run(args.db_path, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
