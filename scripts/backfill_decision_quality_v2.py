"""Backfill player_decision_analysis rows under v2 quality scoring.

v2 swaps `equity_vs_ranges` (equity vs opponent ranges) in for the legacy
random-hand `equity` when computing `ev_call`, `optimal_action`,
`decision_quality`, and `ev_lost`. Random-hand equity systematically
overestimates hero's chances against typical AI ranges, which biased the
old quality numbers — especially against the tiered (sharp) bot whose
solver tables choose moves correct vs realistic ranges but looked like
"mistakes" under the random-hand yardstick.

Idempotent: rows already at `analyzer_version='2.0'` are skipped unless
`--force` is passed.

Usage (run inside the backend container so eval7 is available):
    docker compose exec backend python -m scripts.backfill_decision_quality_v2
    docker compose exec backend python -m scripts.backfill_decision_quality_v2 --dry-run
    docker compose exec backend python -m scripts.backfill_decision_quality_v2 --batch 1000
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path
from typing import Iterable

# Allow `python -m scripts.backfill_decision_quality_v2` from project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from poker.decision_analyzer import DecisionAnalysis, DecisionAnalyzer


DB_PATH_DOCKER = "/app/data/poker_games.db"
DB_PATH_LOCAL = "poker_games.db"


def _resolve_db_path(override: str | None) -> str:
    if override:
        return override
    if Path(DB_PATH_DOCKER).exists():
        return DB_PATH_DOCKER
    return DB_PATH_LOCAL


# Columns we need to rebuild quality scoring. `max_winnable` is computed
# at analyze() time but not persisted — falls back to pot_total in
# _recompute_ev_call below, which matches the live analyzer's behavior.
SELECT_COLS = (
    "id, equity, equity_vs_ranges, ev_call, required_equity, "
    "pot_total, cost_to_call, player_stack, num_opponents, phase, "
    "player_position, action_taken, raise_amount, "
    "decision_quality, optimal_action, analyzer_version"
)


def _row_to_analysis(row: sqlite3.Row) -> DecisionAnalysis:
    return DecisionAnalysis(
        game_id="",  # unused by _evaluate_quality
        player_name="",
        equity=row["equity"],
        equity_vs_ranges=row["equity_vs_ranges"],
        required_equity=row["required_equity"] or 0,
        pot_total=row["pot_total"] or 0,
        cost_to_call=row["cost_to_call"] or 0,
        player_stack=row["player_stack"] or 0,
        num_opponents=row["num_opponents"] or 1,
        phase=row["phase"],
        player_position=row["player_position"],
        action_taken=row["action_taken"],
        raise_amount=row["raise_amount"],
    )


def _recompute_ev_call(a: DecisionAnalysis) -> None:
    # Mirror the ev_call block in DecisionAnalyzer.analyze() so we don't
    # re-run Monte Carlo (equity_vs_ranges is already on the row).
    if a.cost_to_call > 0 and a.pot_total > 0:
        a.required_equity = a.cost_to_call / (a.pot_total + a.cost_to_call)
        eq = DecisionAnalyzer._effective_equity(a)
        if eq is not None:
            winnable_pot = a.max_winnable if a.max_winnable is not None else a.pot_total
            winnable_pot = min(winnable_pot, a.pot_total)
            effective_call = min(a.cost_to_call, a.player_stack)
            a.ev_call = (eq * winnable_pot) - ((1 - eq) * effective_call)
    else:
        a.required_equity = 0
        a.ev_call = 0


def _iter_pending(
    conn: sqlite3.Connection,
    force: bool,
    batch: int,
) -> Iterable[list[sqlite3.Row]]:
    where = "" if force else "WHERE analyzer_version IS NULL OR analyzer_version != '2.0'"
    last_id = 0
    while True:
        cur = conn.execute(
            f"SELECT {SELECT_COLS} FROM player_decision_analysis "
            f"{where} {'AND' if where else 'WHERE'} id > ? "
            f"ORDER BY id LIMIT ?",
            (last_id, batch),
        )
        rows = cur.fetchall()
        if not rows:
            return
        yield rows
        last_id = rows[-1]["id"]


def backfill(db_path: str, dry_run: bool, force: bool, batch: int) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    analyzer = DecisionAnalyzer(iterations=1)  # iterations unused; we don't call analyze()

    total = 0
    flipped_quality = 0
    flipped_optimal = 0
    started = time.time()

    for rows in _iter_pending(conn, force=force, batch=batch):
        updates: list[tuple] = []
        for r in rows:
            a = _row_to_analysis(r)
            _recompute_ev_call(a)
            analyzer._evaluate_quality(a)

            if r["decision_quality"] != a.decision_quality:
                flipped_quality += 1
            if r["optimal_action"] != a.optimal_action:
                flipped_optimal += 1

            updates.append((
                a.ev_call,
                a.required_equity,
                a.optimal_action,
                a.decision_quality,
                a.ev_lost,
                a.quality_score,
                "2.0",
                r["id"],
            ))

        total += len(updates)
        if not dry_run:
            conn.executemany(
                "UPDATE player_decision_analysis SET "
                "ev_call = ?, required_equity = ?, optimal_action = ?, "
                "decision_quality = ?, ev_lost = ?, quality_score = ?, "
                "analyzer_version = ? "
                "WHERE id = ?",
                updates,
            )
            conn.commit()

        elapsed = time.time() - started
        print(
            f"  processed {total} rows  "
            f"(quality flipped: {flipped_quality}, optimal flipped: {flipped_optimal})  "
            f"[{elapsed:.1f}s]",
            flush=True,
        )

    return {
        "total": total,
        "flipped_quality": flipped_quality,
        "flipped_optimal": flipped_optimal,
        "elapsed_s": round(time.time() - started, 1),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", help="SQLite path (defaults to /app/data/poker_games.db inside container)")
    p.add_argument("--dry-run", action="store_true", help="Compute and report changes without writing")
    p.add_argument("--force", action="store_true", help="Re-process rows already at v2")
    p.add_argument("--batch", type=int, default=500, help="Rows per batch (default 500)")
    args = p.parse_args()

    db_path = _resolve_db_path(args.db)
    mode = "DRY RUN" if args.dry_run else "WRITING"
    print(f"[{mode}] db={db_path} force={args.force} batch={args.batch}")

    result = backfill(db_path, dry_run=args.dry_run, force=args.force, batch=args.batch)
    print()
    print("Done.")
    print(f"  rows processed       : {result['total']}")
    print(f"  decision_quality flips: {result['flipped_quality']}")
    print(f"  optimal_action flips : {result['flipped_optimal']}")
    print(f"  elapsed              : {result['elapsed_s']}s")


if __name__ == "__main__":
    main()
