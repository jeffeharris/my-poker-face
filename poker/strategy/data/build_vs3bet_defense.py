#!/usr/bin/env python3
"""Rebuild the base chart's `vs_3bet` section per-node (hero opened, faces a 3-bet).

Implements docs/strategy/PREFLOP_DEFENSE_REGEN_SPEC.md §2. Replaces the old global
generator (one `VILLAIN_3BET` range written to all 15 nodes — the position-
invariant leak the review flagged: fold-to-3bet 65–74% vs the wide-opening
positions, exploitable by any-two 3-bets).

Per-node, self-consistent villain model
---------------------------------------
For each (hero_pos, villain_pos) node the villain's 3-bet range is read from OUR
OWN `vs_open` chart — `vs_open[villain_vs_hero].raise_3x` — so we defend against
the range our own bots actually 3-bet. The VALUE part of that range (raise_3x ≥
the depth cliff) is what continues vs hero's 4-bet; the bluff 3-bets fold to it.
That reuses the bimodal value/bluff weights build_vs_open writes. ⇒ build_vs_open
MUST run before this script (strict order).

MDF anchor (fraction of the OPEN range, matching lints.lint_vs3bet_fold_to_3bet):
hero continues (call + 4-bet) with `k` of their opens — k = 0.45 IP / 0.38 OOP,
tapered down vs a value-only 3-bettor. The equity gradient decides WHICH opens
continue; the anchor decides HOW MANY. 4-bet = 10% of opens (value:bluff 55:45),
suited-only bluffs (AKo the only offsuit 4-bet). Junk keeps a thin call so the
station archetype transform can widen it (mask rule).

Depth contract: t_vs_3bet gates 25bb behavior on the FOLD weight
(`fold >= J25_VS3BET_FOLD_GATE`, 0.50), NOT a raise cliff. So value 4-bets / value
flats carry fold < gate (jam at 25bb) and bluff-4bets / junk carry fold ≥ gate
(fold at 25bb — no bluff-jam). See DEPTH_INTENT_TAG_TECHDEBT.md.

Run inside the backend container (after build_vs_open), then cascade:
    docker compose exec -T backend python -m poker.strategy.data.build_vs_open
    docker compose exec -T backend python -m poker.strategy.data.build_vs3bet_defense
    docker compose exec -T backend python -m poker.strategy.data.build_vs4bet_defense
    docker compose exec -T backend python -m poker.strategy.data.generate_depth_charts
    docker compose exec -T backend python -m experiments.build_archetype_charts
"""

from __future__ import annotations

import json
import os
from typing import Dict, List

from poker.strategy import lints
from poker.strategy.data.generate_push_fold_nash import (
    CANONICAL_HANDS,
    COMBO_COUNT,
    equity_vs_range,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.join(_HERE, "preflop_100bb_6max.json")
_MATRIX = os.path.join(_HERE, "push_fold_equity_matrix.json")

RANKS = "AKQJT98765432"
TOTAL_COMBOS = float(sum(COMBO_COUNT[h] for h in CANONICAL_HANDS))

# Continue factor k (fraction of opens that continue): IP defends wider than OOP.
K_IP, K_OOP = 0.45, 0.38
# Taper k down vs a value-only / narrow 3-bettor (reference width 8% of hands)...
TAPER_REF = 0.08
TAPER_FLOOR = 0.6
# ...but NEVER below MDF: our vs_open villain model includes suited bluffs, so
# folding past the auto-profit line lets those bluffs print. k is floored so
# fold-to-3bet stays under the lint ceiling. (The taper resolves §2-vs-§1.2: it
# only bites where the base k already has MDF headroom — IP mostly.) A
# bluff-frequency-aware taper would be the principled upgrade; tracked, not done.
MDF_BUFFER = 0.01

TARGET_4BET_OF_OPEN = 0.10     # 4-bet 10% of the open range (value+bluff)
VALUE_BLUFF_SPLIT = 0.55       # value share of the 4-bet
VILLAIN_CONTINUE_CLIFF = 0.50  # villain 3-bets with raise_3x ≥ this continue vs a 4-bet
FOLD_GATE = 0.50               # must match generate_depth_charts.J25_VS3BET_FOLD_GATE

# Per-hand distributions, chosen so the FOLD weight lands the hand correctly at 25bb.
DIST_VALUE_4BET = {"raise_2.2x": 0.85, "call": 0.10, "fold": 0.05}  # fold<gate → jams 25bb
DIST_BLUFF_4BET = {"raise_2.2x": 0.30, "call": 0.05, "fold": 0.65}  # fold≥gate → folds 25bb
CALL_CORE_W = 0.85             # core flat weight (fold<gate → value flats jam 25bb)
JUNK_CALL = 0.10               # station-mask floor (fold 0.90 ≥ gate → folds 25bb)

# Suited-only 4-bet bluff pool (blockers first, then suited broadways). AKo is the
# only offsuit 4-bet and rides the value tier. Backfilled from open suited hands.
BLUFF_4BET_POOL: List[str] = [
    "A5s", "A4s", "A3s", "A2s", "KJs", "KTs", "QJs", "QTs", "JTs", "K9s",
]

# NOTE: _playability duplicates build_vs_open's; hoist both (+ the bluff pools and
# _norm) to a shared poker/strategy/data/_chart_gen.py when build_vs4bet is
# refactored too (rule of three).


def _is_suited(h: str) -> bool:
    return len(h) == 3 and h[2] == "s"


def _playability(h: str) -> float:
    hi, lo = RANKS.index(h[0]), RANKS.index(h[1])
    high_card = (12 - hi) / 12 * 0.4
    conn = 0.2 if h[0] == h[1] else max(0, 4 - abs(hi - lo)) / 4 * 0.3
    return high_card + conn + (0.3 if _is_suited(h) else 0.0)


def _norm(d: Dict[str, float]) -> Dict[str, float]:
    s = sum(d.values())
    return {k: round(v / s, 4) for k, v in d.items() if v > 0} if s > 0 else {"fold": 1.0}


def _open_range(rfi_node: Dict[str, Dict[str, float]]) -> Dict[str, float]:
    return {h: d.get("raise_2.5bb", 0.0) for h, d in rfi_node.items() if d.get("raise_2.5bb", 0.0) > 0}


def build_node(hero: str, villain: str, rfi: Dict, vs_open: Dict, matrix: Dict) -> Dict[str, Dict[str, float]]:
    """Generate one vs_3bet node (hero=opener, villain=3-bettor)."""
    hero_open = _open_range(rfi[hero])
    vo = vs_open[f"{villain}_vs_{hero}"]                     # villain defending vs hero's open
    villain_3b = {h: vo[h].get("raise_3x", 0.0) for h in vo if vo[h].get("raise_3x", 0.0) > 0}
    villain_cont = {h: w for h, w in villain_3b.items()
                    if vo[h].get("raise_3x", 0.0) >= VILLAIN_CONTINUE_CLIFF} or dict(villain_3b)

    ip = lints._vs3bet_is_ip(f"{hero}_vs_{villain}")
    v3b_weight = sum(COMBO_COUNT[h] * w for h, w in villain_3b.items()) / TOTAL_COMBOS
    k_tapered = (K_IP if ip else K_OOP) * max(TAPER_FLOOR, min(v3b_weight / TAPER_REF, 1.0))
    ceiling = lints.F3B_CEILING_IP if ip else lints.F3B_CEILING_OOP
    k = max(k_tapered, 1.0 - ceiling + MDF_BUFFER)  # taper may not breach MDF

    # open-weighted combos: a hand contributes proportionally to how often hero opens it
    ow = {h: COMBO_COUNT[h] * hero_open[h] for h in hero_open}
    open_combos = sum(ow.values())
    cont_target = k * open_combos
    value_target = VALUE_BLUFF_SPLIT * TARGET_4BET_OF_OPEN * open_combos
    bluff_target = (1 - VALUE_BLUFF_SPLIT) * TARGET_4BET_OF_OPEN * open_combos

    eq_allin = {h: equity_vs_range(h, matrix, villain_cont) for h in hero_open}
    eq_range = {h: equity_vs_range(h, matrix, villain_3b) for h in hero_open}
    call_score = {h: 0.7 * eq_range[h] + 0.3 * _playability(h) for h in hero_open}

    dist: Dict[str, Dict[str, float]] = {}

    # 1. Value 4-bets: top of the open by all-in equity vs the continue range.
    spent = 0.0
    for h in sorted(hero_open, key=lambda x: eq_allin[x], reverse=True):
        if spent >= value_target:
            break
        dist[h] = dict(DIST_VALUE_4BET)
        spent += ow[h] * DIST_VALUE_4BET["raise_2.2x"]

    # 2. Suited bluff 4-bets: named pool ∩ open, then backfill by playability.
    extra = sorted((h for h in hero_open if _is_suited(h) and h not in BLUFF_4BET_POOL),
                   key=lambda x: _playability(x), reverse=True)
    spent = 0.0
    for h in BLUFF_4BET_POOL + extra:
        if spent >= bluff_target:
            break
        if h in dist or h not in hero_open:
            continue
        dist[h] = dict(DIST_BLUFF_4BET)
        spent += ow[h] * DIST_BLUFF_4BET["raise_2.2x"]

    # 3. Flats: best remaining opens by (equity + playability) until continue == k.
    def _cont() -> float:
        return sum(ow[h] * (d.get("call", 0.0) + d.get("raise_2.2x", 0.0)) for h, d in dist.items())

    for h in sorted(hero_open, key=lambda x: call_score[x], reverse=True):
        c = _cont()
        if c >= cont_target:
            break
        if h in dist:
            continue
        w = min(CALL_CORE_W, (cont_target - c) / ow[h]) if ow[h] > 0 else CALL_CORE_W
        dist[h] = {"call": round(w, 4), "fold": round(1 - w, 4)}

    # 4. Junk floor: EVERY unassigned hand keeps a thin call (station mask).
    #    This also covers the SQUEEZE case: preflop_classifier routes any second
    #    raise to vs_3bet from `raises == 2` alone — it does NOT distinguish the
    #    opener-facing-a-3bet from a cold-caller-facing-a-squeeze. Pure-folding
    #    non-open hands would make cold-callers (esp. station/fish) fold ~everything
    #    to a squeeze — the exact weakness-to-aggression leak this regen targets.
    #    Open-weighted metrics ignore non-open hands, so this moves no lint. A
    #    proper squeeze node keyed on the cold-call range is the real fix (tracked).
    for h in CANONICAL_HANDS:
        dist.setdefault(h, {"call": JUNK_CALL, "fold": round(1 - JUNK_CALL, 4)})

    return {h: _norm(dist[h]) for h in CANONICAL_HANDS}


def _node_metrics(hero: str, node: Dict[str, Dict[str, float]], rfi: Dict) -> tuple:
    """(fold_to_3bet, 4bet_frac) relative to the open range — matches the lint."""
    open_node = rfi[hero]
    owt = cont = fourbet = 0.0
    for h, od in open_node.items():
        owv = od.get("raise_2.5bb", 0.0)
        if owv <= 0:
            continue
        c = COMBO_COUNT[h] * owv
        owt += c
        d = node[h]
        cont += c * (d.get("call", 0.0) + d.get("raise_2.2x", 0.0))
        fourbet += c * d.get("raise_2.2x", 0.0)
    return (1 - cont / owt, fourbet / owt) if owt else (0.0, 0.0)


def patch_base() -> None:
    with open(_BASE) as f:
        chart = json.load(f)
    with open(_MATRIX) as f:
        matrix = json.load(f)["matrix"]
    rfi, vs_open, nodes = chart["rfi"], chart["vs_open"], chart.get("vs_3bet", {})
    if not nodes:
        raise SystemExit("base chart has no vs_3bet section")

    # Run-order guard: vs_3bet's villain model reads on-disk vs_open, so a stale
    # (un-regenerated) vs_open silently produces wrong nodes. A stale vs_open still
    # fails its own BB-defend floors — refuse rather than build against it.
    stale = lints.lint_bb_defend_floors(chart)
    if stale:
        raise SystemExit("vs_open looks stale (run build_vs_open first):\n  " + "\n  ".join(stale[:5]))

    print(f"patching {_BASE}")
    print(f"  {'node':<14} {'fold-to-3bet':>13} {'4bet%open':>10}")
    built = {}
    for node_name in nodes:
        hero, villain = node_name.split("_vs_")
        node = build_node(hero, villain, rfi, vs_open, matrix)
        built[node_name] = node
        f3b, fb = _node_metrics(hero, node, rfi)
        print(f"  {node_name:<14} {100*f3b:>12.1f}% {100*fb:>9.1f}%")

    chart["vs_3bet"] = built
    # gate on the shared lints — refuse to write a chart that fails. Structural
    # lints (weights/vocab/completeness) run over the whole chart; the strategic
    # ones are scoped to vs_3bet (anti_clone too — the old vs_4bet stub isn't ours yet).
    fails = (lints.lint_weights_sum(chart)
             + lints.lint_legal_vocab(chart)
             + lints.lint_completeness(chart)
             + lints.lint_anti_clone(chart, branches=("vs_3bet",))
             + lints.lint_vs3bet_fold_to_3bet(chart)
             + lints.lint_fourbet_band(chart))
    if fails:
        for msg in fails[:12]:
            print(f"  LINT FAIL: {msg}")
        raise SystemExit(f"refusing to write — {len(fails)} lint failures")

    with open(_BASE, "w") as f:
        json.dump(chart, f, indent=2)
        f.write("\n")
    print("  done — vs_3bet regenerated per-node; run build_vs4bet_defense next.")


if __name__ == "__main__":
    patch_base()
