#!/usr/bin/env python3
"""Rung 2 — score the REAL field with the Renown-v2 formula (read-only).

Rung 1 proved the formula's *structure* on fixtures. Rung 2 is the
baseline-first sanity check: point the same scorer at the real dev DB and ask
"do the top names match intuition about who the field's big figures are?"

This reads the LIVE main-worktree DB with `immutable=1` — a consistent
main-file snapshot, no locks, no WAL writes (per docs/.../SQLITE_WAL_BACKUP
guidance: never read a live WAL DB the naive way). It writes NOTHING.

Maps the real repos onto the scorer's symmetric `RenownInputs`:
  breadth/roster_net  <- cash_pair_stats (observer perspective)
  peak_net_worth/#1   <- holdings_snapshots (per-tick net-worth rank)
  backing             <- stakes (staker perspective)
  stakes_hands/tenure <- cash_sessions (HUMAN ONLY — the AI-symmetry gap)
  regard              <- relationship_states INBOUND edges
  scalps / legendary  <- NOT AVAILABLE yet (no cash_scalps table, no nugget
                         log) -> 0 for everyone. The villain *route* therefore
                         can't show in Rung 2; flagged in the output.

Volume is denominated in HANDS here (a static snapshot has no live wall-clock
presence to measure) — the wall-clock anti-treadmill governor was validated in
Rung 1, not here.

Run:  python3 scripts/renown_v2_rung2.py [sandbox_id]
"""

from __future__ import annotations

import os
import sqlite3
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from renown_v2_scorer import (  # noqa: E402
    RenownInputs, Weights, score_field, total_renown, regard_of, high_renown_cut,
    quadrant, DRIVER_ORDER,
)

DB = "/home/jeffh/projects/my-poker-face/data/poker_games.db"
DEFAULT_SANDBOX = "4db9b9f2-0724-439a-a4f9-1329c3678611"
HUMAN_ID = "guest_jeff"


def connect():
    return sqlite3.connect(f"file:{DB}?immutable=1", uri=True)


def load_field(con, sandbox: str):
    c = con.cursor()

    # --- entities = everyone with cash pair-stat activity in this sandbox ---
    pair = defaultdict(dict)       # observer -> {opponent: hands}
    roster_net = defaultdict(int)  # observer -> sum cumulative_pnl
    for obs, opp, pnl, hands in c.execute(
        "SELECT observer_id, opponent_id, cumulative_pnl, hands_played_cash "
        "FROM cash_pair_stats WHERE sandbox_id=? AND hands_played_cash>0", (sandbox,)
    ):
        pair[obs][opp] = hands
        roster_net[obs] += (pnl or 0)
    entities = set(pair) | {HUMAN_ID}

    # --- holdings: peak net worth + time-at-#1 (per-tick net-worth rank) ---
    peak = defaultdict(float)
    tick_best = {}  # captured_at -> (best_net, entity)
    for ts, raw_eid, nw in c.execute(
        "SELECT captured_at, entity_id, net_worth FROM holdings_snapshots "
        "WHERE sandbox_id=?", (sandbox,)
    ):
        # holdings_snapshots prefixes ids ('ai:deadpool'/'player:guest_jeff');
        # cash_pair_stats + stakes use raw ids — strip to join.
        eid = raw_eid.split(":", 1)[-1]
        nw = nw or 0
        if nw > peak[eid]:
            peak[eid] = nw
        cur = tick_best.get(ts)
        if cur is None or nw > cur[0]:
            tick_best[ts] = (nw, eid)
    ticks_at_one = defaultdict(int)
    for _, (_, eid) in tick_best.items():
        ticks_at_one[eid] += 1

    # --- backing (staker perspective): volume + settled profit ---
    backing_vol = defaultdict(float)
    backing_profit = defaultdict(float)
    for sid, principal, status, payout in c.execute(
        "SELECT staker_id, principal, status, staker_payout FROM stakes "
        "WHERE staker_id IS NOT NULL AND staker_id != 'anonymous'"
    ):
        backing_vol[sid] += (principal or 0)
        if status == "settled" and payout is not None:
            backing_profit[sid] += (payout - (principal or 0))

    # --- human sessions: tenure + per-tier hands (human only) ---
    stakes_hands = defaultdict(lambda: defaultdict(int))
    for owner, label, hands in c.execute(
        "SELECT owner_id, stake_label, hands_played FROM cash_sessions "
        "WHERE sandbox_id=? AND ended_at IS NOT NULL", (sandbox,)
    ):
        stakes_hands[owner][label] += (hands or 0)

    # --- inbound regard edges, for every entity ---
    inbound = defaultdict(list)  # target -> [(lik,resp,heat)]
    for tgt, lik, resp, heat in c.execute(
        "SELECT opponent_id, likability, respect, heat FROM relationship_states"
    ):
        inbound[tgt].append((lik, resp, heat))

    # --- assemble RenownInputs per entity ---
    field = {}
    for eid in entities:
        opps = pair.get(eid, {})
        total_hands = sum(opps.values())
        edges = inbound.get(eid, [])
        if edges:
            rl = sum(l - 0.5 for l, _, _ in edges) / len(edges)
            rr = sum(r - 0.5 for _, r, _ in edges) / len(edges)
            rh = sum(h for _, _, h in edges) / len(edges)
        else:
            rl = rr = rh = 0.0
        field[eid] = RenownInputs(
            label=(eid[:22]),
            breadth_opponents=dict(opps),
            total_hands=total_hands,
            wall_clock_hours=float(total_hands),  # proxy; volume denom = hands
            roster_net=float(roster_net.get(eid, 0)),
            peak_net_worth=peak.get(eid, 0.0),
            ticks_at_number_one=ticks_at_one.get(eid, 0),
            backing_volume=backing_vol.get(eid, 0.0),
            backing_profit=backing_profit.get(eid, 0.0),
            stakes_hands=dict(stakes_hands.get(eid, {})),
            regard_likability=rl, regard_respect=rr, regard_heat=rh,
        )
    return field


def main():
    sandbox = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SANDBOX
    con = connect()
    field = load_field(con, sandbox)
    con.close()

    # Volume = hands here (static snapshot has no live wall-clock).
    w = Weights(volume_denominator="hands")
    scored = score_field(field, w)
    renowns = {e: total_renown(c) for e, c in scored.items()}
    cut = high_renown_cut(list(renowns.values()), w)
    order = sorted(renowns, key=renowns.get, reverse=True)

    print(f"RENOWN v2 — RUNG 2: real field, sandbox {sandbox[:8]}…")
    print(f"field={len(field)} entities  |  volume denom=hands (Rung-1 validated "
          f"wall-clock not measurable on static data)")
    print(f"scalps & legendary = 0 for all (no cash_scalps table / nugget log "
          f"yet) → villain ROUTE absent in Rung 2\n")
    n_high = sum(1 for r in renowns.values() if r >= cut)
    print(f"high-renown cut = max(top-20%, 3×median) = {cut:.2f}  →  {n_high} figures\n")

    print(f"{'#':>3} {'entity':24} {'renown':>8} {'regard':>7} {'quadrant':>16}  top driver")
    print("-" * 92)
    for rank, eid in enumerate(order[:25], 1):
        c = scored[eid]
        ren = renowns[eid]
        reg = regard_of(field[eid])
        q = quadrant(ren, reg, cut)
        top = max(c, key=c.get)
        share = (c[top] / ren * 100) if ren > 0 else 0
        mark = " ★" if eid == HUMAN_ID else "  "
        print(f"{rank:>3} {field[eid].label:24}{mark}{ren:7.2f} {reg:7.2f} "
              f"{q:>16}  {top} ({share:.0f}%)")

    # Per-driver breakdown for the top 12 — is the order coming from sensible
    # drivers, or is one driver carrying everything?
    print(f"\n{'-'*92}\nPER-DRIVER BREAKDOWN (top 12)\n{'-'*92}")
    print(f"{'entity':24} " + " ".join(f"{d[:6]:>6}" for d in DRIVER_ORDER))
    for eid in order[:12]:
        c = scored[eid]
        print(f"{field[eid].label:24} " + " ".join(f"{c[d]:6.2f}" for d in DRIVER_ORDER))

    # Where does the human land, and how does v2 compare to v1's verdict?
    hrank = order.index(HUMAN_ID) + 1 if HUMAN_ID in order else None
    print(f"\nHuman ({HUMAN_ID}): rank #{hrank} of {len(field)}, "
          f"renown={renowns.get(HUMAN_ID, 0):.2f}, regard={regard_of(field[HUMAN_ID]):.2f}, "
          f"quadrant={quadrant(renowns[HUMAN_ID], regard_of(field[HUMAN_ID]), cut)}")
    print("(v1 today: renown 0.616 capped, regard -0.247, 'Infamous Villain')")


if __name__ == "__main__":
    main()
