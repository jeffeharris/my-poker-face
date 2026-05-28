"""Admin range-explorer routes — preflop VPIP ranges by player / archetype / bot.

Read-only aggregation over ``player_decision_analysis``. Answers the question
"what starting-hand ranges did each AI actually play, and which controller
(hybrid LLM / tiered lookup / chaos) made the decision?"

Key modelling decisions (see also the inline notes):

* **Dedup.** The analyzer double-logs some decisions (one row linked to the
  LLM capture, a second unlinked shadow). We collapse to one decision per
  ``(game_id, player_name, hand_number, phase)`` so VPIP counts aren't inflated.

* **Controller bucket** is derived per decision from the signals on the row:
  a strategy-pipeline snapshot ⇒ ``tiered`` (the lookup table decided; any LLM
  capture on that decision is just the expression/chat layer); else an LLM
  capture ⇒ ``hybrid``; else, if the player is a known AI persona ⇒ ``chaos``;
  otherwise ``human`` (the no-signal residual that isn't a persona).

* **Archetype** is the tiered pipeline's ``deviation_profile_name``
  (tag / lag / calling_station / rock / nit / maniac), extracted via
  ``json_extract``. Only tiered decisions carry it.

* **VPIP** = voluntarily put money in preflop = action in {call, raise, all_in}.
  The big-blind free *check* is excluded (it's not voluntary).
"""

from __future__ import annotations

import logging
import sqlite3
import time

from flask import Blueprint, jsonify, request

from poker.authorization import require_permission

from .. import extensions

logger = logging.getLogger(__name__)

range_explorer_bp = Blueprint('range_explorer', __name__)

_admin_required = require_permission('can_access_admin_tools')

# Verbose engine position names → compact poker labels.
_POSITION_MAP = {
    'under_the_gun': 'UTG',
    'middle_position_1': 'MP',
    'middle_position_2': 'MP2',
    'middle_position_3': 'MP3',
    'cutoff': 'CO',
    'button': 'BTN',
    'small_blind_player': 'SB',
    'big_blind_player': 'BB',
}
_POSITION_ORDER = ['UTG', 'MP', 'MP2', 'MP3', 'CO', 'BTN', 'SB', 'BB']

_VPIP_ACTIONS = {'call', 'raise', 'all_in'}
# 'human' last: it's excluded from the default "All AI" scope and only shown
# when explicitly selected (humans carry no archetype/strategy signal).
_CONTROLLERS = ['hybrid', 'tiered', 'chaos', 'human']
# Players below this many decisions in the current cut are dropped from the
# by-player ranking — a 1/1 = "100% VPIP" entry is noise, not a read.
_MIN_PLAYER_N = 10
# Stable archetype ordering (loosest-intent first-ish); unknowns appended.
_ARCHETYPE_ORDER = ['tag', 'lag', 'calling_station', 'rock', 'nit', 'maniac']
# Game provenance buckets (see `_build_decisions`).
_SOURCE_ORDER = ['human', 'experiment', 'other']
# Game format, inferred from the game_id prefix: cash-mode/sim ids start with
# 'cash-'/'sim_'; everything else (tracked tournaments + old unprefixed WTA
# SNGs) is a tournament. No game_id rename needed — classified at query time.
_MODE_ORDER = ['tournament', 'cash']

# Strength tiers, strongest→weakest. `player_hand_tier` is a stable function of
# the canonical hand, so we build a canon→tier lookup from the data and use it
# to class *every* decision (backfilling rows whose own tier column is null).
_TIER_ORDER = ['premium', 'strong', 'playable', 'marginal', 'trash']

_RANKS = 'AKQJT98765432'

# Fixed strongest→weakest order of all 169 starting hands, precomputed once via
# eval7 (heads-up all-in equity vs a random hand, 12k iters/hand). Used as the
# column axis for the per-player heatmap matrix so every row lines up by rank.
_HAND_STRENGTH_ORDER = [
    'AA',
    'KK',
    'QQ',
    'JJ',
    'TT',
    '99',
    '88',
    'AKs',
    '77',
    'AQs',
    'AKo',
    'ATs',
    'AJs',
    'AQo',
    'AJo',
    'KQs',
    'A9s',
    '66',
    'ATo',
    'KJs',
    'A8s',
    'KTs',
    'KQo',
    'A9o',
    'KJo',
    'A7s',
    '55',
    'QJs',
    'K9s',
    'A8o',
    'A5s',
    'K8s',
    'A6s',
    'KTo',
    'A7o',
    'A4s',
    'QTs',
    'A3s',
    'QJo',
    'A6o',
    'A2s',
    'Q9s',
    'K9o',
    'A5o',
    'K7s',
    'JTs',
    '44',
    'A4o',
    'K6s',
    'QTo',
    'Q8s',
    'J9s',
    'A3o',
    'K8o',
    'Q9o',
    'K4s',
    'K5s',
    'Q7s',
    'K7o',
    'A2o',
    'JTo',
    'K6o',
    'Q8o',
    'T9s',
    'K3s',
    'J8s',
    'Q6s',
    'J9o',
    '33',
    'Q5s',
    'K5o',
    'K2s',
    'T8s',
    'J7s',
    '98s',
    'K4o',
    'Q4s',
    'T9o',
    'J8o',
    'K3o',
    'Q3s',
    'K2o',
    'T7s',
    '22',
    'Q5o',
    'Q7o',
    'Q2s',
    'J6s',
    'Q6o',
    'J7o',
    'J5s',
    'T8o',
    '97s',
    'Q4o',
    'T6s',
    '98o',
    'J4s',
    'T7o',
    '96s',
    'Q2o',
    'J2s',
    'J3s',
    '87s',
    'J6o',
    'T5s',
    'Q3o',
    'T4s',
    '97o',
    '86s',
    'J4o',
    'J3o',
    'J5o',
    'T3s',
    'T6o',
    'J2o',
    '95s',
    '76s',
    '96o',
    '87o',
    '85s',
    '75s',
    'T2s',
    '94s',
    'T5o',
    'T4o',
    '65s',
    '74s',
    '93s',
    '86o',
    '84s',
    '95o',
    '92s',
    '54s',
    'T3o',
    '76o',
    '64s',
    '85o',
    'T2o',
    '82s',
    '94o',
    '65o',
    '75o',
    '83s',
    '93o',
    '73s',
    '63s',
    '84o',
    '92o',
    '53s',
    '54o',
    '64o',
    '52s',
    '43s',
    '74o',
    '62s',
    '72s',
    '83o',
    '73o',
    '42s',
    '82o',
    '63o',
    '32s',
    '43o',
    '53o',
    '72o',
    '52o',
    '62o',
    '42o',
    '32o',
]


def _is_valid_canon(canon: str | None) -> bool:
    """True for one of the 169 canonical starting hands (e.g. AA, AKs, 72o)."""
    if not canon:
        return False
    if len(canon) == 2:
        return canon[0] in _RANKS and canon[0] == canon[1]
    if len(canon) == 3:
        return canon[0] in _RANKS and canon[1] in _RANKS and canon[2] in ('s', 'o')
    return False


def _open_ro() -> sqlite3.Connection:
    """Read-only connection to the live persistence DB (WAL-safe)."""
    return sqlite3.connect(f'file:{extensions.persistence_db_path}?mode=ro', uri=True)


def _load_persona_names(conn: sqlite3.Connection) -> set[str]:
    """Names of known AI personas — used to separate bots from the human."""
    try:
        return {r[0] for r in conn.execute('SELECT name FROM personalities')}
    except sqlite3.Error:
        return set()


def _load_game_owners(conn: sqlite3.Connection) -> dict[str, str]:
    """game_id → owner_id, for classifying game provenance (experiment vs not)."""
    try:
        return {
            r[0]: r[1]
            for r in conn.execute('SELECT game_id, owner_id FROM games')
            if r[1] is not None
        }
    except sqlite3.Error:
        return {}


def _load_llm_player_names(conn: sqlite3.Connection) -> set[str]:
    """Player names that ever made an LLM call (per ``api_usage``).

    Catches AI run under a display name that differs from its persona row
    (e.g. games used "Daniel Negreanu" while the persona is "Negreanu"), and
    any AI whose decision rows lack a capture/snapshot link (old v1.0 data).
    """
    try:
        return {
            r[0]
            for r in conn.execute(
                'SELECT DISTINCT player_name FROM api_usage WHERE player_name IS NOT NULL'
            )
        }
    except sqlite3.Error:
        return set()


def _build_decisions(conn: sqlite3.Connection, phase: str):
    """Pull + dedup decisions for a phase into one record per decision.

    Returns a list of dicts: player, canon, pos, archetype, controller, vpip.
    """
    personas = _load_persona_names(conn)
    llm_players = _load_llm_player_names(conn)
    owner_by_game = _load_game_owners(conn)
    rows = conn.execute(
        """
        SELECT game_id, player_name, hand_number, player_hand_canonical,
               player_position, action_taken,
               (strategy_pipeline_snapshot_json IS NOT NULL) AS has_snap,
               (capture_id IS NOT NULL) AS has_cap,
               json_extract(strategy_pipeline_snapshot_json,
                            '$.deviation_profile_name') AS archetype,
               player_hand_tier
        FROM player_decision_analysis
        WHERE phase = ?
        """,
        (phase,),
    ).fetchall()

    # canon→tier lookup, built from any row that carries a tier (it's stable
    # per canonical hand), so null-tier rows still get classed.
    tier_by_canon: dict[str, str] = {}
    for r in rows:
        canon, tier = r[3], r[9]
        if tier and canon and canon not in tier_by_canon:
            tier_by_canon[canon] = tier

    # Players that ever carried an AI signal anywhere — so a player whose
    # *this* decision lacks one (old v1.0 rows) still counts as AI.
    signaled: set[str] = {r[1] for r in rows if r[6] or r[7]}
    ai_names = personas | llm_players | signaled

    # Collapse duplicate log rows for the same decision.
    agg: dict[tuple, dict] = {}
    for game_id, player, hand, canon, pos, act, has_snap, has_cap, arch, _tier in rows:
        key = (game_id, player, hand)
        rec = agg.get(key)
        if rec is None:
            agg[key] = {
                'game': game_id,
                'player': player,
                'canon': canon,
                'pos': _POSITION_MAP.get(pos, pos),
                'snap': bool(has_snap),
                'cap': bool(has_cap),
                'arch': arch,
                # VPIP if *any* logged action for this decision was voluntary.
                'vpip': act in _VPIP_ACTIONS,
            }
        else:
            rec['snap'] = rec['snap'] or bool(has_snap)
            rec['cap'] = rec['cap'] or bool(has_cap)
            rec['vpip'] = rec['vpip'] or (act in _VPIP_ACTIONS)
            if arch and not rec['arch']:
                rec['arch'] = arch
            if not rec['canon']:
                rec['canon'] = canon

    decisions = []
    for rec in agg.values():
        if rec['snap']:
            controller = 'tiered'
        elif rec['cap']:
            controller = 'hybrid'
        elif rec['player'] in ai_names:
            controller = 'chaos'
        else:
            controller = 'human'
        decisions.append(
            {
                'game': rec['game'],
                'player': rec['player'],
                'canon': rec['canon'],
                'pos': rec['pos'],
                'archetype': rec['arch'],
                'controller': controller,
                'vpip': rec['vpip'],
                'tier': tier_by_canon.get(rec['canon']),
            }
        )

    # Game provenance ("source"): a game a human sat in → 'human'; else owned by
    # an experiment runner → 'experiment'; everything else (orphaned/cash) →
    # 'other'. Human/experiment never overlap. Computed per-game then attached.
    human_games = {d['game'] for d in decisions if d['controller'] == 'human'}
    for d in decisions:
        gid = d['game'] or ''
        owner = owner_by_game.get(d['game']) or ''
        if d['game'] in human_games:
            d['source'] = 'human'
        elif owner.startswith(('experiment', 'exp_')):
            d['source'] = 'experiment'
        else:
            d['source'] = 'other'
        d['mode'] = 'cash' if gid.startswith(('cash-', 'sim_')) else 'tournament'
    return decisions


# Building the decision set rescans every row for the phase (~1.5s on the full
# table), so cache per phase briefly — filter changes, view switches, and React
# StrictMode's double-mount all reuse it instead of re-hammering the live DB.
_DECISION_CACHE: dict[str, tuple[float, list]] = {}
_CACHE_TTL = 60.0


def _get_decisions(phase: str):
    """Cached `_build_decisions` per phase (60s TTL)."""
    now = time.time()
    hit = _DECISION_CACHE.get(phase)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    conn = _open_ro()
    try:
        decisions = _build_decisions(conn, phase)
    finally:
        conn.close()
    _DECISION_CACHE[phase] = (now, decisions)
    return decisions


def _filter_options(decisions, f_controller: str = '') -> dict:
    """Dropdown options. Archetype/position come from the AI population; the
    player list is contextual to the selected bot so picking 'human' surfaces
    human players (and 'All AI' lists every AI persona)."""
    ai = [d for d in decisions if d['controller'] != 'human']
    if f_controller:
        players = sorted({d['player'] for d in decisions if d['controller'] == f_controller})
    else:
        players = sorted({d['player'] for d in ai})
    return {
        'controllers': _CONTROLLERS,
        'archetypes': [a for a in _ARCHETYPE_ORDER if any(d['archetype'] == a for d in ai)],
        'positions': [p for p in _POSITION_ORDER if any(d['pos'] == p for d in ai)],
        'players': players,
        'sources': [s for s in _SOURCE_ORDER if any(d['source'] == s for d in decisions)],
        'modes': [m for m in _MODE_ORDER if any(d['mode'] == m for d in decisions)],
    }


def _scope(
    decisions,
    f_controller: str,
    f_archetype: str,
    f_position: str,
    f_source: str = '',
    f_mode: str = '',
):
    """Apply the controller/archetype/position/source/mode cut; exclude human by default."""

    def keep(d) -> bool:
        if f_controller:
            if d['controller'] != f_controller:
                return False
        elif d['controller'] == 'human':
            return False
        if f_archetype and d['archetype'] != f_archetype:
            return False
        if f_position and d['pos'] != f_position:
            return False
        if f_source and d['source'] != f_source:
            return False
        if f_mode and d['mode'] != f_mode:
            return False
        return True

    return [d for d in decisions if keep(d)]


@range_explorer_bp.route('/api/admin/range-explorer/grid')
@_admin_required
def range_grid():
    """Aggregate VPIP-by-starting-hand for the requested filter cut.

    Query params (all optional): ``controller`` (hybrid/tiered/chaos),
    ``archetype``, ``position`` (UTG/MP/CO/BTN/SB/BB), ``player``,
    ``phase`` (default PRE_FLOP). Empty/omitted ⇒ no filter on that axis.
    The human player is excluded by default unless ``controller=human``.
    """
    phase = (request.args.get('phase') or 'PRE_FLOP').upper()
    f_controller = request.args.get('controller') or ''
    f_archetype = request.args.get('archetype') or ''
    f_position = request.args.get('position') or ''
    f_player = request.args.get('player') or ''
    f_source = request.args.get('source') or ''
    f_mode = request.args.get('mode') or ''

    try:
        decisions = _get_decisions(phase)
    except sqlite3.Error as e:
        logger.error('range-explorer grid query failed: %s', e, exc_info=True)
        return jsonify({'error': 'Range query failed'}), 500

    scoped = _scope(decisions, f_controller, f_archetype, f_position, f_source, f_mode)

    # Per-hand grid (respects the player filter) + per-player table (does not,
    # so the table always shows every player under the current cut).
    grid_acc: dict[str, list[int]] = {}
    player_acc: dict[str, list[int]] = {}
    class_acc: dict[str, list[int]] = {}
    g_vpip = g_total = 0
    for d in scoped:
        if d['player'] in player_acc:
            pa = player_acc[d['player']]
        else:
            pa = player_acc.setdefault(d['player'], [0, 0])
        pa[0] += d['vpip']
        pa[1] += 1
        if f_player and d['player'] != f_player:
            continue
        if not _is_valid_canon(d['canon']):
            continue
        cell = grid_acc.setdefault(d['canon'], [0, 0])
        cell[0] += d['vpip']
        cell[1] += 1
        tier = d['tier'] or 'untiered'
        tcell = class_acc.setdefault(tier, [0, 0])
        tcell[0] += d['vpip']
        tcell[1] += 1
        g_vpip += d['vpip']
        g_total += 1

    grid = [
        {
            'canon': canon,
            'vpip': v,
            'total': t,
            'pct': round(v / t * 100, 1) if t else None,
        }
        for canon, (v, t) in grid_acc.items()
    ]
    # Strength-tier macro row, strongest→weakest; 'untiered' last when present.
    by_class = [
        {
            'tier': tier,
            'vpip': class_acc[tier][0],
            'total': class_acc[tier][1],
            'pct': round(class_acc[tier][0] / class_acc[tier][1] * 100, 1)
            if class_acc[tier][1]
            else None,
        }
        for tier in [*_TIER_ORDER, 'untiered']
        if tier in class_acc
    ]
    by_player = sorted(
        (
            {
                'player': name,
                'vpip': v,
                'total': t,
                'pct': round(v / t * 100, 1) if t else None,
            }
            for name, (v, t) in player_acc.items()
            if t >= _MIN_PLAYER_N
        ),
        key=lambda r: r['pct'] or 0,
        reverse=True,
    )

    return jsonify(
        {
            'filters': _filter_options(decisions, f_controller),
            'applied': {
                'phase': phase,
                'controller': f_controller,
                'archetype': f_archetype,
                'position': f_position,
                'player': f_player,
                'source': f_source,
                'mode': f_mode,
            },
            'grid': grid,
            'by_class': by_class,
            'by_player': by_player,
            'min_player_n': _MIN_PLAYER_N,
            'summary': {
                'vpip': g_vpip,
                'total': g_total,
                'pct': round(g_vpip / g_total * 100, 1) if g_total else None,
                'decisions': len(scoped),
            },
        }
    )


@range_explorer_bp.route('/api/admin/range-explorer/matrix')
@_admin_required
def range_matrix():
    """Per-player VPIP heatmap: one row per player, 169 hands in strength order.

    Same controller/archetype/position cut as the grid (no player filter — every
    qualifying player is a row). Returns the strength-ordered hand axis, an
    aggregate row (all scoped decisions), and per-player sparse cells.
    """
    phase = (request.args.get('phase') or 'PRE_FLOP').upper()
    f_controller = request.args.get('controller') or ''
    f_archetype = request.args.get('archetype') or ''
    f_position = request.args.get('position') or ''
    f_source = request.args.get('source') or ''
    f_mode = request.args.get('mode') or ''

    try:
        decisions = _get_decisions(phase)
    except sqlite3.Error as e:
        logger.error('range-explorer matrix query failed: %s', e, exc_info=True)
        return jsonify({'error': 'Range query failed'}), 500

    scoped = _scope(decisions, f_controller, f_archetype, f_position, f_source, f_mode)

    aggregate: dict[str, list[int]] = {}
    per_player: dict[str, dict] = {}
    for d in scoped:
        if not _is_valid_canon(d['canon']):
            continue
        a = aggregate.setdefault(d['canon'], [0, 0])
        a[0] += d['vpip']
        a[1] += 1
        p = per_player.setdefault(d['player'], {'tot': [0, 0], 'cells': {}})
        p['tot'][0] += d['vpip']
        p['tot'][1] += 1
        c = p['cells'].setdefault(d['canon'], [0, 0])
        c[0] += d['vpip']
        c[1] += 1

    players = sorted(
        (
            {
                'player': name,
                'vpip': info['tot'][0],
                'total': info['tot'][1],
                'pct': round(info['tot'][0] / info['tot'][1] * 100, 1) if info['tot'][1] else None,
                # sparse: canon → [vpip, total]; missing hands render as no-data
                'cells': info['cells'],
            }
            for name, info in per_player.items()
            if info['tot'][1] >= _MIN_PLAYER_N
        ),
        key=lambda r: r['pct'] or 0,
        reverse=True,
    )

    g_vpip = sum(v for v, _ in aggregate.values())
    g_total = sum(t for _, t in aggregate.values())

    return jsonify(
        {
            'order': _HAND_STRENGTH_ORDER,
            'aggregate': aggregate,
            'players': players,
            'min_player_n': _MIN_PLAYER_N,
            'filters': _filter_options(decisions, f_controller),
            'applied': {
                'phase': phase,
                'controller': f_controller,
                'archetype': f_archetype,
                'position': f_position,
                'source': f_source,
                'mode': f_mode,
            },
            'summary': {
                'vpip': g_vpip,
                'total': g_total,
                'pct': round(g_vpip / g_total * 100, 1) if g_total else None,
                'decisions': len(scoped),
            },
        }
    )
