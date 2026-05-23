"""Run a 10k sim with the MANIAC deviation profile clamped to LAG-like
levels. Mutate `deviation_profiles.py` in place, run sim, restore.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DEVIATION_PROFILES_PATH = "/app/poker/strategy/deviation_profiles.py"

ORIGINAL_MANIAC = """    'maniac': DeviationProfile(
        max_kl=1.2, max_per_action_shift=0.60,
        aggression_scale=2.0, looseness_scale=2.0,
        risk_scale=1.6, ego_fold_penalty=0.60,
    ),"""

NERFED_MANIAC = """    'maniac': DeviationProfile(
        max_kl=0.8, max_per_action_shift=0.40,
        aggression_scale=1.5, looseness_scale=1.5,
        risk_scale=1.2, ego_fold_penalty=0.40,
    ),"""


def main() -> int:
    original_content = Path(DEVIATION_PROFILES_PATH).read_text()
    if ORIGINAL_MANIAC not in original_content:
        logger.error("Original maniac block not found — aborting")
        return 2

    try:
        # Mutate.
        modified_content = original_content.replace(ORIGINAL_MANIAC, NERFED_MANIAC)
        Path(DEVIATION_PROFILES_PATH).write_text(modified_content)
        logger.info("Nerfed maniac profile applied")

        # Seed sandbox.
        seed = subprocess.run(
            ["python", "-m", "scripts.seed_sim_sandbox",
             "--name", "nerfed-maniac-10k", "--owner-id", "sim-bot"],
            capture_output=True, text=True, check=True,
        )
        sandbox = seed.stdout.strip().splitlines()[-1]
        logger.info("Sandbox: %s", sandbox)

        # Run sim.
        Path("/app/data/sim_nerfed_maniac").mkdir(parents=True, exist_ok=True)
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
             "--out", "/app/data/sim_nerfed_maniac/run1"],
            capture_output=True, text=True,
        )
        elapsed = time.monotonic() - t0
        Path("/tmp/nerfed_maniac.log").write_text(
            run.stderr + "\n---STDOUT---\n" + run.stdout
        )
        n_climbs = sum(1 for line in run.stderr.splitlines() if "aspiration_climb" in line)
        logger.info("Sim done in %.0fs (exit=%d, climbs=%d)",
                    elapsed, run.returncode, n_climbs)

    finally:
        # Restore file.
        Path(DEVIATION_PROFILES_PATH).write_text(original_content)
        restored = Path(DEVIATION_PROFILES_PATH).read_text()
        if restored == original_content:
            logger.info("✓ Restored deviation_profiles.py")
        else:
            logger.error("✗ RESTORE MISMATCH")
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
