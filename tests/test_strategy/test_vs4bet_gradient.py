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

from poker.strategy.data.build_vs4bet_defense import (
    build_vs4bet_distributions,
    hand_distribution,
)

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
def test_vs4bet_is_a_real_gradient(path):
    """The stub had exactly 3 distinct distributions across 169 hands."""
    hands = _vs4bet_first_node(path)
    assert hands, f"{path} has no vs_4bet"
    distinct = {tuple(sorted(d.items())) for d in hands.values()}
    assert len(distinct) >= 5, (
        f"{os.path.basename(path)} vs_4bet looks degenerate "
        f"({len(distinct)} distinct distributions — the stub had 3)"
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


def test_generator_is_deterministic_and_grades_by_strength():
    """The generator output is reproducible and orders by hand strength."""
    a = build_vs4bet_distributions()
    b = build_vs4bet_distributions()
    assert a == b
    # AA jams more than QQ, QQ continues more than the marginal pairs.
    assert a['AA']['jam'] > a['QQ'].get('jam', 0)
    assert a['72o'] == {'fold': 1.0}
    # Ax-suited bluff-jams are present (polarized range, not pure value).
    assert a['A5s'].get('jam', 0) > 0


def test_hand_distribution_tiers_sum_to_one():
    for eq in (0.9, 0.45, 0.36, 0.30):
        d = hand_distribution('AA', eq)  # hand arg only matters for bluff set
        assert abs(sum(d.values()) - 1.0) < 1e-9
