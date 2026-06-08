"""Tests for the background-sim per-archetype stat recorder."""

from __future__ import annotations

import random

import pytest

from cash_mode.archetype_stats import ArchetypeStatRecorder, get_recorder
from cash_mode.controller_cache import LruControllerCache
from cash_mode.full_sim import play_one_hand
from cash_mode.tables import ai_slot, open_slot

# The end-to-end test plays a real hand (cold controller setup) — keep it out of
# the --quick loop.
pytestmark = pytest.mark.simulation


def test_recorder_classifies_3bet_war():
    """A rfi-open → 3-bet → fold-to-3bet sequence tallies to the right buckets."""
    r = ArchetypeStatRecorder('sb')
    r.record_decision('tag', 'T', 'PRE_FLOP', 'rfi', 'raise')  # open
    r.record_decision('lag', 'L', 'PRE_FLOP', 'vs_open', 'raise')  # 3-bet
    r.record_decision('tag', 'T', 'PRE_FLOP', 'vs_3bet', 'fold')  # fold to 3-bet
    r.record_decision('lag', 'L', 'FLOP', '', 'raise')  # postflop agg
    r.end_hand()

    tag = r._totals['tag']
    lag = r._totals['lag']
    # tag opened then folded the 3-bet: voluntary + raiser, faced a vs_3bet fold
    assert tag['hands'] == 1 and tag['vpip'] == 1 and tag['pfr'] == 1
    assert tag['vs_3bet'] == 1 and tag['vs_3bet_fold'] == 1 and tag['vs_3bet_agg'] == 0
    # lag 3-bet facing the open, then bet the flop
    assert lag['vs_open'] == 1 and lag['vs_open_agg'] == 1
    assert lag['postflop_agg'] == 1
    # per-hand booleans roll up once each
    assert lag['hands'] == 1 and lag['vpip'] == 1


def test_recorder_no_archetype_is_noop():
    r = ArchetypeStatRecorder('sb')
    r.record_decision(None, 'X', 'PRE_FLOP', 'rfi', 'raise')
    r.end_hand()
    assert not r._totals


def test_end_to_end_recording_via_play_one_hand():
    """Running a real sim hand populates the per-sandbox recorder."""
    sandbox = 'test-archetype-stats-e2e'
    seats = [ai_slot(n, 5000) for n in ('Napoleon', 'Buddha', 'Bob Ross', 'Shakespeare')]
    while len(seats) < 6:
        seats.append(open_slot())

    play_one_hand(
        seats,
        big_blind=100,
        rng=random.Random(7),
        sandbox_id=sandbox,
        name_for=lambda pid: pid,
        controller_cache=LruControllerCache(max_size=10),
    )

    rec = get_recorder(sandbox)
    assert rec is not None
    # At least one archetype recorded at least one hand + preflop decision.
    assert rec._totals, 'recorder captured nothing from a real hand'
    assert sum(c.get('hands', 0) for c in rec._totals.values()) >= 1
    assert sum(c.get('pf_decisions', 0) for c in rec._totals.values()) >= 1
