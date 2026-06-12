#!/usr/bin/env python3
"""Rebuild the base chart's `vs_open` section (defender faces a 2.5bb open).

Implements docs/strategy/PREFLOP_DEFENSE_REGEN_SPEC.md §3. Companion to
`build_vs3bet_defense.py` / `build_vs4bet_defense.py`; this is **step 1** of the
strict regen order (`vs_open → vs_3bet → vs_4bet → depth → archetypes`).

The order matters because `build_vs3bet_defense` (its §2 per-node refactor is
done) reads each villain's 3-bet range from the `vs_open` node this script writes,
and `build_vs4bet_defense` then reads `vs_3bet`'s 4-bet range. So vs_open must be
regenerated first; the downstream generators refuse to run against a stale upstream.

Two node classes, two policies
------------------------------
1. **BB defends** (BB closes the action, always OOP postflop). Defense was a
   measured overfold (review §2): BB defends ~44.6% vs BTN where ~58% is sound.
   These nodes get the full **MDF-anchored widen** to the §3.2 targets.

2. **Non-BB cold-defense** (HJ/CO/BTN/SB call or 3-bet an earlier opener). The
   review flagged these for a *face-up* 3-bet range (only premiums; review §5),
   not for defend width. So we **preserve each node's current defend width** and
   only **re-polarize the 3-bet range**: value top + suited bluffs (A5s–A2s,
   suited broadways/gappers). Offsuit hands 3-bet only as VALUE, never as bluffs —
   AKo on every node, and AQo/KQo/AJo additionally on merged/wide nodes where they
   dominate the calling range; weak offsuit-broadway flats are dropped vs early
   openers. (To instead retune non-BB defend widths from scratch, set
   PRESERVE_NONBB_WIDTH = False and fill in NONBB_TARGETS.)

Action keys match the live schema exactly: ``{raise_3x, call, fold}``. The
``raise_3x`` weight is load-bearing downstream: ``generate_depth_charts.t_vs_open``
classifies a hand as a *value* 3-bet when ``raise_3x >= 0.50`` (VALUE_RAISE_THRESHOLD)
and jams it at 25bb. So value 3-bets carry raise_3x ≥ 0.50 and **bluff** 3-bets
carry raise_3x < 0.50 (treated as marginal → dropped at 25bb, never bluff-jammed).

Run inside the backend container, then cascade:
    docker compose exec -T backend python -m poker.strategy.data.build_vs_open
    docker compose exec -T backend python -m poker.strategy.data.build_vs3bet_defense
    docker compose exec -T backend python -m poker.strategy.data.build_vs4bet_defense
    docker compose exec -T backend python -m poker.strategy.data.generate_depth_charts
    docker compose exec -T backend python -m experiments.build_archetype_charts

(Depth and archetype charts both derive independently from the 100bb base, so the
order between *those two* is functionally moot — but listed depth-then-archetypes
to match the §4 strict order. build_vs3bet_defense's own docstring still lists the
reverse; harmless, worth reconciling when that script is next touched.)
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Tuple

from poker.strategy import lints
from poker.strategy.data._chart_gen import (
    _is_suited,
    _norm,
    _open_range,
    _playability,
)
from poker.strategy.data.generate_push_fold_nash import (
    CANONICAL_HANDS,
    COMBO_COUNT,
    equity_vs_range,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.join(_HERE, "preflop_100bb_6max.json")
_MATRIX = os.path.join(_HERE, "push_fold_equity_matrix.json")

TOTAL_COMBOS = float(sum(COMBO_COUNT[h] for h in CANONICAL_HANDS))  # 1326

# Value/bluff 3-bet weights. Depth derivation reads the explicit per-hand intent
# tag (emitted into vs_open_intent), NOT these weights, so they are free to be any
# frequency — kept bimodal here only because that's the current believable shape.
# (Historically value had to sit ≥ 0.50 and bluff < 0.50 so the 25bb derivation
# could tell them apart from the weight alone; the tag retired that constraint —
# DEPTH_INTENT_TAG_TECHDEBT.md.)
VALUE_RAISE_W = 0.85  # raise_3x weight on a value 3-bet hand (rest flats/folds)
BLUFF_RAISE_W = 0.35  # raise_3x weight on a suited bluff 3-bet (rest folds)
CALL_CORE_W = 0.85    # call weight in the core of the flat range (§3 weight discipline)

# Selector blends — rank by equity vs the opener's range blended with playability.
# Raw all-in equity is the WRONG selector for merged value: the dominant outcome
# of a BvB 3-bet isn't all-in (SB folds or calls; you play a 3-bet pot), so race
# equity overcredits mid pairs / weak offsuit Ax that rank high but DON'T dominate
# the continue range, while suited broadways (KTs/QTs/JTs) that DO dominate the
# KQo/QTo-type calling hands rank low on all-in equity. Playability proxies that
# domination. (§2 uses the same eq+playability blend for the call region.)
VALUE_EQ_W = 0.6   # value selector = 0.6*eq + 0.4*playability
CALL_EQ_W = 0.7    # call  selector = 0.7*eq + 0.3*playability (§2)

# §3.2 BB defense targets (combo-weighted % of all 169 hands): (defend_total, threebet).
# call_total = defend_total - threebet. Floors the review/lint encode as the BB
# overfold fix.
BB_TARGETS: Dict[str, Tuple[float, float]] = {
    "UTG": (0.34, 0.05),
    "HJ": (0.40, 0.06),
    "CO": (0.48, 0.085),
    "BTN": (0.58, 0.12),
    "SB": (0.65, 0.15),
}

# Value share of the 3-bet mass, keyed on the OPENER's width. Vs tight early
# opens the opener's continue-vs-3bet range is strong, so 3-bets are POLARIZED
# (low value share, suited-wheel bluffs do the work). Vs wide late opens
# (BTN ~47%, SB ~40%) that continue range is capped, so 3-bets are MERGED —
# TT/AJs/KQs/AQo are value against the wide range, not bluffs, and a wide linear
# top is correct (PREFLOP_DEFENSE_REGEN §3, option 3). The value top falls out of
# "top by equity vs the opener range" widening automatically as the opener does.
# The 25bb consequence (these re-jam over a late open) is standard short-stack
# play; only the low-equity bluff slice must stay below the depth cliff, which it
# does by construction (BLUFF_RAISE_W < VALUE_RAISE_THRESHOLD).
VALUE_SHARE_BY_OPENER: Dict[str, float] = {
    "UTG": 0.55, "HJ": 0.55, "CO": 0.62, "BTN": 0.78, "SB": 0.82,
}

# Suited-only bluff-3bet pool, priority order (blockers first, then playable
# suited broadways/connectors/gappers). Everything here is suited: the only
# offsuit 3-bets are VALUE (AKo always; AQo/KQo/AJo on merged nodes), never
# bluffs. Mirrors the build_vs3bet/vs4bet suited-only-BLUFF invariant the lints guard.
BLUFF_3BET_POOL: List[str] = [
    "A5s", "A4s", "A3s", "A2s",
    "KJs", "QJs", "JTs", "K9s", "Q9s", "J9s", "T9s",
    "98s", "87s", "76s", "65s", "54s",
]

# Modest pool widen for the wide-open nodes only (BTN/SB openers): the merged
# value top eats the natural-3bet hands, so the (small) bluff slice needs a few
# more suited blockers/gappers to reach target. Still suited-only, still ≤ cliff.
BLUFF_3BET_POOL_WIDE: List[str] = BLUFF_3BET_POOL + [
    "K5s", "K4s", "K3s", "K2s", "Q8s", "J8s", "T8s", "97s", "86s",
]
WIDE_OPENERS = {"BTN", "SB"}

# Opener width ≥ this value share ⇒ MERGED construction (bluff pool open to the
# value pass); below it ⇒ POLARIZED (pool reserved for sub-cliff bluffs).
MERGED_THRESHOLD = 0.70

# Non-BB cold-defense: preserve each node's current DEFEND width but set the
# 3-bet portion to an opener-keyed cold-3bet target (not the inflated face-up
# mass the old flat [0.04, 0.13] clamp preserved — a 13% cold 3-bet vs a 11.5%
# UTG open is nonsense). Cold IP/OOP 3-betting tightens vs early opens, widens vs
# late. Keyed on the OPENER (non-BB openers ∈ {UTG, HJ, CO, BTN}).
PRESERVE_NONBB_WIDTH = True
NONBB_THREEBET_BY_OPENER: Dict[str, float] = {
    "UTG": 0.045, "HJ": 0.05, "CO": 0.07, "BTN": 0.10,
}
NONBB_TARGETS: Dict[str, Tuple[float, float]] = {}  # only used if PRESERVE_NONBB_WIDTH is False


def _current_masses(node: Dict[str, Dict[str, float]]) -> Tuple[float, float]:
    """(defend_total, threebet_total) of an existing node, as combo fractions."""
    defend = three = 0.0
    for h, d in node.items():
        c = COMBO_COUNT[h]
        defend += c * (d.get("call", 0.0) + d.get("raise_3x", 0.0))
        three += c * d.get("raise_3x", 0.0)
    return defend / TOTAL_COMBOS, three / TOTAL_COMBOS


def build_node(
    opener_pos: str,
    rfi: Dict[str, Dict[str, Dict[str, float]]],
    matrix: Dict[str, Dict[str, float]],
    defend_total: float,
    threebet_total: float,
    value_share: float,
    bluff_pool: List[str],
    merged: bool,
) -> Tuple[Dict[str, Dict[str, float]], Dict[str, str]]:
    """Generate one vs_open node from the opener's range + defend/3bet targets.

    Returns ``(node, intent)``: the per-hand action distribution and a parallel
    ``{hand: "value"|"bluff"}`` map covering exactly the 3-bet hands. Intent is
    **recorded at placement** (value pass vs bluff pass), not inferred from the
    final weight — it's the explicit signal the depth derivation reads instead of
    the `raise_3x >= 0.50` cliff (DEPTH_INTENT_TAG_TECHDEBT.md). The chart's
    `vs_open` section carries the node; a sibling `vs_open_intent` section carries
    the map.

    Mass-budget fill: value 3-bets off the top by the blend, suited bluffs, then
    flats by (equity + playability). 3-bet weights are **bimodal** — value snaps
    to VALUE_RAISE_W (≥ cliff → jams shallow), bluff to BLUFF_RAISE_W (< cliff →
    never bluff-jams) — so no 3-bet lands in the ambiguous band the depth
    derivation can't read.

    Value/bluff arbitration: on POLARIZED nodes (tight opener) the designated
    bluff-pool hands are RESERVED for the bluff pass — vs a strong continue range
    a suited wheel ace is a bluff, not value, so it must stay sub-cliff. On MERGED
    late-open nodes the pool is open to the value pass (A5s dominates a wide
    calling range = value there). The bluff pass then backfills any unspent budget
    from the remaining suited hands by playability, so the 3-bet target is
    actually reachable rather than silently underfilling.
    """
    opener_range = _open_range(rfi[opener_pos])
    eq = {h: equity_vs_range(h, matrix, opener_range) for h in CANONICAL_HANDS}
    play = {h: _playability(h) for h in CANONICAL_HANDS}
    value_score = {h: VALUE_EQ_W * eq[h] + (1 - VALUE_EQ_W) * play[h] for h in CANONICAL_HANDS}
    call_score = {h: CALL_EQ_W * eq[h] + (1 - CALL_EQ_W) * play[h] for h in CANONICAL_HANDS}
    bluff_set = set(bluff_pool)

    value_budget = value_share * threebet_total * TOTAL_COMBOS
    bluff_budget = (1 - value_share) * threebet_total * TOTAL_COMBOS

    dist: Dict[str, Dict[str, float]] = {}
    intent: Dict[str, str] = {}

    # 1. Value 3-bets: top by the blend, fixed weight. Offsuit hands reach the
    #    3-bet only here (value) — AKo always, AQo/KQo/AJo on merged nodes.
    #    Polarized nodes reserve the bluff pool for the bluff pass.
    spent = 0.0
    for h in sorted(CANONICAL_HANDS, key=lambda x: value_score[x], reverse=True):
        if spent >= value_budget:
            break
        if not merged and h in bluff_set:
            continue
        dist[h] = {"raise_3x": VALUE_RAISE_W,
                   "call": round((1 - VALUE_RAISE_W) * 0.6, 4),
                   "fold": round((1 - VALUE_RAISE_W) * 0.4, 4)}
        intent[h] = "value"
        spent += COMBO_COUNT[h] * VALUE_RAISE_W

    # 2. Suited bluff 3-bets: named pool first (designated blockers), then backfill
    #    from remaining suited hands by playability. Fixed sub-cliff weight.
    extra = sorted(
        (h for h in CANONICAL_HANDS if _is_suited(h) and h not in bluff_set),
        key=lambda x: play[x], reverse=True,
    )
    spent = 0.0
    for h in list(bluff_pool) + extra:
        if spent >= bluff_budget:
            break
        if h in dist:
            continue
        dist[h] = {"raise_3x": BLUFF_RAISE_W, "fold": round(1 - BLUFF_RAISE_W, 4)}
        intent[h] = "bluff"
        spent += COMBO_COUNT[h] * BLUFF_RAISE_W

    # 3. Flats: fill the calling range up to the node's DEFEND-WIDTH target. The
    #    call budget is whatever remains of defend_total AFTER the 3-bet passes —
    #    which already placed both raise mass AND the value hands' call slivers
    #    ((1-VALUE_RAISE_W)*0.6 per value hand). Charging those slivers here
    #    (rather than budgeting call as defend_total-threebet_total and ignoring
    #    them) is what keeps the node idempotent: total defend == defend_total, so
    #    re-running preserves width instead of ratcheting it ~0.9pp wider per run.
    placed_defend = sum(
        COMBO_COUNT[h] * (d.get("raise_3x", 0.0) + d.get("call", 0.0))
        for h, d in dist.items()
    )
    call_budget = max(0.0, defend_total * TOTAL_COMBOS - placed_defend)
    spent = 0.0
    for h in sorted(CANONICAL_HANDS, key=lambda x: call_score[x], reverse=True):
        if spent >= call_budget:
            break
        if h in dist:
            continue
        take = min(COMBO_COUNT[h] * CALL_CORE_W, call_budget - spent)
        w = take / COMBO_COUNT[h]
        dist[h] = {"call": round(w, 4), "fold": round(1 - w, 4)}
        spent += take

    # 4. Everything else pure-folds.
    for h in CANONICAL_HANDS:
        dist.setdefault(h, {"fold": 1.0})

    return {h: _norm(dist[h]) for h in CANONICAL_HANDS}, intent


def _lint(node_name: str, node: Dict[str, Dict[str, float]], intent: Dict[str, str]) -> None:
    """Refuse to write a node that fails a shared structural lint.

    The intent-presence guard and BB defend floors are delegated to
    `poker.strategy.lints` — the single source of truth, also run as the CI chart
    lints — so write-time refusal and the audit suite can't diverge. The merged
    value-top composition below is vs_open-merged-specific and stays here.
    """
    mini = {"vs_open": {node_name: node}, "vs_open_intent": {node_name: intent}}
    fails = lints.lint_vs_open_intent(mini) + lints.lint_bb_defend_floors(mini)
    if fails:
        raise SystemExit("; ".join(fails))

    defender, opener = node_name.split("_vs_")
    # Merged BB value-top composition (assert the blend's outcome, don't gate it).
    if defender == "BB" and VALUE_SHARE_BY_OPENER[opener] >= MERGED_THRESHOLD:
        value = {h for h, d in node.items() if d.get("raise_3x", 0.0) >= VALUE_RAISE_W}
        spew = value & (
            {"66", "55", "44", "33", "22"}                       # set-mine, not value
            | {"A8o", "A7o", "A6o", "A5o", "A4o", "A3o", "A2o"}   # dominated offsuit Ax
        )
        if spew:
            raise SystemExit(f"{node_name}: spew hands at value weight: {sorted(spew)} "
                             f"(blend should keep these out of the merged value top)")
        if opener == "SB":  # the canonical BvB node the thresholds were drawn for
            required = {"99", "TT", "JJ", "QQ", "KK", "AA",
                        "AJo", "AQo", "AKo", "KTs", "KJs", "KQs"}
            missing = required - value
            if missing:
                raise SystemExit(f"{node_name}: value top missing required {sorted(missing)}")


# Realized-mass tolerances (pp). The 3-bet floor is the one the reviewer flagged:
# without it the bluff slice could silently underfill and miss the target.
_TOL_DEFEND = 0.035
_TOL_3BET = 0.03


def _assert_masses(
    node_name: str,
    node: Dict[str, Dict[str, float]],
    defend_target: float,
    threebet_target: float,
) -> None:
    """Refuse to write a node whose realized defend/3-bet mass drifts off target."""
    d, t = _current_masses(node)
    if abs(d - defend_target) > _TOL_DEFEND:
        raise SystemExit(
            f"{node_name}: defend {100 * d:.1f}% off target {100 * defend_target:.1f}% "
            f"(> {100 * _TOL_DEFEND:.1f}pp)"
        )
    if abs(t - threebet_target) > _TOL_3BET:
        raise SystemExit(
            f"{node_name}: 3-bet {100 * t:.1f}% off target {100 * threebet_target:.1f}% "
            f"(> {100 * _TOL_3BET:.1f}pp) — bluff slice under/overfilled"
        )


def _node_plan(node_name: str, nodes: Dict[str, Dict[str, Dict[str, float]]]):
    """Resolve build inputs for a node — shared by the real build and --diff so
    they can't drift. Returns (opener, defend_total, threebet_total, value_share,
    bluff_pool, merged)."""
    defender, opener = node_name.split("_vs_")
    if defender == "BB":
        defend_total, threebet_total = BB_TARGETS[opener]
    elif PRESERVE_NONBB_WIDTH:
        defend_total = _current_masses(nodes[node_name])[0]   # preserve defend width
        threebet_total = NONBB_THREEBET_BY_OPENER[opener]      # opener-keyed cold-3bet
    else:
        defend_total, threebet_total = NONBB_TARGETS[node_name]
    value_share = VALUE_SHARE_BY_OPENER[opener]
    bluff_pool = BLUFF_3BET_POOL_WIDE if opener in WIDE_OPENERS else BLUFF_3BET_POOL
    return opener, defend_total, threebet_total, value_share, bluff_pool, value_share >= MERGED_THRESHOLD


def diff_report() -> None:
    """Print a per-node old→new defend%/3bet% comparison WITHOUT writing the chart.

    The informational 'Diff report' VALIDATION_SUITE_SPEC §1 calls for — makes a
    regen reviewable. Read-only: builds nodes in memory, leaves the JSON untouched.
    NOTE: combo-weighted aggregate mass only — says nothing about per-cell shape
    or EV; the old 3-bet figures are inflated by thin face-up 0.15-weight raises.
    """
    with open(_BASE) as f:
        chart = json.load(f)
    with open(_MATRIX) as f:
        matrix = json.load(f)["matrix"]
    rfi, nodes = chart["rfi"], chart["vs_open"]

    print("vs_open diff (old → new), combo-weighted — chart NOT modified")
    print(f"  {'node':<14} {'defend%':>16} {'3bet%':>16}")
    for node_name in nodes:
        od, ot = _current_masses(nodes[node_name])
        opener, defend_total, threebet_total, value_share, bluff_pool, merged = _node_plan(node_name, nodes)
        nd, nt = _current_masses(build_node(opener, rfi, matrix, defend_total,
                                            threebet_total, value_share, bluff_pool, merged)[0])
        print(f"  {node_name:<14} {100 * od:>6.1f} → {100 * nd:<6.1f}   {100 * ot:>6.1f} → {100 * nt:<6.1f}")


def patch_base() -> None:
    with open(_BASE) as f:
        chart = json.load(f)
    with open(_MATRIX) as f:
        matrix = json.load(f)["matrix"]

    rfi = chart["rfi"]
    nodes = chart.get("vs_open", {})
    if not nodes:
        raise SystemExit("base chart has no vs_open section")

    print(f"patching {_BASE}")
    print(f"  {'node':<14} {'defend%':>8} {'3bet%':>7}  {'shape':>9}")
    built: Dict[str, Dict[str, Dict[str, float]]] = {}
    built_intent: Dict[str, Dict[str, str]] = {}
    for node_name in nodes:
        opener, defend_total, threebet_total, value_share, bluff_pool, merged = _node_plan(node_name, nodes)
        node, intent = build_node(opener, rfi, matrix, defend_total, threebet_total,
                                  value_share, bluff_pool, merged)
        _lint(node_name, node, intent)
        _assert_masses(node_name, node, defend_total, threebet_total)
        built[node_name] = node
        built_intent[node_name] = intent

        d, t = _current_masses(node)
        shape = "merged" if merged else "polarized"
        print(f"  {node_name:<14} {100 * d:>7.1f}% {100 * t:>6.1f}%  {shape:>9}")

    # eyeball the merged value top on the two wide BB nodes
    for nn in ("BB_vs_SB", "BB_vs_BTN"):
        top = sorted(
            (h for h, d in built[nn].items() if d.get("raise_3x", 0) >= VALUE_RAISE_W),
            key=lambda h: CANONICAL_HANDS.index(h),
        )
        print(f"  value 3-bet top [{nn}]: {' '.join(top)}")

    chart["vs_open"] = built
    # Sibling section (not a scenario): {node: {hand: "value"|"bluff"}} for the
    # 3-bet hands. generate_depth_charts reads it instead of inferring intent from
    # the raise weight; the runtime loader and lints key off an explicit scenario
    # allow-list, so this top-level key is invisible to them.
    chart["vs_open_intent"] = built_intent
    with open(_BASE, "w") as f:
        json.dump(chart, f, indent=2)
        f.write("\n")
    print("  done — run build_vs3bet_defense next (it reads these per-node 3-bet ranges).")


if __name__ == "__main__":
    import sys

    (diff_report if "--diff" in sys.argv else patch_base)()
