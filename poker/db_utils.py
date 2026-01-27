"""Shared database utility functions."""

from pathlib import Path


def get_default_db_path() -> str:
    """Get the default database path based on environment.

    Returns the Docker path if running inside a container,
    otherwise the local development path.
    """
    if Path('/app/data').exists():
        return '/app/data/poker_games.db'
    return str(Path(__file__).parent.parent / 'poker_games.db')
