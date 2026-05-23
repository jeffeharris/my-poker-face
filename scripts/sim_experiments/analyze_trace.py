"""Analyze decision trace and report archetype play statistics."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

TRACE_PATH = "/app/data/sim_trace/decisions.jsonl"


def classify_archetype(looseness, aggression):
    """Match poker/strategy/deviation_profiles.py:select_deviation_profile."""
    if looseness is None or aggression is None:
        return "?"
    if looseness < 0.25 and aggression < 0.25:
        return "nit"
    if looseness > 0.80 and aggression > 0.80:
        return "maniac"
    # Match classify_from_anchors quadrants.
    if looseness < 0.50:
        if aggression < 0.50:
            return "rock"
        else:
            return "tag"
    else:
        if aggression < 0.50:
            return "calling_station"
        else:
            return "lag"


def dominant_base_action(base_strategy_probs):
    """Highest-probability action in the base strategy."""
    if not isinstance(base_strategy_probs, dict) or not base_strategy_probs:
        return None
    return max(base_strategy_probs.items(), key=lambda x: x[1])[0]


def main():
    decisions = []
    with open(TRACE_PATH) as f:
        for line in f:
            try:
                decisions.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    print(f"Loaded {len(decisions):,} decisions\n")

    # Tag every decision with archetype
    for d in decisions:
        d["_archetype"] = classify_archetype(
            d.get("_anchor_looseness"), d.get("_anchor_aggression")
        )

    # Group by archetype
    by_arch = defaultdict(list)
    for d in decisions:
        by_arch[d["_archetype"]].append(d)

    # Phase-aware metrics
    print("=" * 80)
    print("Per-archetype behavior statistics")
    print("=" * 80)
    print(f"{'archetype':>15}  {'n_dec':>7}  {'VPIP%':>6}  {'PFR%':>6}  {'agg%':>6}  "
          f"{'fold%':>6}  {'call%':>6}")
    print("-" * 80)

    arch_stats = {}
    for arch in ["nit", "rock", "tag", "calling_station", "lag", "maniac", "?"]:
        decs = by_arch.get(arch, [])
        if not decs:
            continue

        preflop = [d for d in decs if d.get("phase") == "PREFLOP"]
        all_decs = decs

        # VPIP = preflop decisions where player took non-fold action AND had cost_to_call > 0
        # (excluding BB check is approximated by cost_to_call > 0)
        vpip_eligible = [d for d in preflop if d.get("cost_to_call", 0) > 0]
        vpip_n = sum(1 for d in vpip_eligible
                     if d.get("resolved_action") and d["resolved_action"] != "fold")

        # PFR = preflop raise frequency among all preflop decisions
        pfr_n = sum(1 for d in preflop if d.get("resolved_action") in ("raise", "all_in"))

        # Total action frequencies across all streets
        total = len(all_decs)
        n_fold = sum(1 for d in all_decs if d.get("resolved_action") == "fold")
        n_call = sum(1 for d in all_decs if d.get("resolved_action") == "call")
        n_check = sum(1 for d in all_decs if d.get("resolved_action") == "check")
        n_raise = sum(1 for d in all_decs if d.get("resolved_action") in ("raise", "all_in"))

        vpip = 100 * vpip_n / max(1, len(vpip_eligible))
        pfr = 100 * pfr_n / max(1, len(preflop))
        agg = 100 * n_raise / max(1, total)
        fold = 100 * n_fold / max(1, total)
        call = 100 * (n_call + n_check) / max(1, total)

        print(f"{arch:>15}  {total:>7,d}  "
              f"{vpip:>5.1f}%  {pfr:>5.1f}%  {agg:>5.1f}%  "
              f"{fold:>5.1f}%  {call:>5.1f}%")
        arch_stats[arch] = {
            "n_decisions": total,
            "vpip_pct": vpip,
            "pfr_pct": pfr,
            "agg_pct": agg,
            "fold_pct": fold,
            "call_pct": call,
        }

    print()
    print("=" * 80)
    print("Per-street fold rate (am I folding too much late?)")
    print("=" * 80)
    print(f"{'archetype':>15}  {'PREFLOP':>10}  {'FLOP':>10}  {'TURN':>10}  {'RIVER':>10}")
    print("-" * 80)
    for arch in ["nit", "rock", "tag", "calling_station", "lag", "maniac"]:
        decs = by_arch.get(arch, [])
        if not decs:
            continue
        row = f"{arch:>15}"
        for street in ["PREFLOP", "FLOP", "TURN", "RIVER"]:
            street_decs = [d for d in decs if d.get("phase") == street and d.get("cost_to_call", 0) > 0]
            if not street_decs:
                row += f"  {'—':>9}"
                continue
            n_fold = sum(1 for d in street_decs if d.get("resolved_action") == "fold")
            pct = 100 * n_fold / len(street_decs)
            row += f"  {pct:>7.1f}% ({len(street_decs):>4d})"
        print(row)
    print("(% of decisions that fold, when facing a bet. Count in parens.)")

    print()
    print("=" * 80)
    print("Deviation effect: does final action match the base strategy's TOP pick?")
    print("=" * 80)
    print(f"{'archetype':>15}  {'aligned%':>10}  {'overrode_to_fold':>17}  {'overrode_to_raise':>18}")
    print("-" * 80)
    for arch in ["nit", "rock", "tag", "calling_station", "lag", "maniac"]:
        decs = by_arch.get(arch, [])
        if not decs:
            continue
        aligned = 0
        to_fold = 0
        to_raise = 0
        total_with_base = 0
        for d in decs:
            base = d.get("base_strategy_probs")
            final = d.get("resolved_action")
            if not base or not final:
                continue
            total_with_base += 1
            top_base = dominant_base_action(base)
            if top_base == final:
                aligned += 1
                continue
            # Overrode — to what?
            if final == "fold":
                to_fold += 1
            if final in ("raise", "all_in"):
                to_raise += 1
        if total_with_base == 0:
            continue
        a_pct = 100 * aligned / total_with_base
        f_pct = 100 * to_fold / total_with_base
        r_pct = 100 * to_raise / total_with_base
        print(f"{arch:>15}  {a_pct:>9.1f}%  {f_pct:>16.1f}%  {r_pct:>17.1f}%")
    print()
    print("Reads: aligned% = obeyed table. overrode_to_fold% = deviation made them MORE passive than table said. overrode_to_raise% = deviation made them MORE aggressive than table said.")


if __name__ == "__main__":
    main()
