"""Analyze intervention firing rates per archetype."""
from __future__ import annotations

import json
from collections import Counter, defaultdict

TRACE = "/app/data/sim_trace_v2/decisions.jsonl"


def classify_archetype(loose, agg):
    if loose is None or agg is None:
        return "?"
    if loose < 0.25 and agg < 0.25:
        return "nit"
    if loose > 0.80 and agg > 0.80:
        return "maniac"
    if loose < 0.50:
        return "rock" if agg < 0.50 else "tag"
    return "calling_station" if agg < 0.50 else "lag"


def main():
    rows = []
    with open(TRACE) as f:
        for line in f:
            rows.append(json.loads(line))
    print(f"Loaded {len(rows):,} decisions\n")

    # Tag archetype
    for r in rows:
        r["_arch"] = classify_archetype(
            r.get("_anchor_looseness"), r.get("_anchor_aggression")
        )

    # Universe of rules / layers seen
    all_layers = Counter()
    all_fired_layers = Counter()
    layer_rule_pairs = Counter()
    for r in rows:
        for iv in r.get("_interventions") or []:
            layer = iv.get("layer")
            rule = iv.get("rule_id")
            if not layer:
                continue
            all_layers[layer] += 1
            key = f"{layer}::{rule}"
            if iv.get("fired"):
                all_fired_layers[layer] += 1
            layer_rule_pairs[key] += iv.get("fired", False) and 1 or 0

    print("=" * 80)
    print("Layer firing rates across ALL decisions (any archetype)")
    print("=" * 80)
    total_dec = len(rows)
    print(f"{'layer':>30}  {'evaluated':>10}  {'fired':>10}  {'fire%':>8}")
    print("-" * 80)
    for layer, n in all_layers.most_common():
        nf = all_fired_layers.get(layer, 0)
        pct = 100 * nf / max(1, n)
        print(f"{layer:>30}  {n:>10,d}  {nf:>10,d}  {pct:>7.2f}%")

    print()
    print("=" * 80)
    print("Layer::rule fire counts (non-zero only, top 30)")
    print("=" * 80)
    for k, c in layer_rule_pairs.most_common(30):
        if c == 0:
            continue
        print(f"  {k}: {c:>5,d}")

    # Per archetype × per layer
    print()
    print("=" * 80)
    print("Fire rates by ARCHETYPE × LAYER (% of THAT archetype's decisions)")
    print("=" * 80)
    by_arch = defaultdict(list)
    for r in rows:
        by_arch[r["_arch"]].append(r)
    arch_order = ["nit", "rock", "tag", "calling_station", "lag", "maniac"]

    layer_names = sorted({iv.get("layer") for r in rows
                          for iv in (r.get("_interventions") or [])
                          if iv.get("layer")})

    # Header
    print(f"{'layer':>30}  " + "  ".join(f"{a[:10]:>10}" for a in arch_order))
    print("-" * 100)
    for layer in layer_names:
        row = f"{layer:>30}"
        for arch in arch_order:
            decs = by_arch.get(arch, [])
            if not decs:
                row += f"  {'—':>10}"
                continue
            fires = sum(
                1 for r in decs
                for iv in (r.get("_interventions") or [])
                if iv.get("layer") == layer and iv.get("fired")
            )
            pct = 100 * fires / len(decs)
            row += f"  {pct:>9.2f}%"
        print(row)

    # Specifically: did anyone trigger 'hyper_aggressive' exploitation?
    print()
    print("=" * 80)
    print("hyper_aggressive exploitation firings (this is the maniac-defense rule)")
    print("=" * 80)
    fires_by_arch = Counter()
    fires_by_pid = Counter()
    total_facings = Counter()
    for r in rows:
        arch = r["_arch"]
        pid = r.get("_personality_id")
        for iv in r.get("_interventions") or []:
            if iv.get("layer") == "exploitation" and iv.get("rule_id") == "hyper_aggressive":
                total_facings[arch] += 1
                if iv.get("fired"):
                    fires_by_arch[arch] += 1
                    if pid:
                        fires_by_pid[pid] += 1
    if not total_facings:
        print("  (no 'exploitation::hyper_aggressive' entries in trace at all)")
    else:
        print(f"  {'archetype':>15}  {'evaluated':>10}  {'fired':>8}  {'rate':>8}")
        for arch in arch_order:
            n = total_facings.get(arch, 0)
            nf = fires_by_arch.get(arch, 0)
            if n == 0:
                continue
            print(f"  {arch:>15}  {n:>10,d}  {nf:>8,d}  {100*nf/n:>7.1f}%")
        print()
        print("  Top firers by pid:")
        for pid, c in fires_by_pid.most_common(10):
            print(f"    {pid}: {c}")

    # Check the new induce_override
    print()
    print("=" * 80)
    print("induce_override firings (the new trap-the-barreler rule)")
    print("=" * 80)
    induce_evals = 0
    induce_fires = 0
    for r in rows:
        for iv in r.get("_interventions") or []:
            if iv.get("layer") == "induce_override":
                induce_evals += 1
                if iv.get("fired"):
                    induce_fires += 1
    print(f"  Evaluated: {induce_evals:,}")
    print(f"  Fired:     {induce_fires:,}")
    if induce_fires == 0 and induce_evals > 0:
        print("  (rule wired in but never fired — gate too narrow or sample/board conditions not hit)")
    if induce_evals == 0:
        print("  (rule not wired into this decision path)")


if __name__ == "__main__":
    main()
