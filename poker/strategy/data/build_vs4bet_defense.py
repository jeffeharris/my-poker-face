#!/usr/bin/env python3
"""Rebuild the base chart's `vs_4bet` section per-node (hero 3-bet, faces a 4-bet).

Implements docs/strategy/PREFLOP_DEFENSE_REGEN_SPEC.md §4 (item 3). Replaces the
old global generator (one range written to all 15 nodes — the position-invariant
stub where 72o/47o got the same line as AKo and could be sampled into a trash JAM,
the prod "47o jams into a 4-bet" report).

Per-node, self-consistent
-------------------------
Node = HERO(3-bettor)_vs_VILLAIN(opener/4-bettor). Hero's LIVE range is hero's own
3-bet range, read from `vs_open[hero_vs_villain].raise_3x`. The villain 4-bet range
is read from `vs_3bet[villain_vs_hero].raise_2.2x`; its VALUE part (≥ cliff) is what
continues vs hero's 5-bet jam. ⇒ build_vs_open AND build_vs3bet_defense must run
before this script (strict order; a stale upstream fails its own lints → refused).

MDF anchor: hero continues (jam + call) with `k` of their 3-BET range, so fold-to-
4bet stays under the auto-profit line (the 4-bet bluffs in our own vs_3bet would
otherwise print). The continue range is value 5-bet jams (top by equity vs the
villain's continue range), medium calls, and suited-Ax 5-bet bluff-jams (A5s–A2s,
which block AA/AK — and which were hero's own 3-bet bluffs, so the polarization is
self-consistent).

⚠️ The pure-fold junk floor is LOAD-BEARING and must NOT become a thin call (unlike
vs_3bet): build_archetype_charts._loosen_facing / _station_facing and
generate_depth_charts.t_vs_4bet all skip rows with `fold >= 0.999`, so trash stays
folded across every archetype/depth chart. A thin call would let archetype widening
re-create the trash-jam bug. Folding to a 4-bet is correct, so there's no squeeze
overfold concern (cf. vs_3bet).

Run inside the backend container (after build_vs3bet), then cascade:
    docker compose exec -T backend python -m poker.strategy.data.build_vs4bet_defense
    docker compose exec -T backend python -m poker.strategy.data.generate_depth_charts
    docker compose exec -T backend python -m experiments.build_archetype_charts
"""

from __future__ import annotations

import json
import os
from typing import Dict, List

from poker.strategy import lints
from poker.strategy.data._chart_gen import _norm
from poker.strategy.data.generate_push_fold_nash import (
    CANONICAL_HANDS,
    COMBO_COUNT,
    equity_vs_range,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.join(_HERE, "preflop_100bb_6max.json")
_MATRIX = os.path.join(_HERE, "push_fold_equity_matrix.json")

MDF_BUFFER = 0.02              # continue a touch above the MDF minimum
VILLAIN_CONTINUE_CLIFF = 0.50  # villain 4-bets with raise_2.2x ≥ this continue vs a 5-bet

# Share of the continue (jam+call) mass that is value 5-bet jams; the suited-Ax
# bluff-jams and medium calls backfill the rest up to the MDF anchor.
VALUE_SHARE = 0.55

# Per-hand distributions. Jam-dominant; call is the medium continue.
DIST_VALUE_JAM = {"jam": 0.85, "call": 0.10, "fold": 0.05}
DIST_CALL = {"call": 0.55, "jam": 0.25, "fold": 0.20}
DIST_BLUFF_JAM = {"jam": 0.40, "fold": 0.60}

# Suited-Ax 5-bet bluff-jams (block AA/AK). These are hero's own 3-bet bluffs.
BLUFF_JAM_POOL: List[str] = ["A5s", "A4s", "A3s", "A2s"]

def build_node(hero: str, villain: str, vs_open: Dict, vs_3bet: Dict, matrix: Dict) -> Dict[str, Dict[str, float]]:
    """Generate one vs_4bet node (hero = 3-bettor facing villain's 4-bet)."""
    vo = vs_open[f"{hero}_vs_{villain}"]
    hero_3bet = {h: vo[h].get("raise_3x", 0.0) for h in vo if vo[h].get("raise_3x", 0.0) > 0}
    v4 = vs_3bet[f"{villain}_vs_{hero}"]
    villain_4bet = {h: v4[h].get("raise_2.2x", 0.0) for h in v4 if v4[h].get("raise_2.2x", 0.0) > 0}
    villain_cont = {h: w for h, w in villain_4bet.items()
                    if v4[h].get("raise_2.2x", 0.0) >= VILLAIN_CONTINUE_CLIFF} or dict(villain_4bet)

    ow = {h: COMBO_COUNT[h] * hero_3bet[h] for h in hero_3bet}
    tb = sum(ow.values())
    eq = {h: equity_vs_range(h, matrix, villain_cont) for h in hero_3bet}

    k = (1.0 - lints.F4B_CEILING) + MDF_BUFFER
    cont_target = k * tb
    value_target = VALUE_SHARE * cont_target

    dist: Dict[str, Dict[str, float]] = {}

    def _cont() -> float:
        return sum(ow[h] * (d.get("jam", 0.0) + d.get("call", 0.0)) for h, d in dist.items())

    by_eq = sorted(hero_3bet, key=lambda x: eq[x], reverse=True)
    bluff_set = set(BLUFF_JAM_POOL)

    # 1. Value 5-bet jams: top of hero's 3-bet range by equity vs the continue
    #    range. The suited-Ax blockers are RESERVED for the bluff pass — facing a
    #    4-bet they're blocker bluff-jams, never full value (their equity vs the
    #    value-4bet continue range is low; they jam for the AA/AK blocker + fold eq).
    for h in by_eq:
        if _cont() >= value_target:
            break
        if h in bluff_set:
            continue
        dist[h] = dict(DIST_VALUE_JAM)

    # 2. Suited-Ax 5-bet bluff-jams (designated blockers ∩ hero's 3-bet range).
    for h in BLUFF_JAM_POOL:
        if h in dist or h not in hero_3bet:
            continue
        dist[h] = dict(DIST_BLUFF_JAM)

    # 3. Medium calls backfill the continue range to the MDF anchor (calling a 4-bet
    #    with TT/AJs-type hands, not spew-jamming). Guarantees fold-to-4bet ≤ ceiling.
    for h in by_eq:
        if _cont() >= cont_target:
            break
        if h in dist:
            continue
        dist[h] = dict(DIST_CALL)

    # 4. Everything else PURE-FOLDS (load-bearing — archetype/depth transforms skip
    #    fold>=0.999, so trash never widens into a 4-bet jam).
    for h in CANONICAL_HANDS:
        dist.setdefault(h, {"fold": 1.0})

    return {h: _norm(dist[h]) for h in CANONICAL_HANDS}


def _fold_to_4bet(hero: str, villain: str, node: Dict, vs_open: Dict) -> float:
    tn = vs_open[f"{hero}_vs_{villain}"]
    twt = cont = 0.0
    for h, td in tn.items():
        tw = td.get("raise_3x", 0.0)
        if tw <= 0:
            continue
        c = COMBO_COUNT[h] * tw
        twt += c
        d = node[h]
        cont += c * (d.get("jam", 0.0) + d.get("call", 0.0))
    return (1 - cont / twt) if twt else 0.0


def patch_base() -> None:
    with open(_BASE) as f:
        chart = json.load(f)
    with open(_MATRIX) as f:
        matrix = json.load(f)["matrix"]
    vs_open, vs_3bet, nodes = chart["vs_open"], chart["vs_3bet"], chart.get("vs_4bet", {})
    if not nodes:
        raise SystemExit("base chart has no vs_4bet section")

    # Run-order guard: reads BOTH upstream charts. A stale one fails its own lints.
    stale = lints.lint_bb_defend_floors(chart) + lints.lint_vs3bet_fold_to_3bet(chart)
    if stale:
        raise SystemExit("vs_open/vs_3bet look stale (run build_vs_open + build_vs3bet "
                         "first):\n  " + "\n  ".join(stale[:5]))

    print(f"patching {_BASE}")
    print(f"  {'node':<14} {'fold-to-4bet':>13}")
    built = {}
    for node_name in nodes:
        hero, villain = node_name.split("_vs_")
        node = build_node(hero, villain, vs_open, vs_3bet, matrix)
        built[node_name] = node
        print(f"  {node_name:<14} {100 * _fold_to_4bet(hero, villain, node, vs_open):>12.1f}%")

    chart["vs_4bet"] = built
    fails = (lints.lint_weights_sum(chart)
             + lints.lint_legal_vocab(chart)
             + lints.lint_completeness(chart)
             + lints.lint_anti_clone(chart, branches=("vs_4bet",))
             + lints.lint_vs4bet_fold_to_4bet(chart))
    if fails:
        for msg in fails[:12]:
            print(f"  LINT FAIL: {msg}")
        raise SystemExit(f"refusing to write — {len(fails)} lint failures")

    with open(_BASE, "w") as f:
        json.dump(chart, f, indent=2)
        f.write("\n")
    print("  done — vs_4bet regenerated per-node; run generate_depth_charts next.")


if __name__ == "__main__":
    patch_base()
