"""Relocate the wealthy class to $50, run 10k sim, restore."""
from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DB_PATH = "/app/data/poker_games.db"

# 7 high-starting-bankroll AIs that normally seed at $1000 / $200.
# Held back: blackbeard (55k), napoleon (80k), king_henry_viii (24k)
# as controls — they keep their natural tiers.
RELOCATE = [
    "queen_of_hearts",
    "zeus",
    "ebenezer_scrooge",
    "marie_antoinette",
    "queen_elizabeth_i",
    "cleopatra",
    "louis_xiv",
]

# 10k bankroll = 2 buy-ins at $50 (min_buy_in 2k, max 5k).
# Above peak aspiration_gap (15-20k sweet spot at $50→$200) so they
# won't aspire back up immediately — we want them sitting at $50.
NEW_STARTING_BANKROLL = 10000
NEW_COMFORT = "$50"


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


def main() -> int:
    conn = sqlite3.connect(DB_PATH)

    # Back up every personality we're about to mutate.
    backups: dict[str, dict] = {}
    for pid in RELOCATE:
        cfg = load_config(conn, pid)
        if cfg is None:
            logger.error("Personality %r not found — aborting before mutation", pid)
            return 2
        backups[pid] = cfg
        logger.info(
            "Backed up %s: comfort=%s start=%s",
            pid,
            cfg.get("bankroll_knobs", {}).get("stake_comfort_zone"),
            cfg.get("bankroll_knobs", {}).get("starting_bankroll"),
        )

    try:
        # Apply the relocation.
        for pid in RELOCATE:
            modified = json.loads(json.dumps(backups[pid]))
            knobs = modified.setdefault("bankroll_knobs", {})
            knobs["stake_comfort_zone"] = NEW_COMFORT
            knobs["starting_bankroll"] = NEW_STARTING_BANKROLL
            save_config(conn, pid, modified)
        conn.close()
        logger.info("MUTATED %d personalities to comfort=%s start=%d",
                    len(RELOCATE), NEW_COMFORT, NEW_STARTING_BANKROLL)

        # Seed sandbox.
        seed = subprocess.run(
            ["python", "-m", "scripts.seed_sim_sandbox",
             "--name", "wealthy-at-50-10k", "--owner-id", "sim-bot"],
            capture_output=True, text=True, check=True,
        )
        sandbox = seed.stdout.strip().splitlines()[-1]
        logger.info("Seeded sandbox: %s", sandbox)

        # Run sim.
        Path("/app/data/sim_wealthy_at_50").mkdir(parents=True, exist_ok=True)
        run = subprocess.run(
            ["python", "-m", "scripts.run_economy_sim",
             "--sandbox-id", sandbox,
             "--ticks", "10000",
             "--hand-sim-prob", "1.0",
             "--metrics-every", "10",
             "--audit-every", "500",
             "--progress-every", "1000",
             "--rng-seed", "42",
             "--out", "/app/data/sim_wealthy_at_50/run1"],
            capture_output=True, text=True,
        )
        Path("/tmp/wealthy50.log").write_text(
            run.stderr + "\n---STDOUT---\n" + run.stdout
        )
        logger.info("Sim exit: %d", run.returncode)

        n_climbs = sum(1 for line in run.stderr.splitlines() if "aspiration_climb" in line)
        logger.info("aspiration_climb fires: %d", n_climbs)

    finally:
        # Restore every modified personality, even if the sim crashed.
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
            logger.info("✓ All %d personalities restored", len(backups))
        else:
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
