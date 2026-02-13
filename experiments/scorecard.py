#!/usr/bin/env python3
"""
Experiment Scorecard — one command to evaluate AI decision-making quality.

Usage:
    python3 experiments/scorecard.py <experiment_id>
    python3 experiments/scorecard.py <experiment_id> --compare <baseline_id>
    python3 experiments/scorecard.py <experiment_id> --json
    python3 experiments/scorecard.py <experiment_id> --db data/poker_games.db

From Python:
    from experiments.scorecard import Scorecard
    sc = Scorecard(db_path="data/poker_games.db")
    report = sc.generate(experiment_id=123)
    print(report)
"""

import argparse
import json
import sqlite3
import sys
import os
from collections import defaultdict
from typing import Dict, List, Optional, Any, Tuple

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.dbq import get_db_path


class Scorecard:
    """Generates experiment scorecards with decision quality, behavioral, and cost metrics."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or get_db_path()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.db_path}?immutable=1", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def generate(
        self,
        experiment_id: int,
        baseline_id: int = None,
        format: str = "markdown",
    ) -> str:
        """Generate a scorecard for an experiment.

        Args:
            experiment_id: Experiment to evaluate.
            baseline_id: Optional baseline experiment for delta comparison.
            format: 'markdown' or 'json'.

        Returns:
            Formatted report string.
        """
        metrics = self._collect_metrics(experiment_id)

        baseline_metrics = None
        if baseline_id is not None:
            baseline_metrics = self._collect_metrics(baseline_id)

        if format == "json":
            return self._format_json(metrics, baseline_metrics)
        return self._format_markdown(metrics, baseline_metrics)

    # ------------------------------------------------------------------ #
    #  Data collection (per experiment)
    # ------------------------------------------------------------------ #

    def _collect_metrics(self, experiment_id: int) -> Dict[str, Any]:
        conn = self._connect()
        try:
            info = self._get_experiment_info(conn, experiment_id)
            variant_game_ids = self._get_variant_game_ids(conn, experiment_id)
            all_game_ids = [gid for ids in variant_game_ids.values() for gid in ids]

            decisions = self._fetch_decisions(conn, all_game_ids)
            analysis = self._fetch_analysis(conn, all_game_ids)
            standings = self._fetch_standings(conn, all_game_ids)
            costs = self._fetch_costs(conn, all_game_ids)

            # Group fetched data by variant
            game_to_variant = {}
            for variant, gids in variant_game_ids.items():
                for gid in gids:
                    game_to_variant[gid] = variant

            def by_variant(rows):
                grouped = defaultdict(list)
                for row in rows:
                    v = game_to_variant.get(row["game_id"], "unknown")
                    grouped[v].append(row)
                return dict(grouped)

            decisions_by_v = by_variant(decisions)
            analysis_by_v = by_variant(analysis)
            standings_by_v = by_variant(standings)
            costs_by_v = by_variant(costs)

            result = {
                "experiment_id": experiment_id,
                "info": info,
                "variants": list(variant_game_ids.keys()),
                "total_games": len(all_game_ids),
                "decision_quality": {},
                "behavioral": {},
                "option_presentation": {},
                "outcomes": {},
                "costs": {},
            }

            for variant in variant_game_ids:
                vd = decisions_by_v.get(variant, [])
                va = analysis_by_v.get(variant, [])
                vs = standings_by_v.get(variant, [])
                vc = costs_by_v.get(variant, [])

                result["decision_quality"][variant] = self._compute_decision_quality(vd, va)
                result["behavioral"][variant] = self._compute_behavioral(vd)
                result["option_presentation"][variant] = self._compute_option_presentation(vd)
                result["outcomes"][variant] = self._compute_outcomes(vs)
                result["costs"][variant] = self._compute_costs(vc, len(vd))

            return result
        finally:
            conn.close()

    # ------------------------------------------------------------------ #
    #  Data fetching
    # ------------------------------------------------------------------ #

    def _get_experiment_info(self, conn: sqlite3.Connection, experiment_id: int) -> Dict:
        row = conn.execute(
            "SELECT id, name, status, created_at FROM experiments WHERE id = ?",
            (experiment_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Experiment {experiment_id} not found")
        return dict(row)

    def _get_variant_game_ids(self, conn: sqlite3.Connection, experiment_id: int) -> Dict[str, List[str]]:
        rows = conn.execute(
            "SELECT game_id, variant FROM experiment_games WHERE experiment_id = ?",
            (experiment_id,),
        ).fetchall()
        result: Dict[str, List[str]] = defaultdict(list)
        for row in rows:
            result[row["variant"] or "default"].append(row["game_id"])
        return dict(result)

    def _fetch_decisions(self, conn: sqlite3.Connection, game_ids: List[str]) -> List[Dict]:
        if not game_ids:
            return []
        placeholders = ",".join("?" * len(game_ids))

        # Check if metadata_json column exists (pre-v76 DBs won't have it)
        cursor = conn.execute("PRAGMA table_info(prompt_captures)")
        columns = {row[1] for row in cursor.fetchall()}
        has_metadata = "metadata_json" in columns

        meta_col = ", metadata_json" if has_metadata else ""
        rows = conn.execute(f"""
            SELECT game_id, player_name, hand_number, phase,
                   action_taken, raise_amount, pot_total, cost_to_call,
                   player_stack{meta_col}
            FROM prompt_captures
            WHERE game_id IN ({placeholders})
              AND action_taken IS NOT NULL
            ORDER BY game_id, hand_number, id
        """, game_ids).fetchall()
        results = [dict(r) for r in rows]
        # Ensure metadata_json key exists for downstream code
        if not has_metadata:
            for r in results:
                r["metadata_json"] = None
        return results

    def _fetch_analysis(self, conn: sqlite3.Connection, game_ids: List[str]) -> List[Dict]:
        if not game_ids:
            return []
        placeholders = ",".join("?" * len(game_ids))
        # Check if table has rows for these games
        try:
            rows = conn.execute(f"""
                SELECT game_id, player_name, hand_number, phase,
                       equity, required_equity, ev_lost,
                       decision_quality, action_taken
                FROM player_decision_analysis
                WHERE game_id IN ({placeholders})
                ORDER BY game_id, hand_number, id
            """, game_ids).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    def _fetch_standings(self, conn: sqlite3.Connection, game_ids: List[str]) -> List[Dict]:
        if not game_ids:
            return []
        placeholders = ",".join("?" * len(game_ids))
        try:
            rows = conn.execute(f"""
                SELECT game_id, player_name, finishing_position,
                       eliminated_by, eliminated_at_hand
                FROM tournament_standings
                WHERE game_id IN ({placeholders})
            """, game_ids).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    def _fetch_costs(self, conn: sqlite3.Connection, game_ids: List[str]) -> List[Dict]:
        if not game_ids:
            return []
        placeholders = ",".join("?" * len(game_ids))
        try:
            rows = conn.execute(f"""
                SELECT game_id, estimated_cost, input_tokens, output_tokens,
                       latency_ms
                FROM api_usage
                WHERE game_id IN ({placeholders})
            """, game_ids).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    # ------------------------------------------------------------------ #
    #  Metric computation
    # ------------------------------------------------------------------ #

    def _compute_decision_quality(self, decisions: List[Dict], analysis: List[Dict]) -> Dict:
        result: Dict[str, Any] = {}

        # --- Raise utilization (from bounded_options metadata) ---
        raise_offered = 0
        raise_taken = 0
        for d in decisions:
            options = self._parse_bounded_options(d)
            if not options:
                continue
            has_plus_ev_raise = any(
                o.get("action") == "raise" and o.get("ev_estimate") == "+EV"
                for o in options
            )
            if has_plus_ev_raise:
                raise_offered += 1
                if d["action_taken"] in ("raise", "all_in"):
                    raise_taken += 1
        result["raise_utilization"] = _safe_pct(raise_taken, raise_offered)
        result["raise_utilization_n"] = raise_offered

        # --- From player_decision_analysis ---
        if not analysis:
            result["fold_accuracy"] = None
            result["fold_accuracy_n"] = 0
            result["blunder_rate"] = None
            result["blunder_rate_n"] = 0
            result["ev_lost_per_100"] = None
            return result

        # Fold accuracy: when equity < required_equity * 0.85, did player fold?
        should_fold = [
            a for a in analysis
            if a["equity"] is not None
            and a["required_equity"] is not None
            and a["equity"] < a["required_equity"] * 0.85
        ]
        folded_correctly = sum(1 for a in should_fold if a["action_taken"] == "fold")
        result["fold_accuracy"] = _safe_pct(folded_correctly, len(should_fold))
        result["fold_accuracy_n"] = len(should_fold)

        # Blunder rate: decision_quality == 'mistake' + catastrophes
        mistakes = sum(1 for a in analysis if a["decision_quality"] == "mistake")
        # Catastrophes: fold with >80% equity, call with <5% equity, river all-in <10%
        for a in analysis:
            eq = a["equity"]
            if eq is None:
                continue
            act = a["action_taken"]
            if act == "fold" and eq > 0.80:
                mistakes += 1
            elif act == "call" and eq < 0.05:
                mistakes += 1
            elif act == "all_in" and a["phase"] == "RIVER" and eq < 0.10:
                mistakes += 1
        result["blunder_rate"] = _safe_pct(mistakes, len(analysis))
        result["blunder_rate_n"] = len(analysis)

        # EV lost / 100 hands
        total_ev_lost = sum(a["ev_lost"] or 0 for a in analysis)
        hand_count = len({(a["game_id"], a["hand_number"]) for a in analysis if a["hand_number"] is not None})
        result["ev_lost_per_100"] = round(total_ev_lost / hand_count * 100, 1) if hand_count else None

        return result

    def _compute_behavioral(self, decisions: List[Dict]) -> Dict:
        """Per-player behavioral stats."""
        by_player: Dict[str, List[Dict]] = defaultdict(list)
        for d in decisions:
            by_player[d["player_name"]].append(d)

        players = {}
        for player, decs in by_player.items():
            preflop = [d for d in decs if d["phase"] == "PRE_FLOP"]
            postflop = [d for d in decs if d["phase"] in ("FLOP", "TURN", "RIVER")]

            # VPIP
            vpip_actions = sum(1 for d in preflop if d["action_taken"] in ("call", "raise", "all_in"))
            vpip = _safe_pct(vpip_actions, len(preflop))

            # Postflop aggression
            pf_agg_actions = sum(1 for d in postflop if d["action_taken"] in ("raise", "all_in"))
            pf_agg = _safe_pct(pf_agg_actions, len(postflop))

            # Check rate with strong hands (equity >= 0.65)
            strong_postflop = [d for d in postflop if self._get_equity(d) is not None and self._get_equity(d) >= 0.65]
            strong_checks = sum(1 for d in strong_postflop if d["action_taken"] == "check")
            check_strong = _safe_pct(strong_checks, len(strong_postflop))

            players[player] = {
                "vpip": vpip,
                "vpip_n": len(preflop),
                "postflop_aggression": pf_agg,
                "postflop_n": len(postflop),
                "check_strong_rate": check_strong,
                "check_strong_n": len(strong_postflop),
            }

        # Profile differentiation: VPIP spread
        vpip_values = [p["vpip"] for p in players.values() if p["vpip"] is not None]
        differentiation = round(max(vpip_values) - min(vpip_values), 1) if len(vpip_values) >= 2 else None

        return {"players": players, "vpip_spread": differentiation}

    def _compute_option_presentation(self, decisions: List[Dict]) -> Dict:
        """Option-1 selection rate and avg options presented."""
        option1_matches = 0
        option1_total = 0
        total_option_counts = []

        for d in decisions:
            options = self._parse_bounded_options(d)
            if not options:
                continue
            total_option_counts.append(len(options))

            first_action = options[0].get("action")
            actual = d["action_taken"]
            # Match raise to raise regardless of raise_to amount
            if _actions_match(first_action, actual):
                option1_matches += 1
            option1_total += 1

        return {
            "option1_rate": _safe_pct(option1_matches, option1_total),
            "option1_n": option1_total,
            "avg_options": round(sum(total_option_counts) / len(total_option_counts), 1) if total_option_counts else None,
        }

    def _compute_outcomes(self, standings: List[Dict]) -> Dict:
        if not standings:
            return {"avg_finish": None, "bust_rate": None}

        positions = [s["finishing_position"] for s in standings if s["finishing_position"] is not None]
        avg_finish = round(sum(positions) / len(positions), 2) if positions else None

        # Bust-out rate: players who were eliminated (have eliminated_by set)
        eliminated = sum(1 for s in standings if s.get("eliminated_by"))
        bust_rate = _safe_pct(eliminated, len(standings))

        return {"avg_finish": avg_finish, "bust_rate": bust_rate, "n": len(standings)}

    def _compute_costs(self, costs: List[Dict], decision_count: int) -> Dict:
        if not costs:
            return {"total_cost": None, "cost_per_decision": None, "avg_latency": None}

        total_cost = sum(c["estimated_cost"] or 0 for c in costs)
        latencies = [c["latency_ms"] for c in costs if c["latency_ms"] is not None]
        avg_latency = round(sum(latencies) / len(latencies)) if latencies else None

        return {
            "total_cost": round(total_cost, 4),
            "cost_per_decision": round(total_cost / decision_count, 4) if decision_count else None,
            "avg_latency": avg_latency,
            "n": len(costs),
        }

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    def _parse_bounded_options(self, decision: Dict) -> Optional[List[Dict]]:
        """Parse bounded_options from metadata_json. Returns None if unavailable."""
        raw = decision.get("metadata_json")
        if not raw:
            return None
        try:
            meta = json.loads(raw)
            options = meta.get("bounded_options")
            if isinstance(options, list):
                return options
        except (json.JSONDecodeError, TypeError):
            pass
        return None

    def _get_equity(self, decision: Dict) -> Optional[float]:
        """Get equity from metadata_json if available."""
        raw = decision.get("metadata_json")
        if not raw:
            return None
        try:
            meta = json.loads(raw)
            eq = meta.get("equity")
            return float(eq) if eq is not None else None
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    # ------------------------------------------------------------------ #
    #  Formatting — Markdown
    # ------------------------------------------------------------------ #

    def _format_markdown(self, metrics: Dict, baseline: Optional[Dict] = None) -> str:
        info = metrics["info"]
        variants = metrics["variants"]
        lines = []

        lines.append(f"# Scorecard: experiment {info['id']} ({info['name']})")
        lines.append(f"Variants: {len(variants)} | Games: {metrics['total_games']}")
        lines.append("")

        # --- Decision Quality ---
        lines.append("## Decision Quality")
        dq = metrics["decision_quality"]
        b_dq = baseline["decision_quality"] if baseline else None
        header = ["Metric"] + variants
        if b_dq:
            header.append("\u0394")
        rows = []
        for label, key, fmt_fn in [
            ("Raise utilization", "raise_utilization", _fmt_pct),
            ("Fold accuracy", "fold_accuracy", _fmt_pct),
            ("Blunder rate", "blunder_rate", _fmt_pct),
            ("EV lost / 100 hands", "ev_lost_per_100", _fmt_num),
        ]:
            row = [label]
            for v in variants:
                row.append(fmt_fn(dq[v].get(key)))
            if b_dq:
                row.append(self._delta_str(dq, b_dq, variants, key, fmt_fn, invert=(key == "blunder_rate" or key == "ev_lost_per_100")))
            rows.append(row)
        lines.extend(_md_table(header, rows))
        lines.append("")

        # --- Behavioral ---
        lines.append("## Behavioral")
        beh = metrics["behavioral"]
        beh_header = ["Player", "Variant", "VPIP", "PF Agg", "Check w/ Strong"]
        beh_rows = []
        for v in variants:
            players = beh[v]["players"]
            for player, stats in sorted(players.items()):
                beh_rows.append([
                    player, v,
                    _fmt_pct(stats["vpip"]),
                    _fmt_pct(stats["postflop_aggression"]),
                    _fmt_pct(stats["check_strong_rate"]),
                ])
        lines.extend(_md_table(beh_header, beh_rows))

        # Differentiation
        for v in variants:
            spread = beh[v]["vpip_spread"]
            if spread is not None:
                indicator = "\u2705" if spread >= 15 else "\u26a0\ufe0f" if spread >= 8 else "\u274c"
                lines.append(f"  {v} VPIP spread: {spread:.1f}pp {indicator} (target >15pp)")
        lines.append("")

        # --- Option Presentation ---
        lines.append("## Option Presentation")
        op = metrics["option_presentation"]
        op_header = ["Metric"] + variants
        op_rows = [
            ["Option-1 rate"] + [_fmt_pct(op[v]["option1_rate"]) for v in variants],
            ["Avg options"] + [_fmt_num(op[v]["avg_options"]) for v in variants],
        ]
        lines.extend(_md_table(op_header, op_rows))
        lines.append("")

        # --- Outcomes ---
        lines.append("## Outcomes")
        oc = metrics["outcomes"]
        oc_header = ["Metric"] + variants
        oc_rows = [
            ["Avg finish position"] + [_fmt_num(oc[v]["avg_finish"]) for v in variants],
            ["Bust-out rate"] + [_fmt_pct(oc[v]["bust_rate"]) for v in variants],
        ]
        lines.extend(_md_table(oc_header, oc_rows))
        lines.append("")

        # --- Costs ---
        lines.append("## Costs")
        co = metrics["costs"]
        co_header = ["Metric"] + variants
        co_rows = [
            ["Total cost"] + [f"${co[v]['total_cost']:.4f}" if co[v]["total_cost"] is not None else "N/A" for v in variants],
            ["Cost / decision"] + [f"${co[v]['cost_per_decision']:.4f}" if co[v]["cost_per_decision"] is not None else "N/A" for v in variants],
            ["Avg latency (ms)"] + [_fmt_num(co[v]["avg_latency"]) for v in variants],
        ]
        lines.extend(_md_table(co_header, co_rows))

        return "\n".join(lines) + "\n"

    def _delta_str(
        self,
        current: Dict,
        baseline: Dict,
        variants: List[str],
        key: str,
        fmt_fn,
        invert: bool = False,
    ) -> str:
        """Compute delta between first variant of current vs first variant of baseline."""
        v = variants[0] if variants else None
        if not v:
            return ""
        cur_val = current.get(v, {}).get(key)
        # For baseline, use the first variant available
        b_variants = list(baseline.keys())
        b_v = b_variants[0] if b_variants else None
        bas_val = baseline.get(b_v, {}).get(key) if b_v else None

        if cur_val is None or bas_val is None:
            return "N/A"

        delta = cur_val - bas_val
        # For inverted metrics (lower is better), flip the indicator
        improved = delta < 0 if invert else delta > 0
        regressed = delta > 0 if invert else delta < 0

        delta_str = fmt_fn(abs(delta))
        sign = "+" if delta > 0 else "-" if delta < 0 else ""
        indicator = ""
        if abs(delta) > 5 and improved:
            indicator = " \u2705"
        elif abs(delta) > 5 and regressed:
            indicator = " \u26a0\ufe0f"

        return f"{sign}{delta_str}{indicator}"

    # ------------------------------------------------------------------ #
    #  Formatting — JSON
    # ------------------------------------------------------------------ #

    def _format_json(self, metrics: Dict, baseline: Optional[Dict] = None) -> str:
        output = {"experiment": metrics}
        if baseline:
            output["baseline"] = baseline
        return json.dumps(output, indent=2, default=str)


# ====================================================================== #
#  Utility functions
# ====================================================================== #

def _safe_pct(numerator: int, denominator: int) -> Optional[float]:
    """Return percentage (0-100) or None if denominator is zero."""
    if denominator == 0:
        return None
    return round(numerator / denominator * 100, 1)


def _fmt_pct(val: Optional[float]) -> str:
    return f"{val:.1f}%" if val is not None else "N/A"


def _fmt_num(val) -> str:
    if val is None:
        return "N/A"
    if isinstance(val, float):
        return f"{val:.1f}" if val != int(val) else str(int(val))
    return str(val)


def _actions_match(option_action: str, actual_action: str) -> bool:
    """Check if a bounded option action matches the action_taken."""
    if option_action == actual_action:
        return True
    # 'raise' option matches 'all_in' action and vice versa
    raise_like = {"raise", "all_in"}
    return option_action in raise_like and actual_action in raise_like


def _md_table(header: List[str], rows: List[List[str]]) -> List[str]:
    """Render a markdown table."""
    if not rows:
        return ["(no data)", ""]

    # Calculate column widths
    all_rows = [header] + rows
    widths = [max(len(str(row[i])) for row in all_rows) for i in range(len(header))]

    lines = []
    # Header
    lines.append("| " + " | ".join(str(h).ljust(w) for h, w in zip(header, widths)) + " |")
    # Separator
    lines.append("|" + "|".join("-" * (w + 2) for w in widths) + "|")
    # Rows
    for row in rows:
        lines.append("| " + " | ".join(str(c).ljust(w) for c, w in zip(row, widths)) + " |")
    return lines


# ====================================================================== #
#  CLI
# ====================================================================== #

def main():
    parser = argparse.ArgumentParser(
        description="Generate experiment scorecard with decision quality metrics.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("experiment_id", type=int, help="Experiment ID to evaluate")
    parser.add_argument("--compare", type=int, metavar="BASELINE_ID",
                        help="Baseline experiment ID for delta comparison")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--db", type=str, help="Path to database file")

    args = parser.parse_args()

    try:
        sc = Scorecard(db_path=args.db)
        report = sc.generate(
            experiment_id=args.experiment_id,
            baseline_id=args.compare,
            format="json" if args.json else "markdown",
        )
        print(report)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
