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

from poker.strategy.data.build_vs3bet_defense import (
    build_vs3bet_distributions,
    hand_distribution,
)

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
def test_vs3bet_is_a_real_gradient(path):
    """The stub had exactly 5 distinct distributions across 169 hands."""
    hands = _vs3bet_first_node(path)
    assert hands, f"{path} has no vs_3bet"
    distinct = {tuple(sorted(d.items())) for d in hands.values()}
    assert len(distinct) >= 8, (
        f"{os.path.basename(path)} vs_3bet looks degenerate "
        f"({len(distinct)} distributions — the stub had 5)"
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


def test_generator_polarizes_and_is_deterministic():
    a = build_vs3bet_distributions()
    b = build_vs3bet_distributions()
    assert a == b
    # Suited bluff pool carries 4-bet mass; offsuit twin of similar strength does not.
    assert a['98s'].get('raise_2.2x', 0) > 0
    assert a['98o'].get('raise_2.2x', 0) == 0
    assert a['72o'].get('raise_2.2x', 0) == 0
    # Value hands 4-bet the most.
    assert a['AA']['raise_2.2x'] > a['98s']['raise_2.2x']


def test_hand_distribution_tiers_sum_to_one():
    for hand, eq in [('AA', 0.9), ('98s', 0.30), ('98o', 0.30), ('A5s', 0.39)]:
        d = hand_distribution(hand, eq)
        assert abs(sum(d.values()) - 1.0) < 1e-9
