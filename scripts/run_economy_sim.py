"""Run the cash-mode economy simulator and write CSV + JSONL output.

Wraps `cash_mode.sim_runner.run_sim` in a CLI. Writes three files
alongside the chosen `--out` path:
  * `<out>.csv`        — per-tick aggregate metrics, flat schema
  * `<out>.pids.jsonl` — per-personality bankroll trajectory
  * `<out>.summary.json` — config + headline stats

Usage:
    # Seed a fresh sandbox, then drive it.
    sandbox=$(python3 scripts/seed_sim_sandbox.py --name "baseline")
    python3 scripts/run_economy_sim.py \\
        --sandbox-id "$sandbox" \\
        --ticks 1000 \\
        --out sim-output/baseline

    # Inside Docker
    docker compose exec backend python -m scripts.run_economy_sim \\
        --sandbox-id <uuid> --ticks 500 --out /app/data/sim/run1
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add project root to path when run as script.
_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from cash_mode.full_sim import DEFAULT_HAND_SIM_PROB
from cash_mode.movement import DEFAULT_LIVE_FILL_PROB
from cash_mode.sim_runner import (
    SimConfig,
    flatten_for_csv,
    per_pid_jsonl_records,
    run_sim,
)
from poker.repositories import create_repos

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def _get_db_path(db_path: Optional[str] = None) -> str:
    if db_path:
        return db_path
    if Path('/app/data').exists():
        return '/app/data/poker_games.db'
    return str(Path(__file__).parent.parent / 'data' / 'poker_games.db')


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _write_csv(path: Path, rows: list) -> None:
    if not rows:
        path.write_text('')
        return
    # The union of keys across all rows guarantees a consistent header
    # row, even though `flatten_for_csv` already does this.
    keys = list(rows[0].keys())
    with path.open('w', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def _write_jsonl(path: Path, records: list) -> None:
    with path.open('w') as fh:
        for r in records:
            fh.write(json.dumps(r))
            fh.write('\n')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sandbox-id', required=True, help='Sandbox UUID')
    parser.add_argument('--ticks', type=int, required=True, help='Number of refresh cycles')
    parser.add_argument('--tick-seconds', type=int, default=8,
                        help='Simulated seconds per tick (default: %(default)s)')
    parser.add_argument('--start-at', default=None,
                        help='ISO-8601 timestamp for tick 0 (default: utcnow())')
    parser.add_argument('--rng-seed', type=int, default=0,
                        help='Seed for deterministic randomness (default: %(default)s)')
    parser.add_argument('--metrics-every', type=int, default=1,
                        help='Capture metrics every N ticks (default: %(default)s)')
    parser.add_argument('--audit-every', type=int, default=50,
                        help='Run full audit every N ticks (default: %(default)s)')
    parser.add_argument('--hand-sim-prob', type=float, default=DEFAULT_HAND_SIM_PROB,
                        help='Probability of hand simulation per refresh (default: %(default)s)')
    parser.add_argument('--live-fill-prob', type=float, default=DEFAULT_LIVE_FILL_PROB,
                        help='Probability of live-fill per open seat (default: %(default)s)')
    parser.add_argument('--progress-every', type=int, default=100,
                        help='Log progress every N ticks (0 disables, default: %(default)s)')
    parser.add_argument('--initial-bank-pool-seed', type=int, default=0,
                        help='Closed-economy: seed the bank pool at sim start '
                             'so tourist injection / casino spawn can fire '
                             'before vice deposits land (default: %(default)s)')
    parser.add_argument('--out', required=True,
                        help='Output path prefix (writes <prefix>.csv, '
                             '<prefix>.pids.jsonl, <prefix>.summary.json)')
    parser.add_argument('--db-path', default=None,
                        help='Override DB path. Defaults to '
                             '/app/data/poker_games.db (Docker) or '
                             'data/poker_games.db (local).')
    args = parser.parse_args()

    db_path = _get_db_path(args.db_path)
    logger.info("Using db: %s", db_path)
    repos = create_repos(db_path)

    config = SimConfig(
        sandbox_id=args.sandbox_id,
        num_ticks=args.ticks,
        tick_seconds=args.tick_seconds,
        start_at=_parse_iso(args.start_at),
        rng_seed=args.rng_seed,
        metrics_every=args.metrics_every,
        audit_every=args.audit_every,
        hand_sim_prob=args.hand_sim_prob,
        live_fill_prob=args.live_fill_prob,
        progress_every=args.progress_every,
        initial_bank_pool_seed=args.initial_bank_pool_seed,
    )

    logger.info(
        "Starting sim: sandbox=%s ticks=%d seed=%d hand_sim_prob=%.2f",
        args.sandbox_id, args.ticks, args.rng_seed, args.hand_sim_prob,
    )
    result = run_sim(config, repos=repos)

    # Materialize outputs.
    out_prefix = Path(args.out)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    csv_path = out_prefix.with_suffix('.csv')
    jsonl_path = out_prefix.parent / (out_prefix.name + '.pids.jsonl')
    summary_path = out_prefix.with_suffix('.summary.json')

    _write_csv(csv_path, flatten_for_csv(result.metrics))
    _write_jsonl(jsonl_path, per_pid_jsonl_records(result.metrics))
    summary_payload = {
        'config': {
            'sandbox_id': config.sandbox_id,
            'num_ticks': config.num_ticks,
            'tick_seconds': config.tick_seconds,
            'rng_seed': config.rng_seed,
            'metrics_every': config.metrics_every,
            'audit_every': config.audit_every,
            'hand_sim_prob': config.hand_sim_prob,
            'live_fill_prob': config.live_fill_prob,
        },
        'final_now': result.final_now,
        'summary': result.summary,
    }
    summary_path.write_text(json.dumps(summary_payload, indent=2))

    logger.info(
        "Done in %.1fs — wrote %s, %s, %s",
        result.wall_seconds, csv_path, jsonl_path, summary_path,
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
