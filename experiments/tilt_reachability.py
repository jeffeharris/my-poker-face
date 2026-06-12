"""Per-experiment believability + reachability report (EXP_009).

One screen that answers, for a psychology-on sim run: is tilt BELIEVABLE (PRD
penalty-band distribution + per-persona penalty-time / chronicity) and is the
dramatic deep-tilt state NOTICEABLE / reachable (state mix, `shaken` count, and the
confidence×composure joint at deep penalty — the corner `shaken` needs)?

It is the measurement harness for both Phase A (baseline) and every Phase B tuning
arm: run an arm, then `tilt_reachability.py <experiment_id>` to score it against the
H1 thresholds.

Run: docker compose exec -T backend python3 -m experiments.tilt_reachability [exp_id]
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
TILT_STATES = {'tilted', 'overconfident', 'shaken', 'dissociated'}
# PRD bands (PSYCHOLOGY_DESIGN.md): baseline<0.10, medium<0.50, high<0.75, full>=0.75
PRD = {'baseline': '70-85%', 'medium': '10-20%', 'high': '2-7%', 'full_tilt': '0-2% (Mod <=5)'}


def _db() -> sqlite3.Connection:
    data = PROJECT_ROOT / 'data'
    c = sqlite3.connect(str((data if data.exists() else PROJECT_ROOT) / 'poker_games.db'))
    c.row_factory = sqlite3.Row
    return c


def _band(p: float) -> str:
    if p >= 0.75:
        return 'full_tilt'
    if p >= 0.50:
        return 'high'
    if p >= 0.10:
        return 'medium'
    return 'baseline'


def _latest(c: sqlite3.Connection) -> int:
    r = c.execute(
        "SELECT id FROM experiments WHERE name LIKE 'exp_tilt%' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return int(r[0]) if r else int(c.execute("SELECT MAX(id) FROM experiments").fetchone()[0])


def main() -> None:
    c = _db()
    exp = int(sys.argv[1]) if len(sys.argv) > 1 else _latest(c)
    gids = [
        r[0]
        for r in c.execute("SELECT game_id FROM experiment_games WHERE experiment_id=?", (exp,))
    ]
    if not gids:
        raise SystemExit(f"no games for experiment {exp}")
    ph = ",".join("?" * len(gids))
    rows = c.execute(
        f"""SELECT player_name, zone_composure comp, zone_confidence conf,
            zone_total_penalty_strength p, strategy_pipeline_snapshot_json snap
            FROM player_decision_analysis WHERE game_id IN ({ph})""",
        gids,
    ).fetchall()
    tot = len(rows)

    # --- state mix (from snapshot emotional_state) ---
    import json

    states = Counter()
    per_persona_state = defaultdict(Counter)
    for r in rows:
        st = 'composed'
        if r['snap']:
            try:
                st = (json.loads(r['snap']).get('emotional_state') or {}).get('state', 'composed')
            except (json.JSONDecodeError, TypeError):
                pass
        states[st] += 1
        per_persona_state[r['player_name']][st] += 1

    print('=' * 84)
    print(f'TILT REACHABILITY — experiment {exp}  ({tot} decisions)')
    print('=' * 84)
    print('  state mix:', dict(states.most_common()))
    print(
        f"  SHAKEN decisions: {states.get('shaken', 0)}  "
        f"({100.0 * states.get('shaken', 0) / tot:.2f}%)"
    )

    # --- PRD penalty-band distribution ---
    band = Counter(_band(r['p']) for r in rows if r['p'] is not None)
    pen_tot = sum(band.values())
    print('\n  PRD penalty-band distribution:')
    print(f'    {"band":10s} {"pct":>7s}   target')
    for b in ['baseline', 'medium', 'high', 'full_tilt']:
        print(f'    {b:10s} {100.0 * band.get(b, 0) / pen_tot:6.1f}%   {PRD[b]}')

    # --- per-persona penalty-time + shaken rate (chronicity / character) ---
    print('\n  per-persona: penalty-time, full-tilt%, shaken% (of that persona):')
    print(f'    {"persona":22s} {"pen%":>6s} {"full%":>6s} {"shaken%":>8s}')
    for p in sorted(per_persona_state):
        prows = [r for r in rows if r['player_name'] == p]
        n = len(prows)
        pens = [_band(r['p']) for r in prows if r['p'] is not None]
        pen = sum(1 for b in pens if b != 'baseline')
        full = sum(1 for b in pens if b == 'full_tilt')
        sh = per_persona_state[p].get('shaken', 0)
        print(f'    {p:22s} {100.0*pen/n:5.1f}% {100.0*full/n:5.1f}% {100.0*sh/n:7.2f}%')

    # --- confidence×composure at deep penalty (why shaken fires or not) ---
    deep = [
        r
        for r in rows
        if r['p'] is not None and r['p'] >= 0.50 and r['comp'] is not None and r['conf'] is not None
    ]
    if deep:
        confs = sorted(r['conf'] for r in deep)
        comps = sorted(r['comp'] for r in deep)
        n_corner = sum(1 for r in deep if r['conf'] < 0.35 and r['comp'] < 0.35)
        print(f'\n  deep-penalty (>=0.50) joint, n={len(deep)}:')
        print(f'    composure : min={comps[0]:.2f} median={comps[len(comps)//2]:.2f}')
        print(
            f'    confidence: min={confs[0]:.2f} median={confs[len(confs)//2]:.2f}  '
            f'(shaken corner needs <0.35)'
        )
        print(
            f'    spots in shaken corner (both<0.35): {n_corner}  '
            f'<- this is what makes shaken reachable'
        )


if __name__ == '__main__':
    main()
