#!/usr/bin/env python3
"""Rebuild the base chart's `vs_4bet` section as a real hand-strength gradient.

Why this exists
---------------
The `vs_4bet` section of `preflop_100bb_6max.json` was hand-authored as a
3-bucket STUB: KK/AA jam ~0.75, two hands call ~0.67, and **all other 165 hands
shared one `{fold:0.645, call:0.223, jam:0.132}` blob** — no hand gradient at
all. So 72o/47o/89o got the same line as AKo facing a 4-bet and could be
*sampled into a 13% trash JAM* (amplified to ~22% by the lag personality
distortion). That is the root cause of the prod "47o jams into a 4-bet all-in"
report (see docs/technical/ARCHETYPE_SHAPING_FINDINGS.md § Finding 1a).

What it does
------------
For each of the 169 canonical hands, compute equity vs an assumed opener
4-bet range (`VILLAIN_4BET`) from the precomputed all-in equity matrix
(`push_fold_equity_matrix.json`, the same one the push/fold Nash solver uses),
then map equity → a `{fold, call, jam}` distribution:

  * value 5-bet jam  (AA, KK)         — get it in
  * call / jam mix   (AKs, QQ, AKo)   — strong continues
  * light continue   (JJ..44 pairs)   — mostly fold, small flat/jam
  * Ax-suited bluff-jams (A5s/A4s/A3s) — block AA/AK; balance the value range
  * everything else                   — EXACTLY {fold: 1.0}

The pure-fold floor is load-bearing: `build_archetype_charts._loosen_facing` /
`_station_facing` and `generate_depth_charts.t_vs_4bet` all skip rows with
`fold >= 0.999`, so trash stays folded across EVERY derived archetype/depth
chart. Only the continue range widens per archetype.

Scope: this rebuilds `vs_4bet` only. The distribution is flat across the 15
position matchups (gradient by hand, not by position) — same structural
simplification the stub had; per-position 4-bet ranges are a later refinement.
`vs_3bet` is intentionally left alone (it is entangled with the archetype
fold-to-3bet / 4-bet calibration in ARCHETYPE_SHAPING_FINDINGS.md and needs its
own re-validation).

Run inside the backend container, then cascade:
    docker compose exec -T backend python -m poker.strategy.data.build_vs4bet_defense
    docker compose exec -T backend python -m experiments.build_archetype_charts
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

# Assumed opener 4-bet range (the player hero is responding to). Value-heavy
# QQ+/AK with a pair of wheel-suited-ace bluffs — a believable ~3% opener 4-bet
# range. The exact range only sets the equity *scale*; the gradient ordering is
# robust to reasonable changes here.
VILLAIN_4BET: Dict[str, float] = {
    "AA": 1.0,
    "KK": 1.0,
    "QQ": 0.7,
    "JJ": 0.2,
    "AKs": 1.0,
    "AKo": 0.8,
    "A5s": 0.5,
    "A4s": 0.4,
}

# Equity tier cuts (equity vs VILLAIN_4BET). Compressed because the villain
# range is strong — even AKs/QQ only sit ~0.44. Tuned so the base (TAG) defends
# a tight ~3% (QQ+/AK + a few pairs + Ax bluffs), folding the marginal pairs and
# all trash.
EQ_JAM = 0.50  # AA, KK → value jam
EQ_CALL = 0.40  # AKs, QQ, AKo → call/jam
EQ_FLOOR = 0.355  # JJ..44 → light continue; below this → pure fold

# Distributions per tier (each sums to 1.0).
DIST_JAM = {"jam": 0.82, "call": 0.13, "fold": 0.05}
DIST_CALL = {"call": 0.45, "jam": 0.30, "fold": 0.25}
DIST_LIGHT = {"fold": 0.88, "call": 0.09, "jam": 0.03}

# Ax-suited 5-bet bluff-jams: equity below the floor, but they block AA/AK and
# polarize the range (value + bluffs vs pure value = face-up). Override pure-fold.
BLUFF_JAM = {"A5s": 0.30, "A4s": 0.22, "A3s": 0.12}


def _norm(d: Dict[str, float]) -> Dict[str, float]:
    s = sum(d.values())
    return {k: round(v / s, 4) for k, v in d.items() if v > 0} if s > 0 else {"fold": 1.0}


def hand_distribution(hand: str, equity: float) -> Dict[str, float]:
    """Map a hand's equity-vs-4bet-range to its {fold,call,jam} distribution."""
    if equity >= EQ_JAM:
        return dict(DIST_JAM)
    if equity >= EQ_CALL:
        return dict(DIST_CALL)
    if equity >= EQ_FLOOR:
        return dict(DIST_LIGHT)
    if hand in BLUFF_JAM:
        j = BLUFF_JAM[hand]
        return {"jam": j, "fold": round(1.0 - j, 4)}
    return {"fold": 1.0}


def build_vs4bet_distributions() -> Dict[str, Dict[str, float]]:
    """Return {hand: distribution} for all 169 canonical hands."""
    with open(_MATRIX) as f:
        matrix = json.load(f)["matrix"]
    out = {}
    for hand in CANONICAL_HANDS:
        eq = equity_vs_range(hand, matrix, VILLAIN_4BET)
        out[hand] = _norm(hand_distribution(hand, eq))
    return out


def patch_base() -> None:
    """Replace every vs_4bet node in the base chart with the gradient."""
    with open(_BASE) as f:
        chart = json.load(f)

    dists = build_vs4bet_distributions()
    nodes = chart.get("vs_4bet", {})
    if not nodes:
        raise SystemExit("base chart has no vs_4bet section")
    for hands in nodes.values():
        for hand in hands:
            hands[hand] = dict(dists[hand])

    with open(_BASE, "w") as f:
        json.dump(chart, f, indent=2)
        f.write("\n")

    # Summary
    tot = defend = jam = 0.0
    for hand in CANONICAL_HANDS:
        c = COMBO_COUNT[hand]
        d = dists[hand]
        tot += c
        defend += c * (d.get("call", 0.0) + d.get("jam", 0.0))
        jam += c * d.get("jam", 0.0)
    n_continue = sum(1 for d in dists.values() if d != {"fold": 1.0})
    print(f"patched {_BASE}")
    print(f"  vs_4bet nodes rebuilt: {len(nodes)} (flat by position)")
    print(f"  continue hands: {n_continue}/169")
    print(f"  base defend freq (combo-wt call+jam): {100 * defend / tot:.1f}%")
    print(f"  base jam freq (combo-wt): {100 * jam / tot:.1f}%")


if __name__ == "__main__":
    patch_base()
