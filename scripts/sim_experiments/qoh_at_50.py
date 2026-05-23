"""One-off: relocate queen_of_hearts to $50 comfort, run 10k sim, restore.

DANGER: temporarily mutates queen_of_hearts's personality config in the
production DB. The try/finally guarantees restoration even if the sim
crashes. If the process is killed mid-run, the restore won't fire and
the config will stay modified until manual intervention.
"""
from __future__ import annotations

import csv
import json
import logging
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DB_PATH = "/app/data/poker_games.db"
TARGET_PID = "queen_of_hearts"
NEW_COMFORT = "$50"
NEW_STARTING_BANKROLL = 5000

# --- 1. Backup + mutate ---


def load_config(conn, pid):
    row = conn.execute(
        "SELECT config_json FROM personalities WHERE personality_id = ?",
        (pid,),
    ).fetchone()
    if not row:
        raise SystemExit(f"Personality {pid!r} not found in DB — aborting.")
    return json.loads(row[0])


def save_config(conn, pid, config):
    conn.execute(
        "UPDATE personalities SET config_json = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE personality_id = ?",
        (json.dumps(config), pid),
    )
    conn.commit()


def main() -> int:
    conn = sqlite3.connect(DB_PATH)

    original = load_config(conn, TARGET_PID)
    logger.info("Original QoH bankroll_knobs: %r", original.get("bankroll_knobs"))

    modified = json.loads(json.dumps(original))  # deep copy
    knobs = modified.setdefault("bankroll_knobs", {})
    knobs["stake_comfort_zone"] = NEW_COMFORT
    knobs["starting_bankroll"] = NEW_STARTING_BANKROLL

    try:
        save_config(conn, TARGET_PID, modified)
        conn.close()  # let other connections see the mutation cleanly
        logger.info("MUTATED QoH: comfort=%s, starting_bankroll=%d",
                    NEW_COMFORT, NEW_STARTING_BANKROLL)

        # --- 2. Seed sandbox ---
        seed = subprocess.run(
            ["python", "-m", "scripts.seed_sim_sandbox",
             "--name", "qoh-at-50-10k", "--owner-id", "sim-bot"],
            capture_output=True, text=True, check=True,
        )
        sandbox = seed.stdout.strip().splitlines()[-1]
        logger.info("Seeded sandbox: %s", sandbox)

        # --- 3. Run sim ---
        Path("/app/data/sim_qoh_at_50").mkdir(parents=True, exist_ok=True)
        run = subprocess.run(
            ["python", "-m", "scripts.run_economy_sim",
             "--sandbox-id", sandbox,
             "--ticks", "10000",
             "--hand-sim-prob", "1.0",
             "--metrics-every", "10",
             "--audit-every", "500",
             "--progress-every", "1000",
             "--rng-seed", "42",
             "--out", "/app/data/sim_qoh_at_50/run1"],
            capture_output=True, text=True,
        )
        # Save full log for grep
        Path("/tmp/qoh50.log").write_text(run.stderr + "\n---STDOUT---\n" + run.stdout)
        logger.info("Sim exit: %d", run.returncode)
        if run.returncode != 0:
            logger.error("Sim failed; see /tmp/qoh50.log")

        # --- 4. Quick analysis (also done in post-run analyze) ---
        n_climbs = sum(1 for line in (run.stderr.splitlines()) if "aspiration_climb" in line)
        logger.info("aspiration_climb fires: %d", n_climbs)

    finally:
        # --- 5. RESTORE original config — critical ---
        restore_conn = sqlite3.connect(DB_PATH)
        save_config(restore_conn, TARGET_PID, original)
        restored = load_config(restore_conn, TARGET_PID)
        restore_conn.close()
        if restored == original:
            logger.info("✓ Restored QoH original config: %r",
                        original.get("bankroll_knobs"))
        else:
            logger.error("✗ RESTORE FAILED — manually check %r", TARGET_PID)
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
