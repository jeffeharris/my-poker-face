#!/usr/bin/env python3
"""Chart Opportunity Census — sim driver.

Runs the production sharp bot (`TieredBotController`, full chart stack: depth
tables + width-tier archetype tables + Nash push/fold) as the hero against a
homogeneous opponent FIELD, across a sweep of effective stack DEPTHS, and
persists every hero preflop decision (with the chart-coverage snapshot) into a
throwaway sqlite DB. Feed that DB to `scripts/chart_census.py`.

Each (field × depth) matchup is tagged `census__<field>__<depth>bb` so the
analysis can build the archetype matrix. Depths cover both the deep chart
(100/40/25bb) and the short-stack push/fold + reshove regime (15/10bb).

Decisions go through the EXACT production decision path (reuses
`experiments.simulate_bb100.run_6max_matchup`), so the census reflects real
routing, not a re-implementation.

Usage (inside docker):
    docker compose exec backend python3 scripts/chart_census_sim.py --db /tmp/census.db
    docker compose exec backend python3 scripts/chart_census_sim.py \
        --db /tmp/census.db --hands 300 --jobs 6 \
        --fields station,maniac,tag,nit,folder,balanced \
        --depths 100,40,25,15,10

Then:
    docker compose exec backend python3 scripts/chart_census.py /tmp/census.db

NOTE: the sim is CPU-bound (~1-2 hands/s/worker incl. equity recording). Use
--jobs to parallelize across (field,depth) matchups; scale --hands up for a
tighter census once the wiring looks right.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

# User-archetype -> ARCHETYPES key (experiments/simulate_bb100.py). These are
# the six fields in the chart-opportunity archetype matrix.
FIELD_MAP = {
    "station": "Calling Station",
    "maniac": "Maniac",
    "tag": "TAG",
    "nit": "Nit",
    "folder": "FoldyBot",  # rule-bot that folds to aggression (the foldy opener)
    "balanced": "Defender",
    "weakfish": "WeakFish",
    "lag": "LAG",
}

DEFAULT_FIELDS = ["station", "maniac", "tag", "nit", "folder", "balanced"]
DEFAULT_DEPTHS = [100, 40, 25, 15, 10]


def _run_one_matchup(job: dict) -> dict:
    """Worker: run one (field, depth) matchup into its own part DB.

    Defined top-level so ProcessPoolExecutor can pickle it. Returns a result
    dict with the part-DB path and decision count (or an error string).
    """
    # Imports inside the worker: each process pays the import cost once.
    from experiments.simulate_bb100 import ARCHETYPES, run_6max_matchup
    from poker.repositories import create_repos
    from poker.strategy.strategy_table import load_strategy_table

    field = job["field"]
    field_arch = job["field_arch"]
    depth = job["depth_bb"]
    part_db = job["part_db"]
    if os.path.exists(part_db):
        os.remove(part_db)

    if field_arch not in ARCHETYPES:
        return {"field": field, "depth": depth, "error": f"unknown archetype {field_arch}"}
    if job["hero"] not in ARCHETYPES:
        return {"field": field, "depth": depth, "error": f"unknown hero {job['hero']}"}

    repos = create_repos(part_db)
    repo = repos["decision_analysis_repo"]
    st = load_strategy_table()
    game_id = f"census__{field}__{depth}bb"
    run_6max_matchup(
        job["hero"],
        job["hands"],
        st,
        big_blind=job["big_blind"],
        starting_stack=depth * job["big_blind"],
        base_seed=job["seed"],
        opponents=[field_arch] * 5,
        decision_analysis_repo=repo,
        game_id=game_id,
    )
    con = sqlite3.connect(part_db)
    n = con.execute(
        "SELECT COUNT(*) FROM player_decision_analysis "
        "WHERE strategy_pipeline_snapshot_json IS NOT NULL"
    ).fetchone()[0]
    con.close()
    return {"field": field, "depth": depth, "part_db": part_db, "decisions": n, "game_id": game_id}


def _merge_part(target: str, part_db: str) -> None:
    """Copy games + player_decision_analysis rows from a part DB into target.

    Same SchemaManager on both sides, so schemas match. games(game_id) is the
    FK parent (INSERT OR IGNORE — distinct tag per matchup). Decisions copy all
    columns EXCEPT the autoincrement id to avoid cross-part id collisions.
    """
    con = sqlite3.connect(target)
    try:
        con.execute("ATTACH DATABASE ? AS part", (part_db,))
        con.execute("INSERT OR IGNORE INTO games SELECT * FROM part.games")
        cols = [r[1] for r in con.execute("PRAGMA table_info(player_decision_analysis)")]
        cols = [c for c in cols if c != "id"]
        collist = ", ".join(cols)
        con.execute(
            f"INSERT INTO player_decision_analysis ({collist}) "
            f"SELECT {collist} FROM part.player_decision_analysis"
        )
        con.commit()
        con.execute("DETACH DATABASE part")
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Chart opportunity census sim driver")
    ap.add_argument("--db", default="/tmp/census.db", help="target sqlite DB (throwaway)")
    ap.add_argument("--hands", type=int, default=120, help="hands per (field,depth) matchup")
    ap.add_argument("--hero", default="TAG", help="hero archetype (ARCHETYPES key)")
    ap.add_argument(
        "--fields", default=",".join(DEFAULT_FIELDS), help=f"comma list from {sorted(FIELD_MAP)}"
    )
    ap.add_argument(
        "--depths",
        default=",".join(map(str, DEFAULT_DEPTHS)),
        help="comma list of effective-stack depths in bb",
    )
    ap.add_argument("--big-blind", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--jobs", type=int, default=1, help="parallel worker processes")
    args = ap.parse_args()

    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    depths = [int(d) for d in args.depths.split(",") if d.strip()]
    unknown = [f for f in fields if f not in FIELD_MAP]
    if unknown:
        print(f"Unknown field(s) {unknown}. Valid: {sorted(FIELD_MAP)}", file=sys.stderr)
        return 1

    # Initialize the target DB schema once (also the FK target for merges).
    from poker.repositories import create_repos

    if os.path.exists(args.db):
        os.remove(args.db)
    create_repos(args.db)

    tmpdir = tempfile.mkdtemp(prefix="chart_census_")
    jobs = []
    for fi, field in enumerate(fields):
        for depth in depths:
            jobs.append(
                {
                    "field": field,
                    "field_arch": FIELD_MAP[field],
                    "depth_bb": depth,
                    "hero": args.hero,
                    "hands": args.hands,
                    "big_blind": args.big_blind,
                    # distinct, reproducible deck stream per matchup
                    "seed": args.seed + 1000 * fi + depth,
                    "part_db": os.path.join(tmpdir, f"part_{field}_{depth}bb.db"),
                }
            )

    print(
        f"Census: hero={args.hero}  fields={fields}  depths={depths}bb  "
        f"hands/matchup={args.hands}  matchups={len(jobs)}  jobs={args.jobs}"
    )
    t0 = time.time()
    results = []
    if args.jobs > 1:
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            futs = {ex.submit(_run_one_matchup, j): j for j in jobs}
            for fut in as_completed(futs):
                r = fut.result()
                results.append(r)
                _report_part(r)
    else:
        for j in jobs:
            r = _run_one_matchup(j)
            results.append(r)
            _report_part(r)

    # Merge parts into the target DB.
    total = 0
    for r in results:
        if r.get("error"):
            print(f"  !! {r['field']} {r['depth']}bb: {r['error']}", file=sys.stderr)
            continue
        _merge_part(args.db, r["part_db"])
        total += r["decisions"]
        try:
            os.remove(r["part_db"])
        except OSError:
            pass
    try:
        os.rmdir(tmpdir)
    except OSError:
        pass

    dt = time.time() - t0
    print(f"\nDone in {dt:.0f}s. {total} instrumented preflop decisions -> {args.db}")
    print(f"Analyze with:\n  python3 scripts/chart_census.py {args.db}")
    return 0


def _report_part(r: dict) -> None:
    if r.get("error"):
        print(f"  [x] {r['field']:<10} {r['depth']:>3}bb  ERROR: {r['error']}")
    else:
        print(f"  [ok] {r['field']:<10} {r['depth']:>3}bb  {r['decisions']:>5} decisions")


if __name__ == "__main__":
    sys.exit(main())
