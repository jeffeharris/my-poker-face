"""Locks the vs_4bet charts as a real hand-strength gradient.

Regression guard for the 3-bucket stub that caused trash 4-bet shoves (prod
"47o jams into a 4-bet all-in"; see docs/technical/ARCHETYPE_SHAPING_FINDINGS.md
§ Finding 1a). The stub gave 165/169 hands one shared `{fold,call,jam}` blob
with 13% jam; these tests assert the chart now grades by hand strength and that
trash is *pure-fold* (so the archetype/depth transforms can't re-loosen it).
"""

import glob
import json
import os

import pytest

# NOTE: validates the COMMITTED chart JSON (incl. depth/archetype derivatives).
# The per-node generator's own output is unit-tested in test_build_vs4bet.py; the
# old global-generator internals (build_vs4bet_distributions / hand_distribution)
# were removed in the §4 refactor.

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'poker',
    'strategy',
    'data',
)

# Every production 6-max / depth chart that carries a vs_4bet section.
_CHARTS = sorted(glob.glob(os.path.join(_DATA_DIR, 'preflop_*6max*.json')))

# Hands that must NEVER continue facing a 4-bet — the trash that was shoving.
_TRASH = ['72o', '98o', '74o', 'KJo', 'T8o', 'J9o', 'Q9o', '64s']


def _vs4bet_first_node(path):
    nodes = json.load(open(path)).get('vs_4bet', {})
    return nodes[next(iter(nodes))] if nodes else {}


@pytest.mark.parametrize('path', _CHARTS, ids=lambda p: os.path.basename(p))
def test_vs4bet_varies_by_position(path):
    """Not the stub: the per-node generator differentiates by position, so the 15
    vs_4bet nodes are NOT one range pasted everywhere. (Within-node shape is now
    bimodal jam/call/fold tiers, so distinctness lives across nodes.)"""
    nodes = json.load(open(path)).get('vs_4bet', {})
    assert nodes, f"{path} has no vs_4bet"
    node_sigs = {json.dumps(n, sort_keys=True) for n in nodes.values()}
    assert len(node_sigs) >= 4, (
        f"{os.path.basename(path)} vs_4bet is ~position-invariant "
        f"({len(node_sigs)} distinct nodes / {len(nodes)})"
    )


@pytest.mark.parametrize('path', _CHARTS, ids=lambda p: os.path.basename(p))
def test_trash_is_pure_fold_facing_4bet(path):
    """Trash must be exactly {fold: 1.0} so the archetype/depth transforms'
    pure-fold guard keeps it folded — no archetype may shove it."""
    hands = _vs4bet_first_node(path)
    for t in _TRASH:
        if t in hands:
            assert hands[t] == {
                'fold': 1.0
            }, f"{os.path.basename(path)} jams/calls trash {t}: {hands[t]}"


@pytest.mark.parametrize('path', _CHARTS, ids=lambda p: os.path.basename(p))
def test_premiums_get_it_in_facing_4bet(path):
    """AA continues heavily (jam+call) facing a 4-bet across every archetype."""
    hands = _vs4bet_first_node(path)
    aa = hands.get('AA', {})
    assert aa.get('jam', 0) + aa.get('call', 0) >= 0.8, f"{os.path.basename(path)} AA={aa}"
