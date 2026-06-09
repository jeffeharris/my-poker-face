"""Admin archetype-review routes — actual behavioral stats vs target ranges.

Read-only aggregation over ``player_decision_analysis``, scoped to the tiered
archetypes (``deviation_profile_name``). Answers: "is each archetype actually
behaving like its label — and inside its target band?" so AI opponents can be
shaped into reliable, readable reads.

Stats are derived directly from the decision log:

* **VPIP / PFR** — per preflop hand-instance (``game, player, hand``):
  VPIP = any voluntary preflop action (call/raise/all_in); PFR = any
  preflop raise/all_in.
* **3-bet %** — raise/all_in at a ``vs_open`` ``preflop_node_key`` ÷ decisions
  facing an open. **4-bet %** — same at ``vs_3bet``. **Fold-to-3bet** —
  folds at ``vs_3bet`` ÷ decisions there. (Opportunity-normalized via the
  node key, so they're true frequencies, not raw counts.) 4-bet / fold-to-3bet
  are scored only when the actor was the RFI **opener** facing the 3-bet —
  reconstructed from the rows — so SQUEEZE defence (cold-call then face a 3-bet)
  doesn't contaminate them; the ``vs_3bet`` node alone is raise-count-only.
* **AF** — postflop (bet+raise+all_in) ÷ postflop calls.
* **All-in %** — hand-instances with any all_in ÷ total hand-instances.

Dedup: the analyzer double-logs some decisions, so each
``(game, player, hand, phase, node_key, community_cards, action)`` is counted
once.

Targets + scoring live in ``poker.archetype_targets``.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections import defaultdict

from flask import Blueprint, jsonify, request

from poker.archetype_targets import (
    ARCHETYPE_TARGETS,
    PRODUCTION_ARCHETYPES,
    STAT_LABELS,
    get_targets,
    score_stat,
)
from poker.authorization import require_permission

from .. import extensions

logger = logging.getLogger(__name__)

archetype_review_bp = Blueprint('archetype_review', __name__)

_admin_required = require_permission('can_access_admin_tools')

_VOLUNTARY = {'call', 'raise', 'all_in'}
_AGGRESSIVE = {'raise', 'all_in'}
_POSTFLOP_PHASES = {'FLOP', 'TURN', 'RIVER'}

_SUMMARY_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 60.0


def _open_ro() -> sqlite3.Connection:
    """Read-only connection to the live DB (safe on the WAL file)."""
    return sqlite3.connect(f'file:{extensions.persistence_db_path}?mode=ro', uri=True)


def _mode_clause(mode: str) -> str:
    """SQL fragment selecting the game-mode scope. Defaults to cash."""
    if mode == 'tournament':
        return "game_id LIKE 'tourney-%'"
    if mode == 'all':
        return '1=1'
    # cash (default) — include sim_ cash games alongside live cash-
    return "(game_id LIKE 'cash-%' OR game_id LIKE 'sim\\_%' ESCAPE '\\')"


def _aggregate(conn: sqlite3.Connection, mode: str) -> dict:
    """Compute per-archetype behavioral stats for the given game mode."""
    rows = conn.execute(
        f"""
        SELECT game_id, player_name, hand_number, phase, action_taken,
               COALESCE(preflop_node_key, '') AS node_key,
               COALESCE(community_cards, '') AS board,
               json_extract(strategy_pipeline_snapshot_json,
                            '$.deviation_profile_name') AS archetype
        FROM player_decision_analysis
        WHERE strategy_pipeline_snapshot_json IS NOT NULL
          AND {_mode_clause(mode)}
        """
    ).fetchall()

    # Per-archetype accumulators.
    def _new_acc():
        return {
            'hands': set(),  # (game,player,hand)
            'pf_hands': set(),  # preflop hand-instances
            'vpip_hands': set(),
            'pfr_hands': set(),
            'allin_hands': set(),
            'vs_open': 0,
            'vs_open_agg': 0,
            'vs_3bet': 0,
            'vs_3bet_agg': 0,
            'vs_3bet_fold': 0,
            'pf_agg': 0,
            'pf_call': 0,  # postflop aggressive / calls
        }

    acc: dict[str, dict] = defaultdict(_new_acc)
    seen: set[tuple] = set()

    # Pre-pass: who was the RFI opener in each (game, hand)? fourbet /
    # fold_to_3bet are scored only when the actor at a vs_3bet node WAS the
    # opener (facing a 3-bet as the raiser). A vs_3bet node reached as a
    # cold-caller is SQUEEZE defence — a different stat that folds ~100% and
    # otherwise crushes fold_to_3bet for the wide-flatting archetypes. The
    # preflop_node_key is the strategy node (can't be repurposed), so we
    # reconstruct opener-ness from the same rows. See ARCHETYPE_SHAPING_HANDOFF.
    rfi_raisers: set[tuple] = set()
    for game_id, player, hand, phase, action, node_key, _board, _arch in rows:
        if phase == 'PRE_FLOP' and action in _AGGRESSIVE and node_key.split('|', 1)[0] == 'rfi':
            rfi_raisers.add((game_id, player, hand))

    for game_id, player, hand, phase, action, node_key, board, archetype in rows:
        arch = archetype or 'unknown'
        dedup_key = (game_id, player, hand, phase, node_key, board, action)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        a = acc[arch]
        hand_key = (game_id, player, hand)
        a['hands'].add(hand_key)
        if action == 'all_in':
            a['allin_hands'].add(hand_key)

        if phase == 'PRE_FLOP':
            a['pf_hands'].add(hand_key)
            if action in _VOLUNTARY:
                a['vpip_hands'].add(hand_key)
            if action in _AGGRESSIVE:
                a['pfr_hands'].add(hand_key)
            node = node_key.split('|', 1)[0]
            if node == 'vs_open':
                a['vs_open'] += 1
                if action in _AGGRESSIVE:
                    a['vs_open_agg'] += 1
            elif node == 'vs_3bet' and (game_id, player, hand) in rfi_raisers:
                a['vs_3bet'] += 1
                if action in _AGGRESSIVE:
                    a['vs_3bet_agg'] += 1
                elif action == 'fold':
                    a['vs_3bet_fold'] += 1
        elif phase in _POSTFLOP_PHASES:
            if action in _AGGRESSIVE:
                a['pf_agg'] += 1
            elif action == 'call':
                a['pf_call'] += 1

    def _pct(num: int, den: int):
        return round(100.0 * num / den, 1) if den else None

    per_arch: dict = {}
    for arch, a in acc.items():
        n_hands = len(a['hands'])
        n_pf = len(a['pf_hands'])
        pf_call = a['pf_call']
        af = round(a['pf_agg'] / pf_call, 2) if pf_call else (None if a['pf_agg'] == 0 else 99.0)
        per_arch[arch] = {
            'hands': n_hands,
            'stats': {
                'vpip': (_pct(len(a['vpip_hands']), n_pf), n_pf),
                'pfr': (_pct(len(a['pfr_hands']), n_pf), n_pf),
                'threebet': (_pct(a['vs_open_agg'], a['vs_open']), a['vs_open']),
                'fourbet': (_pct(a['vs_3bet_agg'], a['vs_3bet']), a['vs_3bet']),
                'fold_to_3bet': (_pct(a['vs_3bet_fold'], a['vs_3bet']), a['vs_3bet']),
                'af': (af, pf_call + a['pf_agg']),
                'all_in': (_pct(len(a['allin_hands']), n_hands), n_hands),
            },
        }
    return _build_payload(per_arch, mode=mode, source='live', total_decisions=len(seen))


def _aggregate_sim() -> dict:
    """Per-archetype stats from the background-sim counters (AI-only).

    Reads `archetype_stat_counts` (summed across sandboxes) via the repo and
    converts the raw tallies into the same scored shape as the live path.
    """
    from poker.repositories.archetype_stat_repository import ArchetypeStatRepository

    rows = ArchetypeStatRepository(extensions.persistence_db_path).get_stats()

    def _pct(num: int, den: int):
        return round(100.0 * num / den, 1) if den else None

    per_arch: dict = {}
    total = 0
    for r in rows:
        hands = r['hands']
        total += r['pf_decisions']
        pf_call = r['postflop_call']
        pf_agg = r['postflop_agg']
        af = round(pf_agg / pf_call, 2) if pf_call else (None if pf_agg == 0 else 99.0)
        per_arch[r['archetype']] = {
            'hands': hands,
            'stats': {
                'vpip': (_pct(r['vpip'], hands), hands),
                'pfr': (_pct(r['pfr'], hands), hands),
                'threebet': (_pct(r['vs_open_agg'], r['vs_open']), r['vs_open']),
                'fourbet': (_pct(r['vs_3bet_agg'], r['vs_3bet']), r['vs_3bet']),
                'fold_to_3bet': (_pct(r['vs_3bet_fold'], r['vs_3bet']), r['vs_3bet']),
                'af': (af, pf_call + pf_agg),
                'all_in': (_pct(r['allin_hands'], hands), hands),
            },
        }
    return _build_payload(per_arch, mode='sim', source='sim', total_decisions=total)


def _build_payload(per_arch: dict, *, mode: str, source: str, total_decisions: int) -> dict:
    """Score a {archetype: {hands, stats:{stat:(actual,sample)}}} map vs targets
    and assemble the response. Shared by the live and sim aggregators."""
    targets = get_targets(_load_override())
    results = []
    order = list(PRODUCTION_ARCHETYPES) + [k for k in per_arch if k not in PRODUCTION_ARCHETYPES]
    for arch in order:
        entry = per_arch.get(arch)
        if entry is None and arch not in PRODUCTION_ARCHETYPES:
            continue
        entry = entry or {'hands': 0, 'stats': {s: (None, 0) for s in STAT_LABELS}}
        arch_targets = targets.get(arch, {})
        stat_out = {}
        for stat in STAT_LABELS:
            actual, sample = entry['stats'].get(stat, (None, 0))
            band = arch_targets.get(stat)
            stat_out[stat] = {
                'actual': actual,
                'sample': sample,
                'target': list(band) if band else None,
                'status': score_stat(actual, band, sample) if band else 'no_target',
            }
        results.append(
            {
                'archetype': arch,
                'is_production': arch in PRODUCTION_ARCHETYPES,
                'hands': entry['hands'],
                'stats': stat_out,
            }
        )
    return {
        'mode': mode,
        'source': source,
        'stat_order': list(STAT_LABELS.keys()),
        'stat_labels': STAT_LABELS,
        'archetypes': results,
        'total_decisions': total_decisions,
    }


def _load_override() -> str | None:
    """Read the ARCHETYPE_TARGET_OVERRIDES app setting, if present."""
    try:
        from poker.repositories.settings_repository import SettingsRepository

        repo = SettingsRepository(extensions.persistence_db_path)
        return repo.get_setting('ARCHETYPE_TARGET_OVERRIDES')
    except Exception:  # settings table/repo optional — defaults are fine
        return None


@archetype_review_bp.route('/api/admin/archetype-review/summary')
@_admin_required
def archetype_review_summary():
    """Per-archetype actual-vs-target behavioral stats.

    Query params:
      ``source`` — ``live`` (human-present games, default) or ``sim``
        (background AI-vs-AI counters; the human is NOT in these).
      ``mode`` — ``cash`` (default), ``tournament``, ``all``. Live-only;
        the sim source is cash by construction.
    """
    source = (request.args.get('source') or 'live').lower()
    if source not in ('live', 'sim'):
        source = 'live'
    mode = (request.args.get('mode') or 'cash').lower()
    if mode not in ('cash', 'tournament', 'all'):
        mode = 'cash'

    cache_key = f'{source}:{mode}'
    now = time.time()
    hit = _SUMMARY_CACHE.get(cache_key)
    if hit and now - hit[0] < _CACHE_TTL:
        return jsonify(hit[1])

    try:
        if source == 'sim':
            payload = _aggregate_sim()
        else:
            conn = _open_ro()
            try:
                payload = _aggregate(conn, mode)
            finally:
                conn.close()
    except sqlite3.Error as e:
        logger.error('archetype-review summary query failed: %s', e, exc_info=True)
        return jsonify({'error': 'Archetype review query failed'}), 500

    _SUMMARY_CACHE[cache_key] = (now, payload)
    return jsonify(payload)


@archetype_review_bp.route('/api/admin/archetype-review/targets')
@_admin_required
def archetype_review_targets():
    """Return the default target table (for reference / future editing UI)."""
    return jsonify(
        {
            'defaults': {
                a: {s: list(b) for s, b in stats.items()} for a, stats in ARCHETYPE_TARGETS.items()
            },
            'stat_labels': STAT_LABELS,
        }
    )
