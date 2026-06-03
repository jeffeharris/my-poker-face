"""B4 prestige-seeking — event-level W_MARQUEE calibration probe.

The same-seed economy A/B decoheres under churn (one different seat → different
hands → different economies), so a final-snapshot diff can't calibrate W. This
probe sidesteps that: it measures the marquee term's influence AT THE DECISION
POINT, on identical state, within a SINGLE run — no decoherence.

Mechanic: the marquee bonus is LINEAR in W. For each candidate table at each
seat-fill decision, score(W) = s0 + W·Δ, where
  s0 = table_attractiveness WITHOUT marquee  (status_appetite/marquee = 0)
  Δ  = (score WITH marquee at W=1) − s0       (the per-unit-W contribution)
So one instrumented run at W=1 captures (s0, Δ, occ_prestige) per candidate per
decision, and we can compute the argmax at ANY W offline. The calibration metric
is the INFLUENCE RATE: of decisions that HAD a marquee option (some candidate
with occ_prestige>0), what fraction does a marquee of strength W swing to a
higher-prestige table than the no-marquee pick? Sweep W → pick the value with a
"tasteful" influence (felt, not domineering).

Monkeypatches the greedy seater (no production change). Run in Docker:

    docker compose run --rm --no-deps -v "$PWD/scripts:/app/scripts" backend \
        python3 scripts/sim_prestige_probe.py --ticks 250 --hand-sim-prob 0.5

scripts/ is gitignored — force-add to keep it.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import datetime

from cash_mode import economy_flags
import cash_mode.attractiveness as attr
import cash_mode.lobby as lobby
from cash_mode.attractiveness import seeker_buy_in, table_attractiveness
from cash_mode.closed_economy import load_fish_ids
from cash_mode.sim_runner import SimConfig, run_sim
from poker.repositories import create_repos

sys.path.insert(0, "/app/scripts")
from seed_sim_sandbox import seed_sim_sandbox  # noqa: E402

# Each entry: list of (table_id, s0, delta, occ) for ONE seat-fill decision,
# plus the index of the table the live (W=1) greedy actually chose.
DECISIONS: list = []

_real_greedy = attr.assign_seats_greedy


def _probing_greedy(seekers, tables):
    """Faithful re-impl of assign_seats_greedy (incl. between-pick crowd
    updates) that ALSO logs each seeker's per-candidate (s0, Δ, occ)."""
    assignments = []
    for seeker in seekers:
        cand = []  # (tid, s0, delta, occ, score_with)
        best_id = None
        best_score = None
        for tid in sorted(seeker.allowed_table_ids):
            t = tables.get(tid)
            if t is None or t.open_count <= 0:
                continue
            if seeker.projected_bankroll < seeker_buy_in(t, seeker.buy_in_multiplier):
                continue
            common = dict(
                projected_bankroll=seeker.projected_bankroll,
                starting_bankroll=seeker.starting_bankroll,
                comfort_zone=seeker.comfort_zone, stake_label=t.stake_label,
                fish_chips=t.fish_chips, whale_chips=t.whale_chips,
                other_grinders=t.grinder_count, buy_in_multiplier=seeker.buy_in_multiplier,
                prestige_override=t.prestige_override, venue_appeal=t.venue_appeal,
            )
            s0 = table_attractiveness(**common, marquee_prestige=0.0, status_appetite=0.0)
            s_with = table_attractiveness(**common, marquee_prestige=t.marquee_prestige,
                                          status_appetite=seeker.status_appetite)
            cand.append((tid, s0, s_with - s0, t.marquee_prestige))
            if best_score is None or s_with > best_score:
                best_score, best_id = s_with, tid
        if best_id is None:
            continue
        chosen = tables[best_id]
        chosen.open_count -= 1
        chosen.grinder_count += 1
        assignments.append((seeker.personality_id, best_id))
        if len(cand) >= 2:  # only multi-candidate decisions can be "influenced"
            DECISIONS.append(cand)
    return assignments


def _argmax_at(cand, w):
    """Index of the max-score(W) candidate; W-tie broken by sorted id (as the
    real greedy's `sorted(allowed)` + strict-> does)."""
    best_i, best = 0, None
    for i, (_tid, s0, delta, _occ) in enumerate(cand):
        s = s0 + w * delta
        if best is None or s > best:
            best, best_i = s, i
    return best_i


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ticks", type=int, default=250)
    ap.add_argument("--rng-seed", type=int, default=42)
    ap.add_argument("--hand-sim-prob", type=float, default=0.5)
    ap.add_argument("--famous", type=int, default=4)
    ap.add_argument("--bank-pool", type=int, default=3_000_000)
    ap.add_argument("--w-grid", default="0.5,1,2,3,4,5,6,8,10,15")
    args = ap.parse_args()
    w_grid = [float(x) for x in args.w_grid.split(",")]

    db = tempfile.mktemp(suffix=".db")
    sandbox_id = seed_sim_sandbox(name="b4-probe", owner_id="sim-bot", db_path=db)
    repos = create_repos(db)
    eligible = repos["personality_repo"].list_eligible_for_cash_mode(user_id="sim-bot")
    fish = load_fish_ids(repos["bankroll_repo"], sandbox_id=sandbox_id)
    pids = sorted(p["personality_id"] for p in eligible
                  if p.get("personality_id") and p["personality_id"] not in fish)
    famous = set(pids[:args.famous])
    repos["prestige_snapshots_repo"].record_ai_many(
        sandbox_id=sandbox_id, captured_at="2026-06-02T00:00:00Z",
        rows=[{"owner_id": pid, "renown_v2": 60.0 if pid in famous else 10.0,
               "regard": 0.0, "quadrant": "x",
               "victim_percentile": 0.90 if pid in famous else 0.15,
               "high_cut": 30.0, "components": {}, "field_size": len(pids)}
              for pid in pids])

    # Instrument the greedy seater the lobby calls, run ONE sim at W=1.
    economy_flags.PRESTIGE_SEEKING_ENABLED = True
    attr.W_MARQUEE = 1.0
    lobby.assign_seats_greedy = _probing_greedy
    run_sim(SimConfig(sandbox_id=sandbox_id, num_ticks=args.ticks, rng_seed=args.rng_seed,
                      start_at=datetime(2026, 6, 2, 12, 0, 0), hand_sim_prob=args.hand_sim_prob,
                      initial_bank_pool_seed=args.bank_pool, audit_every=9999, progress_every=0),
            repos=repos)
    lobby.assign_seats_greedy = _real_greedy

    # Decisions that HAD a marquee option (some candidate with occ>0) — the only
    # ones the marquee term can possibly swing.
    relevant = [c for c in DECISIONS if any(occ > 0 for *_x, occ in c)]
    print(f"sandbox={sandbox_id} famous={len(famous)} ticks={args.ticks} "
          f"hand_sim_prob={args.hand_sim_prob}")
    print(f"multi-candidate decisions={len(DECISIONS)}  "
          f"with a marquee option={len(relevant)}")
    print("=" * 70)
    print(f"{'W':>5} | {'influence':>9} | {'mean occ of pick':>16} | "
          f"{'(baseline pick occ)':>20}")
    print("-" * 70)
    if not relevant:
        print("no marquee-eligible decisions — increase ticks / famous / churn")
        return
    base_occ = sum(c[_argmax_at(c, 0.0)][3] for c in relevant) / len(relevant)
    for w in w_grid:
        flips = 0
        pick_occ = 0.0
        for c in relevant:
            i0 = _argmax_at(c, 0.0)
            iw = _argmax_at(c, w)
            if iw != i0:
                flips += 1
            pick_occ += c[iw][3]
        infl = flips / len(relevant)
        print(f"{w:>5g} | {infl*100:>8.1f}% | {pick_occ/len(relevant):>16.3f} | "
              f"{base_occ:>20.3f}")
    print("=" * 70)
    print("Calibration: pick the W whose influence is FELT but not domineering "
          "(~15-35%); mean-occ-of-pick should rise above the baseline pick occ.")


if __name__ == "__main__":
    main()
