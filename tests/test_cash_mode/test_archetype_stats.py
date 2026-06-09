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
    assert lag['flop_agg'] == 1
    # per-hand booleans roll up once each
    assert lag['hands'] == 1 and lag['vpip'] == 1


def test_weak_fish_archetype_key_not_misattributed():
    """Regression: the recorder must identify weak_fish via the deviation
    profile, NOT anchor-based archetype_name (which collapses weak_fish's
    loose-passive anchors to calling_station). full_sim uses
    `_table_archetype_key()` for exactly this reason."""
    from poker.strategy.deviation_profiles import DEVIATION_PROFILES
    from poker.tiered_bot_controller import TieredBotController

    ctrl = TieredBotController.__new__(TieredBotController)
    ctrl._deviation_profile = DEVIATION_PROFILES['weak_fish']
    assert ctrl._table_archetype_key() == 'weak_fish'


def test_recorder_excludes_squeeze_defence_from_vs_3bet():
    """A vs_3bet decision by a NON-opener (squeeze defence: cold-call an open,
    then face a 3-bet) must NOT count toward fourbet / fold_to_3bet — only the
    RFI opener facing a 3-bet does. Without this gate the wide-flatting
    archetypes' fold_to_3bet is crushed by ~100%-folding squeeze spots."""
    r = ArchetypeStatRecorder('sb')
    # Station cold-called an open (vs_open call), then folds to a squeeze 3-bet.
    r.record_decision('calling_station', 'S', 'PRE_FLOP', 'vs_open', 'call')
    r.record_decision('calling_station', 'S', 'PRE_FLOP', 'vs_3bet', 'fold', is_opener=False)
    # A real opener-vs-3bet fold for contrast (counts).
    r.record_decision('tag', 'T', 'PRE_FLOP', 'rfi', 'raise')
    r.record_decision('tag', 'T', 'PRE_FLOP', 'vs_3bet', 'fold', is_opener=True)
    r.end_hand()

    station = r._totals['calling_station']
    # The squeeze fold is excluded — not a fold_to_3bet.
    assert station['vs_3bet'] == 0 and station['vs_3bet_fold'] == 0
    # …but the cold-call at vs_open is still recorded (3-bet stat denominator).
    assert station['vs_open'] == 1 and station['vs_open_agg'] == 0
    # The opener's fold to a 3-bet DOES count.
    tag = r._totals['tag']
    assert tag['vs_3bet'] == 1 and tag['vs_3bet_fold'] == 1


def test_recorder_no_archetype_is_noop():
    r = ArchetypeStatRecorder('sb')
    r.record_decision(None, 'X', 'PRE_FLOP', 'rfi', 'raise')
    r.end_hand()
    assert not r._totals


def test_per_street_postflop_dispatch():
    """Postflop agg/call/fold are tallied per street — the single source of truth
    for AF/AFq (all three share one accumulation timeline). The legacy aggregate
    postflop_agg/postflop_call counters are no longer written."""
    r = ArchetypeStatRecorder('sb')
    r.record_decision('lag', 'L', 'FLOP', '', 'raise')
    r.record_decision('lag', 'L', 'TURN', '', 'call')
    r.record_decision('lag', 'L', 'RIVER', '', 'fold')
    r.end_hand()

    lag = r._totals['lag']
    # Per-street split.
    assert lag['flop_agg'] == 1 and lag['turn_call'] == 1 and lag['river_fold'] == 1
    assert lag['flop_call'] == 0 and lag['flop_fold'] == 0
    # The retired aggregate counters are not written.
    assert 'postflop_agg' not in lag and 'postflop_call' not in lag


def test_saw_flop_boolean_rolls_up():
    """A player with ≥1 postflop decision sets saw_flop; a fold-preflop player
    does not."""
    r = ArchetypeStatRecorder('sb')
    r.record_decision('tag', 'T', 'PRE_FLOP', 'rfi', 'raise')
    r.record_decision('tag', 'T', 'FLOP', '', 'call')  # saw the flop
    r.record_decision('nit', 'N', 'PRE_FLOP', 'vs_open', 'fold')  # never saw flop
    r.end_hand()

    assert r._totals['tag']['saw_flop'] == 1
    assert r._totals['nit'].get('saw_flop', 0) == 0


def test_showdown_and_won_rollup():
    """end_hand(showdown_players, winner_names) increments showdowns for the
    flop-seers who actually went to showdown and showdowns_won for those in
    winner_names. A non-flop-seer is ignored even if it (somehow) appears in
    winners."""
    r = ArchetypeStatRecorder('sb')
    r.record_decision('tag', 'T', 'FLOP', '', 'call')  # saw flop, will win
    r.record_decision('lag', 'L', 'FLOP', '', 'call')  # saw flop, will lose
    r.record_decision('nit', 'N', 'PRE_FLOP', 'rfi', 'fold')  # no flop
    r.end_hand(showdown_players={'T', 'L'}, winner_names={'T', 'N'})

    assert r._totals['tag']['showdowns'] == 1 and r._totals['tag']['showdowns_won'] == 1
    assert r._totals['lag']['showdowns'] == 1 and r._totals['lag']['showdowns_won'] == 0
    # N never saw the flop → no showdown credit despite being a winner.
    assert r._totals['nit'].get('showdowns', 0) == 0


def test_flop_seer_who_folded_postflop_gets_no_showdown_credit():
    """A player who saw the flop but FOLDED before showdown is not in
    showdown_players, so it gets saw_flop but no showdown credit — even though
    the hand showdown'd. This is the WTSD = went-to-showdown fix (backlog #11):
    counting flop-seers who later folded inflates WTSD and blurs the
    station-vs-nit spread."""
    r = ArchetypeStatRecorder('sb')
    r.record_decision('tag', 'T', 'FLOP', '', 'call')  # saw flop, went to SD
    r.record_decision('nit', 'F', 'FLOP', '', 'fold')  # saw flop, folded postflop
    # Hand showdown'd between T and someone else; F is NOT among showdown_players.
    r.end_hand(showdown_players={'T'}, winner_names={'T'})

    assert r._totals['tag']['showdowns'] == 1 and r._totals['tag']['showdowns_won'] == 1
    assert r._totals['nit']['saw_flop'] == 1
    assert r._totals['nit'].get('showdowns', 0) == 0


def test_no_showdown_does_not_count_showdowns():
    """When the hand did not reach showdown (empty showdown_players), flop-seers
    get saw_flop but no showdown."""
    r = ArchetypeStatRecorder('sb')
    r.record_decision('tag', 'T', 'FLOP', '', 'raise')
    r.end_hand(showdown_players=set(), winner_names={'T'})
    assert r._totals['tag']['saw_flop'] == 1
    assert r._totals['tag'].get('showdowns', 0) == 0


def test_end_hand_back_compat_default_args():
    """end_hand() with no outcome args still rolls up the hand (no showdown
    credit) — back-compat for callers without hand-outcome context."""
    r = ArchetypeStatRecorder('sb')
    r.record_decision('tag', 'T', 'PRE_FLOP', 'rfi', 'raise')
    r.record_decision('tag', 'T', 'FLOP', '', 'raise')
    r.end_hand()  # no kwargs
    tag = r._totals['tag']
    assert tag['hands'] == 1 and tag['saw_flop'] == 1
    assert tag.get('showdowns', 0) == 0


def test_cbet_opportunity_made_rollup():
    """The preflop aggressor's first-in flop bet rolls up cbet_opportunity +
    cbet_made; a check (non-aggressive) on the opportunity counts the opportunity
    only."""
    r = ArchetypeStatRecorder('sb')
    # Aggressor c-bets the flop.
    r.record_decision('tag', 'T', 'FLOP', '', 'raise', is_cbet_opportunity=True, is_cbet=True)
    # Different hand: aggressor has the chance but checks (call stands in for a
    # non-aggressive continue) → opportunity, no c-bet.
    r.record_decision('tag', 'T', 'FLOP', '', 'call', is_cbet_opportunity=True, is_cbet=False)
    r.end_hand()

    tag = r._totals['tag']
    assert tag['cbet_opportunity'] == 2
    assert tag['cbet_made'] == 1


def test_fold_to_cbet_rollup():
    """Facing a flop c-bet tallies cbet_faced; a fold there bumps fold_to_cbet,
    a call does not."""
    r = ArchetypeStatRecorder('sb')
    r.record_decision('calling_station', 'S', 'FLOP', '', 'fold', is_facing_cbet=True)
    r.record_decision('lag', 'L', 'FLOP', '', 'call', is_facing_cbet=True)
    r.end_hand()

    station = r._totals['calling_station']
    assert station['cbet_faced'] == 1 and station['fold_to_cbet'] == 1
    lag = r._totals['lag']
    assert lag['cbet_faced'] == 1 and lag.get('fold_to_cbet', 0) == 0


def test_cbet_flags_default_off_back_compat():
    """A postflop decision without the c-bet kwargs touches no c-bet counters
    (back-compat for callers that don't pass them)."""
    r = ArchetypeStatRecorder('sb')
    r.record_decision('tag', 'T', 'FLOP', '', 'raise')
    r.end_hand()
    tag = r._totals['tag']
    assert tag.get('cbet_opportunity', 0) == 0
    assert tag.get('cbet_made', 0) == 0
    assert tag.get('cbet_faced', 0) == 0
    assert tag.get('fold_to_cbet', 0) == 0
    # …but the per-street postflop aggression still moved (proves it ran).
    assert tag['flop_agg'] == 1


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
