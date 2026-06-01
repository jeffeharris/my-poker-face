"""Seed a fresh sandbox for the economy sim.

Creates a new SandboxState, runs `ensure_ai_bankrolls_seeded` against
it, then `ensure_lobby_seeded` to create the 5 stake-tier tables.
Prints the new sandbox_id to stdout so the run_economy_sim CLI can
consume it.

Usage:
    python3 scripts/seed_sim_sandbox.py --name "sim-baseline-v1"
    python3 scripts/seed_sim_sandbox.py --name "sim-foo" --owner-id sim-bot

    # Inside Docker
    docker compose exec backend python -m scripts.seed_sim_sandbox \\
        --name "sim-baseline-v1"
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add project root to path when run as script.
_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from cash_mode.lobby import ensure_ai_bankrolls_seeded, ensure_lobby_seeded
from poker.repositories import create_repos

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def _get_db_path(db_path: Optional[str] = None) -> str:
    """Default to the Docker-mounted path when present, else local data/."""
    if db_path:
        return db_path
    if Path('/app/data').exists():
        return '/app/data/poker_games.db'
    return str(Path(__file__).parent.parent / 'data' / 'poker_games.db')


def seed_sim_sandbox(
    *,
    name: str,
    owner_id: str,
    db_path: Optional[str] = None,
) -> str:
    """Seed a fresh sandbox + its bankrolls + its lobby. Returns sandbox_id."""
    resolved = _get_db_path(db_path)
    logger.info("Using db: %s", resolved)
    repos = create_repos(resolved)

    # A fresh isolated --db-path has the schema but no personality roster
    # (those rows are loaded from poker/personalities.json into the DB, not
    # by create_repos). Without them ensure_ai_bankrolls_seeded funds zero
    # AIs and the sim drives an empty economy. Seed the roster idempotently
    # so a throwaway sim DB has real opponents to seat. overwrite=False makes
    # it a no-op against a DB that already has them.
    json_path = str(Path(__file__).parent.parent / 'poker' / 'personalities.json')
    seeded = repos['personality_repo'].seed_personalities_from_json(
        json_path, overwrite=False
    )
    logger.info("Seeded personalities from JSON: %s", seeded)

    sandbox = repos['sandbox_repo'].create(owner_id=owner_id, name=name)
    logger.info(
        "Created sandbox: name=%r owner=%r sandbox_id=%s",
        sandbox.name, sandbox.owner_id, sandbox.sandbox_id,
    )

    now = datetime.utcnow()
    actions = ensure_ai_bankrolls_seeded(
        personality_repo=repos['personality_repo'],
        bankroll_repo=repos['bankroll_repo'],
        sandbox_id=sandbox.sandbox_id,
        now=now,
        chip_ledger_repo=repos['chip_ledger_repo'],
    )
    created = sum(1 for v in actions.values() if v == 'created')
    logger.info("Seeded AI bankrolls: %d created (of %d eligible)", created, len(actions))

    tables = ensure_lobby_seeded(
        cash_table_repo=repos['cash_table_repo'],
        personality_repo=repos['personality_repo'],
        bankroll_repo=repos['bankroll_repo'],
        sandbox_id=sandbox.sandbox_id,
        now=now,
        # Pass the ledger repo so the boot seat-fill's bankroll→seat debits
        # record `ai_buy_in` transfers under CHIP_CUSTODY_ENABLED (the live
        # callers in cash_routes already do this; omitting it here made the
        # sim's initial seating an unledgered buy-in — a harness artifact, not
        # a production leak).
        chip_ledger_repo=repos['chip_ledger_repo'],
    )
    logger.info("Seeded lobby tables: %d", len(tables))

    return sandbox.sandbox_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--name', default='sim-sandbox',
        help='Display name for the sandbox (default: %(default)s)',
    )
    parser.add_argument(
        '--owner-id', default='sim-bot',
        help='Owner id for the sandbox (default: %(default)s)',
    )
    parser.add_argument(
        '--db-path', default=None,
        help='Override DB path. Defaults to /app/data/poker_games.db (Docker) '
             'or data/poker_games.db (local).',
    )
    args = parser.parse_args()

    sandbox_id = seed_sim_sandbox(
        name=args.name,
        owner_id=args.owner_id,
        db_path=args.db_path,
    )
    # Print the id last + bare so callers can `$(...)` substitute.
    print(sandbox_id)
    return 0


if __name__ == '__main__':
    sys.exit(main())
