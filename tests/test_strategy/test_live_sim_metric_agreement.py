"""Live-vs-sim AGREEMENT test for the Archetype Review metrics.

The Archetype Review tool computes the SAME per-archetype behavioral stats two
independent ways:

* LIVE  (`archetype_review_routes._aggregate`) — reconstructs everything from
  ``player_decision_analysis`` rows (+ a ``hand_history`` join): the preflop
  node/opener gate, the c-bet aggressor, the postflop-fold WTSD exclusion, the
  showdown/winners.
* SIM   (`archetype_review_routes._aggregate_sim`) — reads the
  ``archetype_stat_counts`` counters produced by
  ``cash_mode.archetype_stats.ArchetypeStatRecorder`` (record_decision flags +
  end_hand(showdown_players, winner_names)), as populated inline by
  ``cash_mode.full_sim._run_hand``.

These two were built to mirror each other but had never been asserted to agree on
the same play. This test feeds the SAME ground-truth decision stream into BOTH
representations and asserts identical stat values per archetype.

ANTI-CIRCULARITY (the whole point)
----------------------------------
The shared spec (``HandSpec`` below) is at the RAW decision-stream + hand-outcome
level: who did what on each street, who reached showdown, who won. That is the
ground truth — neither path's derived inputs.

Each path's INPUT is then derived the way PRODUCTION derives it, INDEPENDENTLY:

* LIVE input — we materialize ``player_decision_analysis`` rows (with the real
  ``scenario|position|opener|hand`` ``preflop_node_key`` format) + ``hand_history``
  rows, then run ``_aggregate``. The live aggregator reconstructs opener-ness,
  the c-bet aggressor and the WTSD postflop-fold exclusion FROM THOSE ROWS — we do
  not feed it those derived facts.
* SIM input — we replay the same spec through ``ArchetypeStatRecorder`` and compute
  the recorder flags (node, is_opener, cbet_*, showdown_players) with a faithful
  copy of the ``full_sim._run_hand`` derivation (``_sim_replay`` below). We mirror
  that algorithm (raise-count node classification, rfi_opener/last_pf_raiser
  tracking, flop_bet/cbet state, live-at-hand-end showdown set) rather than
  hand-waving the flags to match the live side — copying full_sim's derivation is
  faithful; copying the live aggregator's would be circular.

If a stat DISAGREES between the two paths, that is a real production bug to
surface — not something to paper over in the spec.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pytest

import flask_app.routes.archetype_review_routes as rr
from cash_mode.archetype_stats import ArchetypeStatRecorder
from poker.repositories import create_repos
from poker.repositories.archetype_stat_repository import ArchetypeStatRepository

pytestmark = [pytest.mark.flask, pytest.mark.integration]


# --------------------------------------------------------------------------- #
# Ground-truth spec types (the SHARED source of truth — raw stream + outcome)  #
# --------------------------------------------------------------------------- #

# Streets in dealing order; PRE_FLOP is the preflop street.
_STREET_ORDER = ['PRE_FLOP', 'FLOP', 'TURN', 'RIVER']
_POSTFLOP = {'FLOP', 'TURN', 'RIVER'}
_AGGRESSIVE = {'raise', 'all_in'}


@dataclass
class Action:
    """One decision in the ground-truth stream: player + action on a street."""

    player: str
    action: str  # fold / check / call / raise / all_in


@dataclass
class HandSpec:
    """A synthetic hand: archetype map, the per-street action streams (in the
    order they happened), the players who actually reached showdown, and the
    winners. This is the GROUND TRUTH both paths derive from."""

    game_id: str
    hand_number: int
    archetypes: Dict[str, str]  # player -> archetype
    # street -> ordered list of Actions on that street
    streets: Dict[str, List[Action]] = field(default_factory=dict)
    # The starting hole-card label per player (only matters for live dedup
    # uniqueness within the node key; the cards themselves are not semantically
    # used by either aggregator beyond dedup).
    hole: Dict[str, str] = field(default_factory=dict)
    # Players who went to showdown (still live with >=2 remaining at hand end).
    showdown_players: set = field(default_factory=set)
    # Players who won chips.
    winners: set = field(default_factory=set)
    # Board label per street (FLOP/TURN/RIVER), used to vary community_cards so
    # the live dedup key is unique per street; defaults are fine.
    board: Dict[str, str] = field(
        default_factory=lambda: {
            'FLOP': 'Ah Kd 2c',
            'TURN': 'Ah Kd 2c 7s',
            'RIVER': 'Ah Kd 2c 7s 9h',
        }
    )


# --------------------------------------------------------------------------- #
# LIVE input materialization — build player_decision_analysis + hand_history   #
# the way the live route reads them. We emit the REAL                          #
# scenario|position|opener|hand preflop_node_key; the route reconstructs       #
# opener/cbet/WTSD from these rows itself.                                     #
# --------------------------------------------------------------------------- #


def _live_node_key(scenario: str, hole: str) -> str:
    """Real preflop_node_key format: ``scenario|position|opener|hand``. Only the
    scenario prefix (rfi/vs_open/vs_3bet/vs_4bet) and the trailing hand are read
    by the aggregator; position/opener fields are present but ignored, so we use
    plausible-but-unread placeholders."""
    return f'{scenario}|CO|BB|{hole}'


def _preflop_scenario_stream(spec: HandSpec) -> List[Tuple[Action, str]]:
    """Walk the preflop street and tag each action with the scenario it FACED,
    using running raise depth — the SAME classification full_sim uses (0 raises =
    rfi, 1 = vs_open, 2 = vs_3bet, 3+ = vs_4bet), bumped AFTER the acting row.

    This is ground-truth bookkeeping the spec author owns; it is the basis for
    BOTH the live node-key prefix and the sim node. (Production derives the same
    classification from raise depth on each side — that shared definition is not
    circular; what must stay independent is the OPENER gate, which each path
    reconstructs differently.)"""
    out: List[Tuple[Action, str]] = []
    raises = 0
    for act in spec.streets.get('PRE_FLOP', []):
        scenario = (
            'rfi'
            if raises == 0
            else 'vs_open'
            if raises == 1
            else 'vs_3bet'
            if raises == 2
            else 'vs_4bet'
        )
        out.append((act, scenario))
        if act.action in _AGGRESSIVE:
            raises += 1
    return out


def _snap(arch: str) -> str:
    return json.dumps({'deviation_profile_name': arch})


def _live_rows_and_hh(specs: List[HandSpec]):
    """Materialize (player_decision_analysis rows, hand_history rows) for the
    live aggregator from the ground-truth specs."""
    rows = []
    hh = []
    for spec in specs:
        # Preflop rows — node key carries the scenario derived from raise depth.
        for act, scenario in _preflop_scenario_stream(spec):
            hole = spec.hole.get(act.player, 'AKs')
            rows.append(
                (
                    spec.game_id,
                    act.player,
                    spec.hand_number,
                    'PRE_FLOP',
                    act.action,
                    _live_node_key(scenario, hole),
                    '',
                    _snap(spec.archetypes[act.player]),
                )
            )
        # Postflop rows — node key empty, community_cards varies per street.
        for street in ('FLOP', 'TURN', 'RIVER'):
            for act in spec.streets.get(street, []):
                rows.append(
                    (
                        spec.game_id,
                        act.player,
                        spec.hand_number,
                        street,
                        act.action,
                        '',
                        spec.board[street],
                        _snap(spec.archetypes[act.player]),
                    )
                )
        # hand_history outcome row: showdown iff anyone reached showdown.
        was_sd = 1 if spec.showdown_players else 0
        winners_json = json.dumps([{'name': w} for w in sorted(spec.winners)])
        hh.append((spec.game_id, spec.hand_number, was_sd, winners_json))
    return rows, hh


def _live_payload(specs: List[HandSpec]):
    rows, hh = _live_rows_and_hh(specs)
    # Reuse the existing route-test fixture connection builder shape.
    import sqlite3

    conn = sqlite3.connect(':memory:')
    conn.execute(
        """CREATE TABLE player_decision_analysis (
            game_id TEXT, player_name TEXT, hand_number INTEGER, phase TEXT,
            action_taken TEXT, preflop_node_key TEXT, community_cards TEXT,
            strategy_pipeline_snapshot_json TEXT)"""
    )
    conn.executemany('INSERT INTO player_decision_analysis VALUES (?,?,?,?,?,?,?,?)', rows)
    conn.execute(
        """CREATE TABLE hand_history (
            game_id TEXT, hand_number INTEGER, showdown BOOLEAN, winners_json TEXT)"""
    )
    conn.executemany('INSERT INTO hand_history VALUES (?,?,?,?)', hh)
    try:
        return rr._aggregate(conn, 'cash')
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# SIM input replay — faithful copy of full_sim._run_hand's flag derivation.    #
# We do NOT reuse the live aggregator's reconstruction; we mirror the sim's    #
# in-process tracking so the two derivations stay independent.                 #
# --------------------------------------------------------------------------- #


def _sim_replay(recorder: ArchetypeStatRecorder, spec: HandSpec) -> None:
    """Replay one ground-truth hand through the ArchetypeStatRecorder exactly the
    way ``full_sim._run_hand`` does: node from running raise depth, opener =
    first preflop raiser, c-bet aggressor = LAST preflop raiser, flop_bet/cbet
    state advanced AFTER recording, showdown set = the spec's showdown_players.

    Lines marked (full_sim) replicate the corresponding logic in
    cash_mode/full_sim.py::_run_hand."""
    preflop_raises = 0  # (full_sim)
    rfi_opener_name: Optional[str] = None  # (full_sim)
    last_pf_raiser_name: Optional[str] = None  # (full_sim)
    flop_bet_made = False  # (full_sim)
    flop_cbet_made = False  # (full_sim)

    for street in _STREET_ORDER:
        for act in spec.streets.get(street, []):
            actor = act.player
            action = act.action
            archetype = spec.archetypes[actor]
            decision_phase = street

            if decision_phase == 'PRE_FLOP':
                node = (  # (full_sim)
                    'rfi'
                    if preflop_raises == 0
                    else 'vs_open'
                    if preflop_raises == 1
                    else 'vs_3bet'
                    if preflop_raises == 2
                    else 'vs_4bet'
                )
            else:
                node = ''

            is_aggr = action in _AGGRESSIVE  # (full_sim)
            is_cbet_opportunity = (  # (full_sim)
                decision_phase == 'FLOP' and actor == last_pf_raiser_name and not flop_bet_made
            )
            is_cbet = is_cbet_opportunity and is_aggr  # (full_sim)
            is_facing_cbet = (  # (full_sim)
                decision_phase == 'FLOP' and flop_cbet_made and actor != last_pf_raiser_name
            )

            recorder.record_decision(
                archetype,
                actor,
                decision_phase,
                node,
                action,
                is_opener=(actor == rfi_opener_name),  # (full_sim)
                is_cbet_opportunity=is_cbet_opportunity,
                is_cbet=is_cbet,
                is_facing_cbet=is_facing_cbet,
            )

            if decision_phase == 'PRE_FLOP' and is_aggr:  # (full_sim)
                if preflop_raises == 0 and rfi_opener_name is None:
                    rfi_opener_name = actor
                last_pf_raiser_name = actor
                preflop_raises += 1
            if decision_phase == 'FLOP' and is_aggr:  # (full_sim)
                if is_cbet:
                    flop_cbet_made = True
                flop_bet_made = True

    # end_hand outcome (full_sim derives showdown_players = live players at hand
    # end when >=2 remain; we pass the spec's ground-truth set directly).
    recorder.end_hand(
        db_path=None,
        showdown_players=set(spec.showdown_players),
        winner_names=set(spec.winners),
    )


def _sim_payload(specs: List[HandSpec], db_path: str, monkeypatch):
    """Replay all specs through a recorder, flush to a real archetype_stat_counts
    table, then run the production sim aggregator against it."""
    create_repos(db_path)  # builds schema incl. archetype_stat_counts
    recorder = ArchetypeStatRecorder(sandbox_id='sim_agreement_test')
    for spec in specs:
        _sim_replay(recorder, spec)
    recorder.flush(db_path)

    # _aggregate_sim reads extensions.persistence_db_path via the repository.
    from flask_app import extensions

    monkeypatch.setattr(extensions, 'persistence_db_path', db_path, raising=False)
    return rr._aggregate_sim()


# --------------------------------------------------------------------------- #
# Comparison harness                                                           #
# --------------------------------------------------------------------------- #

# Every banded/derived stat the two paths both produce.
_AGREEMENT_STATS = [
    'vpip',
    'pfr',
    'threebet',
    'fourbet',
    'fold_to_3bet',
    'af',
    'afq',
    'all_in',
    'wtsd',
    'wsd',
    'flop_af',
    'turn_af',
    'river_af',
    'cbet',
    'fold_to_cbet',
]


def _stats_by_arch(payload) -> Dict[str, dict]:
    return {r['archetype']: r['stats'] for r in payload['archetypes']}


def _assert_paths_agree(specs: List[HandSpec], db_path: str, monkeypatch, archetypes):
    """Run both production aggregators on the same spec and assert per-archetype,
    per-stat equality of (actual, sample). Returns the live payload for any extra
    spot assertions the caller wants to make on absolute values."""
    live = _live_payload(specs)
    sim = _sim_payload(specs, db_path, monkeypatch)
    live_by = _stats_by_arch(live)
    sim_by = _stats_by_arch(sim)

    mismatches = []
    for arch in archetypes:
        l_stats = live_by.get(arch, {})
        s_stats = sim_by.get(arch, {})
        for stat in _AGREEMENT_STATS:
            l = l_stats.get(stat, {})
            s = s_stats.get(stat, {})
            l_pair = (l.get('actual'), l.get('sample'))
            s_pair = (s.get('actual'), s.get('sample'))
            if l_pair != s_pair:
                mismatches.append(f'{arch}.{stat}: live={l_pair} sim={s_pair}')
    assert not mismatches, 'LIVE vs SIM disagree:\n  ' + '\n  '.join(mismatches)
    return live


def _live_stat(payload, arch, stat):
    return next(r for r in payload['archetypes'] if r['archetype'] == arch)['stats'][stat]


# --------------------------------------------------------------------------- #
# The five tricky cases that have actually bitten us.                          #
# --------------------------------------------------------------------------- #


def test_case1_squeeze_coldcaller_excluded_from_fold_to_3bet(db_path, monkeypatch):
    """CASE 1 — opener-conditioning / squeeze. A ``vs_3bet`` decision by a
    COLD-CALLER (not the RFI opener) must be excluded from fold_to_3bet/4-bet on
    BOTH paths; only the RFI opener facing the 3-bet counts.

    Hand: O opens (rfi), C cold-calls (vs_open), R squeezes/3-bets (vs_open),
    then O folds to the 3-bet (vs_3bet AS THE OPENER → real fold_to_3bet) and C
    folds to the 3-bet (vs_3bet AS A COLD-CALLER → squeeze defence, EXCLUDED)."""
    spec = HandSpec(
        game_id='cash-c1',
        hand_number=1,
        archetypes={'O': 'tag', 'C': 'calling_station', 'R': 'lag'},
        hole={'O': 'AJs', 'C': 'T9s', 'R': 'AA'},
        streets={
            'PRE_FLOP': [
                Action('O', 'raise'),  # rfi open
                Action('C', 'call'),  # vs_open cold-call
                Action('R', 'raise'),  # vs_open squeeze (3-bet)
                Action('O', 'fold'),  # vs_3bet AS OPENER → counts
                Action('C', 'fold'),  # vs_3bet AS COLD-CALLER → excluded
            ]
        },
        showdown_players=set(),  # everyone folded to R
        winners={'R'},
    )
    live = _assert_paths_agree([spec], db_path, monkeypatch, ['tag', 'calling_station', 'lag'])
    # Absolute sanity: opener O has 1 fold_to_3bet decision; cold-caller C has 0.
    assert _live_stat(live, 'tag', 'fold_to_3bet')['sample'] == 1
    assert _live_stat(live, 'tag', 'fold_to_3bet')['actual'] == 100.0
    assert _live_stat(live, 'calling_station', 'fold_to_3bet')['sample'] == 0
    assert _live_stat(live, 'calling_station', 'fold_to_3bet')['actual'] is None
    # R's squeeze is a 3-bet at a vs_open node.
    assert _live_stat(live, 'lag', 'threebet')['sample'] == 1
    assert _live_stat(live, 'lag', 'threebet')['actual'] == 100.0


def test_case2_wtsd_postflop_fold_not_showdown(db_path, monkeypatch):
    """CASE 2 — WTSD with a postflop fold. A player who saw the flop but FOLDED
    the turn must NOT be counted as went-to-showdown on either path (the bug we
    just fixed); the hand still showdowns among the others.

    F sees the flop (calls) then folds the turn → in saw-flop denom, NOT in the
    showdown numerator. W reaches showdown and wins."""
    spec = HandSpec(
        game_id='cash-c2',
        hand_number=1,
        archetypes={'F': 'calling_station', 'W': 'tag'},
        hole={'F': 'T9s', 'W': 'AKs'},
        streets={
            'PRE_FLOP': [Action('W', 'raise'), Action('F', 'call')],
            'FLOP': [Action('W', 'raise'), Action('F', 'call')],
            'TURN': [Action('W', 'raise'), Action('F', 'fold')],
        },
        showdown_players=set(),  # F folded the turn; W won uncontested at that point
        winners={'W'},
    )
    # NOTE: with F folding the turn and only W left, there is NO showdown (live=2
    # remaining is false at hand end). Both paths should agree WTSD F = 0/1.
    live = _assert_paths_agree([spec], db_path, monkeypatch, ['calling_station', 'tag'])
    assert _live_stat(live, 'calling_station', 'wtsd')['sample'] == 1  # saw flop
    assert _live_stat(live, 'calling_station', 'wtsd')['actual'] == 0.0  # folded before SD


def test_case2b_wtsd_postflop_fold_with_showdown_among_others(db_path, monkeypatch):
    """CASE 2 variant — the hand DOES showdown among OTHER players, but the
    flop-seer who folded the turn is still excluded from WTSD. F folds turn; A and
    B go to showdown. F must be saw-flop=1, showdown=0 on both paths even though
    the hand_history row says showdown=1."""
    spec = HandSpec(
        game_id='cash-c2b',
        hand_number=1,
        archetypes={'F': 'calling_station', 'A': 'tag', 'B': 'lag'},
        hole={'F': 'T9s', 'A': 'AKs', 'B': 'QQ'},
        streets={
            'PRE_FLOP': [Action('A', 'raise'), Action('B', 'call'), Action('F', 'call')],
            'FLOP': [Action('A', 'raise'), Action('B', 'call'), Action('F', 'call')],
            'TURN': [Action('A', 'call'), Action('B', 'call'), Action('F', 'fold')],
            'RIVER': [Action('A', 'call'), Action('B', 'call')],
        },
        showdown_players={'A', 'B'},  # F folded the turn
        winners={'A'},
    )
    live = _assert_paths_agree([spec], db_path, monkeypatch, ['calling_station', 'tag', 'lag'])
    # F saw the flop but folded the turn → excluded from showdown numerator.
    assert _live_stat(live, 'calling_station', 'wtsd')['sample'] == 1
    assert _live_stat(live, 'calling_station', 'wtsd')['actual'] == 0.0
    # A and B reached showdown (saw flop=1, wtsd=100%); A won.
    assert _live_stat(live, 'tag', 'wtsd')['actual'] == 100.0
    assert _live_stat(live, 'tag', 'wsd')['actual'] == 100.0  # A won
    assert _live_stat(live, 'lag', 'wsd')['actual'] == 0.0  # B lost at showdown


def test_case3_cbet_vs_donk_and_3bet_pot_aggressor(db_path, monkeypatch):
    """CASE 3 — c-bet vs donk + last-raiser keying. Three sub-conditions in one
    multi-hand spec, all of which must agree across paths:

    Hand 1: aggressor bets flop first-in → counts as a c-bet.
    Hand 2: a DONK bets the flop first, the aggressor RAISES after → NOT the
            aggressor's c-bet (prior flop bet existed).
    Hand 3: 3-BET POT — the 3-bettor (LAST preflop raiser), not the RFI opener,
            is the c-bet aggressor. The RFI opener betting the flop is a DONK."""
    h1 = HandSpec(
        game_id='cash-c3a',
        hand_number=1,
        archetypes={'AGG': 'tag', 'V': 'calling_station'},
        hole={'AGG': 'AKs', 'V': 'T9s'},
        streets={
            'PRE_FLOP': [Action('AGG', 'raise'), Action('V', 'call')],
            'FLOP': [Action('AGG', 'raise'), Action('V', 'fold')],  # clean c-bet
        },
        showdown_players=set(),
        winners={'AGG'},
    )
    h2 = HandSpec(
        game_id='cash-c3b',
        hand_number=1,
        archetypes={'AGG': 'tag', 'DONK': 'calling_station'},
        hole={'AGG': 'AKs', 'DONK': 'T9s'},
        streets={
            'PRE_FLOP': [Action('AGG', 'raise'), Action('DONK', 'call')],
            # DONK bets first → AGG raising after is NOT a c-bet (prior bet).
            'FLOP': [Action('DONK', 'raise'), Action('AGG', 'raise')],
        },
        showdown_players=set(),
        winners={'AGG'},
    )
    h3 = HandSpec(
        game_id='cash-c3c',
        hand_number=1,
        archetypes={'OPENER': 'lag', 'THREE': 'tag'},
        hole={'OPENER': 'AJs', 'THREE': 'AA'},
        streets={
            # OPENER opens (rfi), THREE 3-bets (vs_open), OPENER calls the 3-bet.
            'PRE_FLOP': [
                Action('OPENER', 'raise'),
                Action('THREE', 'raise'),
                Action('OPENER', 'call'),
            ],
            # FLOP: OPENER bets first — but THREE is the LAST raiser/aggressor, so
            # OPENER's bet is a DONK, not a c-bet. THREE raising after is facing a
            # prior bet → not a c-bet either.
            'FLOP': [Action('OPENER', 'raise'), Action('THREE', 'raise')],
        },
        showdown_players=set(),
        winners={'THREE'},
    )
    live = _assert_paths_agree(
        [h1, h2, h3], db_path, monkeypatch, ['tag', 'calling_station', 'lag']
    )
    # tag is the clean c-better in h1 (1/1). In h2 tag raises a donked flop (not a
    # c-bet opportunity). In h3 tag is the 3-bettor but faces a donk → not a
    # c-bet. So tag: cbet_made 1 / cbet_opportunity 1 = 100%.
    assert _live_stat(live, 'tag', 'cbet')['sample'] == 1
    assert _live_stat(live, 'tag', 'cbet')['actual'] == 100.0
    # The h1 victim folded to a real c-bet → calling_station fold_to_cbet has it.
    ftc = _live_stat(live, 'calling_station', 'fold_to_cbet')
    assert ftc['sample'] == 1
    assert ftc['actual'] == 100.0
    # lag is the RFI opener in h3 who DONK-bet the flop (THREE is the aggressor),
    # so lag has no c-bet opportunity.
    assert _live_stat(live, 'lag', 'cbet')['sample'] == 0


def test_case4_afq_diverges_from_af(db_path, monkeypatch):
    """CASE 4 — AFq vs AF. A postflop mix with folds so AFq (folds in denom) !=
    AF (folds ignored). Both paths must agree on EACH.

    maniac postflop across the 3 hands: 1 agg (h1 flop raise), 3 calls (h1 turn,
    h3 flop+turn), 2 folds (h2 flop, h3 river). AF = 1/3 = 0.33 (folds ignored).
    AFq = 1 / (1+3+2) = 16.7% (folds in the denominator) — a clear divergence."""
    h1 = HandSpec(
        game_id='cash-c4a',
        hand_number=1,
        archetypes={'M': 'maniac', 'X': 'rock'},
        hole={'M': 'AKs', 'X': 'QQ'},
        streets={
            'PRE_FLOP': [Action('M', 'raise'), Action('X', 'call')],
            'FLOP': [Action('M', 'raise'), Action('X', 'call')],  # M agg
            'TURN': [Action('X', 'raise'), Action('M', 'call')],  # M call
        },
        showdown_players=set(),
        winners={'X'},
    )
    h2 = HandSpec(
        game_id='cash-c4b',
        hand_number=1,
        archetypes={'M': 'maniac', 'Y': 'rock'},
        hole={'M': 'A2s', 'Y': 'KK'},
        streets={
            'PRE_FLOP': [Action('Y', 'raise'), Action('M', 'call')],
            'FLOP': [Action('Y', 'raise'), Action('M', 'fold')],  # M fold
        },
        showdown_players=set(),
        winners={'Y'},
    )
    h3 = HandSpec(
        game_id='cash-c4c',
        hand_number=1,
        archetypes={'M': 'maniac', 'Z': 'rock'},
        hole={'M': 'A3s', 'Z': 'AA'},
        streets={
            'PRE_FLOP': [Action('Z', 'raise'), Action('M', 'call')],
            'FLOP': [Action('Z', 'call'), Action('M', 'call')],
            'TURN': [Action('Z', 'call'), Action('M', 'call')],
            'RIVER': [Action('Z', 'raise'), Action('M', 'fold')],  # M fold
        },
        showdown_players=set(),
        winners={'Z'},
    )
    live = _assert_paths_agree([h1, h2, h3], db_path, monkeypatch, ['maniac', 'rock'])
    af = _live_stat(live, 'maniac', 'af')
    afq = _live_stat(live, 'maniac', 'afq')
    assert af['actual'] == 0.33  # 1 agg / 3 calls (folds ignored)
    assert afq['actual'] == 16.7  # 1 agg / (1+3+2) (folds in denom)
    assert af['actual'] != afq['actual']  # the discriminator is exercised


def test_case5_multi_archetype_table(db_path, monkeypatch):
    """CASE 5 — multi-archetype table so per-archetype bucketing is exercised.
    Two 6-handed-ish hands spanning all production archetypes, with a 3-bet pot, a
    c-bet, postflop folds and a showdown. The whole stat surface must agree
    per-archetype across the two paths."""
    h1 = HandSpec(
        game_id='cash-c5a',
        hand_number=1,
        archetypes={
            'N': 'nit',
            'RK': 'rock',
            'TG': 'tag',
            'LG': 'lag',
            'MN': 'maniac',
            'CS': 'calling_station',
        },
        hole={'N': 'AA', 'RK': 'KK', 'TG': 'AKs', 'LG': 'QJs', 'MN': '72o', 'CS': 'T9s'},
        streets={
            'PRE_FLOP': [
                Action('TG', 'raise'),  # rfi open (aggressor candidate)
                Action('MN', 'raise'),  # vs_open 3-bet → becomes last raiser
                Action('CS', 'call'),  # vs_3bet cold-call (squeeze defence flat)
                Action('TG', 'call'),  # vs_3bet as OPENER (calls the 3-bet)
                Action('N', 'fold'),  # vs_3bet cold (excluded from 4bet/f3b)
                Action('LG', 'fold'),
                Action('RK', 'fold'),
            ],
            # MN is the last preflop raiser → the c-bet aggressor.
            'FLOP': [
                Action('MN', 'raise'),  # c-bet
                Action('CS', 'call'),  # faces c-bet, calls
                Action('TG', 'fold'),  # faces c-bet, folds
            ],
            'TURN': [Action('MN', 'raise'), Action('CS', 'call')],
            'RIVER': [Action('MN', 'raise'), Action('CS', 'call')],
        },
        showdown_players={'MN', 'CS'},
        winners={'MN'},
    )
    h2 = HandSpec(
        game_id='cash-c5b',
        hand_number=1,
        archetypes={
            'N': 'nit',
            'RK': 'rock',
            'TG': 'tag',
            'WF': 'weak_fish',
        },
        hole={'N': 'AA', 'RK': 'KK', 'TG': 'AKs', 'WF': 'J5o'},
        streets={
            'PRE_FLOP': [
                Action('RK', 'raise'),  # rfi
                Action('WF', 'call'),  # vs_open flat
                Action('N', 'raise'),  # vs_open 3-bet
                Action('RK', 'fold'),  # vs_3bet as OPENER → fold_to_3bet
                Action('WF', 'fold'),  # vs_3bet cold-caller → excluded
            ],
        },
        showdown_players=set(),
        winners={'N'},
    )
    archs = ['nit', 'rock', 'tag', 'lag', 'maniac', 'calling_station', 'weak_fish']
    live = _assert_paths_agree([h1, h2], db_path, monkeypatch, archs)
    # A few absolute anchors so the agreement isn't vacuously two-zeros.
    assert _live_stat(live, 'maniac', 'cbet')['actual'] == 100.0  # MN c-bet 1/1
    assert _live_stat(live, 'maniac', 'wsd')['actual'] == 100.0  # MN won at SD
    assert _live_stat(live, 'rock', 'fold_to_3bet')['actual'] == 100.0  # opener fold
    assert _live_stat(live, 'weak_fish', 'fold_to_3bet')['sample'] == 0  # cold-caller excl
    assert _live_stat(live, 'tag', 'wtsd')['actual'] == 0.0  # TG folded the flop


# --------------------------------------------------------------------------- #
# Self-test: confirm the harness can DETECT divergence (anti-vacuity).         #
# If we break one path, the agreement assertion must go red.                   #
# --------------------------------------------------------------------------- #


def test_harness_catches_divergence(db_path, monkeypatch):
    """Sanity check that the agreement harness is not vacuous: temporarily break
    the SIM opener gate (count vs_3bet cold-callers too — the exact squeeze bug),
    and confirm the live/sim comparison FAILS. Restores immediately."""
    spec = HandSpec(
        game_id='cash-break',
        hand_number=1,
        archetypes={'O': 'tag', 'C': 'calling_station', 'R': 'lag'},
        hole={'O': 'AJs', 'C': 'T9s', 'R': 'AA'},
        streets={
            'PRE_FLOP': [
                Action('O', 'raise'),
                Action('C', 'call'),
                Action('R', 'raise'),
                Action('O', 'fold'),  # opener fold (real fold_to_3bet)
                Action('C', 'fold'),  # cold-caller fold (must be excluded)
            ]
        },
        showdown_players=set(),
        winners={'R'},
    )

    # Patch the recorder so the opener gate is IGNORED (the squeeze bug): every
    # vs_3bet decision is counted regardless of is_opener. This is exactly the
    # divergence the agreement test exists to catch.
    orig = ArchetypeStatRecorder.record_decision

    def buggy(self, archetype, player, phase, node, action, is_opener=True, **kw):
        return orig(self, archetype, player, phase, node, action, is_opener=True, **kw)

    monkeypatch.setattr(ArchetypeStatRecorder, 'record_decision', buggy)

    with pytest.raises(AssertionError, match='LIVE vs SIM disagree'):
        _assert_paths_agree([spec], db_path, monkeypatch, ['tag', 'calling_station', 'lag'])
