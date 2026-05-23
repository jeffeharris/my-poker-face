"""v2 trace: capture pipeline snapshot AND intervention traces per decision."""
from __future__ import annotations

import json
import logging
import sys

sys.path.insert(0, "/app")

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def main():
    import sqlite3
    from pathlib import Path

    Path("/app/data/sim_trace_v2").mkdir(parents=True, exist_ok=True)
    trace_file = open("/app/data/sim_trace_v2/decisions.jsonl", "w")

    # Personality lookup
    conn = sqlite3.connect("/app/data/poker_games.db")
    conn.row_factory = sqlite3.Row
    pid_meta = {}
    for r in conn.execute(
        "SELECT personality_id, name, config_json FROM personalities"
    ):
        try:
            cfg = json.loads(r["config_json"] or "{}")
        except Exception:
            cfg = {}
        anchors = cfg.get("anchors") or {}
        pid_meta[r["name"]] = {
            "personality_id": r["personality_id"],
            "looseness": anchors.get("baseline_looseness"),
            "aggression": anchors.get("baseline_aggression"),
        }

    row = conn.execute(
        "SELECT sandbox_id FROM sandboxes WHERE owner_id='guest_jeff' "
        "AND archived_at IS NULL LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        print("No sandbox found")
        return 1
    sandbox = row["sandbox_id"]
    logger.info(f"Sandbox: {sandbox}")

    # Hook
    from poker import tiered_bot_controller as tbc_mod
    original_decide = tbc_mod.TieredBotController.decide_action

    def wrapped(self, *args, **kwargs):
        result = original_decide(self, *args, **kwargs)
        snap = getattr(self, "_last_pipeline_snapshot", None)
        traces = getattr(self, "_last_intervention_trace", None)

        if isinstance(snap, dict):
            display_name = self.player_name
            meta = pid_meta.get(display_name, {})
            record = dict(snap)
            record["_player_name"] = display_name
            record["_personality_id"] = meta.get("personality_id")
            record["_anchor_looseness"] = meta.get("looseness")
            record["_anchor_aggression"] = meta.get("aggression")
            if isinstance(result, dict):
                record["_final_action"] = result.get("action")

            # Serialize intervention traces — just the parts we care about
            intervention_summary = []
            if isinstance(traces, list):
                for t in traces:
                    intervention_summary.append({
                        "layer": getattr(t, "layer", None),
                        "rule_id": getattr(t, "rule_id", None),
                        "fired": getattr(t, "fired", False),
                        "action_changed": getattr(t, "action_changed", False),
                        "reason_code": getattr(t, "reason_code", ""),
                        "before": getattr(t, "primary_action_before", ""),
                        "after": getattr(t, "primary_action_after", ""),
                        "effect_size": getattr(t, "effect_size", 0.0),
                    })
            record["_interventions"] = intervention_summary
            trace_file.write(json.dumps(record, default=str) + "\n")
        return result

    tbc_mod.TieredBotController.decide_action = wrapped

    from cash_mode.sim_runner import SimConfig, run_sim
    from poker.repositories import create_repos

    repos = create_repos("/app/data/poker_games.db")
    config = SimConfig(
        sandbox_id=sandbox,
        num_ticks=500,
        hand_sim_prob=1.0,
        rng_seed=42,
        metrics_every=100,
        audit_every=500,
        progress_every=100,
    )
    result = run_sim(config, repos=repos)
    trace_file.close()
    logger.info(f"Done in {result.wall_seconds:.0f}s")

    with open("/app/data/sim_trace_v2/decisions.jsonl") as f:
        n = sum(1 for _ in f)
    logger.info(f"Trace lines: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
