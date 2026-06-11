"""Unit tests for build_vs3bet_defense.py — the per-node vs_3bet regen.

Locks in the subtle fixes from the §2 build:
  * the MDF floor caps the taper, so fold-to-3bet stays under the IP/OOP ceiling
    even vs a narrow value-heavy 3-bettor (where the raw taper would over-fold);
  * 4-bet bluffs carry fold >= the depth gate, so they fold (not bluff-jam) at 25bb;
  * the per-node generation is not a copied range (cross-opener anti-clone passes).
"""

import json

import pytest

from poker.strategy import lints
from poker.strategy.data import build_vs3bet_defense as b3, build_vs_open as bvo


@pytest.fixture(scope="module")
def built():
    """Regenerate vs_open in memory (the villain model), then all 15 vs_3bet nodes."""
    with open(b3._BASE) as f:
        chart = json.load(f)
    with open(b3._MATRIX) as f:
        matrix = json.load(f)["matrix"]
    rfi = chart["rfi"]
    vs_open = {}
    for nn in chart["vs_open"]:
        opener, d, thr, vs, pool, merged = bvo._node_plan(nn, chart["vs_open"])
        vs_open[nn] = bvo.build_node(opener, rfi, matrix, d, thr, vs, pool, merged)
    nodes = {nn: b3.build_node(*nn.split("_vs_"), rfi, vs_open, matrix) for nn in chart["vs_3bet"]}
    return rfi, nodes


def test_fold_to_3bet_under_ceiling_every_node(built):
    rfi, nodes = built
    for nn, node in nodes.items():
        hero, _ = nn.split("_vs_")
        f3b, _ = b3._node_metrics(hero, node, rfi)
        ceiling = lints.F3B_CEILING_IP if lints._vs3bet_is_ip(nn) else lints.F3B_CEILING_OOP
        assert (
            f3b <= ceiling + 1e-9
        ), f"{nn}: fold-to-3bet {100*f3b:.1f}% > ceiling {100*ceiling:.0f}%"


def test_fourbet_in_band_every_node(built):
    rfi, nodes = built
    lo, hi = lints.FOURBET_BAND
    for nn, node in nodes.items():
        hero, _ = nn.split("_vs_")
        _, fb = b3._node_metrics(hero, node, rfi)
        assert lo - 1e-9 <= fb <= hi + 1e-9, f"{nn}: 4-bet {100*fb:.1f}% outside band"


def test_bluff_4bets_fold_above_depth_gate(built):
    """Any non-value 4-bet must carry fold >= gate so it folds (not jams) at 25bb."""
    _, nodes = built
    for nn, node in nodes.items():
        for h, d in node.items():
            r = d.get("raise_2.2x", 0.0)
            if 0 < r < b3.DIST_VALUE_4BET["raise_2.2x"]:  # a bluff 4-bet, not value
                assert (
                    d.get("fold", 0.0) >= b3.FOLD_GATE - 1e-9
                ), f"{nn}/{h}: bluff 4-bet fold {d.get('fold')} < gate {b3.FOLD_GATE}"


def test_generated_vs3bet_is_not_a_copied_range(built):
    _, nodes = built
    assert lints.lint_anti_clone({"vs_3bet": nodes}, branches=("vs_3bet",)) == []
