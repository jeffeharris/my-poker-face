#!/usr/bin/env python3
"""
Generate interactive psychology trajectory viewer.

Extracts per-hand psychology data from an experiment and generates a
self-contained HTML file with:
  - 2D confidence × composure chart with zone overlays and player trails
  - Hand-by-hand slider with play/pause and keyboard controls
  - Player detail cards showing events, zone changes, and axis deltas

Usage:
    # In Docker:
    docker compose exec backend python -m experiments.generate_trajectory_viewer \
        --experiment 21 --output /app/data/trajectory_viewer.html

    # Locally:
    python -m experiments.generate_trajectory_viewer -e 21 -o viewer.html

    # Then open the HTML file in any browser.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PLAYER_COLORS = [
    '#ff6b6b',
    '#4ecdc4',
    '#ffe66d',
    '#a29bfe',
    '#00b894',
    '#fd79a8',
    '#e17055',
    '#6c5ce7',
]

# Default zone geometry (matches poker/zone_detection.py and poker/zone_config.py)
DEFAULT_SWEET_SPOTS = {
    'poker_face': {'center': [0.52, 0.72], 'label': 'Poker Face', 'radius': 0.16},
    'guarded': {'center': [0.28, 0.72], 'label': 'Guarded', 'radius': 0.15},
    'commanding': {'center': [0.78, 0.78], 'label': 'Commanding', 'radius': 0.14},
    'aggro': {'center': [0.68, 0.48], 'label': 'Aggro', 'radius': 0.12},
}

DEFAULT_PENALTY_THRESHOLDS = {
    'tilted_comp': 0.35,
    'overconfident_conf': 0.90,
    'timid_conf': 0.10,
    'shaken_conf': 0.35,
    'shaken_comp': 0.35,
    'overheated_conf': 0.65,
    'overheated_comp': 0.35,
    'detached_conf': 0.35,
    'detached_comp': 0.65,
}

# Map from zone_params keys to our threshold keys
_THRESHOLD_MAP = {
    'PENALTY_TILTED_THRESHOLD': 'tilted_comp',
    'PENALTY_OVERCONFIDENT_THRESHOLD': 'overconfident_conf',
    'PENALTY_TIMID_THRESHOLD': 'timid_conf',
    'PENALTY_SHAKEN_CONF_THRESHOLD': 'shaken_conf',
    'PENALTY_SHAKEN_COMP_THRESHOLD': 'shaken_comp',
    'PENALTY_OVERHEATED_CONF_THRESHOLD': 'overheated_conf',
    'PENALTY_OVERHEATED_COMP_THRESHOLD': 'overheated_comp',
    'PENALTY_DETACHED_CONF_THRESHOLD': 'detached_conf',
    'PENALTY_DETACHED_COMP_THRESHOLD': 'detached_comp',
}

_RADIUS_MAP = {
    'ZONE_POKER_FACE_RADIUS': ('poker_face', 'radius'),
    'ZONE_GUARDED_RADIUS': ('guarded', 'radius'),
    'ZONE_COMMANDING_RADIUS': ('commanding', 'radius'),
    'ZONE_AGGRO_RADIUS': ('aggro', 'radius'),
}


def _extract_stack_only_players(conn, game_id: str, psychology_players: list) -> dict:
    """Get stack trajectories for players without psychology data (e.g. human players).

    Returns {player_name: [{hand, stack}, ...]} for players in pda that have
    no zone_confidence entries.
    """
    if not psychology_players:
        return {}

    placeholders = ','.join('?' for _ in psychology_players)
    rows = conn.execute(
        f'''
        SELECT player_name, hand_number, player_stack
        FROM player_decision_analysis
        WHERE game_id = ?
          AND player_name NOT IN ({placeholders})
          AND player_stack IS NOT NULL
        GROUP BY player_name, hand_number
        HAVING id = MIN(id)
        ORDER BY hand_number, player_name
    ''',
        (game_id, *psychology_players),
    ).fetchall()

    stack_players = {}
    for row in rows:
        name = row['player_name']
        if name not in stack_players:
            stack_players[name] = []
        stack_players[name].append(
            {
                'hand': row['hand_number'],
                'stack': row['player_stack'] or 0,
            }
        )
    return stack_players


def extract_data(db_path: str, experiment_id: int, game_id: str = None) -> dict:
    """Extract trajectory data for visualization."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Get available games in this experiment
    games = conn.execute(
        '''
        SELECT eg.game_id,
               COUNT(DISTINCT pda.hand_number) as hands,
               GROUP_CONCAT(DISTINCT pda.player_name) as players
        FROM experiment_games eg
        JOIN player_decision_analysis pda ON pda.game_id = eg.game_id
        WHERE eg.experiment_id = ? AND pda.zone_confidence IS NOT NULL
        GROUP BY eg.game_id
        ORDER BY hands DESC
    ''',
        (experiment_id,),
    ).fetchall()

    if not games:
        return None

    selected_game = game_id or games[0]['game_id']

    # Try to read experiment zone_params overrides
    zone_params = {}
    try:
        exp_row = conn.execute(
            'SELECT config_json FROM experiments WHERE id = ?', (experiment_id,)
        ).fetchone()
        if exp_row:
            config = json.loads(exp_row['config_json'] or '{}')
            zone_params = config.get('zone_params', {})
    except Exception:
        pass

    # Build zone config with overrides
    sweet_spots = json.loads(json.dumps(DEFAULT_SWEET_SPOTS))  # deep copy
    penalty_thresholds = dict(DEFAULT_PENALTY_THRESHOLDS)

    for param_key, threshold_key in _THRESHOLD_MAP.items():
        if param_key in zone_params:
            penalty_thresholds[threshold_key] = zone_params[param_key]

    for param_key, (zone_name, field) in _RADIUS_MAP.items():
        if param_key in zone_params:
            sweet_spots[zone_name][field] = zone_params[param_key]

    # Get first decision per hand per player (earliest id wins)
    rows = conn.execute(
        '''
        SELECT pda.*
        FROM player_decision_analysis pda
        INNER JOIN (
            SELECT player_name, hand_number, MIN(id) as min_id
            FROM player_decision_analysis
            WHERE game_id = ? AND zone_confidence IS NOT NULL
            GROUP BY player_name, hand_number
        ) first ON pda.id = first.min_id
        ORDER BY pda.hand_number, pda.player_name
    ''',
        (selected_game,),
    ).fetchall()

    # Organize by player
    players = []
    trajectories = {}

    for row in rows:
        name = row['player_name']
        if name not in trajectories:
            players.append(name)
            trajectories[name] = []

        trajectories[name].append(
            {
                'hand': row['hand_number'],
                'conf': round(row['zone_confidence'], 4),
                'comp': round(row['zone_composure'], 4),
                'energy': round(row['zone_energy'] or 0.5, 4),
                'sweet_spot': row['zone_primary_sweet_spot'],
                'penalty': row['zone_primary_penalty'],
                'penalty_strength': round(row['zone_total_penalty_strength'] or 0, 4),
                'stack': row['player_stack'] or 0,
                'action': row['action_taken'],
                'intrusive': bool(row['zone_intrusive_thoughts_injected']),
                'emotion': row['display_emotion'] or 'poker_face',
            }
        )

    # Get pressure events for this game (keyed by player → hand_number → [events])
    pressure_events = {}
    try:
        pe_rows = conn.execute(
            '''
            SELECT player_name, hand_number, event_type, details_json
            FROM pressure_events
            WHERE game_id = ? AND hand_number IS NOT NULL
            ORDER BY hand_number, id
        ''',
            (selected_game,),
        ).fetchall()

        for row in pe_rows:
            name = row['player_name']
            hand = row['hand_number']
            if name not in pressure_events:
                pressure_events[name] = {}
            if hand not in pressure_events[name]:
                pressure_events[name][hand] = []

            details = json.loads(row['details_json']) if row['details_json'] else {}
            pressure_events[name][hand].append(
                {
                    'type': row['event_type'],
                    'details': details,
                }
            )
    except Exception:
        pass  # Table may not have hand_number column yet

    # Get stack-only players (human players without psychology data)
    stack_players = _extract_stack_only_players(conn, selected_game, players)

    # Assign colors and initials
    all_names = players + list(stack_players.keys())
    player_colors = {}
    player_initials = {}
    for i, name in enumerate(all_names):
        player_colors[name] = PLAYER_COLORS[i % len(PLAYER_COLORS)]
        parts = name.split()
        player_initials[name] = (
            (parts[0][0] + parts[-1][0]) if len(parts) >= 2 else name[:2].upper()
        )

    conn.close()

    return {
        'experiment_id': experiment_id,
        'game_id': selected_game,
        'games': [{'game_id': g['game_id'], 'hands': g['hands']} for g in games],
        'players': players,
        'player_colors': player_colors,
        'player_initials': player_initials,
        'trajectories': trajectories,
        'stack_players': stack_players,
        'pressure_events': pressure_events,
        'zone_config': {
            'sweet_spots': sweet_spots,
            'penalty_thresholds': penalty_thresholds,
        },
    }


def extract_data_for_game(db_path: str, game_id: str) -> dict:
    """Extract trajectory data for a single game (no experiment required).

    Like extract_data() but skips the experiment_games join and zone_params
    overrides. Used by the debug tools to visualize player-played games.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Check that we have psychology data for this game
    count = conn.execute(
        '''
        SELECT COUNT(*) as cnt
        FROM player_decision_analysis
        WHERE game_id = ? AND zone_confidence IS NOT NULL
    ''',
        (game_id,),
    ).fetchone()['cnt']

    if count == 0:
        conn.close()
        return None

    # Use default zone config (no experiment-level overrides)
    sweet_spots = json.loads(json.dumps(DEFAULT_SWEET_SPOTS))
    penalty_thresholds = dict(DEFAULT_PENALTY_THRESHOLDS)

    # Get first decision per hand per player (earliest id wins)
    rows = conn.execute(
        '''
        SELECT pda.*
        FROM player_decision_analysis pda
        INNER JOIN (
            SELECT player_name, hand_number, MIN(id) as min_id
            FROM player_decision_analysis
            WHERE game_id = ? AND zone_confidence IS NOT NULL
            GROUP BY player_name, hand_number
        ) first ON pda.id = first.min_id
        ORDER BY pda.hand_number, pda.player_name
    ''',
        (game_id,),
    ).fetchall()

    # Organize by player
    players = []
    trajectories = {}

    for row in rows:
        name = row['player_name']
        if name not in trajectories:
            players.append(name)
            trajectories[name] = []

        trajectories[name].append(
            {
                'hand': row['hand_number'],
                'conf': round(row['zone_confidence'], 4),
                'comp': round(row['zone_composure'], 4),
                'energy': round(row['zone_energy'] or 0.5, 4),
                'sweet_spot': row['zone_primary_sweet_spot'],
                'penalty': row['zone_primary_penalty'],
                'penalty_strength': round(row['zone_total_penalty_strength'] or 0, 4),
                'stack': row['player_stack'] or 0,
                'action': row['action_taken'],
                'intrusive': bool(row['zone_intrusive_thoughts_injected']),
                'emotion': row['display_emotion'] or 'poker_face',
            }
        )

    # Get pressure events for this game
    pressure_events = {}
    try:
        pe_rows = conn.execute(
            '''
            SELECT player_name, hand_number, event_type, details_json
            FROM pressure_events
            WHERE game_id = ? AND hand_number IS NOT NULL
            ORDER BY hand_number, id
        ''',
            (game_id,),
        ).fetchall()

        for row in pe_rows:
            name = row['player_name']
            hand = row['hand_number']
            if name not in pressure_events:
                pressure_events[name] = {}
            if hand not in pressure_events[name]:
                pressure_events[name][hand] = []

            details = json.loads(row['details_json']) if row['details_json'] else {}
            pressure_events[name][hand].append(
                {
                    'type': row['event_type'],
                    'details': details,
                }
            )
    except Exception:
        pass

    # Get stack-only players (human players without psychology data)
    stack_players = _extract_stack_only_players(conn, game_id, players)

    # Assign colors and initials
    all_names = players + list(stack_players.keys())
    player_colors = {}
    player_initials = {}
    for i, name in enumerate(all_names):
        player_colors[name] = PLAYER_COLORS[i % len(PLAYER_COLORS)]
        parts = name.split()
        player_initials[name] = (
            (parts[0][0] + parts[-1][0]) if len(parts) >= 2 else name[:2].upper()
        )

    conn.close()

    distinct_hands = len(set(row['hand_number'] for row in rows))

    return {
        'experiment_id': None,
        'game_id': game_id,
        'games': [{'game_id': game_id, 'hands': distinct_hands}],
        'players': players,
        'player_colors': player_colors,
        'player_initials': player_initials,
        'trajectories': trajectories,
        'stack_players': stack_players,
        'pressure_events': pressure_events,
        'zone_config': {
            'sweet_spots': sweet_spots,
            'penalty_thresholds': penalty_thresholds,
        },
    }


def _load_html_template() -> str:
    """Load the HTML template from the templates directory."""
    template_path = Path(__file__).parent / "templates" / "trajectory_viewer.html"
    return template_path.read_text()


def generate_html(data: dict) -> str:
    """Generate self-contained HTML viewer with embedded data."""
    data_json = json.dumps(data, separators=(',', ':'))
    return _load_html_template().replace('__DATA_JSON__', data_json)


def main():
    parser = argparse.ArgumentParser(
        description="Generate interactive psychology trajectory viewer"
    )
    parser.add_argument("--experiment", "-e", type=int, required=True, help="Experiment ID")
    parser.add_argument(
        "--output", "-o", default="trajectory_viewer.html", help="Output HTML file path"
    )
    parser.add_argument(
        "--game", "-g", default=None, help="Specific game_id (default: longest game)"
    )
    parser.add_argument("--db", default=None, help="Database path (auto-detected if omitted)")

    args = parser.parse_args()

    # Find database
    if args.db:
        db_path = args.db
    else:
        project_root = Path(__file__).parent.parent
        candidates = [
            project_root / "data" / "poker_games.db",
            project_root / "poker_games.db",
            Path("/app/data/poker_games.db"),
        ]
        db_path = None
        for p in candidates:
            if p.exists():
                db_path = str(p)
                break
        if not db_path:
            print("Error: Could not find database. Use --db to specify path.")
            sys.exit(1)

    print(f"Using database: {db_path}")
    data = extract_data(db_path, args.experiment, args.game)

    if not data:
        print(f"No trajectory data found for experiment {args.experiment}")
        sys.exit(1)

    print(f"Found {len(data['players'])} players:")
    for player in data['players']:
        traj = data['trajectories'][player]
        print(f"  {player}: {len(traj)} hands")

    html = generate_html(data)
    output_path = Path(args.output)
    output_path.write_text(html)
    print(f"\nGenerated: {output_path.resolve()}")
    print("Open in browser to explore trajectories.")


if __name__ == "__main__":
    main()
