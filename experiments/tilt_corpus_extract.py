"""Extract a REAL tilted-decision-spot corpus from a psychology-on sim
(docs/plans/TILT_EV_HARNESS.md, approach C part (a)). This is the build step the
signature + erratic-reads EV probes both depend on for a trustworthy bb/100: it
turns the hand-authored synthetic spots into the spots the bot ACTUALLY reaches
while tilted.

SOURCE: `experiments/configs/tilt_persistence_check.json` run with
`TILT_PERSISTENCE_ENABLED=1` (signature/erratic OFF, so the recorded spots are the
natural trajectory — the probes apply off/on to each spot themselves). The tiered
bot already persists, per decision, a `strategy_pipeline_snapshot_json` containing
`base_strategy_probs` (the PRE-emotion baseline), `emotional_state`, `anchors`,
`legal_actions`, `deviation_profile_name`, and the geometry (`pot_total`,
`cost_to_call`, `player_bet`, `effective_stack_bb`, `big_blind`). Combined with the
row's `player_hand` / `community_cards` / `num_opponents`, that is a complete,
self-contained probe spot — no new capture mechanism needed.

A spot is "tilted" iff its recorded `emotional_state.state` is one the signature /
erratic-reads act on (tilted / overconfident / shaken / dissociated) — that is the
exact gate those features read, so it is the right population. We also record
`zone_composure` for cross-reference with the < 0.40 tilt line.

OUTPUT: a JSONL corpus (one spot per line) + a summary with the TILTED-DECISION
RATE (tilted spots / all decisions) — the per-100-hands multiplier the probes turn
ΔEV-per-spot into bb/100 with — broken down per persona and per state.

Run:
    docker compose exec -T backend python3 -m experiments.tilt_corpus_extract \
        [experiment_id] [--out experiments/data/tilt_corpus.jsonl]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
TILT_STATES = {'tilted', 'overconfident', 'shaken', 'dissociated'}
TILT_LINE = 0.40


def _db_path() -> str:
    data = PROJECT_ROOT / 'data'
    return str((data if data.exists() else PROJECT_ROOT) / 'poker_games.db')


def _latest_experiment(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT id FROM experiments WHERE name LIKE 'exp_tilt_persistence_check%' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        row = conn.execute("SELECT MAX(id) FROM experiments").fetchone()
    return int(row[0])


def _parse_cards(raw) -> list:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def extract(experiment_id: int, out_path: Path) -> dict:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    gids = [
        r[0]
        for r in conn.execute(
            "SELECT game_id FROM experiment_games WHERE experiment_id=?", (experiment_id,)
        )
    ]
    if not gids:
        raise SystemExit(f"no games for experiment {experiment_id}")
    ph = ",".join("?" * len(gids))
    rows = conn.execute(
        f"""SELECT player_name, hand_number, phase, player_hand, community_cards, pot_total,
            cost_to_call, player_stack, num_opponents, zone_composure, action_taken,
            strategy_pipeline_snapshot_json
            FROM player_decision_analysis
            WHERE game_id IN ({ph}) AND strategy_pipeline_snapshot_json IS NOT NULL""",
        gids,
    ).fetchall()

    num_hands = conn.execute(
        f"SELECT COUNT(*) FROM (SELECT DISTINCT game_id, hand_number "
        f"FROM player_decision_analysis WHERE game_id IN ({ph}))",
        gids,
    ).fetchone()[0]

    total = len(rows)
    spots = []
    per_state = Counter()
    per_persona_tilted = Counter()
    per_persona_total = Counter()
    for r in rows:
        per_persona_total[r['player_name']] += 1
        snap = json.loads(r['strategy_pipeline_snapshot_json'])
        es = snap.get('emotional_state') or {}
        state = es.get('state', 'composed')
        if state not in TILT_STATES:
            continue
        base = snap.get('base_strategy_probs')
        anchors = snap.get('anchors')
        if not base or not anchors:
            continue
        per_state[state] += 1
        per_persona_tilted[r['player_name']] += 1
        spots.append(
            {
                'persona': r['player_name'],
                'phase': r['phase'],
                'hand_number': r['hand_number'],
                'hero': _parse_cards(r['player_hand']),
                'board': _parse_cards(r['community_cards']),
                'pot_total': r['pot_total'],
                'cost_to_call': r['cost_to_call'],
                'player_stack': r['player_stack'],
                'player_bet': snap.get('player_bet', 0),
                'big_blind': snap.get('big_blind') or 100,
                'effective_stack_bb': snap.get('effective_stack_bb'),
                'num_opponents': r['num_opponents'],
                'zone_composure': r['zone_composure'],
                'base_strategy_probs': base,
                'legal_actions': snap.get('legal_actions') or list(base.keys()),
                'emotional_state': {
                    'state': state,
                    'severity': es.get('severity', 'moderate'),
                    'intensity': float(es.get('intensity', 0.5) or 0.5),
                },
                'anchors': anchors,
                'deviation_profile_name': snap.get('deviation_profile_name', 'tag'),
                'opponent_archetype': snap.get('opponent_archetype'),
                'resolved_action': snap.get('resolved_action'),
            }
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w') as f:
        for s in spots:
            f.write(json.dumps(s) + '\n')

    meta = {
        'experiment_id': experiment_id,
        'num_hands': num_hands,
        'decisions_total': total,
        'tilted_spots': len(spots),
        'tilted_decision_rate_pct': 100.0 * len(spots) / total if total else 0.0,
        'per_state': dict(per_state),
        'per_persona_tilted': dict(per_persona_tilted),
        'per_persona_total': dict(per_persona_total),
    }
    with open(out_path.with_suffix('.meta.json'), 'w') as f:
        json.dump(meta, f, indent=2)

    rate = 100.0 * len(spots) / total if total else 0.0
    print('=' * 88)
    print(f'TILT CORPUS — experiment {experiment_id}')
    print('=' * 88)
    print(f'  hands played             : {num_hands}')
    print(f'  decisions total          : {total}')
    print(f'  tilted-decision spots    : {len(spots)}')
    print(
        f'  TILTED-DECISION RATE     : {rate:.2f}%  (= spots / all decisions; the bb/100 multiplier)'
    )
    print(f'  corpus written           : {out_path}')
    print('\n  by state:')
    for st, n in per_state.most_common():
        print(f'    {st:14s} {n:5d}  ({100.0 * n / total:.2f}% of decisions)')
    print('\n  by persona (tilted / total = rate):')
    for p in sorted(per_persona_total):
        t = per_persona_tilted.get(p, 0)
        tot = per_persona_total[p]
        pr = 100.0 * t / tot if tot else 0.0
        print(f'    {p:22s} {t:5d} / {tot:5d}  = {pr:5.1f}%')
    # phase mix of tilted spots
    phase_mix = Counter(s['phase'] for s in spots)
    print('\n  tilted-spot phase mix:')
    for ph_, n in phase_mix.most_common():
        print(f'    {str(ph_):10s} {n:5d}  ({100.0 * n / len(spots):.1f}%)' if spots else '')
    return {'total': total, 'tilted': len(spots), 'rate': rate, 'out': str(out_path)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('experiment_id', nargs='?', type=int, default=None)
    ap.add_argument('--out', default='experiments/data/tilt_corpus.jsonl')
    args = ap.parse_args()
    conn = sqlite3.connect(_db_path())
    exp = args.experiment_id if args.experiment_id is not None else _latest_experiment(conn)
    extract(exp, PROJECT_ROOT / args.out)


if __name__ == '__main__':
    main()
