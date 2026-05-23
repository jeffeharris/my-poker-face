"""Test: scrooge (rock) at $1000, maniacs (QoH, blackbeard) demoted.

Hypothesis: rocks at top tier don't dominate like maniacs do. By
keeping scrooge at $1000 with his natural bankroll AND demoting the
two known maniacs at high tiers (QoH, blackbeard), the $1000 dynamic
should produce a much flatter concentration curve.
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

# Mutations to apply. Tuples of (pid, knob_overrides_dict).
MUTATIONS = [
    # Scrooge: fix his comfort_zone ($2 was a config bug). Keep
    # his 250k bankroll so he stays the natural $1000 player.
    ("ebenezer_scrooge", {"stake_comfort_zone": "$1000"}),

    # Queen of Hearts: demote the maniac. 60k bankroll × 2.5
    # multiplier = 150k threshold for $1000, so she can't seed
    # there. She'll seed at $200 instead.
    ("queen_of_hearts", {"starting_bankroll": 60_000}),

    # Blackbeard: maniac already at $200. Push him to $50 by
    # dropping below the $200 threshold. 15k × 2.5 = 37k > 20k
    # ($200 threshold), so still at $200. Drop to 10k → 25k
    # threshold > 20k, still $200. Need to drop below 20k bankroll
    # to push him to $50 ($50 threshold = 2k × 2.5 = 5k).
    # 8k bankroll → 20k threshold → just under $200 → seeds at $50.
    ("blackbeard", {"starting_bankroll": 8_000}),
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


def main() -> int:
    conn = sqlite3.connect(DB_PATH)
    backups: dict[str, dict] = {}
    for pid, _ in MUTATIONS:
        cfg = load_config(conn, pid)
        if cfg is None:
            logger.error("Missing personality %r — aborting before mutation", pid)
            return 2
        backups[pid] = cfg
        knobs = cfg.get("bankroll_knobs", {})
        logger.info(
            "Backed up %s: bankroll=%s comfort=%s",
            pid, knobs.get("starting_bankroll"), knobs.get("stake_comfort_zone"),
        )

    try:
        for pid, overrides in MUTATIONS:
            modified = json.loads(json.dumps(backups[pid]))
            knobs = modified.setdefault("bankroll_knobs", {})
            knobs.update(overrides)
            save_config(conn, pid, modified)
            logger.info("APPLIED %s: %s", pid, overrides)
        conn.close()

        # Seed sandbox.
        seed = subprocess.run(
            ["python", "-m", "scripts.seed_sim_sandbox",
             "--name", "scrooge-at-1000", "--owner-id", "sim-bot"],
            capture_output=True, text=True, check=True,
        )
        sandbox = seed.stdout.strip().splitlines()[-1]
        logger.info("Sandbox: %s", sandbox)

        # Run sim.
        Path("/app/data/sim_scrooge_at_1000").mkdir(parents=True, exist_ok=True)
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
             "--out", "/app/data/sim_scrooge_at_1000/run1"],
            capture_output=True, text=True,
        )
        elapsed = time.monotonic() - t0
        Path("/tmp/scrooge1000.log").write_text(
            run.stderr + "\n---STDOUT---\n" + run.stdout
        )
        n_climbs = sum(1 for line in run.stderr.splitlines() if "aspiration_climb" in line)
        logger.info("Sim done in %.0fs (exit=%d, climbs=%d)", elapsed, run.returncode, n_climbs)

    finally:
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

    return 0


if __name__ == "__main__":
    sys.exit(main())
