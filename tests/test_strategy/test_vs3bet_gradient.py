"""Locks the vs_3bet charts as a polarized hand-strength gradient.

The `vs_3bet` section was a coarse stub (5 distinct distributions / 169 hands)
where 159 hands shared one `{fold:0.75, call:0.15, raise_2.2x:0.10}` blob — so
every hand, including offsuit trash, 4-bet ~10% facing a 3-bet. The regen
(`build_vs3bet_defense.py`) makes it a real gradient and POLARIZES the 4-bet:
value hands + suited blocker bluffs carry the 4-bet; offsuit non-value hands get
call/fold only (no raise key) so no archetype or distortion can 4-bet offsuit
trash. See docs/technical/ARCHETYPE_SHAPING_FINDINGS.md § Finding 1a.
"""

import glob
import json
import os

import pytest

# NOTE: validates the COMMITTED chart JSON (incl. depth/archetype derivatives).
# The per-node generator's own output is unit-tested in test_build_vs3bet.py; the
# old global-generator internals (build_vs3bet_distributions / hand_distribution)
# were removed in the §2 refactor, so the tests that poked them are gone.

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'poker',
    'strategy',
    'data',
)

# 100bb 6-max charts carry the full gradient. Depth charts (50/25bb) apply their
# own commit transform on top, so the gradient-shape asserts target 100bb only.
_CHARTS_100BB = sorted(glob.glob(os.path.join(_DATA_DIR, 'preflop_100bb_6max*.json')))
# Every chart (incl. depth) for the no-offsuit-4bet invariant.
_ALL_CHARTS = sorted(
    glob.glob(os.path.join(_DATA_DIR, 'preflop_*6max*.json'))
    + glob.glob(os.path.join(_DATA_DIR, 'preflop_[0-9]*bb_6max.json'))
)

# Offsuit junk that must NEVER carry a 4-bet (the stub gave each one ~10%).
_OFFSUIT_JUNK = ['72o', 'T8o', 'J9o', 'K9o', 'Q9o', '96o', '83o', '94o', 'J8o']


def _vs3bet_first_node(path):
    nodes = json.load(open(path)).get('vs_3bet', {})
    return nodes[next(iter(nodes))] if nodes else {}


@pytest.mark.parametrize('path', _CHARTS_100BB, ids=lambda p: os.path.basename(p))
def test_vs3bet_varies_by_position(path):
    """Not the stub: the per-node generator differentiates by position, so the 15
    vs_3bet nodes are NOT one range pasted everywhere. (The old stub was all-15
    identical; the within-node shape is now bimodal tiers, so distinctness lives
    across nodes, not within one.)"""
    nodes = json.load(open(path)).get('vs_3bet', {})
    assert nodes, f"{path} has no vs_3bet"
    node_sigs = {json.dumps(n, sort_keys=True) for n in nodes.values()}
    assert len(node_sigs) >= 4, (
        f"{os.path.basename(path)} vs_3bet is ~position-invariant "
        f"({len(node_sigs)} distinct nodes / {len(nodes)})"
    )


@pytest.mark.parametrize('path', _ALL_CHARTS, ids=lambda p: os.path.basename(p))
def test_offsuit_junk_never_4bets(path):
    """Offsuit trash must have no `raise_2.2x` (4-bet) mass in ANY chart — the
    polarization invariant that survives the archetype + depth transforms."""
    hands = _vs3bet_first_node(path)
    for t in _OFFSUIT_JUNK:
        if t in hands:
            assert (
                hands[t].get('raise_2.2x', 0) == 0
            ), f"{os.path.basename(path)} 4-bets offsuit trash {t}: {hands[t]}"


@pytest.mark.parametrize('path', _CHARTS_100BB, ids=lambda p: os.path.basename(p))
def test_premiums_4bet_facing_a_3bet(path):
    """AA carries 4-bet mass facing a 3-bet across every archetype (the station
    damps it hardest but still > 0)."""
    hands = _vs3bet_first_node(path)
    assert (
        hands.get('AA', {}).get('raise_2.2x', 0) > 0
    ), f"{os.path.basename(path)} AA={hands.get('AA')}"
