"""Unit tests for build_vs4bet_defense.py — the per-node vs_4bet regen.

Locks:
  * fold-to-4bet stays under the MDF ceiling (the call-backfill meets the anchor);
  * the LOAD-BEARING pure-fold junk floor — trash must stay {fold: 1.0}, NOT a thin
    call (unlike vs_3bet), or archetype/depth widening reopens the trash-jam bug;
  * premiums jam, and the result is not a copied range.
"""

import json

import pytest

from poker.strategy import lints
from poker.strategy.data import (
    build_vs3bet_defense as b3,
    build_vs4bet_defense as b4,
    build_vs_open as bvo,
)


@pytest.fixture(scope="module")
def built():
    """Regenerate vs_open → vs_3bet → vs_4bet in memory (the full villain chain)."""
    with open(b4._BASE) as f:
        chart = json.load(f)
    with open(b4._MATRIX) as f:
        matrix = json.load(f)["matrix"]
    rfi = chart["rfi"]
    vs_open = {}
    for nn in chart["vs_open"]:
        op, d, thr, vs, pool, m = bvo._node_plan(nn, chart["vs_open"])
        vs_open[nn] = bvo.build_node(op, rfi, matrix, d, thr, vs, pool, m)[0]
    vs_3bet = {
        nn: b3.build_node(*nn.split("_vs_"), rfi, vs_open, matrix) for nn in chart["vs_3bet"]
    }
    vs_4bet = {
        nn: b4.build_node(*nn.split("_vs_"), vs_open, vs_3bet, matrix) for nn in chart["vs_4bet"]
    }
    return vs_open, vs_4bet


def test_fold_to_4bet_under_ceiling(built):
    vs_open, nodes = built
    for nn, node in nodes.items():
        hero, villain = nn.split("_vs_")
        f4b = b4._fold_to_4bet(hero, villain, node, vs_open)
        assert (
            f4b <= lints.F4B_CEILING + 1e-9
        ), f"{nn}: fold-to-4bet {100*f4b:.1f}% > ceiling {100*lints.F4B_CEILING:.0f}%"


def test_offsuit_trash_stays_pure_fold(built):
    """Load-bearing: trash must be {fold: 1.0} so archetype/depth transforms skip it.
    A thin call here would let widening reopen the 47o-jams-into-a-4-bet bug."""
    _, nodes = built
    for nn, node in nodes.items():
        for h in ["72o", "83o", "94o", "T8o", "J9o", "96o"]:
            assert node[h] == {"fold": 1.0}, f"{nn}/{h} = {node[h]} (must be pure fold)"


def test_premiums_jam(built):
    _, nodes = built
    for nn, node in nodes.items():
        assert node["AA"].get("jam", 0.0) > 0, f"{nn}: AA={node['AA']}"


def test_generated_vs4bet_is_not_a_copied_range(built):
    _, nodes = built
    assert lints.lint_anti_clone({"vs_4bet": nodes}, branches=("vs_4bet",)) == []
