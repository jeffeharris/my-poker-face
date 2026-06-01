"""Drop a CaseBotV2 grinder into a fresh casino sandbox at the $10 tier and see
how it does in the living economy.

Vessel: abraham_lincoln (already $10 comfort / 12k bankroll). We temporarily set
its config `rule_strategy='case_based_v2'` so full_sim builds it as a CaseBotV2
RuleBot, seed a fresh sandbox, run the economy sim, then read Lincoln's per-pid
result (net chips / bankroll trajectory). try/finally restores the original
config even if the sim crashes.

Run (inside backend container):
  python -m scripts.sim_experiments.casebot_v2_at_10 --ticks 3000
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DB_PATH = "/app/data/poker_games.db"
TARGET_PID = "abraham_lincoln"
OUTDIR = "/app/data/sim_casebot_v2_at_10"


def load_config(conn, pid):
    row = conn.execute(
        "SELECT config_json FROM personalities WHERE personality_id = ?", (pid,)
    ).fetchone()
    if not row:
        raise SystemExit(f"Personality {pid!r} not found — aborting.")
    return json.loads(row[0])


def save_config(conn, pid, config):
    conn.execute(
        "UPDATE personalities SET config_json = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE personality_id = ?",
        (json.dumps(config), pid),
    )
    conn.commit()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=3000)
    ap.add_argument("--rng-seed", type=int, default=42)
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    original = load_config(conn, TARGET_PID)
    logger.info("Original %s: rule_strategy=%r comfort=%r",
                TARGET_PID, original.get("rule_strategy"),
                (original.get("bankroll_knobs") or {}).get("stake_comfort_zone"))

    modified = json.loads(json.dumps(original))
    modified["rule_strategy"] = "case_based_v2"  # build as CaseBotV2 in the sim
    knobs = modified.setdefault("bankroll_knobs", {})
    knobs["stake_comfort_zone"] = "$10"

    try:
        save_config(conn, TARGET_PID, modified)
        conn.close()
        logger.info("MUTATED %s -> CaseBotV2 @ $10", TARGET_PID)

        seed = subprocess.run(
            ["python", "-m", "scripts.seed_sim_sandbox",
             "--name", "casebot-v2-at-10", "--owner-id", "sim-bot"],
            capture_output=True, text=True, check=True,
        )
        sandbox = seed.stdout.strip().splitlines()[-1]
        logger.info("Seeded sandbox: %s", sandbox)

        Path(OUTDIR).mkdir(parents=True, exist_ok=True)
        run = subprocess.run(
            ["python", "-m", "scripts.run_economy_sim",
             "--sandbox-id", sandbox, "--ticks", str(args.ticks),
             "--hand-sim-prob", "1.0", "--metrics-every", "10",
             "--audit-every", "500", "--progress-every", "500",
             "--rng-seed", str(args.rng_seed),
             "--out", f"{OUTDIR}/run1"],
            capture_output=True, text=True,
        )
        Path("/tmp/casebot_v2_at_10.log").write_text(run.stderr + "\n---OUT---\n" + run.stdout)
        logger.info("Sim exit: %d (full log /tmp/casebot_v2_at_10.log)", run.returncode)

        # ── Read Lincoln's per-pid result ──
        pids_path = Path(f"{OUTDIR}/run1.pids.jsonl")
        if pids_path.exists():
            target_rows = []
            for line in pids_path.read_text().splitlines():
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("pid") == TARGET_PID or r.get("personality_id") == TARGET_PID:
                    target_rows.append(r)
            if target_rows:
                logger.info("=== CaseBotV2 (abraham_lincoln @ $10) result ===")
                logger.info(json.dumps(target_rows[-1], indent=2)[:2000])
            else:
                logger.info("No per-pid row for %s — keys in first row: %s",
                            TARGET_PID,
                            list(json.loads(pids_path.read_text().splitlines()[0]).keys())
                            if pids_path.read_text().strip() else "(empty)")
        else:
            logger.error("No pids.jsonl produced — see /tmp/casebot_v2_at_10.log")

    finally:
        restore_conn = sqlite3.connect(DB_PATH)
        save_config(restore_conn, TARGET_PID, original)
        ok = load_config(restore_conn, TARGET_PID) == original
        restore_conn.close()
        logger.info("✓ Restored %s" if ok else "✗ RESTORE FAILED for %s", TARGET_PID)
        if not ok:
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
