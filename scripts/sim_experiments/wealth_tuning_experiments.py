"""Three back-to-back wealth-tuning experiments. Each:
1. Snapshots the 7 wealthy AIs' configs
2. Mutates to the experiment-specific values
3. Seeds a fresh sandbox + runs a 10k sim, seed 42
4. Restores via finally
5. Moves to the next experiment

Even if any single sim crashes, the restore in finally returns the
production DB to original. The script also rebackups between
experiments — i.e., never assumes a clean starting state.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DB_PATH = "/app/data/poker_games.db"

WEALTHY = [
    "queen_of_hearts",
    "zeus",
    "ebenezer_scrooge",
    "marie_antoinette",
    "queen_elizabeth_i",
    "cleopatra",
    "louis_xiv",
]

# Each experiment: name + per-pid override dict
EXPERIMENTS = [
    {
        "name": "exp_a_equalize_100k",
        "out_dir": "/app/data/sim_exp_a_equalize",
        "label": "A: equalize at 100k",
        "overrides": {
            "queen_of_hearts": 100_000,
            "zeus": 100_000,
            "ebenezer_scrooge": 100_000,
            "marie_antoinette": 100_000,
            "queen_elizabeth_i": 100_000,
            "cleopatra": 100_000,
            "louis_xiv": 100_000,
        },
    },
    {
        "name": "exp_b_demote_60k",
        "out_dir": "/app/data/sim_exp_b_demote",
        "label": "B: demote-via-bankroll to 60k",
        "overrides": {p: 60_000 for p in WEALTHY},
    },
    {
        "name": "exp_c_gradient",
        "out_dir": "/app/data/sim_exp_c_gradient",
        "label": "C: gradient compression",
        "overrides": {
            "ebenezer_scrooge": 150_000,
            "zeus": 130_000,
            "louis_xiv": 110_000,
            "queen_of_hearts": 100_000,
            "cleopatra": 95_000,
            "queen_elizabeth_i": 85_000,
            "marie_antoinette": 80_000,
        },
    },
]


def load_config(conn, pid):
    row = conn.execute(
        "SELECT config_json FROM personalities WHERE personality_id = ?",
        (pid,),
    ).fetchone()
    if not row:
        return None
    return json.loads(row[0])


def save_config(conn, pid, config):
    conn.execute(
        "UPDATE personalities SET config_json = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE personality_id = ?",
        (json.dumps(config), pid),
    )
    conn.commit()


def run_one_experiment(exp):
    label = exp["label"]
    name = exp["name"]
    overrides = exp["overrides"]
    logger.info("=" * 60)
    logger.info("Starting experiment: %s", label)
    logger.info("=" * 60)

    # Snapshot every personality we'll mutate.
    conn = sqlite3.connect(DB_PATH)
    backups: dict[str, dict] = {}
    for pid in overrides:
        cfg = load_config(conn, pid)
        if cfg is None:
            logger.error("Missing personality %r — skipping experiment", pid)
            conn.close()
            return None
        backups[pid] = cfg

    try:
        # Apply overrides.
        for pid, new_bankroll in overrides.items():
            modified = json.loads(json.dumps(backups[pid]))
            knobs = modified.setdefault("bankroll_knobs", {})
            knobs["starting_bankroll"] = new_bankroll
            save_config(conn, pid, modified)
        conn.close()
        logger.info("Mutations applied for %d personalities", len(overrides))

        # Seed sandbox.
        seed = subprocess.run(
            ["python", "-m", "scripts.seed_sim_sandbox",
             "--name", name, "--owner-id", "sim-bot"],
            capture_output=True, text=True, check=True,
        )
        sandbox = seed.stdout.strip().splitlines()[-1]
        logger.info("Sandbox: %s", sandbox)

        # Run sim.
        Path(exp["out_dir"]).mkdir(parents=True, exist_ok=True)
        t0 = time.monotonic()
        run = subprocess.run(
            ["python", "-m", "scripts.run_economy_sim",
             "--sandbox-id", sandbox,
             "--ticks", "10000",
             "--hand-sim-prob", "1.0",
             "--metrics-every", "10",
             "--audit-every", "500",
             "--progress-every", "2000",
             "--rng-seed", "42",
             "--out", f"{exp['out_dir']}/run1"],
            capture_output=True, text=True,
        )
        elapsed = time.monotonic() - t0
        Path(f"/tmp/{name}.log").write_text(
            run.stderr + "\n---STDOUT---\n" + run.stdout
        )
        n_climbs = sum(1 for line in run.stderr.splitlines() if "aspiration_climb" in line)
        logger.info("Sim done in %.0fs (exit=%d, climbs=%d)",
                    elapsed, run.returncode, n_climbs)

        return sandbox

    finally:
        # Always restore.
        restore_conn = sqlite3.connect(DB_PATH)
        all_ok = True
        for pid, original in backups.items():
            save_config(restore_conn, pid, original)
            restored = load_config(restore_conn, pid)
            if restored != original:
                logger.error("✗ Restore FAILED for %s", pid)
                all_ok = False
        restore_conn.close()
        if all_ok:
            logger.info("✓ %d personalities restored", len(backups))


def main() -> int:
    for exp in EXPERIMENTS:
        sandbox = run_one_experiment(exp)
        if sandbox is None:
            logger.error("Experiment %s aborted — continuing to next", exp["name"])
            continue

    logger.info("All experiments complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
