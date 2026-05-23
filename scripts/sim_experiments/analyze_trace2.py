"""Better analyzer — fixes phase string match + adds per-pid spot-check."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

TRACE_PATH = "/app/data/sim_trace/decisions.jsonl"


def classify_archetype(looseness, aggression):
    if looseness is None or aggression is None:
        return "?"
    if looseness < 0.25 and aggression < 0.25:
        return "nit"
    if looseness > 0.80 and aggression > 0.80:
        return "maniac"
    if looseness < 0.50:
        return "rock" if aggression < 0.50 else "tag"
    return "calling_station" if aggression < 0.50 else "lag"


def dominant_base_action(base):
    if not isinstance(base, dict) or not base:
        return None
    return max(base.items(), key=lambda x: x[1])[0]


def main():
    decisions = []
    with open(TRACE_PATH) as f:
        for line in f:
            try:
                decisions.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    print(f"Loaded {len(decisions):,} decisions\n")

    for d in decisions:
        d["_arch"] = classify_archetype(
            d.get("_anchor_looseness"), d.get("_anchor_aggression")
        )

    by_arch = defaultdict(list)
    for d in decisions:
        by_arch[d["_arch"]].append(d)

    # =========================================================
    # Per-archetype play stats
    # =========================================================
    print("=" * 80)
    print("Per-archetype behavior")
    print("=" * 80)
    print(f"{'archetype':>15}  {'n':>6}  {'VPIP':>6}  {'PFR':>6}  "
          f"{'pre-fold%':>10}  {'post-fold%':>11}  {'overall agg%':>13}")
    print("-" * 80)

    BASELINES = {
        "nit":             {"VPIP": "8-15",  "PFR": "5-10",   "post-fold": "50-65"},
        "rock":            {"VPIP": "12-20", "PFR": "10-15",  "post-fold": "50-60"},
        "tag":             {"VPIP": "18-24", "PFR": "14-22",  "post-fold": "45-55"},
        "calling_station": {"VPIP": "35-50", "PFR": "5-15",   "post-fold": "20-30"},
        "lag":             {"VPIP": "25-35", "PFR": "20-30",  "post-fold": "35-45"},
        "maniac":          {"VPIP": "40+",   "PFR": "30+",    "post-fold": "25-35"},
    }

    for arch in ["nit", "rock", "tag", "calling_station", "lag", "maniac"]:
        decs = by_arch.get(arch, [])
        if not decs:
            continue

        pre = [d for d in decs if d.get("phase") == "PRE_FLOP"]
        post = [d for d in decs if d.get("phase") == "POSTFLOP"]

        # VPIP: % of preflop where player did not fold AND had to put money in
        # Excluding BB free-check: cost_to_call > 0 catches "facing action"
        # Note: this overcounts BB calls in unraised pots (which IS VPIP).
        vpip_pool = [d for d in pre if (d.get("cost_to_call") or 0) > 0
                     or d.get("resolved_action") in ("raise", "all_in")]
        n_vpip = sum(1 for d in vpip_pool if d.get("resolved_action") and
                     d["resolved_action"] not in ("fold", "check"))
        vpip = 100 * n_vpip / max(1, len(vpip_pool))

        # PFR: % of preflop where player raised
        pfr = 100 * sum(1 for d in pre
                        if d.get("resolved_action") in ("raise", "all_in")) / max(1, len(pre))

        # Pre-flop fold rate when facing action
        pre_facing = [d for d in pre if (d.get("cost_to_call") or 0) > 0]
        pre_fold = 100 * sum(1 for d in pre_facing
                             if d.get("resolved_action") == "fold") / max(1, len(pre_facing))

        # Postflop fold rate when facing a bet
        post_facing = [d for d in post if (d.get("cost_to_call") or 0) > 0]
        post_fold = 100 * sum(1 for d in post_facing
                              if d.get("resolved_action") == "fold") / max(1, len(post_facing))

        # Overall aggression
        n_raise = sum(1 for d in decs if d.get("resolved_action") in ("raise", "all_in"))
        agg = 100 * n_raise / max(1, len(decs))

        baseline = BASELINES.get(arch, {})
        b_vpip = baseline.get("VPIP", "?")
        b_pfr = baseline.get("PFR", "?")
        b_postfold = baseline.get("post-fold", "?")

        print(f"{arch:>15}  {len(decs):>6,}  {vpip:>5.1f}%  {pfr:>5.1f}%  "
              f"{pre_fold:>9.1f}%  {post_fold:>10.1f}%  {agg:>12.1f}%")
        print(f"{'(baseline)':>15}  {'':>6}  {b_vpip:>5s}%  {b_pfr:>5s}%  "
              f"{'':>10s}  {b_postfold:>10s}%")

    # =========================================================
    # Deviation effect
    # =========================================================
    print()
    print("=" * 80)
    print("Deviation effect: final-vs-base alignment")
    print("=" * 80)
    print(f"{'archetype':>15}  {'aligned%':>10}  {'->fold%':>10}  {'->call%':>10}  {'->raise%':>10}  {'->check%':>10}")
    print("-" * 80)
    for arch in ["nit", "rock", "tag", "calling_station", "lag", "maniac"]:
        decs = by_arch.get(arch, [])
        if not decs:
            continue
        n = 0
        aligned = 0
        to = defaultdict(int)
        for d in decs:
            base = d.get("base_strategy_probs")
            final = d.get("resolved_action")
            if not base or not final:
                continue
            n += 1
            top = dominant_base_action(base)
            if top == final:
                aligned += 1
                continue
            if final == "all_in":
                final = "raise"
            to[final] += 1
        if n == 0:
            continue
        def pct(x):
            return 100 * x / n
        print(f"{arch:>15}  {pct(aligned):>9.1f}%  "
              f"{pct(to['fold']):>9.1f}%  {pct(to['call']):>9.1f}%  "
              f"{pct(to['raise']):>9.1f}%  {pct(to['check']):>9.1f}%")

    # =========================================================
    # Per-pid spot check: the maniacs and key losers
    # =========================================================
    print()
    print("=" * 80)
    print("Per-pid spot check (specific characters)")
    print("=" * 80)
    spotlight = ["queen_of_hearts", "blackbeard", "don_quixote", "ebenezer_scrooge",
                 "zeus", "buddha", "abraham_lincoln"]
    by_pid = defaultdict(list)
    for d in decisions:
        pid = d.get("_personality_id")
        if pid:
            by_pid[pid].append(d)
    print(f"{'pid':>22}  {'arch':>10}  {'n':>5}  {'agg%':>6}  {'pre-fold%':>10}  "
          f"{'post-fold%':>11}  {'overridden_to_raise%':>22}")
    print("-" * 95)
    for pid in spotlight:
        decs = by_pid.get(pid)
        if not decs:
            continue
        arch = decs[0]["_arch"]
        n_raise = sum(1 for d in decs if d.get("resolved_action") in ("raise", "all_in"))
        agg = 100 * n_raise / max(1, len(decs))

        pre_facing = [d for d in decs if d.get("phase") == "PRE_FLOP"
                      and (d.get("cost_to_call") or 0) > 0]
        post_facing = [d for d in decs if d.get("phase") == "POSTFLOP"
                       and (d.get("cost_to_call") or 0) > 0]
        pre_fold = 100 * sum(1 for d in pre_facing
                             if d.get("resolved_action") == "fold") / max(1, len(pre_facing))
        post_fold = 100 * sum(1 for d in post_facing
                              if d.get("resolved_action") == "fold") / max(1, len(post_facing))

        # Override-to-raise rate
        overrode = 0
        total_with_base = 0
        for d in decs:
            base = d.get("base_strategy_probs")
            final = d.get("resolved_action")
            if not base or not final:
                continue
            total_with_base += 1
            if dominant_base_action(base) != final and final in ("raise", "all_in"):
                overrode += 1
        or_pct = 100 * overrode / max(1, total_with_base)

        print(f"{pid:>22}  {arch:>10}  {len(decs):>5,}  {agg:>5.1f}%  "
              f"{pre_fold:>9.1f}%  {post_fold:>10.1f}%  {or_pct:>21.1f}%")


if __name__ == "__main__":
    main()
