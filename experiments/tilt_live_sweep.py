"""Multi-seed LIVE tilt sweep — a firm per-archetype %time-tilted anchor.

Single 1,200-hand runs of `tilt_persistence_check.json` are too noisy to trust as
a point (hothead %time swung 8–16% across three runs; see
TILT_EXCURSION_DESIGN.md). This driver runs the config across N base seeds per arm,
measures each run's per-hand %time-tilted from the persisted
`player_decision_analysis.zone_composure`, and reports mean ± sd (and range) across
seeds — the anchor `measure_zone_distribution.py`'s LOSS_MIX is calibrated to.

WHY SUBPROCESS: each arm needs its own feature-flag env (TILT_PERSISTENCE_ENABLED),
and flags resolve via env at process start. Running each (arm, seed) as a fresh
subprocess (the proven manual path) sidesteps any in-process flag caching. The base
seed is varied via `run_from_config --seed`, which reseeds the decks/seating, so
each seed is an independent sample (without it every run reuses the config's
random_seed => identical decks => zero variance).

NOTE: each run writes an experiments row + decision rows to the main DB. This is a
measurement tool — run it in a sandbox/eval DB, not against a live game DB you care
about.

Run (OFF arm, 5 seeds):
    docker compose exec -T backend python3 -m experiments.tilt_live_sweep \
        --arms off --seeds 42,142,242,342,442
Both arms:
    docker compose exec -T backend python3 -m experiments.tilt_live_sweep --arms both
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_CONFIG = 'experiments/configs/tilt_persistence_check.json'
TILT_LINE = 0.40

# Flag env per arm. Signature/erratic held OFF so we isolate persistence.
ARMS = {
    'off': {'TILT_PERSISTENCE_ENABLED': '0'},
    'on': {'TILT_PERSISTENCE_ENABLED': '1'},
}
_BASE_FLAGS = {'TILT_SIGNATURE_ENABLED': '0', 'TILT_ERRATIC_READS_ENABLED': '0'}

_EXP_ID_RE = re.compile(r'Experiment ID:\s*(\d+)')


def _db_path() -> str:
    data = PROJECT_ROOT / 'data'
    return str((data if data.exists() else PROJECT_ROOT) / 'poker_games.db')


def _run_once(config: str, seed: int, arm_env: Dict[str, str], extra: List[str]) -> int:
    """Run one experiment as a subprocess; return its experiment_id."""
    env = {**os.environ, **_BASE_FLAGS, **arm_env}
    proc = subprocess.run(
        [sys.executable, '-m', 'experiments.run_from_config', config, '--seed', str(seed), *extra],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    m = _EXP_ID_RE.search(proc.stdout)
    if not m:
        sys.stderr.write(proc.stdout[-2000:] + '\n' + proc.stderr[-2000:] + '\n')
        raise RuntimeError(f'no Experiment ID parsed (seed={seed}, rc={proc.returncode})')
    return int(m.group(1))


def _per_hand_tilt(conn: sqlite3.Connection, exp_id: int, persona: str) -> float:
    """% of distinct hands where ANY street's composure was below the tilt line."""
    gids = [
        r[0]
        for r in conn.execute(
            'SELECT game_id FROM experiment_games WHERE experiment_id=?', (exp_id,)
        )
    ]
    if not gids:
        return float('nan')
    ph = ','.join('?' * len(gids))
    rows = conn.execute(
        f'''SELECT MIN(zone_composure) mc FROM player_decision_analysis
            WHERE game_id IN ({ph}) AND player_name=? AND zone_composure IS NOT NULL
            GROUP BY game_id, hand_number''',
        gids + [persona],
    ).fetchall()
    if not rows:
        return float('nan')
    tilt = sum(1 for (mc,) in rows if mc is not None and mc < TILT_LINE)
    return 100.0 * tilt / len(rows)


def _personas(config: str) -> List[str]:
    import json

    with open(PROJECT_ROOT / config) as f:
        return list(json.load(f).get('personalities', []))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=DEFAULT_CONFIG)
    ap.add_argument(
        '--seeds',
        default='42,142,242,342,442',
        help='comma-separated base seeds (one run each, per arm)',
    )
    ap.add_argument('--arms', choices=['off', 'on', 'both'], default='off')
    ap.add_argument('--hands', type=int, help='override hands/tournament (for quick wiring checks)')
    ap.add_argument('--tournaments', type=int, help='override tournaments/run')
    args = ap.parse_args()

    extra: List[str] = []
    if args.hands:
        extra += ['--hands', str(args.hands)]
    if args.tournaments:
        extra += ['--tournaments', str(args.tournaments)]

    seeds = [int(s) for s in args.seeds.split(',') if s.strip()]
    arms = ['off', 'on'] if args.arms == 'both' else [args.arms]
    personas = _personas(args.config)
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row

    # arm -> persona -> [pct per seed]
    results: Dict[str, Dict[str, List[float]]] = {a: {p: [] for p in personas} for a in arms}

    for arm in arms:
        for seed in seeds:
            print(f'[{arm}] seed={seed} running…', flush=True)
            exp_id = _run_once(args.config, seed, ARMS[arm], extra)
            for p in personas:
                results[arm][p].append(_per_hand_tilt(conn, exp_id, p))
            print(
                f'[{arm}] seed={seed} exp_id={exp_id} '
                + '  '.join(f'{p.split()[0]}={results[arm][p][-1]:.1f}%' for p in personas),
                flush=True,
            )

    print('\n' + '=' * 84)
    print(
        f'LIVE TILT SWEEP — per-hand %time-tilted, {len(seeds)} seeds/arm '
        f'({len(seeds)} runs × {len(arms)} arm(s))'
    )
    print('=' * 84)
    print(f'  {"arm":4s} {"persona":22s} {"mean":>7s} {"sd":>6s} {"min":>6s} {"max":>6s}  per-seed')
    for arm in arms:
        for p in personas:
            xs = [x for x in results[arm][p] if x == x]  # drop NaN
            if not xs:
                continue
            mean = statistics.mean(xs)
            sd = statistics.stdev(xs) if len(xs) > 1 else 0.0
            seedstr = ' '.join(f'{x:.1f}' for x in xs)
            print(
                f'  {arm:4s} {p:22s} {mean:6.1f}% {sd:5.1f} {min(xs):5.1f} {max(xs):5.1f}  [{seedstr}]'
            )

    print('\n  Use the per-archetype MEAN as the LOSS_MIX recalibration anchor in')
    print('  measure_zone_distribution.py. sd shows the single-run noise the prior point missed.')
    print('  on-vs-off are SEPARATE trajectories (RNG desync), so the across-arm difference is NOT')
    print(
        '  a clean persistence effect — for that, use the paired harness (docs/plans/TILT_EV_HARNESS.md).'
    )


if __name__ == '__main__':
    main()
