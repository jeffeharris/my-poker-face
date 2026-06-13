"""Phantom-clawback diagnostic + planner for the seat double-drain residue.

The seat double-drain (closed by PR #334) left a residue in the prod sandbox
`bfa7050b…`: AI bankrolls over-credited, `seat:ai` accounts driven negative.
This tool figures out WHAT that residue actually is and proposes how to clean it.

The key question the handoff never definitively answered — and the thing that
decides the remediation mechanism — is whether chips were genuinely MINTED into
the universe, or merely MISALLOCATED:

  * Every double-drain cash-out was a `seat:ai → ai` TRANSFER, and transfers do
    not change the ledger's total supply. So if the ledger conserves globally
    (`Σ non-bank balances + balance_of(central_bank) == 0`), nothing was minted
    at the ledger level — the damage is (a) inflated `ai:` balances offset by
    negative `seat:ai` balances (a misallocation that nets to ~0), and (b) stale
    bankroll INTS (the cache `compute_audit` drift actually measures).
  * Only a NON-ZERO global residual means real over-supply that a `central_bank`
    DESTRUCTION must remove.

So this script LEADS with that residual, then recommends:
  - residual == 0  → REVERSAL: move phantom chips back `ai:<pid> → seat:ai`
    (heal the negative seats) + reconcile the int cache to derived. No bank
    destruction (the bank-neutral path the 2026-06-08 attempt got blamed for was
    only "wrong" because derive-reads was off then; it's on now).
  - residual != 0  → DESTRUCTION: real `ai:<pid> → central_bank` clawback of the
    over-supply, distributed across the inflated bankrolls.

SAFETY: report-only by default. `--apply` is gated behind `--i-have-backed-up`
AND an explicit `--sandbox-id`, refuses to guess, and never runs the destruction
path unless the residual analysis calls for it. Read-only against prod is safe:
    python3 scripts/phantom_clawback.py --db-path /opt/poker/data/poker_games.db \
        --sandbox-id bfa7050b-5762-4ff3-8551-1781f367ee74

(Run on the prod host against the live DB for a READ-ONLY report; the file is
opened `mode=ro`. Apply only after PR #334 has deployed and the seat-ledger
heartbeat has flattened — otherwise the residue is still moving.)
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict

_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


def _account_balances(conn: sqlite3.Connection, sandbox_id: str) -> Dict[str, int]:
    """Per-account ledger balance (Σ sink − Σ source) for one sandbox."""
    bal: Dict[str, int] = defaultdict(int)
    for source, sink, amount in conn.execute(
        "SELECT source, sink, amount FROM chip_ledger_entries WHERE sandbox_id = ?",
        (sandbox_id,),
    ):
        a = int(amount)
        bal[sink] += a
        bal[source] -= a
    return dict(bal)


def _live_seat_stacks(conn: sqlite3.Connection, sandbox_id: str) -> Dict[str, int]:
    """{pid: chips} for every AI currently sitting at a cash table (the chips that
    the seat ledger SHOULD reflect right now)."""
    out: Dict[str, int] = defaultdict(int)
    for (seats_json,) in conn.execute(
        "SELECT seats_json FROM cash_tables WHERE sandbox_id = ?", (sandbox_id,)
    ):
        try:
            for slot in json.loads(seats_json or "[]"):
                if slot.get("kind") == "ai" and slot.get("personality_id"):
                    out[slot["personality_id"]] += int(slot.get("chips", 0) or 0)
        except (ValueError, TypeError):
            continue
    return dict(out)


def _stored_ints(conn: sqlite3.Connection, sandbox_id: str) -> Dict[str, int]:
    return {
        r[0]: int(r[1])
        for r in conn.execute(
            "SELECT personality_id, chips FROM ai_bankroll_state WHERE sandbox_id = ?",
            (sandbox_id,),
        )
    }


def diagnose(db_path: str, sandbox_id: str) -> dict:
    """Read-only. Returns the full residue picture + a recommended mechanism."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        bal = _account_balances(conn, sandbox_id)
        live = _live_seat_stacks(conn, sandbox_id)
        ints = _stored_ints(conn, sandbox_id)
    finally:
        conn.close()

    seat_prefix = f"seat:ai:{sandbox_id}:"
    seat_bals = {a[len(seat_prefix) :]: b for a, b in bal.items() if a.startswith(seat_prefix)}
    ai_bals = {a[len("ai:") :]: b for a, b in bal.items() if a.startswith("ai:")}

    # Global conservation residual — the single number that decides the mechanism.
    central_bank = bal.get("central_bank", 0)
    non_bank_sum = sum(b for a, b in bal.items() if a != "central_bank")
    global_residual = non_bank_sum + central_bank

    # Seat misallocation: the seat ledger SHOULD equal the chips currently at
    # seats; the gap is the phantom drained from (or stranded in) seats.
    sum_seat = sum(seat_bals.values())
    sum_live = sum(live.values())
    seat_misallocation = sum_seat - sum_live  # negative ⇒ seats over-drained

    negative_seats = {p: b for p, b in seat_bals.items() if b < 0}
    sum_negative = sum(negative_seats.values())

    # Int cache staleness (what compute_audit's drift is built on): stored − derived.
    int_gap = {p: ints.get(p, 0) - ai_bals.get(p, 0) for p in set(ints) | set(ai_bals)}
    sum_int_gap = sum(int_gap.values())

    minted = global_residual != 0
    recommendation = (
        "DESTRUCTION — global residual is non-zero, so real chips exist beyond "
        "central_bank emission. Destroy the over-supply via ai→central_bank."
        if minted
        else "REVERSAL + INT-RECONCILE — ledger conserves globally (residual 0); "
        "the damage is misallocation (inflated ai: vs negative seat:ai) plus a "
        "stale int cache. Reverse ai→seat to heal the negative seats and reconcile "
        "the int to the derived balance. NO central_bank destruction."
    )

    worst_neg = sorted(negative_seats.items(), key=lambda kv: kv[1])[:15]
    worst_intgap = sorted(int_gap.items(), key=lambda kv: -abs(kv[1]))[:15]

    return {
        "sandbox_id": sandbox_id,
        "n_ai_accounts": len(ai_bals),
        "n_seat_accounts": len(seat_bals),
        "central_bank_balance": central_bank,
        "non_bank_sum": non_bank_sum,
        "global_residual": global_residual,
        "minted_chips": minted,
        "sum_seat_ledger": sum_seat,
        "sum_live_seat_stacks": sum_live,
        "seat_misallocation": seat_misallocation,
        "n_negative_seats": len(negative_seats),
        "sum_negative_seats": sum_negative,
        "sum_int_cache_gap_stored_minus_derived": sum_int_gap,
        "worst_negative_seats": dict(worst_neg),
        "worst_int_gaps": dict(worst_intgap),
        "recommendation": recommendation,
    }


def _print_report(d: dict) -> None:
    print("\n=== PHANTOM CLAWBACK DIAGNOSTIC (read-only) ===")
    print(f"sandbox: {d['sandbox_id']}")
    print(f"AI accounts: {d['n_ai_accounts']}   seat accounts: {d['n_seat_accounts']}")
    print("\n--- Global conservation (the mechanism decider) ---")
    print(f"  central_bank balance      : {d['central_bank_balance']:>14,}")
    print(f"  Σ non-bank balances       : {d['non_bank_sum']:>14,}")
    print(
        f"  GLOBAL RESIDUAL           : {d['global_residual']:>14,}   "
        f"({'MINTED — real over-supply' if d['minted_chips'] else 'ZERO — ledger conserves'})"
    )
    print("\n--- Seat misallocation ---")
    print(f"  Σ seat:ai ledger          : {d['sum_seat_ledger']:>14,}")
    print(f"  Σ live seat stacks (now)  : {d['sum_live_seat_stacks']:>14,}")
    print(
        f"  seat misallocation        : {d['seat_misallocation']:>14,}   "
        f"(negative ⇒ seats over-drained)"
    )
    print(
        f"  negative seats            : {d['n_negative_seats']} totalling "
        f"{d['sum_negative_seats']:,}"
    )
    print("\n--- Int cache staleness (compute_audit drift basis) ---")
    print(f"  Σ (stored int − derived)  : {d['sum_int_cache_gap_stored_minus_derived']:>14,}")
    print("\n--- Worst negative seats ---")
    for pid, b in d["worst_negative_seats"].items():
        print(f"    {pid:<28} {b:>14,}")
    print("\n>>> RECOMMENDATION:")
    print("   ", d["recommendation"])
    print()


def plan_reversal(db_path: str, sandbox_id: str) -> list:
    """Per-seat reversal plan: reconcile every `seat:ai` to its live stack.

    The double-drain misallocation is conservation-neutral (residual 0), so the
    fix is a pure ai↔seat TRANSFER per AI — no creation/destruction. For each seat
    with balance `b` and live-stack target `t`, move `t − b`:
      * b < t (negative / under-target seat) → `ai:<pid> → seat` (de-inflate the
        over-credited bankroll, heal the seat up to its stack),
      * b > t (over-target seat)             → `seat → ai:<pid>` (return stranded
        chips to the bankroll).
    After the plan every `seat:ai` == its live stack; the global residual is
    unchanged (every row is a transfer). Returns a list of dict rows.
    """
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        bal = _account_balances(conn, sandbox_id)
        live = _live_seat_stacks(conn, sandbox_id)
    finally:
        conn.close()

    seat_prefix = f"seat:ai:{sandbox_id}:"
    plan = []
    for acct, b in bal.items():
        if not acct.startswith(seat_prefix):
            continue
        pid = acct[len(seat_prefix) :]
        target = int(live.get(pid, 0))
        delta = target - b
        if delta == 0:
            continue
        ai_acct = f"ai:{pid}"
        ai_bal = bal.get(ai_acct, 0)
        if delta > 0:  # move INTO seat from bankroll (de-inflate)
            source, sink, amount = ai_acct, acct, delta
            ai_after = ai_bal - delta
        else:  # move OUT of seat into bankroll (return stranded)
            source, sink, amount = acct, ai_acct, -delta
            ai_after = ai_bal + (-delta)
        plan.append(
            {
                "pid": pid,
                "seat_before": b,
                "seat_target": target,
                "amount": amount,
                "source": source,
                "sink": sink,
                "ai_before": ai_bal,
                "ai_after": ai_after,
                "ai_goes_negative": ai_after < 0,
            }
        )
    plan.sort(key=lambda r: -r["amount"])
    return plan


def apply_reversal(db_path: str, sandbox_id: str, plan: list) -> dict:
    """Write the reversal rows in ONE transaction; re-verify conservation after.

    Each row is an append-only `phantom_reversal` transfer (ai↔seat, no
    central_bank side). Re-reads balances afterward and asserts: every seat ==
    its target, the global residual is unchanged, no AI bankroll went negative.
    Rolls back on any assertion failure.
    """
    import json as _json

    conn = sqlite3.connect(db_path)  # read-write (NOT mode=ro)
    try:
        before = _account_balances(conn, sandbox_id)
        residual_before = sum(b for a, b in before.items() if a != "central_bank") + before.get(
            "central_bank", 0
        )
        ctx = _json.dumps({"site": "phantom_clawback", "reverses": "seat_double_drain"})
        with conn:  # transaction
            for r in plan:
                conn.execute(
                    "INSERT INTO chip_ledger_entries (source, sink, amount, reason, "
                    "context_json, sandbox_id) VALUES (?, ?, ?, 'phantom_reversal', ?, ?)",
                    (r["source"], r["sink"], int(r["amount"]), ctx, sandbox_id),
                )
            after = _account_balances(conn, sandbox_id)
            residual_after = sum(b for a, b in after.items() if a != "central_bank") + after.get(
                "central_bank", 0
            )
            seat_prefix = f"seat:ai:{sandbox_id}:"
            live = _live_seat_stacks(conn, sandbox_id)
            bad_seats = {
                a[len(seat_prefix) :]: after[a]
                for a in after
                if a.startswith(seat_prefix) and after[a] != int(live.get(a[len(seat_prefix) :], 0))
            }
            neg_ai = {a: after[a] for a in after if a.startswith("ai:") and after[a] < 0}
            if residual_after != residual_before:
                raise AssertionError(
                    f"residual changed {residual_before} → {residual_after} (reversal must "
                    "be conservation-neutral) — rolling back"
                )
            if bad_seats:
                raise AssertionError(f"{len(bad_seats)} seats != live stack after — rolling back")
            if neg_ai:
                raise AssertionError(
                    f"{len(neg_ai)} AI bankrolls went negative — rolling back: "
                    f"{dict(list(neg_ai.items())[:5])}"
                )
        return {
            "rows_written": len(plan),
            "residual_before": residual_before,
            "residual_after": residual_after,
            "ok": True,
        }
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", required=True, help="SQLite DB (read-only unless --apply)")
    ap.add_argument("--sandbox-id", required=True, help="Target sandbox (no guessing)")
    ap.add_argument("--out", default=None, help="Optional JSON dump of the diagnostic")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Write the reversal (default is a dry-run that prints the plan only).",
    )
    ap.add_argument(
        "--i-have-backed-up",
        action="store_true",
        help="Required with --apply: confirm the DB is backed up (WAL-quiesced).",
    )
    args = ap.parse_args()

    d = diagnose(args.db_path, args.sandbox_id)
    _print_report(d)
    if args.out:
        Path(args.out).write_text(json.dumps(d, indent=2, default=str))
        print(f"wrote {args.out}")

    if d["minted_chips"]:
        print(
            "\nABORTING: global residual is NON-ZERO — real chips exist beyond central_bank "
            "emission. A seat-reversal would NOT be conservation-correct here; this needs a "
            "central_bank destruction designed separately. Not touching the DB.",
            file=sys.stderr,
        )
        return 3

    plan = plan_reversal(args.db_path, args.sandbox_id)
    total_into_seats = sum(r["amount"] for r in plan if r["sink"].startswith("seat:ai:"))
    total_out_of_seats = sum(r["amount"] for r in plan if r["source"].startswith("seat:ai:"))
    print("\n--- REVERSAL PLAN (reconcile every seat to its live stack) ---")
    print(
        f"  rows: {len(plan)}   into seats (de-inflate bankrolls): {total_into_seats:,}   "
        f"out of seats (return stranded): {total_out_of_seats:,}"
    )
    for r in plan[:20]:
        arrow = "ai→seat" if r["sink"].startswith("seat:ai:") else "seat→ai"
        print(
            f"    {r['pid']:<26} {arrow}  {r['amount']:>12,}   "
            f"ai {r['ai_before']:>12,} → {r['ai_after']:>12,}"
            f"{'  ⚠ NEGATIVE' if r['ai_goes_negative'] else ''}"
        )
    if any(r["ai_goes_negative"] for r in plan):
        print("\n  ⚠ Some AI bankrolls would go negative — apply will refuse (rolls back).")

    if not args.apply:
        print("\n(dry-run — no writes. Re-run with --apply --i-have-backed-up to execute.)")
        return 0
    if not args.i_have_backed_up:
        print("\nREFUSING --apply without --i-have-backed-up.", file=sys.stderr)
        return 2
    print("\nAPPLYING reversal …")
    result = apply_reversal(args.db_path, args.sandbox_id, plan)
    print(
        f"  DONE: {result['rows_written']} rows; residual "
        f"{result['residual_before']} → {result['residual_after']} (unchanged ⇒ conserved)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
