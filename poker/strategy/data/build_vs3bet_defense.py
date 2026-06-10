#!/usr/bin/env python3
"""Rebuild the base chart's `vs_3bet` section as a hand-strength gradient.

Companion to `build_vs4bet_defense.py`. The `vs_3bet` section of
`preflop_100bb_6max.json` was a coarse stub (5 distinct distributions / 169
hands): **159 of 169 hands shared one `{fold:0.75, call:0.15, raise_2.2x:0.10}`
blob** — so the opener 4-bet (raise_2.2x) trash ~10% of the time facing a 3-bet,
and the call/fold split was hand-independent.

This rebuilds `vs_3bet` (hero opened, faces a 3-bet, decides fold / call /
4-bet=`raise_2.2x`) from the all-in equity matrix vs an assumed villain 3-bet
range (`VILLAIN_3BET`).

Two design rules distinguish this from vs_4bet:

1. **No `raise_2.2x` (4-bet) mass below the value/bluff tiers.** Only premium
   value hands and a couple of designated blocker bluffs carry a 4-bet. Junk
   gets fold + a small call with NO raise key — so neither the archetype
   transforms nor the personality distortion can re-create a trash 4-bet (an
   offset can't amplify mass that isn't there). This kills the stub's 10%
   trash-4-bet leak.

2. **Junk is NOT pure-folded (unlike vs_4bet).** Facing a 3-bet, a calling
   station / fish should defend WIDE by flat-calling. So junk keeps a small
   `call` that `_station_facing` (low keep_fold) widens for the passive tiers,
   while `_loosen_facing` / tight transforms keep it folded for the tight tiers.
   Pure-folding junk here would collapse the station's wide 3-bet defense.

Scope/validation: the realized per-archetype `fourbet` / `fold_to_3bet` bands
(poker/archetype_targets.py) are sensitive to this node, so any change here is
validated with `scripts/archetype_mixedfield_probe.py` (the same 6-max mixed
field the bands were written for). See ARCHETYPE_SHAPING_FINDINGS.md § Finding 1a.

Run inside the backend container, then cascade:
    docker compose exec -T backend python -m poker.strategy.data.build_vs3bet_defense
    docker compose exec -T backend python -m experiments.build_archetype_charts
    docker compose exec -T backend python -m experiments.build_wider_rfi_chart
    docker compose exec -T backend python -m poker.strategy.data.generate_depth_charts
"""

from __future__ import annotations

import json
import os
from typing import Dict

from poker.strategy.data.generate_push_fold_nash import (
    CANONICAL_HANDS,
    COMBO_COUNT,
    equity_vs_range,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.join(_HERE, "preflop_100bb_6max.json")
_MATRIX = os.path.join(_HERE, "push_fold_equity_matrix.json")

# Assumed villain 3-bet range (the player 3-betting hero's open): value QQ+/AK +
# AQs/JJ/TT, polarized with suited-ace and suited-connector bluffs. Wider and
# weaker than the 4-bet range, so equities are less compressed.
VILLAIN_3BET: Dict[str, float] = {
    "AA": 1.0,
    "KK": 1.0,
    "QQ": 1.0,
    "JJ": 0.6,
    "TT": 0.4,
    "AKs": 1.0,
    "AKo": 1.0,
    "AQs": 0.8,
    "A5s": 0.6,
    "A4s": 0.5,
    "A3s": 0.4,
    "A2s": 0.3,
    "KJs": 0.4,
    "KTs": 0.4,
    "K9s": 0.2,
    "QJs": 0.3,
    "JTs": 0.4,
    "T9s": 0.3,
    "98s": 0.3,
    "87s": 0.3,
    "76s": 0.3,
    "65s": 0.2,
    "54s": 0.2,
}

# Equity tier cuts (equity vs VILLAIN_3BET).
EQ_VALUE = 0.52  # AA,KK,QQ,AKs → value 4-bet + call
EQ_STRONG = 0.46  # AKo,JJ,TT → call-heavy + some 4-bet
EQ_WIDE = 0.40  # wide flat-call tier
EQ_THIN = 0.355  # thin call tier; below → junk

# Tier distributions (each sums to 1.0). The 4-bet (raise_2.2x) is POLARIZED:
# value hands 4-bet for value, and SUITED hands below value carry the
# bluff-4-bet mass (blockers + playability). OFFSUIT non-value hands get
# call/fold only — NO raise key — so neither the archetype transforms nor the
# personality distortion can 4-bet offsuit trash (no mass to amplify). The
# loose archetypes' raise-share then amplifies the suited bluff mass into a wide
# *polarized* 4-bet (a maniac 4-bets a wide suited range, never 72o); the tight
# archetypes' damp_raise routes it to call. This is what reproduces the
# defining lag/maniac 4-bet frequency WITHOUT a universal trash-4-bet.
DIST_VALUE = {"raise_2.2x": 0.55, "call": 0.35, "fold": 0.10}
DIST_STRONG = {"call": 0.55, "raise_2.2x": 0.22, "fold": 0.23}
DIST_WIDE_S = {"call": 0.46, "raise_2.2x": 0.16, "fold": 0.38}  # suited
DIST_WIDE_O = {"call": 0.44, "fold": 0.56}  # offsuit (no 4-bet)
DIST_THIN_S = {"call": 0.22, "raise_2.2x": 0.16, "fold": 0.62}  # suited
DIST_THIN_O = {"call": 0.18, "fold": 0.82}  # offsuit (no 4-bet)
DIST_JUNK_S = {"fold": 0.74, "call": 0.10, "raise_2.2x": 0.16}  # suited bluff pool
DIST_JUNK_O = {"fold": 0.90, "call": 0.10}  # offsuit trash: no 4-bet, small call

# Designated blocker 4-bet bluffs (block AA/AK), forced regardless of tier.
BLUFF_4BET = {
    "A5s": {"raise_2.2x": 0.30, "call": 0.30, "fold": 0.40},
    "A4s": {"raise_2.2x": 0.24, "call": 0.28, "fold": 0.48},
}


def _is_suited(hand: str) -> bool:
    return len(hand) == 3 and hand[2] == "s"


def _norm(d: Dict[str, float]) -> Dict[str, float]:
    s = sum(d.values())
    return {k: round(v / s, 4) for k, v in d.items() if v > 0} if s > 0 else {"fold": 1.0}


def hand_distribution(hand: str, equity: float) -> Dict[str, float]:
    """Map a hand's equity-vs-3bet-range to its {fold,call,raise_2.2x} dist.

    Suited hands below the value tier carry bluff-4-bet mass; offsuit hands get
    call/fold only (no raise key) so no archetype can 4-bet offsuit trash.
    """
    if hand in BLUFF_4BET:
        return dict(BLUFF_4BET[hand])
    if equity >= EQ_VALUE:
        return dict(DIST_VALUE)
    if equity >= EQ_STRONG:
        return dict(DIST_STRONG)
    suited = _is_suited(hand)
    if equity >= EQ_WIDE:
        return dict(DIST_WIDE_S if suited else DIST_WIDE_O)
    if equity >= EQ_THIN:
        return dict(DIST_THIN_S if suited else DIST_THIN_O)
    return dict(DIST_JUNK_S if suited else DIST_JUNK_O)


def build_vs3bet_distributions() -> Dict[str, Dict[str, float]]:
    with open(_MATRIX) as f:
        matrix = json.load(f)["matrix"]
    return {
        hand: _norm(hand_distribution(hand, equity_vs_range(hand, matrix, VILLAIN_3BET)))
        for hand in CANONICAL_HANDS
    }


def patch_base() -> None:
    with open(_BASE) as f:
        chart = json.load(f)
    dists = build_vs3bet_distributions()
    nodes = chart.get("vs_3bet", {})
    if not nodes:
        raise SystemExit("base chart has no vs_3bet section")
    for hands in nodes.values():
        for hand in hands:
            hands[hand] = dict(dists[hand])
    with open(_BASE, "w") as f:
        json.dump(chart, f, indent=2)
        f.write("\n")

    tot = defend = fourbet = 0.0
    for hand in CANONICAL_HANDS:
        c = COMBO_COUNT[hand]
        d = dists[hand]
        tot += c
        defend += c * (d.get("call", 0.0) + d.get("raise_2.2x", 0.0))
        fourbet += c * d.get("raise_2.2x", 0.0)
    n_4bet = sum(1 for d in dists.values() if d.get("raise_2.2x", 0) > 0)
    print(f"patched {_BASE}")
    print(f"  vs_3bet nodes rebuilt: {len(nodes)} (flat by position)")
    print(f"  hands with a 4-bet: {n_4bet}/169 (was 169 — every hand 4-bet 10%)")
    print(f"  base defend freq over all 169 (combo-wt call+4bet): {100 * defend / tot:.1f}%")
    print(f"  base 4-bet freq over all 169 (combo-wt): {100 * fourbet / tot:.1f}%")


if __name__ == "__main__":
    patch_base()
