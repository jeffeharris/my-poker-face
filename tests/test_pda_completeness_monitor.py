"""Unit tests for the PDA<->hand_history completeness monitor.

Locks the detector that would have caught the Fast-Forward bug class: a hand
recorded in `hand_history` whose per-decision children are missing from
`player_decision_analysis`."""

import json
import sqlite3

from experiments.pda_completeness_monitor import analyze_completeness


def _db():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE hand_history (game_id TEXT, hand_number INTEGER, actions_json TEXT)")
    c.execute(
        "CREATE TABLE player_decision_analysis "
        "(game_id TEXT, player_name TEXT, hand_number INTEGER, phase TEXT, "
        "preflop_node_key TEXT, community_cards TEXT, action_taken TEXT)"
    )
    return c


def _hh(c, g, h, actions):
    c.execute("INSERT INTO hand_history VALUES (?,?,?)", (g, h, json.dumps(actions)))


def _pda(c, g, p, h, phase, action="check", node="", board=""):
    c.execute(
        "INSERT INTO player_decision_analysis VALUES (?,?,?,?,?,?,?)",
        (g, p, h, phase, node, board, action),
    )


def test_clean_hand_no_gap():
    c = _db()
    _hh(c, "cash-1", 1, [{"player_name": "P", "phase": "FLOP"}])
    _pda(c, "cash-1", "P", 1, "FLOP")
    assert analyze_completeness(c, "cash")["postflop_gap"] == 0


def test_ff_bug_postflop_gap_detected():
    """HH records P acting on the flop, but PDA has only its preflop decision —
    the dropped postflop rows are exactly the FF-bug signature."""
    c = _db()
    _hh(
        c,
        "cash-1",
        1,
        [{"player_name": "P", "phase": "PRE_FLOP"}, {"player_name": "P", "phase": "FLOP"}],
    )
    _pda(c, "cash-1", "P", 1, "PRE_FLOP", "raise")
    r = analyze_completeness(c, "cash")
    assert r["postflop_gap"] == 1
    assert r["postflop_gap_pct"] > 0


def test_dedup_does_not_create_false_gap():
    """Double-logged PDA postflop rows collapse to one logical decision and
    still count as present (no false gap)."""
    c = _db()
    _hh(c, "cash-1", 1, [{"player_name": "P", "phase": "FLOP"}])
    _pda(c, "cash-1", "P", 1, "FLOP", "bet")
    _pda(c, "cash-1", "P", 1, "FLOP", "bet")  # exact duplicate
    assert analyze_completeness(c, "cash")["postflop_gap"] == 0


def test_orphan_hand_counts():
    c = _db()
    _hh(c, "cash-1", 1, [{"player_name": "P", "phase": "FLOP"}])  # outcome, no decisions
    _pda(c, "cash-2", "P", 1, "FLOP")  # decisions, no outcome
    r = analyze_completeness(c, "cash")
    assert r["hh_only_hands"] == 1 and r["pda_only_hands"] == 1
