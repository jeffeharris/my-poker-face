"""Run a sim with decision tracing.

Wraps `TieredBotController.decide_action` to capture every decision's
pipeline snapshot to a JSONL file. The snapshot is already built by
the controller; we just persist it.

Output: one JSONL line per decision, fields needed for archetype
behavior analysis.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import sqlite3
from datetime import datetime
from pathlib import Path

import sys as _sys
_sys.path.insert(0, "/app")

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

OUT_DIR = "/app/data/sim_trace"
TRACE_PATH = f"{OUT_DIR}/decisions.jsonl"


def main():
    Path(OUT_DIR).mkdir(parents=True, exist_ok=True)
    # Truncate prior trace.
    trace_file = open(TRACE_PATH, "w")

    # Resolve personality_id → display name + archetype lookup.
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

    # Pick an existing sandbox to run against.
    row = conn.execute(
        "SELECT sandbox_id FROM sandboxes WHERE owner_id='guest_jeff' "
        "AND archived_at IS NULL LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        print("No sandbox found")
        return 1
    sandbox = row["sandbox_id"]
    logger.info(f"Using sandbox: {sandbox}")

    # Hook into the controller.
    from poker import tiered_bot_controller as tbc_mod
    original_decide = tbc_mod.TieredBotController.decide_action

    def wrapped_decide(self, *args, **kwargs):
        result = original_decide(self, *args, **kwargs)
        snap = getattr(self, "_last_pipeline_snapshot", None)
        if isinstance(snap, dict):
            display_name = self.player_name
            meta = pid_meta.get(display_name, {})
            # Add identity info to each snapshot.
            record = dict(snap)
            record["_player_name"] = display_name
            record["_personality_id"] = meta.get("personality_id")
            record["_anchor_looseness"] = meta.get("looseness")
            record["_anchor_aggression"] = meta.get("aggression")
            if isinstance(result, dict):
                record["_final_decision_action"] = result.get("action")
                record["_final_decision_raise_to"] = result.get("raise_to")
            trace_file.write(json.dumps(record, default=str) + "\n")
        return result

    tbc_mod.TieredBotController.decide_action = wrapped_decide

    # Run sim — keep it focused; we don't need 10k ticks for behavior stats.
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
    logger.info(
        f"Sim done in {result.wall_seconds:.0f}s. "
        f"Trace written to {TRACE_PATH}"
    )
    trace_file.close()

    # Quick line count
    with open(TRACE_PATH) as f:
        n = sum(1 for _ in f)
    logger.info(f"Total trace lines: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
