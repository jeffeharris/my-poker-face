#!/usr/bin/env python3
"""Static preflop-chart lints (VALIDATION_SUITE_SPEC.md §1).

Pure-JSON structural + strategic checks — no eval7, no simulation, milliseconds.
Each lint returns a list of human-readable failure strings (empty == pass), so a
regen script can refuse to write a chart that fails and CI can gate on the same
set. Importable by generators (write-time refusal) and tests.

Run a full report against the live data dir:
    docker compose exec -T backend python -m poker.strategy.lints

Some lints are EXPECTED to fail against the current charts — that's the point:
they document the known-open bugs (`vs_3bet` copied range, BB overfold) until the
generators that fix them land. The report prints PASS/FAIL per lint per chart.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(_HERE, "data")

RANKS = "AKQJT98765432"

# Action vocabulary legal at 100bb, per branch. Depth charts add `jam` (handled
# separately). A cell may use any subset (a pure fold is {fold: 1.0}).
LEGAL_ACTIONS: Dict[str, set] = {
    "rfi": {"raise_2.5bb", "fold"},
    "vs_open": {"raise_3x", "call", "fold"},
    "vs_3bet": {"raise_2.2x", "call", "fold"},  # no jam in 3-bet pots at 100bb
    "vs_squeeze": {
        "raise_2.2x",
        "call",
        "fold",
    },  # cold-caller faces a 3-bet; same vocab as vs_3bet
    "vs_4bet": {"jam", "call", "fold"},
}
FACING_BRANCHES = ("vs_open", "vs_3bet", "vs_4bet")
# Structural lints (weights/vocab) also cover the OPTIONAL vs_squeeze section (base
# chart only — depth/archetype charts omit it and fall back to vs_3bet). vs_squeeze
# is deliberately NOT in FACING_BRANCHES: the strategic lints (anti-clone, fold-to-
# 3bet, 4-bet band) are opener-range-relative and don't apply to a cold-call range.
STRUCTURAL_BRANCHES = ("rfi", "vs_open", "vs_3bet", "vs_squeeze", "vs_4bet")

# BB vs_open defend floors (PREFLOP_DEFENSE_REGEN §5.1).
BB_DEFEND_FLOOR = {"UTG": 0.30, "HJ": 0.36, "CO": 0.43, "BTN": 0.52, "SB": 0.58}

# vs_3bet fold-to-3bet ceilings, by hero's postflop position (buffer under the
# 65.2% auto-profit line).
F3B_CEILING_OOP = 0.65
F3B_CEILING_IP = 0.58

# vs_4bet fold-to-4bet ceiling (relative to hero's 3-bet range). Villain 4-bets to
# ~16.5bb risking ~14 to win ~9 → auto-profits at ~61% folds; ceiling sits under it.
F4B_CEILING = 0.62

# 4-bet (raise_2.2x) mass as a fraction of the opener's open range, per vs_3bet node.
FOURBET_BAND = (0.06, 0.14)

# The depth value/bluff cliff: when a vs_open cell has no explicit intent tag,
# generate_depth_charts FALLS BACK to treating a 3-bet weight ≥ this as value
# (jams it at 25bb), below as bluff. The tag is primary (DEPTH_INTENT_TAG_TECHDEBT.md);
# this threshold is only the legacy fallback for charts that predate vs_open_intent.
VALUE_RAISE_THRESHOLD = 0.50
# Valid intent-tag values for a vs_open 3-bet cell.
INTENT_VALUES = ("value", "bluff")

WEIGHT_SUM_TOL = 0.01
DEPTH_FLAT_RETENTION = 0.40  # 50bb node must keep ≥40% of the 100bb flat mass

# BB defend floors at depth — the BB closes the action and gets a price, so
# short-stacked it should jam its defense vs a steal, not collapse. (Only the
# steal-ish openers CO/BTN/SB; BB-vs-UTG/HJ defends tight by design.)
DEPTH_BB_DEFEND_FLOOR = {
    50: {"CO": 0.32, "BTN": 0.40, "SB": 0.44},
    25: {"CO": 0.27, "BTN": 0.34, "SB": 0.38},
}


def _combos(hand: str) -> int:
    return 6 if len(hand) == 2 else (4 if hand[2] == "s" else 12)


def canonical_hands() -> List[str]:
    """The 169 canonical hand classes (13 pairs + 78 suited + 78 offsuit)."""
    out: List[str] = []
    for i in range(len(RANKS)):
        for j in range(i, len(RANKS)):
            if i == j:
                out.append(RANKS[i] * 2)
            else:
                out.append(RANKS[i] + RANKS[j] + "s")
                out.append(RANKS[i] + RANKS[j] + "o")
    return out


CANONICAL = canonical_hands()
TOTAL_COMBOS = float(sum(_combos(h) for h in CANONICAL))  # 1326


def _continue_mass(node: Dict[str, Dict[str, float]]) -> float:
    """Combo-weighted call+raise+jam fraction over all 169 hands."""
    tot = defend = 0.0
    for h, d in node.items():
        c = _combos(h)
        tot += c
        defend += c * (
            d.get("call", 0.0)
            + d.get("raise_3x", 0.0)
            + d.get("raise_2.2x", 0.0)
            + d.get("jam", 0.0)
        )
    return defend / tot if tot else 0.0


def _vs3bet_is_ip(node_name: str) -> bool:
    """vs_3bet node = OPENER_vs_3BETTOR. Opener is IP postflop iff the 3-bettor is
    a blind (acts first postflop) — except SB-open vs BB-3bet, where SB is OOP."""
    opener, threebettor = node_name.split("_vs_")
    return threebettor in ("SB", "BB") and not (opener == "SB" and threebettor == "BB")


# ── Structural lints (any branch) ─────────────────────────────────────────────


def lint_weights_sum(chart: Dict) -> List[str]:
    fails = []
    for scenario in STRUCTURAL_BRANCHES:
        for node_name, node in chart.get(scenario, {}).items():
            for h, d in node.items():
                if any(v < 0 for v in d.values()):
                    fails.append(f"{scenario}/{node_name}/{h}: negative weight {d}")
                s = sum(d.values())
                if abs(s - 1.0) > WEIGHT_SUM_TOL:
                    fails.append(f"{scenario}/{node_name}/{h}: weights sum {s:.4f} ≠ 1.0")
    return fails


def lint_legal_vocab(chart: Dict, *, allow_jam: bool = False) -> List[str]:
    fails = []
    for scenario in STRUCTURAL_BRANCHES:
        legal = set(LEGAL_ACTIONS[scenario])
        if allow_jam:
            legal |= {"jam"}
        for node_name, node in chart.get(scenario, {}).items():
            for h, d in node.items():
                illegal = set(d) - legal
                if illegal:
                    fails.append(f"{scenario}/{node_name}/{h}: illegal action(s) {sorted(illegal)}")
    return fails


def lint_completeness(chart: Dict) -> List[str]:
    fails = []
    expected_nodes = {"rfi": 5, "vs_open": 15, "vs_3bet": 15, "vs_4bet": 15}
    for scenario, n in expected_nodes.items():
        nodes = chart.get(scenario, {})
        if len(nodes) != n:
            fails.append(f"{scenario}: {len(nodes)} nodes (expected {n})")
        for node_name, node in nodes.items():
            if len(node) != 169:
                fails.append(f"{scenario}/{node_name}: {len(node)} hands (expected 169)")
    # vs_squeeze is OPTIONAL (base chart only). When present it has exactly the 20
    # per-opener {caller}_vs_{opener}_vs_{squeezer} nodes: HJ/CO/BTN/SB callers ×
    # each earlier opener × each later squeezer (HJ:4, CO:6, BTN:6, SB:4).
    squeeze = chart.get("vs_squeeze", {})
    if squeeze:
        if len(squeeze) != 20:
            fails.append(f"vs_squeeze: {len(squeeze)} nodes (expected 20)")
        for node_name, node in squeeze.items():
            if len(node) != 169:
                fails.append(f"vs_squeeze/{node_name}: {len(node)} hands (expected 169)")
    return fails


# ── Postflop lints ──────────────────────────────────────────────────────────
# The postflop charts (postflop_strategies*.json) are a FLAT map of
# node_key -> {action: prob}, NOT the [scenario][node][hand] preflop shape, so
# they need their own structural lints (they had ZERO coverage before this).
# Fixed legal postflop actions; sized bet_<pct>/raise_<pct> are validated via
# action_vocab.is_sized.
POSTFLOP_FIXED_ACTIONS = {"check", "call", "fold", "jam"}


def _postflop_nodes(chart: Dict):
    """Yield (node_key, leaf) for every non-meta node. A leaf is the
    {action: prob} dict the bot samples at that node."""
    for key, leaf in chart.items():
        if key == "meta":
            continue
        yield key, leaf


def lint_postflop_nonempty(chart: Dict) -> List[str]:
    """Every node is a non-empty {action: float} leaf (no empty/nested nodes)."""
    fails = []
    for key, leaf in _postflop_nodes(chart):
        if not isinstance(leaf, dict) or not leaf:
            fails.append(f"{key}: empty or non-dict node")
            continue
        if not all(isinstance(v, int | float) for v in leaf.values()):
            fails.append(f"{key}: non-leaf node (values not all numeric)")
    return fails


def lint_postflop_weights_sum(chart: Dict) -> List[str]:
    """Each node's action weights sum to 1.0 (±tol) and are non-negative."""
    fails = []
    for key, leaf in _postflop_nodes(chart):
        if not isinstance(leaf, dict) or not leaf:
            continue  # reported by lint_postflop_nonempty
        if any(isinstance(v, int | float) and v < 0 for v in leaf.values()):
            fails.append(f"{key}: negative weight {leaf}")
        s = sum(v for v in leaf.values() if isinstance(v, int | float))
        if abs(s - 1.0) > WEIGHT_SUM_TOL:
            fails.append(f"{key}: weights sum {s:.4f} ≠ 1.0")
    return fails


def lint_postflop_legal_vocab(chart: Dict) -> List[str]:
    """Actions are postflop-legal: check/call/fold/jam or sized bet_/raise_."""
    from .action_vocab import is_sized

    fails = []
    for key, leaf in _postflop_nodes(chart):
        if not isinstance(leaf, dict):
            continue
        illegal = {a for a in leaf if a not in POSTFLOP_FIXED_ACTIONS and not is_sized(a)}
        if illegal:
            fails.append(f"{key}: illegal action(s) {sorted(illegal)}")
    return fails


POSTFLOP_LINTS = (
    lint_postflop_nonempty,
    lint_postflop_weights_sum,
    lint_postflop_legal_vocab,
)


# Which node-name token is the OPENER (whose RFI range drives the spot).
_OPENER_TOKEN = {"vs_open": 1, "vs_3bet": 0, "vs_4bet": 1}


def lint_anti_clone(chart: Dict, branches=FACING_BRANCHES) -> List[str]:
    """Flag a CROSS-OPENER byte-identical clone — the copied-range bug (one range
    pasted across positions).

    Same-opener nodes may legitimately share a distribution: these charts are
    opener-keyed, not defender-seat-keyed (an accepted simplification — e.g.
    CO-vs-UTG and HJ-vs-UTG defend a UTG open the same), so that is NOT flagged.
    The bug this guards is the old all-15-identical paste, which spans openers.
    """
    fails = []
    for scenario in branches:
        idx = _OPENER_TOKEN[scenario]
        seen: Dict[str, tuple] = {}  # value -> (node_name, opener)
        for node_name, node in chart.get(scenario, {}).items():
            opener = node_name.split("_vs_")[idx]
            key = json.dumps(node, sort_keys=True)
            if key in seen and seen[key][1] != opener:
                fails.append(
                    f"{scenario}/{node_name}: byte-identical to {seen[key][0]} "
                    f"(different opener — copied range)"
                )
            elif key not in seen:
                seen[key] = (node_name, opener)
    return fails


# ── Strategic lints (100bb base) ──────────────────────────────────────────────


def lint_bb_defend_floors(chart: Dict) -> List[str]:
    fails = []
    for node_name, node in chart.get("vs_open", {}).items():
        defender, opener = node_name.split("_vs_")
        if defender != "BB":
            continue
        d = _continue_mass(node)
        floor = BB_DEFEND_FLOOR[opener]
        if d + 1e-9 < floor:
            fails.append(f"vs_open/{node_name}: BB defend {100*d:.1f}% < floor {100*floor:.0f}%")
    return fails


def lint_vs3bet_fold_to_3bet(chart: Dict) -> List[str]:
    """Fold-to-3bet relative to the opener's own range, per node — the lint that
    catches the position-invariant copied range. Ceiling depends on hero IP/OOP."""
    fails = []
    rfi = chart.get("rfi", {})
    for node_name, node in chart.get("vs_3bet", {}).items():
        opener, _ = node_name.split("_vs_")
        open_node = rfi.get(opener, {})
        owt = cont = 0.0
        for h, od in open_node.items():
            ow = od.get("raise_2.5bb", 0.0)
            if ow <= 0:
                continue
            c = _combos(h) * ow
            owt += c
            dd = node.get(h, {})
            cont += c * (dd.get("call", 0.0) + dd.get("raise_2.2x", 0.0))
        if owt <= 0:
            continue
        f3b = 1.0 - cont / owt
        ceiling = F3B_CEILING_IP if _vs3bet_is_ip(node_name) else F3B_CEILING_OOP
        pos = "IP" if _vs3bet_is_ip(node_name) else "OOP"
        if f3b > ceiling + 1e-9:
            fails.append(
                f"vs_3bet/{node_name}: fold-to-3bet {100*f3b:.1f}% > {pos} ceiling {100*ceiling:.0f}%"
            )
    return fails


def lint_vs4bet_fold_to_4bet(chart: Dict) -> List[str]:
    """Fold-to-4bet relative to hero's own 3-BET range, per node (hero = 3-bettor).
    The 3-bet weights come from vs_open[hero_vs_villain].raise_3x; continue = jam+call."""
    fails = []
    vs_open = chart.get("vs_open", {})
    for node_name, node in chart.get("vs_4bet", {}).items():
        hero, villain = node_name.split("_vs_")
        threebet_node = vs_open.get(f"{hero}_vs_{villain}", {})
        twt = cont = 0.0
        for h, td in threebet_node.items():
            tw = td.get("raise_3x", 0.0)
            if tw <= 0:
                continue
            c = _combos(h) * tw
            twt += c
            d = node.get(h, {})
            cont += c * (d.get("jam", 0.0) + d.get("call", 0.0))
        if twt <= 0:
            continue
        f4b = 1.0 - cont / twt
        if f4b > F4B_CEILING + 1e-9:
            fails.append(
                f"vs_4bet/{node_name}: fold-to-4bet {100*f4b:.1f}% > ceiling {100*F4B_CEILING:.0f}%"
            )
    return fails


def lint_fourbet_band(chart: Dict) -> List[str]:
    """4-bet (raise_2.2x) mass ∈ [6%, 14%] of the opener's open range, per node."""
    fails = []
    rfi = chart.get("rfi", {})
    lo, hi = FOURBET_BAND
    for node_name, node in chart.get("vs_3bet", {}).items():
        opener, _ = node_name.split("_vs_")
        open_node = rfi.get(opener, {})
        owt = fourbet = 0.0
        for h, od in open_node.items():
            ow = od.get("raise_2.5bb", 0.0)
            if ow <= 0:
                continue
            owt += _combos(h) * ow
            fourbet += _combos(h) * ow * node.get(h, {}).get("raise_2.2x", 0.0)
        if owt <= 0:
            continue
        frac = fourbet / owt
        if not (lo - 1e-9 <= frac <= hi + 1e-9):
            fails.append(
                f"vs_3bet/{node_name}: 4-bet {100*frac:.1f}% of opens outside [{100*lo:.0f},{100*hi:.0f}]%"
            )
    return fails


def lint_vs_open_intent(chart: Dict) -> List[str]:
    """Every vs_open 3-bet cell carries an explicit value/bluff intent tag.

    The tag (in the sibling ``vs_open_intent`` section) is what depth derivation
    reads to decide jam-vs-fold at 25bb — replacing the old implicit ``raise_3x
    >= 0.50`` cliff (DEPTH_INTENT_TAG_TECHDEBT.md). This lint guards the tag
    instead of the weight: it checks presence + validity, NOT the weight side, so
    3-bet frequencies are free.

    **vs_open only.** `t_vs_3bet` gates on the **fold** weight (a continuous
    'do I continue' signal with no ambiguous band), so vs_3bet needs no tag.

    Legacy/optional: a chart with no ``vs_open_intent`` section at all is skipped
    (depth derivation falls back to the weight threshold), matching the optional
    treatment of ``vs_squeeze``.
    """
    intent_section = chart.get("vs_open_intent")
    if intent_section is None:
        return []
    fails = []
    for node_name, node in chart.get("vs_open", {}).items():
        node_intent = intent_section.get(node_name, {})
        for h, d in node.items():
            if d.get("raise_3x", 0.0) <= 0:
                continue
            tag = node_intent.get(h)
            if tag is None:
                fails.append(f"vs_open/{node_name}/{h}: 3-bet cell missing intent tag")
            elif tag not in INTENT_VALUES:
                fails.append(
                    f"vs_open_intent/{node_name}/{h}: invalid intent {tag!r} "
                    f"(expected one of {INTENT_VALUES})"
                )
    return fails


# ── Depth-chart lints (need the 100bb base for comparison) ────────────────────


def lint_depth_rfi_passthrough(base: Dict, depth: Dict, depth_bb: int) -> List[str]:
    """Depth RFI must equal the 100bb RFI (t_rfi is identity — catches drift)."""
    fails = []
    for pos, node in base.get("rfi", {}).items():
        if json.dumps(node, sort_keys=True) != json.dumps(
            depth.get("rfi", {}).get(pos, {}), sort_keys=True
        ):
            fails.append(f"{depth_bb}bb rfi/{pos}: differs from 100bb RFI")
    return fails


def lint_depth_flat_retention(base: Dict, depth: Dict, depth_bb: int) -> List[str]:
    """Each depth vs_open node keeps ≥40% of the 100bb node's flat mass (catches
    the flat-deletion cliff)."""
    fails = []
    for node_name, base_node in base.get("vs_open", {}).items():
        base_flat = sum(_combos(h) * d.get("call", 0.0) for h, d in base_node.items())
        if base_flat <= 0:
            continue
        depth_node = depth.get("vs_open", {}).get(node_name, {})
        depth_flat = sum(_combos(h) * d.get("call", 0.0) for h, d in depth_node.items())
        if depth_flat < DEPTH_FLAT_RETENTION * base_flat - 1e-9:
            fails.append(
                f"{depth_bb}bb vs_open/{node_name}: flat mass "
                f"{100*depth_flat/base_flat:.0f}% of 100bb (< {100*DEPTH_FLAT_RETENTION:.0f}%)"
            )
    return fails


def lint_depth_bb_defense(_base: Dict, depth: Dict, depth_bb: int) -> List[str]:
    """BB defends wide enough at depth (continue = call+jam+raise_3x over the BB
    vs_open node) — catches the 25bb flat-drop overfold."""
    fails = []
    for opener, floor in DEPTH_BB_DEFEND_FLOOR.get(depth_bb, {}).items():
        node = depth.get("vs_open", {}).get(f"BB_vs_{opener}", {})
        tot = cont = 0.0
        for h, d in node.items():
            c = _combos(h)
            tot += c
            cont += c * (d.get("call", 0.0) + d.get("jam", 0.0) + d.get("raise_3x", 0.0))
        if tot and cont / tot + 1e-9 < floor:
            fails.append(
                f"{depth_bb}bb vs_open/BB_vs_{opener}: BB defend "
                f"{100*cont/tot:.1f}% < floor {100*floor:.0f}%"
            )
    return fails


# ── Runner ────────────────────────────────────────────────────────────────────

BASE_LINTS = (
    lint_weights_sum,
    lint_legal_vocab,
    lint_completeness,
    lint_anti_clone,
    lint_bb_defend_floors,
    lint_vs3bet_fold_to_3bet,
    lint_vs4bet_fold_to_4bet,
    lint_fourbet_band,
    lint_vs_open_intent,
)


def lint_base_chart(chart: Dict) -> Dict[str, List[str]]:
    """Run every base-chart lint. Returns {lint_name: failures}."""
    return {fn.__name__: fn(chart) for fn in BASE_LINTS}


def run_report() -> int:
    """Print a PASS/FAIL report over the live data dir. Returns failure count."""
    with open(os.path.join(_DATA, "preflop_100bb_6max.json")) as f:
        base = json.load(f)

    total_fail = 0
    print("=== 100bb base ===")
    for name, fails in lint_base_chart(base).items():
        mark = "PASS" if not fails else f"FAIL ({len(fails)})"
        print(f"  [{mark:>9}] {name}")
        for msg in fails[:8]:
            print(f"             - {msg}")
        if len(fails) > 8:
            print(f"             … +{len(fails) - 8} more")
        total_fail += len(fails)

    for depth_bb in (50, 25):
        path = os.path.join(_DATA, f"preflop_{depth_bb}bb_6max.json")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            depth = json.load(f)
        print(f"=== {depth_bb}bb depth ===")
        # RFI passthrough applies at every depth; the flat-retention floor is a
        # 50bb-only check (per spec) — heavy flat-drop is correct at 25bb.
        depth_lints = [lint_depth_rfi_passthrough, lint_depth_bb_defense]
        if depth_bb == 50:
            depth_lints.append(lint_depth_flat_retention)
        for fn in depth_lints:
            fails = fn(base, depth, depth_bb)
            mark = "PASS" if not fails else f"FAIL ({len(fails)})"
            print(f"  [{mark:>9}] {fn.__name__}")
            for msg in fails[:8]:
                print(f"             - {msg}")
            total_fail += len(fails)

    for fname in (
        "postflop_strategies.json",
        "postflop_strategies_low_spr.json",
        "postflop_strategies_3bp.json",
    ):
        path = os.path.join(_DATA, fname)
        if not os.path.exists(path):
            continue
        with open(path) as f:
            postflop = json.load(f)
        print(f"=== postflop: {fname} ({sum(1 for k in postflop if k != 'meta')} nodes) ===")
        for fn in POSTFLOP_LINTS:
            fails = fn(postflop)
            mark = "PASS" if not fails else f"FAIL ({len(fails)})"
            print(f"  [{mark:>9}] {fn.__name__}")
            for msg in fails[:8]:
                print(f"             - {msg}")
            total_fail += len(fails)

    print(
        f"\n{total_fail} total failures "
        f"(some are EXPECTED until vs_3bet/vs_open generators land — see docstring)."
    )
    return total_fail


if __name__ == "__main__":
    raise SystemExit(1 if run_report() else 0)
