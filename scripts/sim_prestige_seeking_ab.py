"""B4 prestige-seeking economy A/B gate.

Same-seed paired probe for `PRESTIGE_SEEKING_ENABLED`: seed one sandbox + a
renown field, copy it, run the economy sim twice (flag OFF vs ON) from the
identical start, and compare:

  1. ROUTING — do non-fish grinders co-locate with the famous AIs more when the
     marquee pull is on? (the feature's whole point)
  2. CONSERVATION — does `audit_drift` stay ~0 in BOTH arms? (the new seat path
     must not mint/destroy chips)
  3. NO STARVATION — do fish tables still draw grinders when the flag is on?
     (the marquee shouldn't drain the EV economy)

Run in Docker (needs the app deps):

    docker compose run --rm --no-deps -v "$PWD/scripts:/app/scripts" backend \
        python3 scripts/sim_prestige_seeking_ab.py --ticks 300 --hand-sim-prob 0.0

scripts/ is gitignored — force-add to keep it.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime

from cash_mode import economy_flags
from cash_mode.closed_economy import load_fish_ids
from cash_mode.sim_runner import SimConfig, run_sim
from poker.repositories import create_repos

# Local import (scripts/ is mounted, not on the package path by default).
import sys
sys.path.insert(0, "/app/scripts")
from seed_sim_sandbox import seed_sim_sandbox  # noqa: E402


def _seed_renown(db_path, sandbox_id, owner_id, n_famous):
    """Seed a victim_percentile field: the top `n_famous` non-fish personas are
    'famous' (0.90), everyone else gets a modest 0.15. Returns the famous set."""
    repos = create_repos(db_path)
    eligible = repos["personality_repo"].list_eligible_for_cash_mode(user_id=owner_id)
    fish = load_fish_ids(repos["bankroll_repo"], sandbox_id=sandbox_id)
    pids = [p["personality_id"] for p in eligible
            if p.get("personality_id") and p["personality_id"] not in fish]
    pids.sort()  # deterministic
    famous = set(pids[:n_famous])
    rows = [{
        "owner_id": pid,
        "renown_v2": 60.0 if pid in famous else 10.0,
        "regard": 0.0,
        "quadrant": "Infamous Villain" if pid in famous else "Up-and-comer",
        "victim_percentile": 0.90 if pid in famous else 0.15,
        "high_cut": 30.0, "components": {"breadth": 1.0}, "field_size": len(pids),
    } for pid in pids]
    repos["prestige_snapshots_repo"].record_ai_many(
        sandbox_id=sandbox_id, captured_at="2026-06-02T00:00:00Z", rows=rows,
    )
    return famous, fish


def _checkpoint(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()


def _run_arm(db_path, sandbox_id, *, enabled, ticks, seed, hand_sim_prob):
    economy_flags.PRESTIGE_SEEKING_ENABLED = enabled
    repos = create_repos(db_path)
    result = run_sim(
        SimConfig(
            sandbox_id=sandbox_id, num_ticks=ticks, rng_seed=seed,
            start_at=datetime(2026, 6, 2, 12, 0, 0), hand_sim_prob=hand_sim_prob,
            audit_every=25, progress_every=0,
        ),
        repos=repos,
    )
    # Final seating snapshot.
    tables = repos["cash_table_repo"].list_all_tables(sandbox_id=sandbox_id)
    drifts = [abs(m.audit_drift) for m in result.metrics if m.audit_drift is not None]
    return tables, (max(drifts) if drifts else 0), result


def _routing_metrics(tables, famous, fish):
    """Co-location of non-fish, non-famous grinders with a famous occupant."""
    grinder_seats = marquee_seats = fish_table_grinders = 0
    for t in tables:
        occ = [s.get("personality_id") for s in t.seats if s.get("kind") == "ai"]
        has_famous = any(p in famous for p in occ)
        has_fish = any(p in fish for p in occ)
        for p in occ:
            if p in fish or p in famous:
                continue
            grinder_seats += 1
            if has_famous:
                marquee_seats += 1
            if has_fish:
                fish_table_grinders += 1
    rate = (marquee_seats / grinder_seats) if grinder_seats else 0.0
    return {
        "grinder_seats": grinder_seats,
        "at_marquee": marquee_seats,
        "co_location_rate": rate,
        "at_fish_tables": fish_table_grinders,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ticks", type=int, default=300)
    ap.add_argument("--rng-seed", type=int, default=42)
    ap.add_argument("--hand-sim-prob", type=float, default=0.0,
                    help="0.0 = movement-only (cleanest routing attribution)")
    ap.add_argument("--famous", type=int, default=4)
    ap.add_argument("--w-marquee", type=float, default=None,
                    help="Override attractiveness.W_MARQUEE (sim-tune the pull)")
    args = ap.parse_args()

    if args.w_marquee is not None:
        import cash_mode.attractiveness as _attr
        _attr.W_MARQUEE = args.w_marquee
        print(f"(W_MARQUEE overridden to {args.w_marquee})")

    tmp = tempfile.mkdtemp(prefix="b4ab_")
    base = f"{tmp}/base.db"
    sandbox_id = seed_sim_sandbox(name="b4-ab", owner_id="sim-bot", db_path=base)
    famous, fish = _seed_renown(base, sandbox_id, "sim-bot", args.famous)
    _checkpoint(base)
    off_db, on_db = f"{tmp}/off.db", f"{tmp}/on.db"
    shutil.copy(base, off_db)
    shutil.copy(base, on_db)

    print(f"sandbox={sandbox_id}  famous={sorted(famous)}  fish={len(fish)}  "
          f"ticks={args.ticks} seed={args.rng_seed} hand_sim_prob={args.hand_sim_prob}")
    print("-" * 72)
    out = {}
    for arm, db, enabled in (("OFF", off_db, False), ("ON", on_db, True)):
        tables, max_drift, _ = _run_arm(
            db, sandbox_id, enabled=enabled, ticks=args.ticks,
            seed=args.rng_seed, hand_sim_prob=args.hand_sim_prob)
        m = _routing_metrics(tables, famous, fish)
        out[arm] = (m, max_drift)
        print(f"[{arm:3s}] co-location {m['co_location_rate']*100:5.1f}%  "
              f"({m['at_marquee']}/{m['grinder_seats']} grinder seats at a "
              f"famous table)  fish-table grinders={m['at_fish_tables']}  "
              f"max|audit_drift|={max_drift}")
    print("-" * 72)
    off_m, off_d = out["OFF"]; on_m, on_d = out["ON"]
    lift = on_m["co_location_rate"] - off_m["co_location_rate"]
    print(f"ROUTING lift (ON − OFF): {lift*100:+.1f} pp co-location")
    print(f"CONSERVATION: max|drift| OFF={off_d} ON={on_d} "
          f"({'OK' if off_d == 0 and on_d == 0 else 'CHECK'})")
    if fish:
        starv_ok = on_m["at_fish_tables"] > 0
        print(f"STARVATION: fish-table grinders OFF={off_m['at_fish_tables']} "
              f"ON={on_m['at_fish_tables']} ({'OK' if starv_ok else 'STARVED'})")
    else:
        starv_ok = True  # no fish seeded → starvation check N/A (clean isolate)
        print("STARVATION: N/A (no fish seeded — grinder-only routing isolate)")
    verdict = "PASS" if (lift > 0.0 and off_d == 0 and on_d == 0 and starv_ok) else "REVIEW"
    print(f"VERDICT: {verdict}")


if __name__ == "__main__":
    main()
