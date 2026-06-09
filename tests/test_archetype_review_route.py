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


def _conn_with_rows(rows, hand_history=None):
    conn = sqlite3.connect(':memory:')
    conn.execute(
        """CREATE TABLE player_decision_analysis (
            game_id TEXT, player_name TEXT, hand_number INTEGER, phase TEXT,
            action_taken TEXT, preflop_node_key TEXT, community_cards TEXT,
            strategy_pipeline_snapshot_json TEXT)"""
    )
    conn.executemany('INSERT INTO player_decision_analysis VALUES (?,?,?,?,?,?,?,?)', rows)
    if hand_history is not None:
        conn.execute(
            """CREATE TABLE hand_history (
                game_id TEXT, hand_number INTEGER, showdown BOOLEAN, winners_json TEXT)"""
        )
        conn.executemany('INSERT INTO hand_history VALUES (?,?,?,?)', hand_history)
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


def test_afq_counts_folds_in_denominator():
    """AFq = (bet+raise)/(bet+raise+call+fold) — postflop folds are in the
    denominator (unlike AF, which ignores them). 1 raise + 1 call + 2 folds =>
    AFq 1/4 = 25%, but AF = 1/1 = 1.0."""
    rows = [
        ('cash-1', 'M', 1, 'FLOP', 'raise', '', 'Ah Kd Qs', _snap('maniac')),
        ('cash-1', 'M', 1, 'TURN', 'call', '', 'Ah Kd Qs 2c', _snap('maniac')),
        ('cash-2', 'M', 2, 'FLOP', 'fold', '', 'Ah Kd Qs', _snap('maniac')),
        ('cash-3', 'M', 3, 'RIVER', 'fold', '', 'Ah Kd Qs 2c 7h', _snap('maniac')),
    ]
    payload = rr._aggregate(_conn_with_rows(rows), 'cash')
    afq = _stat(payload, 'maniac', 'afq')
    assert afq['sample'] == 4  # agg + call + fold + fold
    assert afq['actual'] == 25.0
    # AF (folds excluded) is unchanged: 1 agg / 1 call = 1.0.
    assert _stat(payload, 'maniac', 'af')['actual'] == 1.0


def test_per_street_af_split():
    """Per-street AF splits postflop aggression by flop/turn/river. flop = 2 agg
    / 1 call = 2.0; turn = 1 agg / 1 call = 1.0; river has no calls → all-agg
    sentinel 99.0. No target band (no_target)."""
    rows = [
        ('cash-1', 'L', 1, 'FLOP', 'raise', '', 'b', _snap('lag')),
        ('cash-1', 'L', 1, 'FLOP', 'raise', '', 'b2', _snap('lag')),
        ('cash-1', 'L', 1, 'FLOP', 'call', '', 'b3', _snap('lag')),
        ('cash-1', 'L', 1, 'TURN', 'raise', '', 'bt', _snap('lag')),
        ('cash-1', 'L', 1, 'TURN', 'call', '', 'bt2', _snap('lag')),
        ('cash-1', 'L', 1, 'RIVER', 'raise', '', 'br', _snap('lag')),
    ]
    payload = rr._aggregate(_conn_with_rows(rows), 'cash')
    assert _stat(payload, 'lag', 'flop_af')['actual'] == 2.0
    assert _stat(payload, 'lag', 'turn_af')['actual'] == 1.0
    assert _stat(payload, 'lag', 'river_af')['actual'] == 99.0
    # Per-street AF ships with no target band.
    assert _stat(payload, 'lag', 'flop_af')['status'] == 'no_target'


def test_wtsd_wsd_joined_from_hand_history():
    """WTSD = went-to-showdown / saw-flop; W$SD = won / showdown. Player T sees
    the flop in 2 hands; hand 1 showdowns and T wins, hand 2 doesn't showdown.
    WTSD = 1/2 = 50%; W$SD = 1/1 = 100%."""
    rows = [
        ('cash-1', 'T', 1, 'FLOP', 'call', '', 'Ah Kd Qs', _snap('tag')),
        ('cash-1', 'T', 2, 'FLOP', 'raise', '', 'Ah Kd Qs', _snap('tag')),
    ]
    hh = [
        ('cash-1', 1, 1, '[{"name": "T"}]'),  # showdown, T won
        ('cash-1', 2, 0, '[{"name": "T"}]'),  # no showdown
    ]
    payload = rr._aggregate(_conn_with_rows(rows, hand_history=hh), 'cash')
    wtsd = _stat(payload, 'tag', 'wtsd')
    assert wtsd['sample'] == 2  # saw flop in 2 hands
    assert wtsd['actual'] == 50.0
    wsd = _stat(payload, 'tag', 'wsd')
    assert wsd['sample'] == 1  # reached 1 showdown
    assert wsd['actual'] == 100.0


def test_wsd_loss_at_showdown():
    """A flop-seeing player who reaches showdown but is NOT in winners → W$SD 0."""
    rows = [
        ('cash-1', 'F', 1, 'FLOP', 'call', '', 'Ah Kd Qs', _snap('calling_station')),
    ]
    hh = [('cash-1', 1, 1, '[{"name": "SomeoneElse"}]')]  # showdown, F lost
    payload = rr._aggregate(_conn_with_rows(rows, hand_history=hh), 'cash')
    assert _stat(payload, 'calling_station', 'wtsd')['actual'] == 100.0
    assert _stat(payload, 'calling_station', 'wsd')['actual'] == 0.0


def test_cbet_made_by_preflop_aggressor():
    """The preflop aggressor (last preflop raiser) betting the flop first-in is a
    c-bet: 1 cbet_made / 1 cbet_opportunity = 100%."""
    rows = [
        ('cash-1', 'A', 1, 'PRE_FLOP', 'raise', 'rfi|CO||AKs', '', _snap('tag')),
        ('cash-1', 'A', 1, 'FLOP', 'raise', '', 'Ah Kd 2c', _snap('tag')),
    ]
    payload = rr._aggregate(_conn_with_rows(rows), 'cash')
    cbet = _stat(payload, 'tag', 'cbet')
    assert cbet['sample'] == 1
    assert cbet['actual'] == 100.0


def test_cbet_opportunity_not_taken():
    """Aggressor checks/calls the flop first-in → opportunity counted, no c-bet
    (0%)."""
    rows = [
        ('cash-1', 'A', 1, 'PRE_FLOP', 'raise', 'rfi|CO||AKs', '', _snap('rock')),
        ('cash-1', 'A', 1, 'FLOP', 'check', '', 'Ah Kd 2c', _snap('rock')),
    ]
    payload = rr._aggregate(_conn_with_rows(rows), 'cash')
    cbet = _stat(payload, 'rock', 'cbet')
    assert cbet['sample'] == 1
    assert cbet['actual'] == 0.0


def test_donk_bet_is_not_a_cbet():
    """A NON-aggressor betting the flop first (a donk) means the aggressor acting
    after is NOT c-betting (there was a prior flop bet). The aggressor's later
    bet is not a c-bet opportunity; the donk bettor isn't the aggressor."""
    rows = [
        # B opens preflop (aggressor); D cold-calls.
        ('cash-1', 'B', 1, 'PRE_FLOP', 'raise', 'rfi|CO||AKs', '', _snap('tag')),
        ('cash-1', 'D', 1, 'PRE_FLOP', 'call', 'vs_open|BB|CO|T9s', '', _snap('calling_station')),
        # FLOP: D donk-bets FIRST, then B raises.
        ('cash-1', 'D', 1, 'FLOP', 'raise', '', 'Ah Kd 2c', _snap('calling_station')),
        ('cash-1', 'B', 1, 'FLOP', 'raise', '', 'Ah Kd 2c', _snap('tag')),
    ]
    payload = rr._aggregate(_conn_with_rows(rows), 'cash')
    # B (aggressor) acted on an ALREADY-bet flop → no c-bet opportunity for B.
    assert _stat(payload, 'tag', 'cbet')['sample'] == 0
    # D is not the aggressor → no c-bet opportunity for D either.
    assert _stat(payload, 'calling_station', 'cbet')['sample'] == 0


def test_fold_to_cbet_live():
    """A non-aggressor folding to the aggressor's flop c-bet → fold_to_cbet 100%.
    The aggressor itself never 'faces' a c-bet."""
    rows = [
        ('cash-1', 'A', 1, 'PRE_FLOP', 'raise', 'rfi|CO||AKs', '', _snap('tag')),
        ('cash-1', 'V', 1, 'PRE_FLOP', 'call', 'vs_open|BB|CO|T9s', '', _snap('calling_station')),
        # FLOP: aggressor c-bets, victim folds to it.
        ('cash-1', 'A', 1, 'FLOP', 'raise', '', 'Ah Kd 2c', _snap('tag')),
        ('cash-1', 'V', 1, 'FLOP', 'fold', '', 'Ah Kd 2c', _snap('calling_station')),
    ]
    payload = rr._aggregate(_conn_with_rows(rows), 'cash')
    ftc = _stat(payload, 'calling_station', 'fold_to_cbet')
    assert ftc['sample'] == 1
    assert ftc['actual'] == 100.0
    # The c-better never faces a c-bet.
    assert _stat(payload, 'tag', 'fold_to_cbet')['sample'] == 0


def test_cbet_graceful_with_no_flop_aggressor_row():
    """Robust to gaps: a flop where the aggressor has NO logged row (non-tiered /
    human seat) — no c-bet opportunity is fabricated and fold-to-c-bet is only
    counted once an aggressor flop-bet row exists (here it doesn't), so a victim's
    flop fold is NOT mis-counted as fold-to-c-bet."""
    rows = [
        ('cash-1', 'A', 1, 'PRE_FLOP', 'raise', 'rfi|CO||AKs', '', _snap('tag')),
        ('cash-1', 'V', 1, 'PRE_FLOP', 'call', 'vs_open|BB|CO|T9s', '', _snap('calling_station')),
        # FLOP: aggressor A has NO row (not logged); only the victim acts.
        ('cash-1', 'V', 1, 'FLOP', 'fold', '', 'Ah Kd 2c', _snap('calling_station')),
    ]
    payload = rr._aggregate(_conn_with_rows(rows), 'cash')
    # No c-bet was ever recorded, so the victim never 'faced' one.
    assert _stat(payload, 'calling_station', 'fold_to_cbet')['sample'] == 0
    assert _stat(payload, 'calling_station', 'cbet')['sample'] == 0
    assert _stat(payload, 'tag', 'cbet')['sample'] == 0


def test_wtsd_graceful_without_hand_history():
    """No hand_history table (or no matching rows) → WTSD/W$SD report no_data
    rather than erroring (the saw-flop hands have no outcome to join)."""
    rows = [
        ('cash-1', 'T', 1, 'FLOP', 'call', '', 'Ah Kd Qs', _snap('tag')),
    ]
    # No hand_history table at all.
    payload = rr._aggregate(_conn_with_rows(rows), 'cash')
    wtsd = _stat(payload, 'tag', 'wtsd')
    # saw-flop denominator exists, but no showdown rows → 0 showdowns / 1 saw-flop.
    assert wtsd['sample'] == 1
    assert wtsd['actual'] == 0.0
    # W$SD has a zero denominator (no showdowns) → no_data.
    assert _stat(payload, 'tag', 'wsd')['actual'] is None
