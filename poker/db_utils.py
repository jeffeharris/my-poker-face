"""Shared database utility functions.

Single source of truth for database path detection.
"""

import os
from pathlib import Path


def get_default_db_path() -> str:
    """Get the default database path based on environment.

    Returns the Docker path if running inside a container,
    otherwise the local development path (data/poker_games.db).
    """
    if Path('/app/data').exists():
        return '/app/data/poker_games.db'
    return str(Path(__file__).parent.parent / 'data' / 'poker_games.db')


def ensure_db_dir(db_path: str) -> None:
    """Ensure the directory for the database file exists."""
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
