"""Live-path aggregation for the Archetype Review route.

Focused on the opener-conditioning of fourbet / fold_to_3bet: a vs_3bet node
reached as a cold-caller (SQUEEZE defence) must not contaminate the stat — only
the RFI opener facing a 3-bet counts. The route reconstructs opener-ness from the
decision rows (preflop_node_key is the strategy node and can't be repurposed).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

import flask_app.routes.archetype_review_routes as rr

pytestmark = pytest.mark.flask


def _snap(arch: str) -> str:
    return json.dumps({'deviation_profile_name': arch})


def _conn_with_rows(rows):
    conn = sqlite3.connect(':memory:')
    conn.execute(
        """CREATE TABLE player_decision_analysis (
            game_id TEXT, player_name TEXT, hand_number INTEGER, phase TEXT,
            action_taken TEXT, preflop_node_key TEXT, community_cards TEXT,
            strategy_pipeline_snapshot_json TEXT)"""
    )
    conn.executemany('INSERT INTO player_decision_analysis VALUES (?,?,?,?,?,?,?,?)', rows)
    return conn


def _stat(payload, archetype, stat):
    entry = next(r for r in payload['archetypes'] if r['archetype'] == archetype)
    return entry['stats'][stat]


def test_squeeze_defence_excluded_from_fold_to_3bet():
    rows = [
        # Hand 1: station COLD-CALLS an open, then folds to a squeeze 3-bet.
        ('cash-1', 'S', 1, 'PRE_FLOP', 'call', 'vs_open|BB|CO|T9s', '', _snap('calling_station')),
        ('cash-1', 'S', 1, 'PRE_FLOP', 'fold', 'vs_3bet|BB|CO|T9s', '', _snap('calling_station')),
        # Hand 2: station OPENS (rfi raise), then folds to a 3-bet → real fold_to_3bet.
        ('cash-2', 'S', 2, 'PRE_FLOP', 'raise', 'rfi|CO||AJs', '', _snap('calling_station')),
        ('cash-2', 'S', 2, 'PRE_FLOP', 'fold', 'vs_3bet|CO|BB|AJs', '', _snap('calling_station')),
    ]
    payload = rr._aggregate(_conn_with_rows(rows), 'cash')
    f = _stat(payload, 'calling_station', 'fold_to_3bet')
    # Only the opener-vs-3bet fold counts: 1 fold / 1 vs_3bet decision = 100%.
    assert f['sample'] == 1
    assert f['actual'] == 100.0
    # The cold-call at vs_open is still counted in the 3-bet denominator.
    assert _stat(payload, 'calling_station', 'threebet')['sample'] == 1


def test_opener_fourbet_counts():
    rows = [
        # tag opens, faces a 3-bet, 4-bets (raise at vs_3bet as opener).
        ('cash-3', 'T', 1, 'PRE_FLOP', 'raise', 'rfi|CO||AA', '', _snap('tag')),
        ('cash-3', 'T', 1, 'PRE_FLOP', 'raise', 'vs_3bet|CO|BB|AA', '', _snap('tag')),
    ]
    payload = rr._aggregate(_conn_with_rows(rows), 'cash')
    fourbet = _stat(payload, 'tag', 'fourbet')
    assert fourbet['sample'] == 1
    assert fourbet['actual'] == 100.0
    # And it is NOT a fold_to_3bet.
    assert _stat(payload, 'tag', 'fold_to_3bet')['actual'] == 0.0
