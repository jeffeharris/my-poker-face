#!/usr/bin/env python3
"""Chart Opportunity Census — analyze WHERE preflop decisions land across the
solver charts, HOW MUCH money rides on each spot, WHICH archetype field they
arise against, and WHERE they fall through to the conservative default / deep
chart instead of a specialized chart.

Reads `player_decision_analysis` rows produced by the sharp bot
(`TieredBotController`). Every preflop decision carries a
`strategy_pipeline_snapshot_json` blob with the chart-coverage instrumentation:

    node_key            scenario|position|opener|hand  (PreflopNode.key)
    chart_label         which base chart fed the line ('6max@100bb', 'HU', ...)
    chart_source        push_fold | facing_all_in_veto | chart_hit | chart_fallback
    chart_lookup_source hit | squeeze_degrade | masked_out | miss  (deep-table only)
    push_fold_routed     bool   — push/fold chart produced the action
    push_fold_enabled    bool   — persona opted into Nash push/fold at all
    effective_stack_bb   float  — short-stack regime is <= 15bb
    big_blind, cost_to_call, pot_total, player_stack, resolved_action, resolved_raise_to

Pure stdlib (sqlite3 + json) so it runs against a sim DB or a copy of prod
without importing the `poker` package. Steps 1-4 of the chart-opportunity
priority model (census / money / archetype matrix / fall-through audit).

Usage:
    python3 scripts/chart_census.py [DB_PATH] [--field-from-game-id] [--bb 15]
    # Inside docker (sim DB):
    docker compose exec backend python3 scripts/chart_census.py /tmp/census.db

DB_PATH defaults to the local poker_games.db, then ./data/poker_games.db.

Census-sim game_ids are tagged `census__<field>__<depth>bb`; the field/depth
are parsed from there for the archetype matrix. Rows whose game_id doesn't
match are bucketed under field='(unknown)'.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

# Short-stack push/fold scope. Mirrors PUSH_FOLD_THRESHOLD_BB in
# poker/tiered_bot_controller.py — kept as a CLI knob so the analysis stays
# import-free and can be re-pointed if the threshold ever moves.
DEFAULT_PUSH_FOLD_BB = 15.0


@dataclass
class Decision:
    game_id: str
    field: str  # opponent archetype field (from game_id tag) or '(unknown)'
    depth_tag: str  # e.g. '100bb' (from game_id tag) or '(unknown)'
    scenario: str  # rfi | vs_open | vs_3bet | vs_squeeze | vs_4bet
    position: str
    opener: str
    hand: str
    chart_label: str
    chart_source: str  # push_fold | facing_all_in_veto | chart_hit | chart_fallback
    lookup_source: str  # hit | squeeze_degrade | masked_out | miss | ''
    push_fold_routed: bool
    push_fold_enabled: bool
    eff_bb: Optional[float]
    big_blind: float
    action: str
    risk_bb: float


# ── loading ──────────────────────────────────────────────────────────────


def _default_db() -> str:
    for p in ("poker_games.db", os.path.join("data", "poker_games.db"), "/app/data/poker_games.db"):
        if os.path.exists(p):
            return p
    return "poker_games.db"


def _parse_tag(game_id: str) -> tuple[str, str]:
    """`census__<field>__<depth>bb` -> (field, depth_tag). Else (unknown,unknown)."""
    parts = game_id.split("__")
    if len(parts) >= 3 and parts[0] == "census":
        return parts[1], parts[2]
    return "(unknown)", "(unknown)"


def _risk_bb(snap: dict, action: str) -> float:
    """bb the decision puts at stake. Approximation:
    all_in -> effective stack (the capped at-risk vs the covering opp)
    raise  -> the raise-to total committed this street
    call   -> cost_to_call
    fold/check -> 0
    """
    bb = float(snap.get("big_blind") or 0) or 1.0
    if action == "all_in":
        eff = snap.get("effective_stack_bb")
        if eff is not None:
            return float(eff)
        return float(snap.get("player_stack") or 0) / bb
    if action == "raise":
        return float(snap.get("resolved_raise_to") or 0) / bb
    if action == "call":
        return float(snap.get("cost_to_call") or 0) / bb
    return 0.0


def load_decisions(db_path: str) -> list[Decision]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    out: list[Decision] = []
    rows = con.execute(
        "SELECT game_id, action_taken, strategy_pipeline_snapshot_json AS snap "
        "FROM player_decision_analysis "
        "WHERE strategy_pipeline_snapshot_json IS NOT NULL"
    ).fetchall()
    con.close()
    for r in rows:
        try:
            snap = json.loads(r["snap"])
        except (TypeError, json.JSONDecodeError):
            continue
        # Preflop instrumentation only — postflop snapshots have no chart_source.
        if "chart_source" not in snap:
            continue
        node_key = snap.get("node_key") or "|||"
        scen, pos, opener, hand = (node_key.split("|") + ["", "", "", ""])[:4]
        action = snap.get("resolved_action") or r["action_taken"] or "?"
        field, depth_tag = _parse_tag(r["game_id"])
        out.append(
            Decision(
                game_id=r["game_id"],
                field=field,
                depth_tag=depth_tag,
                scenario=scen or "(none)",
                position=pos,
                opener=opener,
                hand=hand,
                chart_label=snap.get("chart_label") or "(none)",
                chart_source=snap.get("chart_source") or "(none)",
                lookup_source=snap.get("chart_lookup_source") or "",
                push_fold_routed=bool(snap.get("push_fold_routed")),
                push_fold_enabled=bool(snap.get("push_fold_enabled")),
                eff_bb=snap.get("effective_stack_bb"),
                big_blind=float(snap.get("big_blind") or 0),
                action=action,
                risk_bb=_risk_bb(snap, action),
            )
        )
    return out


# ── fall-through taxonomy ─────────────────────────────────────────────────


def fallthrough_class(d: Decision, push_fold_bb: float) -> Optional[str]:
    """Classify a decision that wanted specialized-chart behavior but fell
    through. Returns None when a specialized chart served it.

      conservative_default:<scenario>:<lookup>  — deep chart had no node (or all
          actions masked out) -> conservative fold/check default. A true miss.
      pushfold_fallthrough:<scenario>            — short-stack (<=push_fold_bb)
          spot with push/fold ENABLED that did NOT route to the push/fold chart
          and wasn't a pot-odds all-in veto. 'facing single open' (vs_open) is
          the high-value reshove class; rfi=limped/walk; vs_3bet+=3bet war.
    """
    if d.chart_source == "chart_fallback":
        # BB first-in/option (folded or limped around to the BB): the chart has
        # no rfi|BB node by design and the conservative 'check' is correct, so
        # this is not a chart gap — don't flag it.
        if d.scenario == "rfi" and d.position == "BB":
            return None
        return f"conservative_default:{d.scenario}:{d.lookup_source or 'miss'}"
    if (
        d.push_fold_enabled
        and d.eff_bb is not None
        and d.eff_bb <= push_fold_bb
        and not d.push_fold_routed
        and d.chart_source != "facing_all_in_veto"
    ):
        return f"pushfold_fallthrough:{d.scenario}"
    return None


# ── report helpers ─────────────────────────────────────────────────────────


def _bar(frac: float, width: int = 24) -> str:
    return "█" * int(round(frac * width))


def _hdr(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


# ── compute (single source of truth; both text + JSON consume these) ────────

SCENARIO_ORDER = ["rfi", "vs_open", "vs_3bet", "vs_squeeze", "vs_4bet"]


def _ordered_scenarios(ds: list[Decision]) -> list[str]:
    return SCENARIO_ORDER + sorted({d.scenario for d in ds} - set(SCENARIO_ORDER))


def compute_spot_census(ds: list[Decision]) -> dict:
    total = len(ds) or 1
    by_scen: dict[str, int] = defaultdict(int)
    by_src: dict[str, int] = defaultdict(int)
    by_label: dict[str, int] = defaultdict(int)
    by_spot: dict[tuple[str, str], int] = defaultdict(int)
    for d in ds:
        by_scen[d.scenario] += 1
        by_src[d.chart_source] += 1
        by_label[d.chart_label] += 1
        by_spot[(d.scenario, d.chart_source)] += 1

    def rows(counter, key):
        return [
            {key: k, "count": n, "pct": 100.0 * n / total}
            for k, n in sorted(counter.items(), key=lambda kv: -kv[1])
        ]

    return {
        "total": len(ds),
        "by_scenario": rows(by_scen, "scenario"),
        "by_chart_label": rows(by_label, "label"),
        "by_chart_source": rows(by_src, "source"),
        "scenario_x_source": [
            {"scenario": s, "source": src, "count": n, "pct": 100.0 * n / total}
            for (s, src), n in sorted(by_spot.items(), key=lambda kv: -kv[1])
        ],
    }


def compute_money_census(ds: list[Decision]) -> dict:
    total_risk = sum(d.risk_bb for d in ds) or 1.0
    by_scen: dict[str, list[float]] = defaultdict(list)
    by_spot: dict[tuple[str, str], list[float]] = defaultdict(list)
    for d in ds:
        by_scen[d.scenario].append(d.risk_bb)
        by_spot[(d.scenario, d.chart_source)].append(d.risk_bb)

    scen_rows = [
        {
            "scenario": s,
            "n": len(r),
            "sum_bb": sum(r),
            "pct_risk": 100.0 * sum(r) / total_risk,
            "mean_bb": sum(r) / len(r),
            "max_bb": max(r),
        }
        for s, r in by_scen.items()
    ]
    scen_rows.sort(key=lambda x: -x["sum_bb"])
    top = sorted(by_spot.items(), key=lambda kv: -sum(kv[1]))[:12]
    return {
        "total_risk_bb": sum(d.risk_bb for d in ds),
        "by_scenario": scen_rows,
        "top_spots": [
            {
                "scenario": s,
                "source": src,
                "n": len(r),
                "sum_bb": sum(r),
                "pct_risk": 100.0 * sum(r) / total_risk,
            }
            for (s, src), r in top
        ],
    }


def compute_archetype_matrix(ds: list[Decision], push_fold_bb: float) -> dict:
    fields = sorted({d.field for d in ds})
    scenarios = _ordered_scenarios(ds)
    field_tot = {f: sum(1 for d in ds if d.field == f) for f in fields}
    # share[field][scenario] = % of that field's decisions
    share = {f: {} for f in fields}
    for f in fields:
        for scen in scenarios:
            n = sum(1 for d in ds if d.field == f and d.scenario == scen)
            share[f][scen] = 100.0 * n / (field_tot[f] or 1)
    field_risk = {f: sum(d.risk_bb for d in ds if d.field == f) for f in fields}
    field_ft = {}
    for f in fields:
        fd = [d for d in ds if d.field == f]
        ft = sum(1 for d in fd if fallthrough_class(d, push_fold_bb))
        field_ft[f] = {"count": ft, "total": len(fd), "pct": 100.0 * ft / (len(fd) or 1)}
    # Drop all-zero scenario rows for a tighter matrix.
    used = [s for s in scenarios if any(share[f][s] for f in fields)]
    return {
        "fields": fields,
        "scenarios": used,
        "spot_share_pct": share,
        "field_decisions": field_tot,
        "field_risk_bb": field_risk,
        "field_fallthrough": field_ft,
    }


def compute_fallthrough_audit(ds: list[Decision], push_fold_bb: float) -> dict:
    classes: dict[str, list[Decision]] = defaultdict(list)
    for d in ds:
        c = fallthrough_class(d, push_fold_bb)
        if c:
            classes[c].append(d)
    total = len(ds)
    total_ft = sum(len(v) for v in classes.values())
    rows = [
        {
            "klass": c,
            "count": len(dd),
            "pct_all": 100.0 * len(dd) / (total or 1),
            "risk_bb": sum(d.risk_bb for d in dd),
            "by_field": _count_by_field(dd),
        }
        for c, dd in sorted(classes.items(), key=lambda kv: -len(kv[1]))
    ]
    return {
        "total_decisions": total,
        "total_fallthrough": total_ft,
        "pct": 100.0 * total_ft / (total or 1),
        "classes": rows,
    }


def _count_by_field(dd: list[Decision]) -> dict:
    by_field: dict[str, int] = defaultdict(int)
    for d in dd:
        by_field[d.field] += 1
    return dict(sorted(by_field.items(), key=lambda kv: -kv[1]))


def build_payload(ds: list[Decision], db_path: str, push_fold_bb: float) -> dict:
    return {
        "meta": {
            "source_db": db_path,
            "total_preflop_decisions": len(ds),
            "push_fold_bb": push_fold_bb,
            "fields": sorted({d.field for d in ds}),
            "depth_tags": sorted({d.depth_tag for d in ds}),
        },
        "spot_census": compute_spot_census(ds),
        "money_census": compute_money_census(ds),
        "archetype_matrix": compute_archetype_matrix(ds, push_fold_bb),
        "fallthrough_audit": compute_fallthrough_audit(ds, push_fold_bb),
    }


# ── text reports (render from the computed payload) ─────────────────────────


def report_spot_census(sc: dict) -> None:
    _hdr("1. SPOT CENSUS — where preflop decisions land")
    total = sc["total"]
    print(f"Total preflop sharp-bot decisions: {total}\n")
    print("By scenario:")
    for r in sc["by_scenario"]:
        print(
            f"  {r['scenario']:<12} {r['count']:>7}  {r['pct']:>5.1f}%  " f"{_bar(r['pct'] / 100)}"
        )
    print("\nBy base chart selected (which chart the line started from):")
    for r in sc["by_chart_label"]:
        print(f"  {r['label']:<22} {r['count']:>7}  {r['pct']:>5.1f}%  " f"{_bar(r['pct'] / 100)}")
    print("\nBy chart source (which layer produced the action):")
    for r in sc["by_chart_source"]:
        print(f"  {r['source']:<22} {r['count']:>7}  {r['pct']:>5.1f}%  " f"{_bar(r['pct'] / 100)}")
    print("\nScenario × chart source:")
    print(f"  {'scenario':<12} {'chart_source':<22} {'count':>7}  {'pct':>6}")
    for r in sc["scenario_x_source"]:
        print(f"  {r['scenario']:<12} {r['source']:<22} {r['count']:>7}  {r['pct']:>5.1f}%")


def report_money_census(mc: dict) -> None:
    _hdr("2. MONEY CENSUS — bb at risk by spot (count can mislead)")
    print(
        "'risk_bb' = bb the decision puts at stake (all_in=eff stack, "
        "raise=raise-to, call=cost-to-call, fold/check=0).\n"
    )
    print(f"  {'scenario':<12} {'n':>6} {'sum_bb':>10} {'%risk':>7} {'mean':>7} {'max':>8}")
    for r in mc["by_scenario"]:
        print(
            f"  {r['scenario']:<12} {r['n']:>6} {r['sum_bb']:>10.0f} "
            f"{r['pct_risk']:>6.1f}% {r['mean_bb']:>7.1f} {r['max_bb']:>8.1f}  "
            f"{_bar(r['pct_risk'] / 100, 18)}"
        )
    print("\nTop spots by total bb at risk (scenario × chart source):")
    print(f"  {'scenario':<12} {'chart_source':<20} {'n':>6} {'sum_bb':>10} {'%risk':>7}")
    for r in mc["top_spots"]:
        print(
            f"  {r['scenario']:<12} {r['source']:<20} {r['n']:>6} "
            f"{r['sum_bb']:>10.0f} {r['pct_risk']:>6.1f}%"
        )


def report_archetype_matrix(am: dict) -> None:
    _hdr("3. ARCHETYPE MATRIX — spot distribution & fall-through by field")
    fields = am["fields"]
    print("Spot share within each opponent field (% of that field's decisions):")
    print("  " + f"{'scenario':<12}" + "".join(f"{f[:11]:>12}" for f in fields))
    for scen in am["scenarios"]:
        cells = "".join(f"{am['spot_share_pct'][f][scen]:>11.1f}%" for f in fields)
        print(f"  {scen:<12}" + cells)
    print("  " + "-" * (12 + 12 * len(fields)))
    print(f"  {'(decisions)':<12}" + "".join(f"{am['field_decisions'][f]:>12}" for f in fields))
    print("\nTotal bb at risk per field:")
    for f in fields:
        print(
            f"  {f:<14} {am['field_risk_bb'][f]:>12.0f} bb   "
            f"over {am['field_decisions'][f]} decisions"
        )
    print("\nFall-through rate per field (specialized chart NOT used):")
    for f in fields:
        ft = am["field_fallthrough"][f]
        print(f"  {f:<14} {ft['count']:>6}/{ft['total']:<6} {ft['pct']:>5.1f}%")


def report_fallthrough_audit(fa: dict, push_fold_bb: float) -> None:
    _hdr("4. FALL-THROUGH AUDIT — wanted chart behavior, got fallback")
    print(
        f"Fall-through decisions: {fa['total_fallthrough']}/{fa['total_decisions']}  "
        f"({fa['pct']:.1f}% of all preflop decisions)\n"
    )
    if not fa["classes"]:
        print("  (none — every decision was served by a specialized chart)")
        return
    print(f"  {'fall-through class':<44} {'count':>6} {'%all':>6} {'risk_bb':>9}")
    for r in fa["classes"]:
        print(f"  {r['klass']:<44} {r['count']:>6} {r['pct_all']:>5.1f}% {r['risk_bb']:>9.0f}")
    reshove = next((r for r in fa["classes"] if r["klass"] == "pushfold_fallthrough:vs_open"), None)
    if reshove:
        print(
            f"\n  >> 'facing a single open at <= {push_fold_bb:.0f}bb' (reshove class), "
            "by field:"
        )
        for f, n in reshove["by_field"].items():
            print(f"       {f:<14} {n:>5}")
        print(
            "     (push/fold ENABLED but did not reshove: flag off, gate " "declined, or no read)"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="Chart opportunity census (steps 1-4)")
    ap.add_argument("db", nargs="?", default=_default_db(), help="sqlite DB path")
    ap.add_argument(
        "--bb",
        type=float,
        default=DEFAULT_PUSH_FOLD_BB,
        help=f"push/fold scope in bb (default {DEFAULT_PUSH_FOLD_BB})",
    )
    ap.add_argument(
        "--json", metavar="PATH", help="write the census payload as JSON (for the admin dashboard)"
    )
    ap.add_argument("--quiet", action="store_true", help="suppress text reports")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"DB not found: {args.db}", file=sys.stderr)
        return 1
    ds = load_decisions(args.db)
    if not ds:
        print(
            "No instrumented preflop decisions found. Run the census sim first:\n"
            "  docker compose exec backend python3 scripts/chart_census_sim.py "
            "--db /tmp/census.db",
            file=sys.stderr,
        )
        return 1

    payload = build_payload(ds, args.db, args.bb)
    if args.json:
        with open(args.json, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"Wrote census payload -> {args.json}  ({len(ds)} decisions)")

    if not args.quiet:
        print(f"Loaded {len(ds)} instrumented preflop sharp-bot decisions from {args.db}")
        report_spot_census(payload["spot_census"])
        report_money_census(payload["money_census"])
        report_archetype_matrix(payload["archetype_matrix"])
        report_fallthrough_audit(payload["fallthrough_audit"], args.bb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
