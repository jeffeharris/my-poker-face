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
        })

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
.container{max-width:1100px;margin:0 auto;padding:16px 20px}
header{text-align:center;margin-bottom:12px}
h1{font-size:1.3rem;font-weight:500;color:#fff;letter-spacing:-0.3px}
.meta{color:#666;font-size:0.8rem;margin-top:2px}
.game-selector{margin-top:6px}
.game-selector select{background:#161b22;color:#e6e6e6;border:1px solid #30363d;padding:4px 8px;border-radius:4px;font-size:0.8rem}

.chart-section{display:flex;justify-content:center;margin-bottom:12px}
canvas{border-radius:8px;cursor:crosshair}

.controls{display:flex;align-items:center;gap:10px;padding:10px 14px;background:#161b22;border-radius:8px;margin-bottom:12px;border:1px solid #21262d}
.control-btn{background:#21262d;border:1px solid #30363d;color:#e6e6e6;width:32px;height:32px;border-radius:6px;cursor:pointer;font-size:12px;display:flex;align-items:center;justify-content:center;transition:background .15s}
.control-btn:hover{background:#30363d}
.control-btn:active{background:#3d444d}
input[type="range"]{flex:1;-webkit-appearance:none;height:6px;background:#30363d;border-radius:3px;outline:none}
input[type="range"]::-webkit-slider-thumb{-webkit-appearance:none;width:16px;height:16px;border-radius:50%;background:#58a6ff;cursor:pointer;transition:transform .1s}
input[type="range"]::-webkit-slider-thumb:hover{transform:scale(1.2)}
input[type="range"]::-moz-range-thumb{width:16px;height:16px;border-radius:50%;background:#58a6ff;cursor:pointer;border:none}
.hand-info{font-size:0.85rem;white-space:nowrap;min-width:110px;text-align:right;font-variant-numeric:tabular-nums}
.kbd-hint{color:#444;font-size:0.7rem;margin-left:auto}

.player-grid{display:flex;flex-direction:column;gap:6px}
.player-card{display:flex;align-items:center;gap:14px;padding:10px 14px;background:#161b22;border-radius:8px;border-left:3px solid transparent;border:1px solid #21262d;cursor:pointer;transition:background .15s,border-color .15s}
.player-card:hover{background:#1c2129}
.player-card.highlighted{border-color:#58a6ff !important;background:#1c2433}
.player-card.dimmed{opacity:0.35}

.player-left{display:flex;align-items:center;gap:10px;min-width:160px}
.avatar{width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px;color:#000;flex-shrink:0}
.player-name{font-weight:500;font-size:0.88rem;white-space:nowrap}

.player-middle{flex:1;min-width:0}
.events{display:flex;flex-wrap:wrap;gap:4px;min-height:24px}
.event-badge{padding:2px 7px;border-radius:3px;font-size:0.72rem;font-weight:500;white-space:nowrap}
.event-badge.win{background:rgba(0,184,148,0.13);color:#00b894}
.event-badge.loss{background:rgba(255,107,107,0.13);color:#ff6b6b}
.event-badge.zone{background:rgba(88,166,255,0.13);color:#58a6ff}
.event-badge.penalty{background:rgba(255,107,107,0.18);color:#ff6b6b;border:1px solid rgba(255,107,107,0.25)}
.event-badge.neutral{background:rgba(150,150,150,0.1);color:#888}
.event-badge.thoughts{background:rgba(255,230,109,0.15);color:#ffe66d}

.player-right{display:flex;flex-direction:column;gap:3px;min-width:220px}
.axis-row{display:flex;align-items:center;gap:6px;font-size:0.78rem}
.axis-label{width:46px;color:#666;text-align:right}
.axis-bar{flex:1;height:5px;background:#21262d;border-radius:2px;overflow:hidden;min-width:60px}
.axis-fill{height:100%;border-radius:2px;transition:width .3s ease}
.axis-value{width:32px;text-align:right;font-variant-numeric:tabular-nums;color:#ccc}
.axis-delta{width:48px;text-align:right;font-variant-numeric:tabular-nums;font-size:0.72rem}
.positive{color:#00b894}
.negative{color:#ff6b6b}
.neutral-delta{color:#444}

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

  <div class="chart-section">
    <canvas id="chart"></canvas>
  </div>

  <div class="controls">
    <button class="control-btn" id="prev-btn" title="Previous hand (←)">&#9664;</button>
    <button class="control-btn" id="play-btn" title="Play / Pause (Space)">&#9654;</button>
    <button class="control-btn" id="next-btn" title="Next hand (→)">&#9654;</button>
    <input type="range" id="slider" min="0" max="0" value="0">
    <div class="hand-info" id="hand-info">Hand 1 / 1</div>
    <span class="kbd-hint">← → Space</span>
  </div>

  <div class="player-grid" id="player-grid"></div>
</div>

<script>
const DATA = __DATA_JSON__;

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
  const maxSize = Math.min(container.clientWidth - 8, 640);
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

// --- Drawing ---
function render(idx) {
  ctx.clearRect(0, 0, chartSize, chartSize);
  drawBackground();
  drawPenaltyZones();
  drawGrid();
  drawSweetSpots();
  drawPenaltyLabels();
  drawAxes();
  drawBaselines();
  drawTrails(idx);
  drawDots(idx);
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

function drawTrails(currentIdx) {
  const trailLen = 20;
  for (const player of DATA.players) {
    const traj = DATA.trajectories[player];
    if (!traj || traj.length === 0) continue;
    const color = DATA.player_colors[player];
    const dimmed = highlightedPlayer && highlightedPlayer !== player;
    const baseAlpha = dimmed ? 0.04 : 1.0;
    const start = Math.max(0, currentIdx - trailLen);
    const end = Math.min(currentIdx, traj.length - 1);

    for (let i = start; i < end; i++) {
      const progress = (i - start) / Math.max(1, end - start);
      const alpha = (0.08 + 0.45 * progress) * baseAlpha;
      ctx.strokeStyle = hexAlpha(color, alpha);
      ctx.lineWidth = dimmed ? 1 : 2;
      ctx.beginPath();
      const [x1, y1] = toCanvas(traj[i].conf, traj[i].comp);
      const [x2, y2] = toCanvas(traj[i + 1].conf, traj[i + 1].comp);
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();
    }
  }
}

function drawDots(handIdx) {
  for (const player of DATA.players) {
    const traj = DATA.trajectories[player];
    if (!traj || handIdx >= traj.length) continue;
    const point = traj[handIdx];
    const color = DATA.player_colors[player];
    const dimmed = highlightedPlayer && highlightedPlayer !== player;
    const [x, y] = toCanvas(point.conf, point.comp);

    if (dimmed) {
      ctx.fillStyle = hexAlpha(color, 0.15);
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
      '<div class="player-left">' +
        '<div class="avatar" style="background:' + color + '">' + initials + '</div>' +
        '<div class="player-name">' + player + '</div>' +
      '</div>' +
      '<div class="player-middle">' +
        '<div class="events" id="events-' + sid + '"></div>' +
      '</div>' +
      '<div class="player-right">' +
        makeAxisRow('Conf', sid, 'conf', color, 1) +
        makeAxisRow('Comp', sid, 'comp', color, 1) +
        makeAxisRow('Energy', sid, 'energy', color, 0.6) +
      '</div>';

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

function updatePlayerCards(handIdx) {
  for (const player of DATA.players) {
    const traj = DATA.trajectories[player];
    if (!traj || traj.length === 0) continue;
    const eliminated = handIdx >= traj.length;
    const effectiveIdx = eliminated ? traj.length - 1 : handIdx;
    const current = traj[effectiveIdx];
    const prev = effectiveIdx > 0 ? traj[effectiveIdx - 1] : null;
    const sid = sanitize(player);

    // Bars
    el(sid, 'conf-bar').style.width = (current.conf * 100) + '%';
    el(sid, 'comp-bar').style.width = (current.comp * 100) + '%';
    el(sid, 'energy-bar').style.width = (current.energy * 100) + '%';

    // Values
    el(sid, 'conf-val').textContent = current.conf.toFixed(2);
    el(sid, 'comp-val').textContent = current.comp.toFixed(2);
    el(sid, 'energy-val').textContent = current.energy.toFixed(2);

    // Deltas
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

    // Events
    const evEl = document.getElementById('events-' + sid);
    if (eliminated) {
      evEl.innerHTML = '<span class="event-badge loss">Eliminated</span>';
    } else {
      evEl.innerHTML = computeEvents(current, prev);
    }
  }
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

function computeEvents(current, prev) {
  if (!prev) return '<span class="event-badge neutral">Baseline</span>';
  const b = [];

  // Stack change
  const sd = current.stack - prev.stack;
  if (sd > 0) b.push('<span class="event-badge win">+' + sd + ' chips</span>');
  else if (sd < 0) b.push('<span class="event-badge loss">' + sd + ' chips</span>');

  // Zone transitions
  if (current.sweet_spot !== prev.sweet_spot) {
    if (current.sweet_spot)
      b.push('<span class="event-badge zone">\u2192 ' + current.sweet_spot + '</span>');
    if (prev.sweet_spot && !current.sweet_spot)
      b.push('<span class="event-badge neutral">left ' + prev.sweet_spot + '</span>');
  }

  // Penalty changes
  if (current.penalty !== prev.penalty) {
    if (current.penalty)
      b.push('<span class="event-badge penalty">\u26a0 ' + current.penalty + '</span>');
    if (prev.penalty && !current.penalty)
      b.push('<span class="event-badge zone">\u2713 left ' + prev.penalty + '</span>');
  } else if (current.penalty) {
    // Still in penalty
    b.push('<span class="event-badge penalty">' + current.penalty +
      ' (' + (current.penalty_strength * 100).toFixed(0) + '%)</span>');
  }

  // Intrusive thoughts
  if (current.intrusive)
    b.push('<span class="event-badge thoughts">\ud83d\udcad intrusive thoughts</span>');

  // Action
  if (current.action)
    b.push('<span class="event-badge neutral">' + current.action + '</span>');

  return b.length ? b.join('') : '<span class="event-badge neutral">steady</span>';
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

  maxHands = 0;
  for (const player of DATA.players) {
    maxHands = Math.max(maxHands, DATA.trajectories[player].length);
  }

  slider.max = maxHands - 1;
  slider.value = 0;

  document.getElementById('meta').textContent =
    'Experiment ' + DATA.experiment_id + ' | Game ' + DATA.game_id.substring(0, 12) +
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
      // Reload would be needed for different game data.
      // For now just show a note.
      document.getElementById('meta').textContent =
        'Re-generate with --game ' + this.value + ' to view this game';
    });
  }
}

function updateHandInfo(idx) {
  const traj = DATA.trajectories[DATA.players[0]];
  const hand = traj && idx < traj.length ? traj[idx].hand : idx + 1;
  document.getElementById('hand-info').textContent =
    'Hand ' + hand + ' (' + (idx + 1) + '/' + maxHands + ')';
}

// --- Utilities ---
function sanitize(name) {
  return name.replace(/[^a-zA-Z0-9]/g, '_');
}

// --- Init ---
function init() {
  setupCanvas();
  createPlayerCards();
  setupControls();
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
