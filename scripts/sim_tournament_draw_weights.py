#!/usr/bin/env python3
"""Tune the tournaments-as-a-draw DrawWeights (EXP_007).

Pure tuning loop — no full tournaments. Build the candidate pool's `DrawInputs`
from a real dev-DB snapshot (the actual `build_draw_inputs` over real repos),
then sweep weight vectors and, for each, rank the field across many seeds and
measure:

  - H1 redistribution : median(drawn bankrolls) / median(pool bankrolls)  (want ≤ 0.80)
  - H3 mechanism fires : does each term measurably move the ranking? (zero-one-term delta)
  - variety           : distinct top-N fields across seeds (reported, not gating)
  - comfort resistance: top-stack seated personas excluded from the field (reported)

Run inside the backend container (needs /app/data/poker_games.db):

    docker compose exec backend python scripts/sim_tournament_draw_weights.py
    docker compose exec backend python scripts/sim_tournament_draw_weights.py --sandbox <id> --seeds 60

Writes a JSON results blob to docs/experiments/data/ and prints a summary table.
This loop tunes WHO gets drawn, not the downstream economic effect — a promising
vector still needs a hands-on/ticker sim before flipping TOURNAMENT_DRAW_ENABLED.
See docs/experiments/EXP_007_TOURNAMENT_DRAW_WEIGHTS.md.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import random
import sqlite3
import statistics
import sys
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask_app.services.tournament_draw import (  # noqa: E402
    DEFAULT_WEIGHTS,
    DrawContext,
    DrawWeights,
    build_draw_inputs,
    rank_field,
)

DB_PATH = os.environ.get("POKER_DB", "/app/data/poker_games.db")


def _discover_sandbox(db_path: str) -> str | None:
    """The sandbox with the most AI bankroll rows — the richest pool to tune on."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT sandbox_id, COUNT(*) c FROM ai_bankroll_state "
            "GROUP BY sandbox_id ORDER BY c DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def _build_inputs(sandbox_id: str, owner_id: str | None, field_size: int):
    """The real candidate pool as DrawInputs, via the production builder + repos."""
    from poker.repositories.bankroll_repository import BankrollRepository
    from poker.repositories.cash_table_repository import CashTableRepository
    from poker.repositories.chip_ledger_repository import ChipLedgerRepository
    from poker.repositories.personality_repository import PersonalityRepository
    from poker.repositories.prestige_snapshots_repository import PrestigeSnapshotsRepository

    ctx = DrawContext(
        personality_repo=PersonalityRepository(DB_PATH),
        bankroll_repo=BankrollRepository(DB_PATH),
        prestige_repo=PrestigeSnapshotsRepository(DB_PATH),
        cash_table_repo=CashTableRepository(DB_PATH),
        ledger_repo=ChipLedgerRepository(DB_PATH),
    )
    return ctx, build_draw_inputs(
        ctx, sandbox_id=sandbox_id, owner_id=owner_id, field_size=field_size
    )


def _median(xs):
    return statistics.median(xs) if xs else 0.0


def _redistribution(inputs, drawn_ids):
    """drawn-field median bankroll ÷ pool median bankroll (lower = more downward)."""
    pool_med = _median([i.own_bankroll for i in inputs])
    drawn = [i.own_bankroll for i in inputs if i.personality_id in drawn_ids]
    if pool_med <= 0:
        return None
    return _median(drawn) / pool_med


def _comfort_resistance(inputs, drawn_ids):
    """Fraction of top-quartile cash_comfort personas that AVOID the field."""
    seated = sorted((i for i in inputs if i.cash_comfort > 0), key=lambda i: -i.cash_comfort)
    if not seated:
        return None
    top = seated[: max(1, len(seated) // 4)]
    avoided = sum(1 for i in top if i.personality_id not in drawn_ids)
    return avoided / len(top)


def _eval_weights(inputs, weights, field_size, seeds):
    """Rank across seeds; aggregate the metrics for one weight vector."""
    fields = [
        tuple(sorted(rank_field(inputs, field_size, weights=weights, rng=random.Random(s))))
        for s in range(seeds)
    ]
    # The deterministic (no-jitter) field is the representative one for H1.
    base_field = set(rank_field(inputs, field_size, weights=weights, rng=None))
    return {
        "redistribution": _redistribution(inputs, base_field),
        "comfort_resistance": _comfort_resistance(inputs, base_field),
        "variety_distinct_fields": len(set(fields)),
        "variety_of": seeds,
    }


def _run_config(inputs, field_size, seeds, grid):
    """Run the full weight grid for one (already-overridden) input set. Returns
    the default-weights metrics, the best-redistribution vector, the spread of
    redistribution across the grid (how much the weights actually matter here),
    and the per-term firing (H3)."""
    fires = _term_fires(inputs, field_size)
    base = _eval_weights(inputs, DEFAULT_WEIGHTS, field_size, seeds)
    best = None
    redists = []
    for p, r, f, c in product(grid, grid, grid, grid):
        m = _eval_weights(
            inputs, DrawWeights(prize=p, renown=r, field=f, cash_comfort=c), field_size, seeds
        )
        if m["redistribution"] is None:
            continue
        redists.append(m["redistribution"])
        if best is None or m["redistribution"] < best["redistribution"]:
            best = {"weights": {"prize": p, "renown": r, "field": f, "cash_comfort": c}, **m}
    spread = (max(redists) - min(redists)) if redists else 0.0
    return {"default": base, "best": best, "redist_spread": spread, "term_fires": fires}


def _term_fires(inputs, field_size):
    """H3: zero each term in turn; does the no-jitter field change vs the default?"""
    base = set(rank_field(inputs, field_size, weights=DEFAULT_WEIGHTS, rng=None))
    out = {}
    for term in ("prize", "renown", "field", "cash_comfort"):
        kw = {
            "prize": DEFAULT_WEIGHTS.prize,
            "renown": DEFAULT_WEIGHTS.renown,
            "field": DEFAULT_WEIGHTS.field,
            "cash_comfort": DEFAULT_WEIGHTS.cash_comfort,
        }
        kw[term] = 0.0
        zeroed = set(rank_field(inputs, field_size, weights=DrawWeights(**kw), rng=None))
        out[term] = {"changed_members": len(base ^ zeroed), "fires": base != zeroed}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sandbox", default=None)
    ap.add_argument("--owner", default=None)
    ap.add_argument("--field-size", type=int, default=18)
    ap.add_argument("--seeds", type=int, default=40)
    ap.add_argument("--top", type=int, default=10, help="weight vectors to print")
    ap.add_argument(
        "--prize-pool",
        type=int,
        default=None,
        help="Override prize_pool on every input. The real dev pool often has "
        "prize_pool=0 (bank not flush) → the prize term is inert; pass a realistic "
        "overlay (e.g. the DEFAULT_MAIN_EVENT pool) to exercise the redistribution "
        "lever against the real bankroll spread.",
    )
    ap.add_argument(
        "--overlay-sweep",
        default=None,
        help="Comma list of prize-pool sizes as MULTIPLES of the pool median "
        "bankroll, e.g. '0.5,1,2,5,10'. For each, run the full weight grid and "
        "report redistribution + weight-sensitivity + per-term firing — to find "
        "the overlay where prize_appeal stops saturating yet still redistributes.",
    )
    args = ap.parse_args()

    sandbox_id = args.sandbox or _discover_sandbox(DB_PATH)
    if not sandbox_id:
        print("No sandbox with ai_bankroll_state rows found — pass --sandbox.")
        return 1

    inputs = _build_inputs(sandbox_id, args.owner, args.field_size)[1]
    if args.prize_pool is not None:
        inputs = [dataclasses.replace(i, prize_pool=args.prize_pool) for i in inputs]
        print(f"[override] prize_pool={args.prize_pool} on all {len(inputs)} inputs")
    print(
        f"sandbox={sandbox_id} pool={len(inputs)} field_size={args.field_size} seeds={args.seeds}"
    )
    if len(inputs) < 2:
        print("Pool too small to tune.")
        return 1

    renown_present = any(i.own_renown > 0 for i in inputs)
    comfort_present = any(i.cash_comfort > 0 for i in inputs)
    print(
        f"renown data present: {renown_present} | cash-seated personas present: {comfort_present}"
    )

    grid = [0.1, 0.2, 0.3, 0.4, 0.5]

    # --- Overlay sweep: how redistribution + weight-leverage move with prize size.
    if args.overlay_sweep:
        pool_median = _median([i.own_bankroll for i in inputs])
        mults = [float(x) for x in args.overlay_sweep.split(",")]
        print(f"\n[overlay sweep] pool median bankroll = {pool_median:.0f}")
        print(
            f"  {'×med':>5} {'overlay':>8} | {'dflt':>5} {'best':>5} {'spread':>6} "
            f"{'prizeFires':>10} {'cmftR':>6} {'variety':>8}"
        )
        sweep = []
        for m in mults:
            overlay = int(m * pool_median)
            cfg = [dataclasses.replace(i, prize_pool=overlay) for i in inputs]
            s = _run_config(cfg, args.field_size, args.seeds, grid)
            d, b = s["default"], s["best"]
            cr = "n/a" if d["comfort_resistance"] is None else f"{d['comfort_resistance']:.2f}"
            print(
                f"  {m:>5.1f} {overlay:>8d} | {d['redistribution']:>5.2f} "
                f"{b['redistribution']:>5.2f} {s['redist_spread']:>6.3f} "
                f"{str(s['term_fires']['prize']['fires']):>10} {cr:>6} "
                f"{d['variety_distinct_fields']:>3}/{d['variety_of']:<4}"
            )
            sweep.append({"mult": m, "overlay": overlay, **s})
        out_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "docs",
            "experiments",
            "data",
        )
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "EXP_007_overlay_sweep.json")
        with open(out_path, "w") as fh:
            json.dump(
                {
                    "sandbox_id": sandbox_id,
                    "pool_size": len(inputs),
                    "pool_median_bankroll": pool_median,
                    "field_size": args.field_size,
                    "seeds": args.seeds,
                    "renown_present": renown_present,
                    "sweep": sweep,
                },
                fh,
                indent=2,
            )
        print(f"\nwrote {out_path}  ({len(sweep)} overlays)")
        print(
            "\nRead: 'dflt'/'best' = drawn-field median ÷ pool median at default/"
            "best weights (want ≤0.80); 'spread' = how much weights move it "
            "(≈0 → prize saturates, weights moot); 'prizeFires' = H3 for prize."
        )
        return 0

    # H3 — which terms actually fire on this pool.
    fires = _term_fires(inputs, args.field_size)
    print("\n[H3] per-term firing (zero-one-term Δ vs default field):")
    for term, r in fires.items():
        print(f"  {term:13s} changed={r['changed_members']:3d}  fires={r['fires']}")

    # Sweep the coarse grid. The formula isn't normalized, but comparable ratios
    # are what matter; sweep each term over a small range.
    rows = []
    for p, r, f, c in product(grid, grid, grid, grid):
        w = DrawWeights(prize=p, renown=r, field=f, cash_comfort=c)
        m = _eval_weights(inputs, w, args.field_size, args.seeds)
        if m["redistribution"] is None:
            continue
        rows.append({"weights": {"prize": p, "renown": r, "field": f, "cash_comfort": c}, **m})

    # Baseline (current defaults) for reference.
    base_m = _eval_weights(inputs, DEFAULT_WEIGHTS, args.field_size, args.seeds)

    rows.sort(key=lambda x: x["redistribution"])  # lower redistribution = more downward
    print(
        f"\n[baseline .40/.25/.15/.20] redistribution={base_m['redistribution']:.3f} "
        f"comfort_resist={base_m['comfort_resistance']} "
        f"variety={base_m['variety_distinct_fields']}/{base_m['variety_of']}"
    )
    print(f"\n[H1] top {args.top} weight vectors by redistribution (want ≤ 0.80):")
    print(
        f"  {'prize':>5} {'renwn':>5} {'field':>5} {'cmft':>5} | {'redist':>6} {'cmftR':>6} {'variety':>8}"
    )
    for x in rows[: args.top]:
        w = x["weights"]
        cr = "n/a" if x["comfort_resistance"] is None else f"{x['comfort_resistance']:.2f}"
        print(
            f"  {w['prize']:>5.2f} {w['renown']:>5.2f} {w['field']:>5.2f} {w['cash_comfort']:>5.2f} | "
            f"{x['redistribution']:>6.3f} {cr:>6} {x['variety_distinct_fields']:>3}/{x['variety_of']:<4}"
        )

    out_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "experiments", "data"
    )
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "EXP_007_draw_weights_sweep.json")
    with open(out_path, "w") as fh:
        json.dump(
            {
                "sandbox_id": sandbox_id,
                "pool_size": len(inputs),
                "field_size": args.field_size,
                "seeds": args.seeds,
                "renown_present": renown_present,
                "comfort_present": comfort_present,
                "term_fires": fires,
                "baseline": base_m,
                "results": rows,
            },
            fh,
            indent=2,
        )
    print(f"\nwrote {out_path}  ({len(rows)} vectors)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
