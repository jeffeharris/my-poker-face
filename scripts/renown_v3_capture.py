#!/usr/bin/env python3
"""Rung 3 — capture a FROZEN renown-input log (the thing the sweep re-scores).

Re-scoring one frozen log under many weight grids is a *perfectly paired* A/B
(identical underlying events — no RNG desync). This script produces that log;
`renown_v3_sweep.py` consumes it. Two modes:

  --from-db   (host, read-only): snapshot the real sandbox field's renown
              inputs straight from the DB. No scalps (no cash_scalps table) —
              used to validate the sweep machinery on real data.

  --from-sim  (Docker): run the cash world sim (`full_sim.play_one_hand`,
              rule-based, no LLM, no DB writes) over the sandbox's AI field,
              derive SCALPS via cash_mode.scalps.eliminations_from_sim, and
              overlay the play-derived drivers (scalps, volume, breadth,
              time-at-#1, peak stack) onto the DB field's economy/social
              drivers (backing, regard). This is the complete Rung-3 log — the
              first one where the villain/scalp route is populated.

Frozen-log JSON schema:
  {"meta": {...}, "entities": {entity_id: <RenownInputs asdict> , ...}}

Run (host):    python3 scripts/renown_v3_capture.py --from-db -o /tmp/renown_log.json
Run (Docker):  docker compose exec backend python3 scripts/renown_v3_capture.py \
                   --from-sim --hands 400 -o /app/data/renown_log_sim.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import asdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from renown_v2_scorer import RenownInputs  # noqa: E402

# Sim knobs (only used by --from-sim).
SIM_SEED = 1729
SIM_BIG_BLIND = 50
SIM_STARTING_STACK = 5_000
SIM_TABLE_SIZE = 6


def _dump(field, meta, out_path):
    payload = {"meta": meta, "entities": {eid: asdict(inp) for eid, inp in field.items()}}
    with open(out_path, "w") as fh:
        json.dump(payload, fh, indent=1)
    print(f"wrote {len(field)} entities → {out_path}  (source={meta.get('source')})")


def capture_from_db(sandbox, out_path):
    """Read-only snapshot of the real field's renown inputs (no scalps)."""
    from renown_v2_rung2 import DEFAULT_SANDBOX, HUMAN_ID, connect, load_field

    sb = sandbox or DEFAULT_SANDBOX
    con = connect()
    field = load_field(con, sb)
    con.close()
    _dump(field, {"sandbox": sb, "source": "db", "human_id": HUMAN_ID,
                  "note": "scalps=0 (no cash_scalps table); volume=hands"}, out_path)


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def capture_from_sim(sandbox, hands, out_path):
    """Run the real cash sim over the field's AI ids, derive scalps, and merge
    the play-derived drivers onto the DB field. Docker only (imports engine)."""
    import random

    from renown_v2_rung2 import DEFAULT_SANDBOX, HUMAN_ID, connect, load_field
    from cash_mode.controller_cache import LruControllerCache
    from cash_mode.full_sim import play_one_hand
    from cash_mode.scalps import eliminations_from_sim
    from cash_mode.tables import ai_slot, open_slot

    sb = sandbox or DEFAULT_SANDBOX
    con = connect()
    field = load_field(con, sb)
    con.close()
    ai_ids = [e for e in field if e != HUMAN_ID]

    scalps = defaultdict(lambda: defaultdict(int))
    sim_hands = defaultdict(int)
    coseat = defaultdict(lambda: defaultdict(int))
    leader_ticks = defaultdict(int)
    peak = defaultdict(float)

    rng = random.Random(SIM_SEED)
    cache = LruControllerCache(max_size=64)
    pool = list(ai_ids)
    rng.shuffle(pool)

    def name_for(pid):  # identity — controllers fall back to pid; no DB needed
        return pid

    for chunk in _chunks(pool, SIM_TABLE_SIZE):
        if len(chunk) < 2:
            continue  # need ≥2 to play
        seats = [ai_slot(pid, SIM_STARTING_STACK) for pid in chunk]
        while len(seats) < SIM_TABLE_SIZE:
            seats.append(open_slot())
        for _ in range(hands):
            r = play_one_hand(
                seats, big_blind=SIM_BIG_BLIND, rng=rng, sandbox_id="renown_v3_sim",
                name_for=name_for, controller_cache=cache,
                bankroll_repo=None, chip_ledger_repo=None,
            )
            for elim, vic in eliminations_from_sim(r):
                scalps[elim][vic] += 1
            seated = [s["personality_id"] for s in seats
                      if s.get("kind") == "ai" and s.get("chips", 0) > 0]
            for pid in seated:
                sim_hands[pid] += 1
            for a in seated:
                for b in seated:
                    if a != b:
                        coseat[a][b] += 1
            if r.delta > 0:
                seats = r.new_seats
            ai_seats = [(s.get("personality_id"), s.get("chips", 0))
                        for s in seats if s.get("kind") == "ai"]
            if ai_seats:
                lead = max(ai_seats, key=lambda x: x[1])
                if lead[1] > 0:
                    leader_ticks[lead[0]] += 1
                for pid, ch in ai_seats:
                    peak[pid] = max(peak[pid], ch)
            # rebuy busted seats in place so hands keep flowing
            seats = [ai_slot(s["personality_id"], SIM_STARTING_STACK)
                     if s.get("kind") == "ai" and s.get("chips", 0) <= 0 else s
                     for s in seats]

    # Overlay play-derived drivers onto the DB field (keep backing/regard).
    for pid in ai_ids:
        inp = field[pid]
        inp.scalps = dict(scalps.get(pid, {}))
        inp.total_hands = sim_hands.get(pid, 0)
        inp.wall_clock_hours = float(sim_hands.get(pid, 0))
        if coseat.get(pid):
            inp.breadth_opponents = dict(coseat[pid])
        inp.ticks_at_number_one = leader_ticks.get(pid, 0)
        inp.peak_net_worth = max(inp.peak_net_worth, peak.get(pid, 0.0))

    total_scalps = sum(sum(v.values()) for v in scalps.values())
    _dump(field, {"sandbox": sb, "source": "sim", "human_id": HUMAN_ID,
                  "hands_per_table": hands, "ai_entities": len(ai_ids),
                  "total_scalps": total_scalps,
                  "note": "play-derived drivers from sim; backing/regard from DB"},
          out_path)


def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--from-db", action="store_true", help="read-only DB snapshot (host)")
    mode.add_argument("--from-sim", action="store_true", help="run the cash sim (Docker)")
    ap.add_argument("--sandbox", default=None)
    ap.add_argument("--hands", type=int, default=400, help="sim hands per table")
    ap.add_argument("-o", "--out", default="/tmp/renown_log.json")
    args = ap.parse_args()
    if args.from_db:
        capture_from_db(args.sandbox, args.out)
    else:
        capture_from_sim(args.sandbox, args.hands, args.out)


if __name__ == "__main__":
    main()
