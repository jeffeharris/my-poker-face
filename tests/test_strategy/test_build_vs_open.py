"""Unit tests for poker/strategy/data/build_vs_open.py.

Regression guards for the June chart-review findings:
  * polarized (early-open) nodes must keep designated bluff-pool hands tagged
    "bluff" — otherwise depth derivation jams them shallow;
  * realized 3-bet AND defend mass must track the node target (the bluff backfill
    closes the gap the named pool alone can't fill);
  * every 3-bet cell carries an explicit value/bluff intent tag (depth derivation
    reads the tag, not the weight — DEPTH_INTENT_TAG_TECHDEBT.md);
  * 3-bet weights stay bimodal (depth-safe even via the legacy weight fallback);
  * the merged BvB value top excludes set-mine pairs / dominated offsuit Ax.
"""

import json

import pytest

from poker.strategy import lints
from poker.strategy.data import build_vs_open as bvo


@pytest.fixture(scope="module")
def inputs():
    with open(bvo._BASE) as f:
        chart = json.load(f)
    with open(bvo._MATRIX) as f:
        matrix = json.load(f)["matrix"]
    return chart["rfi"], matrix, chart["vs_open"]


def _build(opener, rfi, matrix, defend, threebet, merged):
    """Return just the node (build_node now also returns the intent map)."""
    pool = bvo.BLUFF_3BET_POOL_WIDE if opener in bvo.WIDE_OPENERS else bvo.BLUFF_3BET_POOL
    return bvo.build_node(
        opener, rfi, matrix, defend, threebet, bvo.VALUE_SHARE_BY_OPENER[opener], pool, merged
    )[0]


def _build_with_intent(opener, rfi, matrix, defend, threebet, merged):
    pool = bvo.BLUFF_3BET_POOL_WIDE if opener in bvo.WIDE_OPENERS else bvo.BLUFF_3BET_POOL
    return bvo.build_node(
        opener, rfi, matrix, defend, threebet, bvo.VALUE_SHARE_BY_OPENER[opener], pool, merged
    )


def test_polarized_bluff_pool_stays_sub_cliff(inputs):
    """A suited wheel ace vs a tight UTG open is a bluff, not a value jam."""
    rfi, matrix, _ = inputs
    node = _build("UTG", rfi, matrix, 0.20, bvo.NONBB_THREEBET_BY_OPENER["UTG"], merged=False)
    offenders = [
        h
        for h in bvo.BLUFF_3BET_POOL
        if node[h].get("raise_3x", 0.0) >= lints.VALUE_RAISE_THRESHOLD
    ]
    assert offenders == [], f"bluff-pool hands promoted to value weight: {offenders}"


def test_intent_map_covers_exactly_the_3bet_hands(inputs):
    """build_node tags every 3-bet hand value/bluff and nothing else; the tag
    matches the weight side (value=VALUE_RAISE_W, bluff=BLUFF_RAISE_W)."""
    rfi, matrix, _ = inputs
    node, intent = _build_with_intent("SB", rfi, matrix, *bvo.BB_TARGETS["SB"], merged=True)
    threebet_hands = {h for h, d in node.items() if d.get("raise_3x", 0.0) > 0}
    assert set(intent) == threebet_hands, "intent map must cover exactly the 3-bet hands"
    assert all(v in ("value", "bluff") for v in intent.values())
    for h, tag in intent.items():
        w = node[h]["raise_3x"]
        if tag == "value":
            assert w == bvo.VALUE_RAISE_W, f"{h}: value tag but weight {w}"
        else:
            assert w == bvo.BLUFF_RAISE_W, f"{h}: bluff tag but weight {w}"


@pytest.mark.parametrize(
    "opener,defend,threebet,merged",
    [
        ("UTG", 0.20, 0.045, False),  # polarized cold-defense
        ("SB", 0.65, 0.15, True),  # merged BvB
    ],
)
def test_realized_masses_hit_target(inputs, opener, defend, threebet, merged):
    rfi, matrix, _ = inputs
    node = _build(opener, rfi, matrix, defend, threebet, merged)
    d, t = bvo._current_masses(node)
    assert abs(t - threebet) <= bvo._TOL_3BET, f"3-bet {t:.3f} vs target {threebet:.3f}"
    assert abs(d - defend) <= bvo._TOL_DEFEND, f"defend {d:.3f} vs target {defend:.3f}"


def test_no_3bet_in_cliff_band_any_node(inputs):
    rfi, matrix, vo = inputs
    for nn in vo:
        defender, opener = nn.split("_vs_")
        merged = bvo.VALUE_SHARE_BY_OPENER[opener] >= bvo.MERGED_THRESHOLD
        if defender == "BB":
            defend, threebet = bvo.BB_TARGETS[opener]
        else:
            defend = bvo._current_masses(vo[nn])[0]
            threebet = bvo.NONBB_THREEBET_BY_OPENER[opener]
        node = _build(opener, rfi, matrix, defend, threebet, merged)
        band = [
            h
            for h, dd in node.items()
            if 0.45 < dd.get("raise_3x", 0.0) < lints.VALUE_RAISE_THRESHOLD
        ]
        assert band == [], f"{nn}: 3-bet weights in the cliff band: {band}"


def test_merged_bvb_value_top_excludes_spew(inputs):
    rfi, matrix, _ = inputs
    defend, threebet = bvo.BB_TARGETS["SB"]
    node = _build("SB", rfi, matrix, defend, threebet, merged=True)
    value = {h for h, d in node.items() if d.get("raise_3x", 0.0) >= bvo.VALUE_RAISE_W}
    assert not value & {"66", "55", "44", "33", "22"}, "set-mine pairs at value weight"
    assert not value & {"A8o", "A7o", "A6o", "A5o", "A4o", "A3o", "A2o"}, "dominated Axo at value"
    assert {"99", "AJo", "KTs"} <= value, "merged value top missing required hands"
