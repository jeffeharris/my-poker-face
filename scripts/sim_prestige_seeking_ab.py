"""B4 prestige-seeking economy A/B + W_MARQUEE calibration sweep.

Same-seed paired probe for `PRESTIGE_SEEKING_ENABLED`: seed one sandbox + a
renown field, copy it per arm, run the FULL economy sim (casinos + fish +
churn) from the identical start, and compare flag-OFF vs flag-ON across a sweep
of `W_MARQUEE` values. Per arm we report:

  1. ROUTING — co-location of non-fish, non-famous grinders with a famous AI
     (the marquee pull's whole point). ON should rise above OFF.
  2. CONSERVATION — `audit_drift` must stay ~0 (the new seat path must not
     mint/destroy chips).
  3. STARVATION — grinders still seated at fish (casino) tables. The marquee
     must NOT drain the EV economy. The calibrated W_MARQUEE is the largest
     pull that lifts routing WITHOUT collapsing this.

Fish/casinos spawn over ticks from the seeded bank pool, so this needs
`hand_sim_prob > 0` and enough ticks. Long sweeps want a detached box
(docs/EVAL_RUNNER.md) — the bash cap kills multi-arm churn runs locally.

    docker compose run --rm --no-deps -v "$PWD/scripts:/app/scripts" backend \
        python3 scripts/sim_prestige_seeking_ab.py \
        --ticks 500 --hand-sim-prob 0.5 --w-sweep 1,3,5,8

scripts/ is gitignored — force-add to keep it.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime

from cash_mode import economy_flags
from cash_mode.closed_economy import load_fish_ids
from cash_mode.sim_runner import SimConfig, run_sim
from poker.repositories import create_repos

sys.path.insert(0, "/app/scripts")
from seed_sim_sandbox import seed_sim_sandbox  # noqa: E402


def _seed_renown(db_path, sandbox_id, owner_id, n_famous):
    repos = create_repos(db_path)
    eligible = repos["personality_repo"].list_eligible_for_cash_mode(user_id=owner_id)
    fish = load_fish_ids(repos["bankroll_repo"], sandbox_id=sandbox_id)
    pids = sorted(
        p["personality_id"]
        for p in eligible
        if p.get("personality_id") and p["personality_id"] not in fish
    )
    famous = set(pids[:n_famous])
    rows = [
        {
            "owner_id": pid,
            "renown_v2": 60.0 if pid in famous else 10.0,
            "regard": 0.0,
            "quadrant": "Infamous Villain" if pid in famous else "Up-and-comer",
            "victim_percentile": 0.90 if pid in famous else 0.15,
            "high_cut": 30.0,
            "components": {"breadth": 1.0},
            "field_size": len(pids),
        }
        for pid in pids
    ]
    repos["prestige_snapshots_repo"].record_ai_many(
        sandbox_id=sandbox_id, captured_at="2026-06-02T00:00:00Z", rows=rows
    )
    return famous


def _checkpoint(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()


def _run_arm(db_path, sandbox_id, *, enabled, w_marquee, ticks, seed, hand_sim_prob, bank_pool):
    economy_flags.PRESTIGE_SEEKING_ENABLED = enabled
    if w_marquee is not None:
        import cash_mode.attractiveness as _attr

        _attr.W_MARQUEE = w_marquee
    repos = create_repos(db_path)
    result = run_sim(
        SimConfig(
            sandbox_id=sandbox_id,
            num_ticks=ticks,
            rng_seed=seed,
            start_at=datetime(2026, 6, 2, 12, 0, 0),
            hand_sim_prob=hand_sim_prob,
            initial_bank_pool_seed=bank_pool,
            audit_every=25,
            progress_every=0,
        ),
        repos=repos,
    )
    tables = repos["cash_table_repo"].list_all_tables(sandbox_id=sandbox_id)
    fish = load_fish_ids(repos["bankroll_repo"], sandbox_id=sandbox_id)  # post-run
    drifts = [abs(m.audit_drift) for m in result.metrics if m.audit_drift is not None]
    return tables, fish, (max(drifts) if drifts else 0)


def _metrics(tables, famous, fish):
    grinder = marquee = fish_table = 0
    for t in tables:
        occ = [s.get("personality_id") for s in t.seats if s.get("kind") == "ai"]
        has_famous = any(p in famous for p in occ)
        has_fish = any(p in fish for p in occ)
        for p in occ:
            if p in fish or p in famous:
                continue
            grinder += 1
            marquee += 1 if has_famous else 0
            fish_table += 1 if has_fish else 0
    return {
        "grinders": grinder,
        "co_location": (marquee / grinder) if grinder else 0.0,
        "at_fish": fish_table,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ticks", type=int, default=500)
    ap.add_argument("--rng-seed", type=int, default=42)
    ap.add_argument("--hand-sim-prob", type=float, default=0.5)
    ap.add_argument("--famous", type=int, default=4)
    ap.add_argument("--bank-pool", type=int, default=3_000_000)
    ap.add_argument(
        "--w-sweep", default="1,3,5,8", help="Comma list of W_MARQUEE values to test (ON arms)"
    )
    args = ap.parse_args()
    w_values = [float(x) for x in args.w_sweep.split(",")]

    tmp = tempfile.mkdtemp(prefix="b4ab_")
    base = f"{tmp}/base.db"
    sandbox_id = seed_sim_sandbox(name="b4-ab", owner_id="sim-bot", db_path=base)
    famous = _seed_renown(base, sandbox_id, "sim-bot", args.famous)
    _checkpoint(base)

    print(
        f"sandbox={sandbox_id} famous={sorted(famous)} ticks={args.ticks} "
        f"seed={args.rng_seed} hand_sim_prob={args.hand_sim_prob} "
        f"bank_pool={args.bank_pool} w_sweep={w_values}"
    )
    print("=" * 78)

    def arm(label, enabled, w):
        db = f"{tmp}/{label}.db"
        shutil.copy(base, db)
        tables, fish, drift = _run_arm(
            db,
            sandbox_id,
            enabled=enabled,
            w_marquee=w,
            ticks=args.ticks,
            seed=args.rng_seed,
            hand_sim_prob=args.hand_sim_prob,
            bank_pool=args.bank_pool,
        )
        m = _metrics(tables, famous, fish)
        print(
            f"[{label:10s}] co-loc {m['co_location']*100:5.1f}%  "
            f"grinders={m['grinders']:3d}  at-fish={m['at_fish']:3d}  "
            f"fish_seen={len(fish):2d}  max|drift|={drift}"
        )
        return m, drift

    off_m, off_d = arm("OFF", False, None)
    print("-" * 78)
    for w in w_values:
        on_m, on_d = arm(f"ON_w{w:g}", True, w)
        lift = (on_m["co_location"] - off_m["co_location"]) * 100
        fish_keep = (on_m["at_fish"] / off_m["at_fish"]) if off_m["at_fish"] else 1.0
        flags = []
        flags.append("route+" if lift > 1.0 else "route~")
        flags.append("drift!" if on_d != 0 else "drift0")
        flags.append("STARVE" if fish_keep < 0.6 else "fishOK")
        print(
            f"    └─ W={w:g}: routing {lift:+.1f}pp | fish-table grinders "
            f"{on_m['at_fish']} vs {off_m['at_fish']} ({fish_keep*100:.0f}%) | "
            f"{' '.join(flags)}"
        )
    print("=" * 78)
    print(f"CONSERVATION: OFF drift={off_d} (must be 0 across all arms above)")
    print("Calibration target: the largest W with routing+ AND fishOK AND drift0.")


if __name__ == "__main__":
    main()
