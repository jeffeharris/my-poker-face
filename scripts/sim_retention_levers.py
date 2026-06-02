"""Monte-Carlo validation for the cash-mode retention levers.

Drives the real movement decision functions (no DB, no live game) to confirm
the two retention levers behave as designed:

  1. Dwell floor  — a freshly-seated AI's DISCRETIONARY leave pressure ramps
     in over ~DWELL_PATIENCE_HANDS hands, so it never bolts on the hand it
     sits and departures spread out (no synchronized exodus).
  2. Grinder rebuy — a busted non-fish with bankroll "sometimes" reloads;
     the odds decay with each prior rebuy at the table ("stop the bleeding")
     and pull back on a thin bankroll cushion; on decline it take_breaks.

Run inside the backend container (has cash_mode importable):
    docker exec -i <backend> python3 - < scripts/sim_retention_levers.py
or, with the package on PYTHONPATH:
    python3 scripts/sim_retention_levers.py

Pure/offline — safe to run anywhere; touches no database or network.
"""

import collections
import random
import statistics

from cash_mode.movement import (
    DWELL_PATIENCE_HANDS,
    MovementContext,
    _coerce_grinder_rebuy,
    _coerce_predator_retention,
    evaluate_ai_movement,
)

RNG = random.Random(12345)
N = 8000
MIN_BI, MAX_BI = 80, 200


def _ctx_grinder(hands_here, energy=0.3, chips=400, bankroll=4000, deadness=1.0):
    # 2-buy-in stack (not short), tired-ish, at a dead all-shark casino: only
    # discretionary pressure (dead + tenure), so we isolate the dwell floor.
    return MovementContext(
        ai_chips=chips, min_buy_in=MIN_BI, max_buy_in=MAX_BI,
        projected_bankroll=bankroll, stake_idx=0, next_tier_min_buy_in=None,
        energy=energy, table_deadness=deadness, hands_here=hands_here,
    )


def _busted_ctx(bankroll, rebuys_here):
    return MovementContext(
        ai_chips=10, min_buy_in=MIN_BI, max_buy_in=MAX_BI,
        projected_bankroll=bankroll, stake_idx=0, next_tier_min_buy_in=None,
        rebuys_here=rebuys_here,
    )


def sim_dwell_floor():
    def one_life():
        h = 0
        while h < 60:
            d = evaluate_ai_movement(_ctx_grinder(h), RNG)
            d = _coerce_predator_retention(d, False, 0.3, wealth_excess=0.0)
            if d != "stay":
                return h, d
            h += 1
        return h, "stay"

    dist = collections.Counter()
    decs = collections.Counter()
    for _ in range(N):
        h, d = one_life()
        dist[h] += 1
        decs[d] += 1

    print("=" * 64)
    print(f"SIM 1 — DWELL FLOOR (DWELL_PATIENCE_HANDS={DWELL_PATIENCE_HANDS}; energy=0.3, dead casino)")
    print("Hands survived before a voluntary leave:")
    for h in range(0, 13):
        pct = 100 * dist[h] / N
        print(f"  hands_here={h:2}: {pct:5.1f}%  {'#' * int(pct)}")
    print(f"  >>> left on hand 0 (just sat): {100 * dist[0] / N:.2f}%  (target 0.00%)")
    print(f"  leave-decision mix: {dict(decs)}")


def sim_grinder_rebuy():
    def bust_sequence(bankroll, cap=12):
        rebuys, bk, seq = 0, bankroll, []
        for _ in range(cap):
            d = _coerce_grinder_rebuy("forced_leave", _busted_ctx(bk, rebuys), RNG)
            seq.append(d)
            if d == "rebuy":
                rebuys += 1
                bk -= MIN_BI
                continue
            break
        return seq

    print("=" * 64)
    print("SIM 2 — GRINDER REBUY (busts at a table, bankroll=800 = 10 buy-ins)")
    reloads = [bust_sequence(800).count("rebuy") for _ in range(N)]
    cc = collections.Counter(reloads)
    for k in sorted(cc):
        print(f"  {k} reload(s) then stop: {100 * cc[k] / N:5.1f}%  {'#' * int(60 * cc[k] / N)}")
    print(f"  mean reloads before stopping: {statistics.mean(reloads):.2f}")

    print("  single-bust outcome by rebuys already done (bankroll=800):")
    for r in range(4):
        o = collections.Counter(
            _coerce_grinder_rebuy("forced_leave", _busted_ctx(800, r), RNG)
            for _ in range(4000)
        )
        print(f"    rebuys_here={r}: rebuy={o['rebuy'] / 40:.0f}%  take_break={o['take_break'] / 40:.0f}%")

    print("  thin-bankroll cushion (rebuys_here=0):")
    for bk in (80, 160, 240, 400, 800):
        o = collections.Counter(
            _coerce_grinder_rebuy("forced_leave", _busted_ctx(bk, 0), RNG)
            for _ in range(4000)
        )
        print(
            f"    bankroll={bk:4} ({bk // MIN_BI} buy-ins): "
            f"rebuy={o['rebuy'] / 40:.0f}%  take_break={o['take_break'] / 40:.0f}%  "
            f"forced_leave={o['forced_leave'] / 40:.0f}%"
        )
    print("=" * 64)


if __name__ == "__main__":
    sim_dwell_floor()
    sim_grinder_rebuy()
