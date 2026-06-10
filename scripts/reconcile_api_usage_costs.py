#!/usr/bin/env python3
"""Reconcile historical api_usage cost/token data after pricing + parser fixes.

Background (see the cost audit, 2026-06-10):
  1. xai/grok-4-fast-reasoning logged output_tokens = completion - reasoning,
     which double-counted (xAI reports them separately) — output went negative
     and cost was understated by ~reasoning worth of output tokens.
  2. Several image models (runware:101@1, runware:400@4 @512x512) had no
     matching pricing SKU, so estimated_cost was NULL (silently $0).
  3. A handful of text rows (gpt-5, gpt-5-nano) were NULL before pricing existed.

The code paths are fixed going forward; this script repairs the rows already in
the table. It is:
  - DRY-RUN by default (pass --apply to write).
  - IDEMPOTENT: every row it touches gets a "reconciled" marker in pricing_ids,
    and marked rows are skipped on re-run.
  - SCOPED by --cutoff (default: now): only rows created before the cutoff are
    eligible, so it never "corrects" rows produced by the already-fixed code.
    In prod, pass the fix-deploy timestamp as --cutoff.

Cost math mirrors core/llm/tracking.py:_calculate_cost. Error rows are left at
$0 (correct — a failed call costs nothing).

Usage:
  python scripts/reconcile_api_usage_costs.py                 # dry-run
  python scripts/reconcile_api_usage_costs.py --apply
  python scripts/reconcile_api_usage_costs.py --db /app/data/poker_games.db --apply
"""

from __future__ import annotations

import argparse
import collections
import json
import sqlite3
from datetime import datetime, timezone

GROK_REASONING = ("xai", "grok-4-fast-reasoning")
MARKER = "reconciled"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_pricing(conn: sqlite3.Connection):
    """(provider, model, unit) -> list of rows, for current-valid lookup."""
    table = collections.defaultdict(list)
    for r in conn.execute(
        "SELECT provider, model, unit, cost, valid_from, valid_until, id FROM model_pricing"
    ):
        table[(r["provider"], r["model"], r["unit"])].append(dict(r))
    return table


def current_price(pricing, provider, model, unit, at_iso):
    """Mirror _get_sku_pricing: the row valid at `at_iso`, latest valid_from wins.

    Returns (cost, id) or (None, None).
    """
    rows = pricing.get((provider, model, unit))
    if not rows:
        return None, None
    valid = [
        r
        for r in rows
        if (r["valid_from"] is None or r["valid_from"] <= at_iso)
        and (r["valid_until"] is None or r["valid_until"] > at_iso)
    ]
    if not valid:
        valid = rows  # degrade gracefully rather than drop the cost
    best = max(valid, key=lambda r: r["valid_from"] or "")
    return best["cost"], best["id"]


def text_cost(pricing, provider, model, inp, outp, cached, reasoning, at_iso):
    """Replicate the text branch of _calculate_cost. Returns (cost, pricing_ids) or None."""
    in_rate, in_id = current_price(pricing, provider, model, "input_tokens_1m", at_iso)
    out_rate, out_id = current_price(pricing, provider, model, "output_tokens_1m", at_iso)
    if in_rate is None or out_rate is None:
        return None
    cached_rate, cached_id = current_price(
        pricing, provider, model, "cached_input_tokens_1m", at_iso
    )
    if cached_rate is None:
        cached_rate = in_rate / 2
    reason_rate, reason_id = current_price(pricing, provider, model, "reasoning_tokens_1m", at_iso)
    if reason_rate is None:
        reason_rate = out_rate
    uncached = (inp or 0) - (cached or 0)
    cost = (
        uncached * in_rate / 1_000_000
        + (cached or 0) * cached_rate / 1_000_000
        + (outp or 0) * out_rate / 1_000_000
        + (reasoning or 0) * reason_rate / 1_000_000
    )
    ids = {"input": in_id, "output": out_id, MARKER: 1}
    if cached_id:
        ids["cached"] = cached_id
    if reason_id:
        ids["reasoning"] = reason_id
    return cost, ids


def fmt(n):
    return f"${n:.4f}" if n is not None else "—"


def reconcile(db_path: str, apply: bool, cutoff: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    pricing = load_pricing(conn)
    at = now_iso()  # value-as-of time for current-valid pricing
    not_marked = f"(pricing_ids IS NULL OR pricing_ids NOT LIKE '%{MARKER}%')"

    updates = []  # (id, new_output_tokens_or_None, new_cost, new_pricing_ids_json)
    summary = collections.Counter()
    delta_cost = collections.defaultdict(float)  # category -> cost delta

    # --- A. grok-4-fast-reasoning: fix output_tokens, recompute cost ---
    # The bug stored output = completion - reasoning; the true completion (visible
    # output) is recovered as stored_output + reasoning. Recost with corrected output.
    for r in conn.execute(
        f"""SELECT id, input_tokens, output_tokens, cached_tokens, reasoning_tokens,
                   estimated_cost
            FROM api_usage
            WHERE provider=? AND model=? AND reasoning_tokens>0
              AND status!='error' AND created_at < ? AND {not_marked}""",
        (*GROK_REASONING, cutoff),
    ):
        corrected_out = (r["output_tokens"] or 0) + (r["reasoning_tokens"] or 0)
        res = text_cost(
            pricing,
            *GROK_REASONING,
            r["input_tokens"],
            corrected_out,
            r["cached_tokens"],
            r["reasoning_tokens"],
            at,
        )
        if res is None:
            summary["grok_skipped_no_pricing"] += 1
            continue
        new_cost, ids = res
        updates.append((r["id"], corrected_out, new_cost, json.dumps(ids)))
        summary["grok_fixed"] += 1
        delta_cost["grok"] += new_cost - (r["estimated_cost"] or 0)

    # --- B. image rows: NULL cost, now have a matching image_<size> SKU ---
    for r in conn.execute(
        f"""SELECT id, provider, model, image_count, image_size, estimated_cost
            FROM api_usage
            WHERE image_count>0 AND estimated_cost IS NULL AND status!='error'
              AND created_at < ? AND {not_marked}""",
        (cutoff,),
    ):
        size = r["image_size"] or "1024x1024"
        cost, pid = current_price(pricing, r["provider"], r["model"], f"image_{size}", at)
        if cost is None:
            summary["image_skipped_no_pricing"] += 1
            continue
        new_cost = (r["image_count"] or 1) * cost
        ids = {"image": pid, MARKER: 1}
        updates.append((r["id"], None, new_cost, json.dumps(ids)))
        summary["image_fixed"] += 1
        delta_cost["image"] += new_cost  # was NULL ~ 0

    # --- C. text rows: NULL cost, model now priced ---
    for r in conn.execute(
        f"""SELECT id, provider, model, input_tokens, output_tokens, cached_tokens,
                   reasoning_tokens, estimated_cost
            FROM api_usage
            WHERE image_count=0 AND estimated_cost IS NULL AND status!='error'
              AND created_at < ? AND {not_marked}""",
        (cutoff,),
    ):
        # Skip grok-reasoning (handled in A with token correction)
        if (r["provider"], r["model"]) == GROK_REASONING:
            continue
        res = text_cost(
            pricing,
            r["provider"],
            r["model"],
            r["input_tokens"],
            r["output_tokens"],
            r["cached_tokens"],
            r["reasoning_tokens"],
            at,
        )
        if res is None:
            summary["text_skipped_no_pricing"] += 1
            continue
        new_cost, ids = res
        updates.append((r["id"], None, new_cost, json.dumps(ids)))
        summary["text_fixed"] += 1
        delta_cost["text"] += new_cost  # was NULL ~ 0

    # --- Report ---
    print(f"{'APPLY' if apply else 'DRY-RUN'} — db={db_path}  cutoff={cutoff}")
    print("-" * 70)
    print("Rows to update:")
    for k in ("grok_fixed", "image_fixed", "text_fixed"):
        print(f"  {k:28} {summary[k]:>7}   cost delta {fmt(delta_cost[k.split('_')[0]])}")
    for k in ("grok_skipped_no_pricing", "image_skipped_no_pricing", "text_skipped_no_pricing"):
        if summary[k]:
            print(f"  {k:28} {summary[k]:>7}   (still need a pricing SKU)")
    total_delta = sum(delta_cost.values())
    print("-" * 70)
    print(f"  TOTAL rows: {len(updates)}   TOTAL recovered cost: {fmt(total_delta)}")

    if not updates:
        print("\nNothing to do.")
        conn.close()
        return

    if not apply:
        print("\nDry-run only. Re-run with --apply to write these changes.")
        conn.close()
        return

    with conn:
        for row_id, new_out, new_cost, ids_json in updates:
            if new_out is None:
                conn.execute(
                    "UPDATE api_usage SET estimated_cost=?, pricing_ids=? WHERE id=?",
                    (new_cost, ids_json, row_id),
                )
            else:
                conn.execute(
                    "UPDATE api_usage SET output_tokens=?, estimated_cost=?, pricing_ids=? WHERE id=?",
                    (new_out, new_cost, ids_json, row_id),
                )
    print(f"\nApplied {len(updates)} updates.")
    conn.close()


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--db", default="/app/data/poker_games.db", help="SQLite DB path")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument(
        "--cutoff",
        default=None,
        help="ISO timestamp; only reconcile rows created before this (default: now)",
    )
    args = ap.parse_args()
    reconcile(args.db, args.apply, args.cutoff or now_iso())


if __name__ == "__main__":
    main()
