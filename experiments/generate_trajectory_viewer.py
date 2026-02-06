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
    '#ff6b6b', '#4ecdc4', '#ffe66d', '#a29bfe',
    '#00b894', '#fd79a8', '#e17055', '#6c5ce7',
]

# Default zone geometry (matches poker/zone_detection.py and poker/zone_config.py)
DEFAULT_SWEET_SPOTS = {
    'poker_face': {'center': [0.52, 0.72], 'label': 'Poker Face', 'radius': 0.16},
    'guarded':    {'center': [0.28, 0.72], 'label': 'Guarded',    'radius': 0.15},
    'commanding': {'center': [0.78, 0.78], 'label': 'Commanding', 'radius': 0.14},
    'aggro':      {'center': [0.68, 0.48], 'label': 'Aggro',      'radius': 0.12},
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


def extract_data(db_path: str, experiment_id: int, game_id: str = None) -> dict:
    """Extract trajectory data for visualization."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Get available games in this experiment
    games = conn.execute('''
        SELECT eg.game_id,
               COUNT(DISTINCT pda.hand_number) as hands,
               GROUP_CONCAT(DISTINCT pda.player_name) as players
        FROM experiment_games eg
        JOIN player_decision_analysis pda ON pda.game_id = eg.game_id
        WHERE eg.experiment_id = ? AND pda.zone_confidence IS NOT NULL
        GROUP BY eg.game_id
        ORDER BY hands DESC
    ''', (experiment_id,)).fetchall()

    if not games:
        return None

    selected_game = game_id or games[0]['game_id']

    # Try to read experiment zone_params overrides
    zone_params = {}
    try:
        exp_row = conn.execute(
            'SELECT config_json FROM experiments WHERE id = ?',
            (experiment_id,)
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
    rows = conn.execute('''
        SELECT pda.*
        FROM player_decision_analysis pda
        INNER JOIN (
            SELECT player_name, hand_number, MIN(id) as min_id
            FROM player_decision_analysis
            WHERE game_id = ? AND zone_confidence IS NOT NULL
            GROUP BY player_name, hand_number
        ) first ON pda.id = first.min_id
        ORDER BY pda.hand_number, pda.player_name
    ''', (selected_game,)).fetchall()

    # Organize by player
    players = []
    trajectories = {}

    for row in rows:
        name = row['player_name']
        if name not in trajectories:
            players.append(name)
            trajectories[name] = []

        trajectories[name].append({
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
        })

    # Get pressure events for this game (keyed by player → hand_number → [events])
    pressure_events = {}
    try:
        pe_rows = conn.execute('''
            SELECT player_name, hand_number, event_type, details_json
            FROM pressure_events
            WHERE game_id = ? AND hand_number IS NOT NULL
            ORDER BY hand_number, id
        ''', (selected_game,)).fetchall()

        for row in pe_rows:
            name = row['player_name']
            hand = row['hand_number']
            if name not in pressure_events:
                pressure_events[name] = {}
            if hand not in pressure_events[name]:
                pressure_events[name][hand] = []

            details = json.loads(row['details_json']) if row['details_json'] else {}
            pressure_events[name][hand].append({
                'type': row['event_type'],
                'details': details,
            })
    except Exception:
        pass  # Table may not have hand_number column yet

    # Assign colors and initials
    player_colors = {}
    player_initials = {}
    for i, name in enumerate(players):
        player_colors[name] = PLAYER_COLORS[i % len(PLAYER_COLORS)]
        parts = name.split()
        player_initials[name] = (parts[0][0] + parts[-1][0]) if len(parts) >= 2 else name[:2].upper()

    conn.close()

    return {
        'experiment_id': experiment_id,
        'game_id': selected_game,
        'games': [{'game_id': g['game_id'], 'hands': g['hands']} for g in games],
        'players': players,
        'player_colors': player_colors,
        'player_initials': player_initials,
        'trajectories': trajectories,
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
    count = conn.execute('''
        SELECT COUNT(*) as cnt
        FROM player_decision_analysis
        WHERE game_id = ? AND zone_confidence IS NOT NULL
    ''', (game_id,)).fetchone()['cnt']

    if count == 0:
        conn.close()
        return None

    # Use default zone config (no experiment-level overrides)
    sweet_spots = json.loads(json.dumps(DEFAULT_SWEET_SPOTS))
    penalty_thresholds = dict(DEFAULT_PENALTY_THRESHOLDS)

    # Get first decision per hand per player (earliest id wins)
    rows = conn.execute('''
        SELECT pda.*
        FROM player_decision_analysis pda
        INNER JOIN (
            SELECT player_name, hand_number, MIN(id) as min_id
            FROM player_decision_analysis
            WHERE game_id = ? AND zone_confidence IS NOT NULL
            GROUP BY player_name, hand_number
        ) first ON pda.id = first.min_id
        ORDER BY pda.hand_number, pda.player_name
    ''', (game_id,)).fetchall()

    # Organize by player
    players = []
    trajectories = {}

    for row in rows:
        name = row['player_name']
        if name not in trajectories:
            players.append(name)
            trajectories[name] = []

        trajectories[name].append({
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
        })

    # Get pressure events for this game
    pressure_events = {}
    try:
        pe_rows = conn.execute('''
            SELECT player_name, hand_number, event_type, details_json
            FROM pressure_events
            WHERE game_id = ? AND hand_number IS NOT NULL
            ORDER BY hand_number, id
        ''', (game_id,)).fetchall()

        for row in pe_rows:
            name = row['player_name']
            hand = row['hand_number']
            if name not in pressure_events:
                pressure_events[name] = {}
            if hand not in pressure_events[name]:
                pressure_events[name][hand] = []

            details = json.loads(row['details_json']) if row['details_json'] else {}
            pressure_events[name][hand].append({
                'type': row['event_type'],
                'details': details,
            })
    except Exception:
        pass

    # Assign colors and initials
    player_colors = {}
    player_initials = {}
    for i, name in enumerate(players):
        player_colors[name] = PLAYER_COLORS[i % len(PLAYER_COLORS)]
        parts = name.split()
        player_initials[name] = (parts[0][0] + parts[-1][0]) if len(parts) >= 2 else name[:2].upper()

    conn.close()

    return {
        'experiment_id': None,
        'game_id': game_id,
        'games': [{'game_id': game_id, 'hands': len(rows)}],
        'players': players,
        'player_colors': player_colors,
        'player_initials': player_initials,
        'trajectories': trajectories,
        'pressure_events': pressure_events,
        'zone_config': {
            'sweet_spots': sweet_spots,
            'penalty_thresholds': penalty_thresholds,
        },
    }


def generate_html(data: dict) -> str:
    """Generate self-contained HTML viewer with embedded data."""
    data_json = json.dumps(data, separators=(',', ':'))
    return HTML_TEMPLATE.replace('__DATA_JSON__', data_json)


HTML_TEMPLATE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Psychology Trajectory Viewer</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#e6e6e6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;overflow-x:hidden}
.container{max-width:1400px;margin:0 auto;padding:16px 20px}
header{text-align:center;margin-bottom:12px}
h1{font-size:1.3rem;font-weight:500;color:#fff;letter-spacing:-0.3px}
.meta{color:#666;font-size:0.8rem;margin-top:2px}
.game-selector{margin-top:6px}
.game-selector select{background:#161b22;color:#e6e6e6;border:1px solid #30363d;padding:4px 8px;border-radius:4px;font-size:0.8rem}

.main-layout{display:flex;gap:16px;align-items:flex-start}
.left-panel{flex:1;min-width:280px;overflow-y:auto;max-height:calc(100vh - 140px)}
.right-panel{flex:2;position:sticky;top:16px}

.chart-section{display:flex;justify-content:center}
canvas{border-radius:8px;cursor:crosshair}

.controls{display:flex;align-items:center;gap:10px;padding:10px 14px;background:#161b22;border-radius:8px;margin-top:12px;margin-bottom:12px;border:1px solid #21262d;flex-wrap:wrap}
.controls-row{display:flex;align-items:center;gap:10px;flex:1;min-width:0}
.control-btn{background:#21262d;border:1px solid #30363d;color:#e6e6e6;width:32px;height:32px;border-radius:6px;cursor:pointer;font-size:12px;display:flex;align-items:center;justify-content:center;transition:background .15s;flex-shrink:0}
.control-btn:hover{background:#30363d}
.control-btn:active{background:#3d444d}
input[type="range"]{flex:1;-webkit-appearance:none;height:6px;background:#30363d;border-radius:3px;outline:none;min-width:100px}
input[type="range"]::-webkit-slider-thumb{-webkit-appearance:none;width:16px;height:16px;border-radius:50%;background:#58a6ff;cursor:pointer;transition:transform .1s}
input[type="range"]::-webkit-slider-thumb:hover{transform:scale(1.2)}
input[type="range"]::-moz-range-thumb{width:16px;height:16px;border-radius:50%;background:#58a6ff;cursor:pointer;border:none}
.hand-info{font-size:0.85rem;white-space:nowrap;min-width:110px;text-align:right;font-variant-numeric:tabular-nums}
.kbd-hint{color:#444;font-size:0.7rem}

.overlay-toggles{display:flex;align-items:center;gap:6px}
.overlay-toggle{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:12px;border:1px solid #30363d;font-size:0.72rem;cursor:pointer;color:#666;transition:all 0.15s;user-select:none;background:transparent}
.overlay-toggle:hover{border-color:#444}
.overlay-toggle.active{color:var(--toggle-color);border-color:var(--toggle-color);background:var(--toggle-bg)}
.overlay-dot{width:6px;height:6px;border-radius:50%;background:var(--toggle-color);opacity:0.5}
.overlay-toggle.active .overlay-dot{opacity:1}

.player-grid{display:flex;flex-direction:column;gap:6px}
.player-card{display:flex;flex-direction:column;gap:4px;padding:8px 10px;background:#161b22;border-radius:6px;border-left:3px solid transparent;border:1px solid #21262d;cursor:pointer;transition:background .15s,border-color .15s}
.player-card:hover{background:#1c2129}
.player-card.highlighted{border-color:#58a6ff !important;background:#1c2433}
.player-card.dimmed{opacity:0.35}

.player-header-row{display:flex;align-items:center;gap:8px;width:100%}
.player-left{display:flex;align-items:center;gap:8px;flex:1;min-width:0}
.avatar-wrap{position:relative;flex-shrink:0}
.avatar{width:26px;height:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:10px;color:#000;flex-shrink:0}
.avatar-emotion{position:absolute;bottom:-2px;right:-4px;font-size:12px;line-height:1;filter:drop-shadow(0 0 2px rgba(0,0,0,0.8));display:none}
.player-name-wrap{display:flex;align-items:baseline;gap:6px;min-width:0}
.player-name{font-weight:500;font-size:0.82rem;white-space:nowrap}
.player-emotion-text{font-size:0.66rem;display:none}
.card-summary{display:flex;flex-wrap:wrap;gap:3px;flex:1;min-width:0;justify-content:flex-end}
.expand-btn{background:none;border:none;color:#555;font-size:11px;cursor:pointer;padding:2px 4px;transition:color .15s,transform .15s;flex-shrink:0}
.expand-btn:hover{color:#999}
.player-card.expanded .expand-btn{transform:rotate(90deg)}

.card-details{max-height:0;overflow:hidden;transition:max-height .25s ease}
.player-card.expanded .card-details{max-height:500px}

.player-middle{min-width:0;overflow:hidden}
.events{min-height:16px}
.event-badge{padding:2px 7px;border-radius:3px;font-size:0.72rem;font-weight:500;white-space:nowrap}
.event-badge.win{background:rgba(0,184,148,0.13);color:#00b894}
.event-badge.loss{background:rgba(255,107,107,0.13);color:#ff6b6b}
.event-badge.zone{background:rgba(88,166,255,0.13);color:#58a6ff}
.event-badge.penalty{background:rgba(255,107,107,0.18);color:#ff6b6b;border:1px solid rgba(255,107,107,0.25)}
.event-badge.neutral{background:rgba(150,150,150,0.1);color:#888}
.event-badge.thoughts{background:rgba(255,230,109,0.15);color:#ffe66d}

.force-table{width:100%;font-size:0.72rem;font-variant-numeric:tabular-nums;border-collapse:collapse}
.force-table td{padding:1px 4px;white-space:nowrap}
.force-table .force-label{color:#888;text-align:left;width:100px}
.force-table .force-val{text-align:right;width:72px;font-family:monospace}
.force-table .force-net{border-top:1px solid #30363d}
.force-table .force-net .force-label{color:#ccc;font-weight:600}
.force-table .force-pos{color:#00b894}
.force-table .force-neg{color:#ff6b6b}
.force-table .force-zero{color:#444}
.force-badges{display:flex;flex-wrap:wrap;gap:4px;margin-top:3px}

.player-right{display:flex;flex-direction:column;gap:2px;width:100%}
.axis-row{display:flex;align-items:center;gap:4px;font-size:0.72rem}
.axis-label{width:18px;color:#666;text-align:right;font-size:0.66rem}
.axis-bar{flex:1;height:4px;background:#21262d;border-radius:2px;overflow:hidden;min-width:40px}
.axis-fill{height:100%;border-radius:2px;transition:width .3s ease}
.axis-value{width:30px;text-align:right;font-variant-numeric:tabular-nums;color:#ccc;font-size:0.7rem}
.axis-delta{width:42px;text-align:right;font-variant-numeric:tabular-nums;font-size:0.66rem}
.positive{color:#00b894}
.negative{color:#ff6b6b}
.neutral-delta{color:#444}

.affinity-section{margin-top:3px;padding-top:3px;border-top:1px solid #21262d}
.affinity-row{display:flex;align-items:center;gap:4px;height:12px;font-size:0.66rem}
.affinity-label{width:18px;color:#666;text-align:right;font-weight:500;font-variant-numeric:tabular-nums}
.affinity-bar-bg{flex:1;height:3px;background:#21262d;border-radius:2px;overflow:hidden;min-width:30px}
.affinity-bar-fill{height:100%;border-radius:2px;transition:width .3s ease}
.affinity-pct{width:26px;text-align:right;color:#888;font-variant-numeric:tabular-nums}
.affinity-active .affinity-label{color:#FFD700}
.affinity-active .affinity-pct{color:#FFD700}

@media(max-width:900px){
  .main-layout{flex-direction:column}
  .left-panel{max-height:none;overflow-y:visible}
  .right-panel{position:static;width:100%}
}
@media(max-width:800px){
  .player-left{min-width:120px}
  .player-right{min-width:160px}
  .player-card{flex-wrap:wrap}
}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>Psychology Trajectory Viewer</h1>
    <div class="meta" id="meta"></div>
    <div class="game-selector" id="game-selector-wrap" style="display:none">
      <select id="game-selector"></select>
    </div>
  </header>

  <div class="main-layout">
    <div class="left-panel">
      <div class="player-grid" id="player-grid"></div>
    </div>
    <div class="right-panel">
      <div class="chart-section">
        <canvas id="chart"></canvas>
      </div>
    </div>
  </div>

  <div class="controls">
    <div class="controls-row">
      <button class="control-btn" id="prev-btn" title="Previous hand (←)">&#9664;</button>
      <button class="control-btn" id="play-btn" title="Play / Pause (Space)">&#9654;</button>
      <button class="control-btn" id="next-btn" title="Next hand (→)">&#9654;</button>
      <input type="range" id="slider" min="0" max="0" value="0">
      <div class="hand-info" id="hand-info">Hand 1 / 1</div>
      <span class="kbd-hint">← → Space</span>
    </div>
    <div class="overlay-toggles">
      <span class="overlay-toggle active" id="toggle-zones" style="--toggle-color:#90EE90;--toggle-bg:rgba(144,238,144,0.08)" title="Sweet spot zones">
        <span class="overlay-dot"></span>Zones
      </span>
      <span class="overlay-toggle active" id="toggle-penalties" style="--toggle-color:#ff6b6b;--toggle-bg:rgba(255,107,107,0.08)" title="Penalty zones">
        <span class="overlay-dot"></span>Penalties
      </span>
      <span class="overlay-toggle" id="toggle-emotions" style="--toggle-color:#FFD700;--toggle-bg:rgba(255,215,0,0.08)" title="Emotion quadrants">
        <span class="overlay-dot"></span>Emotions
      </span>
    </div>
    <span style="display:flex;align-items:center;gap:6px">
      <label style="font-size:0.75rem;color:#666;cursor:pointer;display:flex;align-items:center;gap:4px">
        <input type="checkbox" id="auto-refresh"> Live
      </label>
      <button class="control-btn" id="refresh-btn" title="Refresh data (R)">↻</button>
    </span>
  </div>
</div>

<script>
const DATA = __DATA_JSON__;

// --- Global hand list: all players share the same hand sequence ---
const allHandSet = new Set();
for (const p of DATA.players)
  for (const e of DATA.trajectories[p]) allHandSet.add(e.hand);
const allHands = [...allHandSet].sort((a, b) => a - b);

// Per-player trajectory indexed by hand number
const trajByHand = {};
for (const p of DATA.players) {
  trajByHand[p] = {};
  for (const e of DATA.trajectories[p]) trajByHand[p][e.hand] = e;
}

// Find player state at a given hand (exact match or most recent before)
function getPlayerState(player, handNum) {
  if (trajByHand[player][handNum]) return trajByHand[player][handNum];
  const traj = DATA.trajectories[player];
  let best = null;
  for (const entry of traj) {
    if (entry.hand <= handNum) best = entry;
    else break;
  }
  return best;
}

// --- Zone config from embedded data ---
const SWEET_SPOTS = DATA.zone_config.sweet_spots;
const PENALTIES = DATA.zone_config.penalty_thresholds;

const SWEET_SPOT_COLORS = {
  poker_face: '#90EE90',
  guarded:    '#87CEEB',
  commanding: '#FFD700',
  aggro:      '#FFA500',
};

const PENALTY_COLORS = {
  tilted:        'rgba(255,68,68,',
  overconfident: 'rgba(155,89,182,',
  timid:         'rgba(100,100,255,',
  shaken:        'rgba(255,140,0,',
  overheated:    'rgba(255,69,0,',
  detached:      'rgba(100,149,237,',
};

// --- Canvas setup ---
const canvas = document.getElementById('chart');
const ctx = canvas.getContext('2d');
let chartSize = 600;
const PAD = 54;

function toCanvas(conf, comp) {
  const ds = chartSize - 2 * PAD;
  return [PAD + conf * ds, PAD + (1 - comp) * ds];
}

function setupCanvas() {
  const container = canvas.parentElement;
  const maxSize = Math.min(container.clientWidth - 8, 860);
  chartSize = Math.max(400, maxSize);
  const dpr = window.devicePixelRatio || 1;
  canvas.width = chartSize * dpr;
  canvas.height = chartSize * dpr;
  canvas.style.width = chartSize + 'px';
  canvas.style.height = chartSize + 'px';
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

// --- Color helpers ---
function hexAlpha(hex, a) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${a})`;
}

// --- Overlay toggles ---
let showZones = true;
let showPenalties = true;
let showEmotions = false;

// --- Drawing ---
function render(idx) {
  ctx.clearRect(0, 0, chartSize, chartSize);
  drawBackground();
  if (showPenalties) drawPenaltyZones();
  drawGrid();
  if (showZones) drawSweetSpots();
  if (showPenalties) drawPenaltyLabels();
  if (showEmotions) drawEmotionZones();
  drawAxes();
  drawBaselines();
  drawTrails(idx);
  drawDots(idx);
  if (showEmotions) drawEmotionLabels(idx);
  drawLegend();
  updatePlayerCards(idx);
}

function drawBackground() {
  ctx.fillStyle = '#0d1117';
  ctx.fillRect(0, 0, chartSize, chartSize);
  // Data area slightly lighter
  const ds = chartSize - 2 * PAD;
  ctx.fillStyle = '#111820';
  ctx.fillRect(PAD, PAD, ds, ds);
}

function drawGrid() {
  const ds = chartSize - 2 * PAD;
  for (let i = 0; i <= 10; i++) {
    const v = i / 10;
    const alpha = (i % 5 === 0) ? 0.12 : 0.04;
    ctx.strokeStyle = `rgba(255,255,255,${alpha})`;
    ctx.lineWidth = 1;
    // Vertical
    const x = PAD + v * ds;
    ctx.beginPath(); ctx.moveTo(x, PAD); ctx.lineTo(x, PAD + ds); ctx.stroke();
    // Horizontal
    const y = PAD + (1 - v) * ds;
    ctx.beginPath(); ctx.moveTo(PAD, y); ctx.lineTo(PAD + ds, y); ctx.stroke();
  }
}

function drawPenaltyZones() {
  const ds = chartSize - 2 * PAD;
  const a = 0.06;

  // Tilted: composure < threshold (bottom band)
  const tiltY = toCanvas(0, PENALTIES.tilted_comp)[1];
  const botY = toCanvas(0, 0)[1];
  ctx.fillStyle = PENALTY_COLORS.tilted + a + ')';
  ctx.fillRect(PAD, tiltY, ds, botY - tiltY);

  // Overconfident: confidence > threshold (right band)
  const ocX = toCanvas(PENALTIES.overconfident_conf, 0)[0];
  const rightX = toCanvas(1, 0)[0];
  ctx.fillStyle = PENALTY_COLORS.overconfident + a + ')';
  ctx.fillRect(ocX, PAD, rightX - ocX, ds);

  // Timid: confidence < threshold (left band)
  const timX = toCanvas(PENALTIES.timid_conf, 0)[0];
  ctx.fillStyle = PENALTY_COLORS.timid + a + ')';
  ctx.fillRect(PAD, PAD, timX - PAD, ds);

  // Shaken: conf < thresh AND comp < thresh (bottom-left)
  const shX = toCanvas(PENALTIES.shaken_conf, 0)[0];
  const shY = toCanvas(0, PENALTIES.shaken_comp)[1];
  ctx.fillStyle = PENALTY_COLORS.shaken + a + ')';
  ctx.fillRect(PAD, shY, shX - PAD, botY - shY);

  // Overheated: conf > thresh AND comp < thresh (bottom-right)
  const ohX = toCanvas(PENALTIES.overheated_conf, 0)[0];
  const ohY = toCanvas(0, PENALTIES.overheated_comp)[1];
  ctx.fillStyle = PENALTY_COLORS.overheated + a + ')';
  ctx.fillRect(ohX, ohY, rightX - ohX, botY - ohY);

  // Detached: conf < thresh AND comp > thresh (top-left)
  const dtX = toCanvas(PENALTIES.detached_conf, 0)[0];
  const dtY = toCanvas(0, PENALTIES.detached_comp)[1];
  ctx.fillStyle = PENALTY_COLORS.detached + a + ')';
  ctx.fillRect(PAD, PAD, dtX - PAD, dtY - PAD);

  // Dashed threshold lines
  ctx.setLineDash([4, 4]);
  ctx.lineWidth = 1;

  // Tilted line
  ctx.strokeStyle = PENALTY_COLORS.tilted + '0.25)';
  ctx.beginPath(); ctx.moveTo(PAD, tiltY); ctx.lineTo(PAD + ds, tiltY); ctx.stroke();

  // Overconfident line
  ctx.strokeStyle = PENALTY_COLORS.overconfident + '0.25)';
  ctx.beginPath(); ctx.moveTo(ocX, PAD); ctx.lineTo(ocX, PAD + ds); ctx.stroke();

  ctx.setLineDash([]);
}

function drawPenaltyLabels() {
  ctx.font = '9px sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';

  const ds = chartSize - 2 * PAD;
  const botY = toCanvas(0, 0)[1];

  // Tilted
  ctx.fillStyle = PENALTY_COLORS.tilted + '0.4)';
  const tiltLabelY = (toCanvas(0, PENALTIES.tilted_comp)[1] + botY) / 2;
  ctx.fillText('TILTED', PAD + ds / 2, tiltLabelY);

  // Overconfident
  ctx.fillStyle = PENALTY_COLORS.overconfident + '0.4)';
  ctx.save();
  const ocX = (toCanvas(PENALTIES.overconfident_conf, 0)[0] + toCanvas(1, 0)[0]) / 2;
  ctx.translate(ocX, PAD + ds / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText('OVERCONFIDENT', 0, 0);
  ctx.restore();

  // Shaken
  ctx.fillStyle = PENALTY_COLORS.shaken + '0.3)';
  ctx.fillText('SHAKEN', (PAD + toCanvas(PENALTIES.shaken_conf, 0)[0]) / 2, tiltLabelY);

  // Overheated
  ctx.fillStyle = PENALTY_COLORS.overheated + '0.3)';
  const ohMidX = (toCanvas(PENALTIES.overheated_conf, 0)[0] + toCanvas(1, 0)[0]) / 2;
  ctx.fillText('OVERHEATED', ohMidX, tiltLabelY);

  // Detached
  ctx.fillStyle = PENALTY_COLORS.detached + '0.3)';
  const dtMidX = (PAD + toCanvas(PENALTIES.detached_conf, 0)[0]) / 2;
  const dtMidY = (PAD + toCanvas(0, PENALTIES.detached_comp)[1]) / 2;
  ctx.fillText('DETACHED', dtMidX, dtMidY);
}

function drawSweetSpots() {
  for (const [name, zone] of Object.entries(SWEET_SPOTS)) {
    const color = SWEET_SPOT_COLORS[name] || '#fff';
    const [cx, cy] = toCanvas(zone.center[0], zone.center[1]);
    const r = zone.radius * (chartSize - 2 * PAD);

    // Radial gradient fill
    const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
    grad.addColorStop(0, hexAlpha(color, 0.18));
    grad.addColorStop(1, hexAlpha(color, 0.02));
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();

    // Border
    ctx.strokeStyle = hexAlpha(color, 0.3);
    ctx.lineWidth = 1;
    ctx.stroke();

    // Label
    ctx.fillStyle = hexAlpha(color, 0.5);
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(zone.label, cx, cy);
  }
}

// --- Emotion overlay ---
const EMOTION_EMOJI = {
  poker_face: '\u{1F610}', confident: '\u{1F60F}', smug: '\u{1F60E}',
  angry: '\u{1F621}', frustrated: '\u{1F624}', nervous: '\u{1F630}',
  thinking: '\u{1F914}', shocked: '\u{1F631}', elated: '\u{1F929}', happy: '\u{1F60A}',
};

const QUADRANT_COLORS = {
  commanding: '#FFD700',
  overheated: '#FF6347',
  guarded:    '#6495ED',
  shaken:     '#FF8C00',
};

function drawEmotionZones() {
  const ds = chartSize - 2 * PAD;
  // Quadrant boundary lines at conf=0.5, comp=0.5
  const [mx, _my1] = toCanvas(0.5, 0);
  const [_mx2, my] = toCanvas(0, 0.5);

  ctx.save();
  ctx.strokeStyle = 'rgba(255,255,255,0.12)';
  ctx.lineWidth = 1;
  // Vertical line at conf=0.5
  ctx.beginPath(); ctx.moveTo(mx, PAD); ctx.lineTo(mx, PAD + ds); ctx.stroke();
  // Horizontal line at comp=0.5
  ctx.beginPath(); ctx.moveTo(PAD, my); ctx.lineTo(PAD + ds, my); ctx.stroke();

  // SHAKEN corner highlight (conf<0.35, comp<0.35)
  const [shX, _] = toCanvas(0.35, 0);
  const [__, shY] = toCanvas(0, 0.35);
  ctx.strokeStyle = 'rgba(255,140,0,0.15)';
  ctx.lineWidth = 1;
  ctx.setLineDash([3, 3]);
  ctx.beginPath(); ctx.moveTo(shX, PAD + ds); ctx.lineTo(shX, shY); ctx.lineTo(PAD + ds, shY); ctx.stroke();
  ctx.setLineDash([]);

  // Quadrant emoji labels
  ctx.font = '10px sans-serif';
  ctx.textBaseline = 'middle';
  const labelPad = 8;

  // COMMANDING (upper-right): conf>0.5, comp>0.5
  ctx.fillStyle = 'rgba(255,215,0,0.45)';
  ctx.textAlign = 'right';
  ctx.fillText('COMMANDING \u{1F60E}\u{1F60F}', PAD + ds - labelPad, PAD + labelPad + 4);

  // OVERHEATED (lower-right): conf>0.5, comp<=0.5
  ctx.fillStyle = 'rgba(255,99,71,0.45)';
  ctx.textAlign = 'right';
  ctx.fillText('OVERHEATED \u{1F621}\u{1F624}', PAD + ds - labelPad, PAD + ds - labelPad);

  // GUARDED (upper-left): conf<=0.5, comp>0.5
  ctx.fillStyle = 'rgba(100,149,237,0.45)';
  ctx.textAlign = 'left';
  ctx.fillText('\u{1F914}\u{1F630} GUARDED', PAD + labelPad, PAD + labelPad + 4);

  // SHAKEN (lower-left): conf<0.35, comp<0.35
  ctx.fillStyle = 'rgba(255,140,0,0.45)';
  ctx.textAlign = 'left';
  ctx.fillText('\u{1F631}\u{1F630} SHAKEN', PAD + labelPad, PAD + ds - labelPad);

  ctx.restore();
}

function drawEmotionLabels(globalIdx) {
  const currentHand = allHands[globalIdx];
  const positions = []; // track drawn positions to avoid overlap

  for (const player of DATA.players) {
    const point = getPlayerState(player, currentHand);
    if (!point) continue;
    const dimmed = highlightedPlayer && highlightedPlayer !== player;
    const eliminated = point.hand < currentHand;
    if (dimmed || eliminated) continue;

    const emotion = point.emotion || 'poker_face';
    const emoji = EMOTION_EMOJI[emotion] || EMOTION_EMOJI.poker_face;
    const [x, y] = toCanvas(point.conf, point.comp);

    // Offset: above-right of dot, shift right for overlapping players
    let ox = x + 12;
    let oy = y - 12;
    for (const pos of positions) {
      if (Math.abs(ox - pos[0]) < 22 && Math.abs(oy - pos[1]) < 16) {
        ox += 22;
      }
    }
    positions.push([ox, oy]);

    // Dark pill background
    const pillW = 20, pillH = 18;
    ctx.fillStyle = 'rgba(13,17,23,0.85)';
    ctx.beginPath();
    const r = 4;
    ctx.moveTo(ox - pillW/2 + r, oy - pillH/2);
    ctx.lineTo(ox + pillW/2 - r, oy - pillH/2);
    ctx.quadraticCurveTo(ox + pillW/2, oy - pillH/2, ox + pillW/2, oy - pillH/2 + r);
    ctx.lineTo(ox + pillW/2, oy + pillH/2 - r);
    ctx.quadraticCurveTo(ox + pillW/2, oy + pillH/2, ox + pillW/2 - r, oy + pillH/2);
    ctx.lineTo(ox - pillW/2 + r, oy + pillH/2);
    ctx.quadraticCurveTo(ox - pillW/2, oy + pillH/2, ox - pillW/2, oy + pillH/2 - r);
    ctx.lineTo(ox - pillW/2, oy - pillH/2 + r);
    ctx.quadraticCurveTo(ox - pillW/2, oy - pillH/2, ox - pillW/2 + r, oy - pillH/2);
    ctx.closePath();
    ctx.fill();

    // Emoji
    ctx.font = '13px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#fff';
    ctx.fillText(emoji, ox, oy);
  }
}

function drawAxes() {
  const ds = chartSize - 2 * PAD;
  ctx.fillStyle = '#666';
  ctx.font = '11px sans-serif';

  // X-axis label
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  ctx.fillText('Confidence \u2192', PAD + ds / 2, PAD + ds + 30);

  // Y-axis label
  ctx.save();
  ctx.translate(14, PAD + ds / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  ctx.fillText('Composure \u2192', 0, 0);
  ctx.restore();

  // Tick labels
  ctx.fillStyle = '#555';
  ctx.font = '9px sans-serif';
  for (let i = 0; i <= 10; i += 2) {
    const v = i / 10;
    // X ticks
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.fillText(v.toFixed(1), toCanvas(v, 0)[0], toCanvas(0, 0)[1] + 4);
    // Y ticks
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    ctx.fillText(v.toFixed(1), PAD - 5, toCanvas(0, v)[1]);
  }
}

function drawBaselines() {
  // Draw small diamond at each player's first position (approximate baseline)
  for (const player of DATA.players) {
    const traj = DATA.trajectories[player];
    if (!traj || traj.length === 0) continue;
    const [bx, by] = toCanvas(traj[0].conf, traj[0].comp);
    const color = DATA.player_colors[player];
    const s = 4;
    ctx.fillStyle = hexAlpha(color, 0.35);
    ctx.beginPath();
    ctx.moveTo(bx, by - s);
    ctx.lineTo(bx + s, by);
    ctx.lineTo(bx, by + s);
    ctx.lineTo(bx - s, by);
    ctx.closePath();
    ctx.fill();
  }
}

let highlightedPlayer = null;

function drawTrails(globalIdx) {
  const trailLen = 20;
  const currentHand = allHands[globalIdx];
  for (const player of DATA.players) {
    const traj = DATA.trajectories[player];
    if (!traj || traj.length === 0) continue;
    const color = DATA.player_colors[player];
    const dimmed = highlightedPlayer && highlightedPlayer !== player;
    const baseAlpha = dimmed ? 0.04 : 1.0;

    // Get player's trajectory points up to current hand
    const visible = traj.filter(t => t.hand <= currentHand);
    const start = Math.max(0, visible.length - trailLen - 1);
    const points = visible.slice(start);

    for (let i = 0; i < points.length - 1; i++) {
      const progress = i / Math.max(1, points.length - 2);
      const alpha = (0.08 + 0.45 * progress) * baseAlpha;
      ctx.strokeStyle = hexAlpha(color, alpha);
      ctx.lineWidth = dimmed ? 1 : 2;
      ctx.beginPath();
      const [x1, y1] = toCanvas(points[i].conf, points[i].comp);
      const [x2, y2] = toCanvas(points[i + 1].conf, points[i + 1].comp);
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();
    }
  }
}

function drawDots(globalIdx) {
  const currentHand = allHands[globalIdx];
  for (const player of DATA.players) {
    const point = getPlayerState(player, currentHand);
    if (!point) continue;
    const color = DATA.player_colors[player];
    const dimmed = highlightedPlayer && highlightedPlayer !== player;
    const eliminated = point.hand < currentHand; // last entry is from an earlier hand
    const [x, y] = toCanvas(point.conf, point.comp);

    if (dimmed || eliminated) {
      ctx.fillStyle = hexAlpha(color, eliminated ? 0.08 : 0.15);
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI * 2);
      ctx.fill();
      continue;
    }

    // Glow
    ctx.fillStyle = hexAlpha(color, 0.25);
    ctx.beginPath();
    ctx.arc(x, y, 11, 0, Math.PI * 2);
    ctx.fill();

    // Dot
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(x, y, 5, 0, Math.PI * 2);
    ctx.fill();

    // Border
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 1.5;
    ctx.stroke();

    // Initials label (offset up-right)
    ctx.fillStyle = hexAlpha(color, 0.8);
    ctx.font = 'bold 9px sans-serif';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'bottom';
    ctx.fillText(DATA.player_initials[player], x + 8, y - 4);
  }
}

function drawLegend() {
  ctx.font = '10px sans-serif';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  let y = PAD + 12;
  const x = chartSize - 10;
  for (const player of DATA.players) {
    const color = DATA.player_colors[player];
    ctx.fillStyle = color;
    const tw = ctx.measureText(player).width;
    ctx.beginPath();
    ctx.arc(x - tw - 8, y, 3.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = '#bbb';
    ctx.fillText(player, x, y);
    y += 16;
  }
}

// --- Player cards ---
function createPlayerCards() {
  const grid = document.getElementById('player-grid');
  grid.innerHTML = '';
  for (const player of DATA.players) {
    const color = DATA.player_colors[player];
    const initials = DATA.player_initials[player];
    const sid = sanitize(player);

    const card = document.createElement('div');
    card.className = 'player-card';
    card.id = 'card-' + sid;
    card.style.borderLeftColor = color;

    card.innerHTML =
      '<div class="player-header-row">' +
        '<div class="player-left">' +
          '<div class="avatar-wrap">' +
            '<div class="avatar" style="background:' + color + '">' + initials + '</div>' +
            '<span class="avatar-emotion" id="avatar-emo-' + sid + '"></span>' +
          '</div>' +
          '<div class="player-name-wrap">' +
            '<div class="player-name">' + player + '</div>' +
            '<span class="player-emotion-text" id="emo-text-' + sid + '"></span>' +
          '</div>' +
        '</div>' +
        '<div class="card-summary" id="summary-' + sid + '"></div>' +
        '<button class="expand-btn" title="Show details">&#9656;</button>' +
      '</div>' +
      '<div class="player-right">' +
        makeAxisRow('C', sid, 'conf', color, 1) +
        makeAxisRow('M', sid, 'comp', color, 1) +
        makeAxisRow('E', sid, 'energy', color, 0.6) +
        '<div class="affinity-section">' +
          makeAffinityRow(AFFINITY_LABELS.guarded, sid, 'guarded', AFFINITY_COLORS.guarded) +
          makeAffinityRow(AFFINITY_LABELS.poker_face, sid, 'poker_face', AFFINITY_COLORS.poker_face) +
          makeAffinityRow(AFFINITY_LABELS.commanding, sid, 'commanding', AFFINITY_COLORS.commanding) +
          makeAffinityRow(AFFINITY_LABELS.aggro, sid, 'aggro', AFFINITY_COLORS.aggro) +
        '</div>' +
      '</div>' +
      '<div class="card-details">' +
        '<div class="player-middle">' +
          '<div class="events" id="events-' + sid + '"></div>' +
        '</div>' +
      '</div>';

    // Expand button toggles details
    card.querySelector('.expand-btn').addEventListener('click', function(e) {
      e.stopPropagation();
      card.classList.toggle('expanded');
    });
    // Card click highlights on chart
    card.addEventListener('click', () => toggleHighlight(player));
    grid.appendChild(card);
  }
}

function makeAxisRow(label, sid, axis, color, opacity) {
  return '<div class="axis-row">' +
    '<span class="axis-label">' + label + '</span>' +
    '<div class="axis-bar"><div class="axis-fill" id="' + axis + '-bar-' + sid + '" ' +
      'style="background:' + color + ';opacity:' + opacity + '"></div></div>' +
    '<span class="axis-value" id="' + axis + '-val-' + sid + '">\u2014</span>' +
    '<span class="axis-delta" id="' + axis + '-delta-' + sid + '">\u2014</span>' +
  '</div>';
}

// --- Affinity computation (mirrors playstyle_selector.py) ---
const AFFINITY_SIGMA = 0.25;
const AFFINITY_STYLES = ['guarded', 'poker_face', 'commanding', 'aggro'];
const AFFINITY_LABELS = {guarded: 'GU', poker_face: 'PF', commanding: 'CM', aggro: 'AG'};
const AFFINITY_COLORS = {guarded: '#87CEEB', poker_face: '#90EE90', commanding: '#FFD700', aggro: '#FFA500'};

function computeAffinities(conf, comp) {
  const raw = {};
  let total = 0;
  for (const style of AFFINITY_STYLES) {
    const center = SWEET_SPOTS[style].center;
    const dSq = (conf - center[0]) ** 2 + (comp - center[1]) ** 2;
    const v = Math.exp(-dSq / (2 * AFFINITY_SIGMA ** 2));
    raw[style] = v;
    total += v;
  }
  if (total === 0) return {guarded: 0.25, poker_face: 0.25, commanding: 0.25, aggro: 0.25};
  const result = {};
  for (const s of AFFINITY_STYLES) result[s] = raw[s] / total;
  return result;
}

function makeAffinityRow(label, sid, style, color) {
  return '<div class="affinity-row" id="aff-row-' + style + '-' + sid + '">' +
    '<span class="affinity-label">' + label + '</span>' +
    '<div class="affinity-bar-bg"><div class="affinity-bar-fill" id="aff-bar-' + style + '-' + sid + '" ' +
      'style="background:' + color + '"></div></div>' +
    '<span class="affinity-pct" id="aff-pct-' + style + '-' + sid + '">0%</span>' +
  '</div>';
}

function toggleHighlight(player) {
  highlightedPlayer = (highlightedPlayer === player) ? null : player;
  // Update card classes
  for (const p of DATA.players) {
    const card = document.getElementById('card-' + sanitize(p));
    if (!card) continue;
    card.classList.remove('highlighted', 'dimmed');
    if (highlightedPlayer) {
      card.classList.add(p === highlightedPlayer ? 'highlighted' : 'dimmed');
    }
  }
  render(parseInt(document.getElementById('slider').value));
}

function updatePlayerCards(globalIdx) {
  const currentHand = allHands[globalIdx];
  const prevHand = globalIdx > 0 ? allHands[globalIdx - 1] : null;

  for (const player of DATA.players) {
    const current = getPlayerState(player, currentHand);
    if (!current) continue;
    const prev = prevHand ? getPlayerState(player, prevHand) : null;
    const eliminated = current.hand < currentHand;
    const sid = sanitize(player);

    // Bars
    el(sid, 'conf-bar').style.width = (current.conf * 100) + '%';
    el(sid, 'comp-bar').style.width = (current.comp * 100) + '%';
    el(sid, 'energy-bar').style.width = (current.energy * 100) + '%';

    // Values
    el(sid, 'conf-val').textContent = current.conf.toFixed(2);
    el(sid, 'comp-val').textContent = current.comp.toFixed(2);
    el(sid, 'energy-val').textContent = current.energy.toFixed(2);

    // Deltas (only if player was active this hand and prev exists)
    if (prev && !eliminated) {
      setDelta(sid, 'conf', current.conf - prev.conf);
      setDelta(sid, 'comp', current.comp - prev.comp);
      setDelta(sid, 'energy', current.energy - prev.energy);
    } else {
      for (const ax of ['conf', 'comp', 'energy']) {
        el(sid, ax + '-delta').textContent = '\u2014';
        el(sid, ax + '-delta').className = 'axis-delta neutral-delta';
      }
    }

    // Affinities
    const affinities = computeAffinities(current.conf, current.comp);
    for (const style of AFFINITY_STYLES) {
      const pct = affinities[style];
      const barEl = document.getElementById('aff-bar-' + style + '-' + sid);
      const pctEl = document.getElementById('aff-pct-' + style + '-' + sid);
      const rowEl = document.getElementById('aff-row-' + style + '-' + sid);
      if (barEl) barEl.style.width = (pct * 100) + '%';
      if (pctEl) pctEl.textContent = Math.round(pct * 100) + '%';
      if (rowEl) {
        if (current.sweet_spot === style) rowEl.classList.add('affinity-active');
        else rowEl.classList.remove('affinity-active');
      }
    }

    // Events (in details)
    const evEl = document.getElementById('events-' + sid);
    if (eliminated) {
      evEl.innerHTML = '<span class="event-badge loss">Eliminated</span>';
    } else {
      evEl.innerHTML = computeEvents(player, currentHand, prevHand, current, prev);
    }

    // Summary badges (always visible in header row)
    const summaryEl = document.getElementById('summary-' + sid);
    if (summaryEl) {
      summaryEl.innerHTML = computeSummary(player, currentHand, prevHand, current, prev, eliminated);
    }

    // Emotion indicator on card (visible when emotions overlay active)
    const emotion = current.emotion || 'poker_face';
    const emoji = EMOTION_EMOJI[emotion] || EMOTION_EMOJI.poker_face;
    const avatarEmo = document.getElementById('avatar-emo-' + sid);
    const emoText = document.getElementById('emo-text-' + sid);
    if (avatarEmo) {
      avatarEmo.textContent = emoji;
      avatarEmo.style.display = showEmotions ? '' : 'none';
    }
    if (emoText) {
      const quadrant = getQuadrant(current.conf, current.comp);
      const qColor = QUADRANT_COLORS[quadrant] || '#888';
      emoText.textContent = emotion.replace('_', ' ');
      emoText.style.color = qColor;
      emoText.style.display = showEmotions ? '' : 'none';
    }
  }
}

function getQuadrant(conf, comp) {
  if (conf > 0.5 && comp > 0.5) return 'commanding';
  if (conf > 0.5 && comp <= 0.5) return 'overheated';
  if (conf <= 0.5 && comp > 0.5) return 'guarded';
  return 'shaken';
}

function el(sid, prefix) {
  return document.getElementById(prefix + '-' + sid);
}

function setDelta(sid, axis, value) {
  const e = el(sid, axis + '-delta');
  if (Math.abs(value) < 0.001) {
    e.textContent = '\u2014';
    e.className = 'axis-delta neutral-delta';
  } else {
    e.textContent = (value > 0 ? '+' : '') + value.toFixed(3);
    e.className = 'axis-delta ' + (value > 0 ? 'positive' : 'negative');
  }
}

// Event type display names and CSS classes
const EVENT_STYLES = {
  big_win: {label: 'big win', cls: 'win'},
  win: {label: 'win', cls: 'win'},
  successful_bluff: {label: 'bluff worked', cls: 'win'},
  suckout: {label: 'suckout', cls: 'win'},
  double_up: {label: 'double up!', cls: 'win'},
  eliminated_opponent: {label: 'eliminated opp', cls: 'win'},
  winning_streak: {label: 'streak \u2191', cls: 'win'},
  nemesis_win: {label: 'nemesis win', cls: 'win'},
  big_loss: {label: 'big loss', cls: 'loss'},
  bluff_called: {label: 'bluff called', cls: 'loss'},
  bad_beat: {label: 'bad beat', cls: 'loss'},
  got_sucked_out: {label: 'sucked out on', cls: 'loss'},
  cooler: {label: 'cooler', cls: 'loss'},
  crippled: {label: 'crippled', cls: 'loss'},
  short_stack: {label: 'short stack', cls: 'loss'},
  losing_streak: {label: 'streak \u2193', cls: 'loss'},
  nemesis_loss: {label: 'nemesis loss', cls: 'loss'},
  fold_under_pressure: {label: 'folded under pressure', cls: 'neutral'},
  _recovery: {label: 'recovery', cls: 'zone'},
  _gravity: {label: 'zone gravity', cls: 'thoughts'},
};

function fmtDelta(v) {
  if (Math.abs(v) < 0.0005) return {text: '\u2014', cls: 'force-zero'};
  const sign = v > 0 ? '+' : '';
  return {text: sign + v.toFixed(3), cls: v > 0 ? 'force-pos' : 'force-neg'};
}

function computeEvents(playerName, currentHand, prevHand, current, prev) {
  if (!prev || !prevHand) return '<span class="event-badge neutral">Baseline</span>';

  const pe = DATA.pressure_events || {};
  const playerEvents = pe[playerName] || {};
  // Collect events from all hands between prev.hand (inclusive) and current.hand (exclusive).
  // When a player skips hands (e.g. all-in), prev.hand may be earlier than prevHand,
  // so we need events from multiple hands to explain the full movement.
  let handEvents = [];
  for (const h of Object.keys(playerEvents).map(Number).sort((a,b) => a-b)) {
    if (h >= prev.hand && h < current.hand) {
      handEvents = handEvents.concat(playerEvents[h]);
    }
  }

  // Separate events into force rows (have deltas) and badges (no deltas)
  const forceRows = [];
  const badges = [];
  let netConf = 0, netComp = 0, netEnergy = 0;

  for (const evt of handEvents) {
    const style = EVENT_STYLES[evt.type] || {label: evt.type, cls: 'neutral'};
    const d = evt.details || {};
    const hasDeltas = d.conf_delta !== undefined || d.comp_delta !== undefined;

    if (hasDeltas) {
      const cd = d.conf_delta || 0;
      const md = d.comp_delta || 0;
      const ed = d.energy_delta || 0;
      netConf += cd;
      netComp += md;
      netEnergy += ed;
      forceRows.push({label: style.label, cls: style.cls, conf: cd, comp: md, energy: ed});
    } else {
      badges.push('<span class="event-badge ' + style.cls + '">' + style.label + '</span>');
    }
  }

  let html = '';

  // Force breakdown table
  if (forceRows.length > 0) {
    html += '<table class="force-table">';
    for (const row of forceRows) {
      const c = fmtDelta(row.conf);
      const m = fmtDelta(row.comp);
      const e = fmtDelta(row.energy);
      html += '<tr>' +
        '<td class="force-label"><span class="event-badge ' + row.cls + '" style="padding:1px 5px;font-size:0.68rem">' + row.label + '</span></td>' +
        '<td class="force-val ' + c.cls + '">conf ' + c.text + '</td>' +
        '<td class="force-val ' + m.cls + '">comp ' + m.text + '</td>' +
        '<td class="force-val ' + e.cls + '">nrg ' + e.text + '</td>' +
        '</tr>';
    }
    // Net row (only if multiple forces)
    if (forceRows.length > 1) {
      const nc = fmtDelta(netConf);
      const nm = fmtDelta(netComp);
      const ne = fmtDelta(netEnergy);
      html += '<tr class="force-net">' +
        '<td class="force-label">net</td>' +
        '<td class="force-val ' + nc.cls + '">conf ' + nc.text + '</td>' +
        '<td class="force-val ' + nm.cls + '">comp ' + nm.text + '</td>' +
        '<td class="force-val ' + ne.cls + '">nrg ' + ne.text + '</td>' +
        '</tr>';
    }
    html += '</table>';
  }

  // Badge section (zone transitions, penalties, old events without deltas)
  const badgeParts = badges.slice();

  // Stack change
  const sd = current.stack - prev.stack;
  if (sd > 0) badgeParts.push('<span class="event-badge win">+' + sd + ' chips</span>');
  else if (sd < 0) badgeParts.push('<span class="event-badge loss">' + sd + ' chips</span>');

  // Zone transitions
  if (current.sweet_spot !== prev.sweet_spot) {
    if (current.sweet_spot)
      badgeParts.push('<span class="event-badge zone">\u2192 ' + current.sweet_spot + '</span>');
    if (prev.sweet_spot && !current.sweet_spot)
      badgeParts.push('<span class="event-badge neutral">left ' + prev.sweet_spot + '</span>');
  }

  // Penalty changes
  if (current.penalty !== prev.penalty) {
    if (current.penalty)
      badgeParts.push('<span class="event-badge penalty">\u26a0 ' + current.penalty + '</span>');
    if (prev.penalty && !current.penalty)
      badgeParts.push('<span class="event-badge zone">\u2713 left ' + prev.penalty + '</span>');
  } else if (current.penalty) {
    badgeParts.push('<span class="event-badge penalty">' + current.penalty +
      ' (' + (current.penalty_strength * 100).toFixed(0) + '%)</span>');
  }

  // Intrusive thoughts
  if (current.intrusive)
    badgeParts.push('<span class="event-badge thoughts">\ud83d\udcad intrusive thoughts</span>');

  if (badgeParts.length > 0) {
    html += '<div class="force-badges">' + badgeParts.join('') + '</div>';
  }

  return html || '<span class="event-badge neutral">steady</span>';
}

function computeSummary(playerName, currentHand, prevHand, current, prev, eliminated) {
  if (eliminated) return '<span class="event-badge loss">Eliminated</span>';
  if (!prev || !prevHand) return '<span class="event-badge neutral">Baseline</span>';

  const parts = [];

  // Stack change
  const sd = current.stack - prev.stack;
  if (sd > 0) parts.push('<span class="event-badge win">+' + sd + '</span>');
  else if (sd < 0) parts.push('<span class="event-badge loss">' + sd + '</span>');

  // Zone transition
  if (current.sweet_spot !== prev.sweet_spot && current.sweet_spot) {
    parts.push('<span class="event-badge zone">\u2192 ' + current.sweet_spot + '</span>');
  }

  // Penalty
  if (current.penalty) {
    parts.push('<span class="event-badge penalty">\u26a0 ' + current.penalty + '</span>');
  }

  // Intrusive thoughts
  if (current.intrusive) parts.push('<span class="event-badge thoughts">\ud83d\udcad</span>');

  return parts.join('') || '<span class="event-badge neutral">steady</span>';
}

// --- Controls ---
let maxHands = 0;
let playing = false;
let playInterval = null;

function setupControls() {
  const slider = document.getElementById('slider');
  const playBtn = document.getElementById('play-btn');
  const prevBtn = document.getElementById('prev-btn');
  const nextBtn = document.getElementById('next-btn');

  maxHands = allHands.length;

  slider.max = maxHands - 1;
  slider.value = 0;

  document.getElementById('meta').textContent =
    (DATA.experiment_id ? 'Experiment ' + DATA.experiment_id + ' | ' : '') +
    'Game ' + DATA.game_id.substring(0, 12) +
    '... | ' + DATA.players.length + ' players, ' + maxHands + ' hands';

  updateHandInfo(0);

  slider.addEventListener('input', function() {
    const idx = parseInt(this.value);
    render(idx);
    updateHandInfo(idx);
  });

  playBtn.addEventListener('click', function() {
    playing = !playing;
    this.innerHTML = playing ? '&#9646;&#9646;' : '&#9654;';
    if (playing) {
      playInterval = setInterval(function() {
        let idx = parseInt(slider.value);
        if (idx >= maxHands - 1) {
          playing = false;
          playBtn.innerHTML = '&#9654;';
          clearInterval(playInterval);
          return;
        }
        slider.value = idx + 1;
        render(idx + 1);
        updateHandInfo(idx + 1);
      }, 400);
    } else {
      clearInterval(playInterval);
    }
  });

  prevBtn.addEventListener('click', function() {
    let idx = Math.max(0, parseInt(slider.value) - 1);
    slider.value = idx;
    render(idx);
    updateHandInfo(idx);
  });

  nextBtn.addEventListener('click', function() {
    let idx = Math.min(maxHands - 1, parseInt(slider.value) + 1);
    slider.value = idx;
    render(idx);
    updateHandInfo(idx);
  });

  document.addEventListener('keydown', function(e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
    if (e.key === 'ArrowLeft') { prevBtn.click(); e.preventDefault(); }
    else if (e.key === 'ArrowRight') { nextBtn.click(); e.preventDefault(); }
    else if (e.key === ' ') { playBtn.click(); e.preventDefault(); }
    else if (e.key === 'Home') { slider.value = 0; render(0); updateHandInfo(0); e.preventDefault(); }
    else if (e.key === 'End') { slider.value = maxHands - 1; render(maxHands - 1); updateHandInfo(maxHands - 1); e.preventDefault(); }
  });

  // Game selector (if multiple games)
  if (DATA.games.length > 1) {
    const wrap = document.getElementById('game-selector-wrap');
    const sel = document.getElementById('game-selector');
    wrap.style.display = '';
    for (const g of DATA.games) {
      const opt = document.createElement('option');
      opt.value = g.game_id;
      opt.textContent = g.game_id.substring(0, 12) + '... (' + g.hands + ' hands)';
      if (g.game_id === DATA.game_id) opt.selected = true;
      sel.appendChild(opt);
    }
    sel.addEventListener('change', function() {
      // Navigate to same endpoint with game parameter
      const url = new URL(window.location.href);
      url.searchParams.set('game', this.value);
      window.location.href = url.toString();
    });
  }
}

function updateHandInfo(idx) {
  const hand = allHands[idx] || (idx + 1);
  document.getElementById('hand-info').textContent =
    'Hand ' + hand + ' (' + (idx + 1) + '/' + maxHands + ')';
}

// --- Utilities ---
function sanitize(name) {
  return name.replace(/[^a-zA-Z0-9]/g, '_');
}

// --- Init ---
// --- Auto-refresh for live viewing ---
let autoRefreshInterval = null;

function setupRefresh() {
  const refreshBtn = document.getElementById('refresh-btn');
  const autoCheck = document.getElementById('auto-refresh');

  refreshBtn.addEventListener('click', function() {
    window.location.reload();
  });

  autoCheck.addEventListener('change', function() {
    if (this.checked) {
      autoRefreshInterval = setInterval(function() {
        window.location.reload();
      }, 10000); // refresh every 10 seconds
    } else {
      clearInterval(autoRefreshInterval);
      autoRefreshInterval = null;
    }
  });

  // 'R' key to refresh
  document.addEventListener('keydown', function(e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
    if (e.key === 'r' || e.key === 'R') {
      window.location.reload();
    }
  });
}

function setupOverlayToggles() {
  const toggles = [
    {id: 'toggle-zones', getter: () => showZones, setter: v => { showZones = v; }},
    {id: 'toggle-penalties', getter: () => showPenalties, setter: v => { showPenalties = v; }},
    {id: 'toggle-emotions', getter: () => showEmotions, setter: v => { showEmotions = v; }},
  ];
  for (const t of toggles) {
    const el = document.getElementById(t.id);
    el.addEventListener('click', function() {
      const newVal = !t.getter();
      t.setter(newVal);
      this.classList.toggle('active', newVal);
      render(parseInt(document.getElementById('slider').value));
    });
  }
}

function init() {
  setupCanvas();
  createPlayerCards();
  setupControls();
  setupOverlayToggles();
  setupRefresh();
  render(0);
}

window.addEventListener('load', init);
window.addEventListener('resize', function() {
  setupCanvas();
  render(parseInt(document.getElementById('slider').value));
});
</script>
</body>
</html>
'''


def main():
    parser = argparse.ArgumentParser(
        description="Generate interactive psychology trajectory viewer"
    )
    parser.add_argument("--experiment", "-e", type=int, required=True,
                        help="Experiment ID")
    parser.add_argument("--output", "-o", default="trajectory_viewer.html",
                        help="Output HTML file path")
    parser.add_argument("--game", "-g", default=None,
                        help="Specific game_id (default: longest game)")
    parser.add_argument("--db", default=None,
                        help="Database path (auto-detected if omitted)")

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
