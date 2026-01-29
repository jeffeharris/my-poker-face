"""DB-backed LLM settings.

Runtime getters that check app_settings in the database before falling back
to the static defaults in core.llm.config.  Both poker/ and flask_app/ can
import from here without circular dependencies.

The GamePersistence import is lazy (inside function body) so that core/
has no import-time dependency on poker/.
"""

import os
from functools import lru_cache

from .config import (
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    FAST_MODEL,
    FAST_PROVIDER,
    ASSISTANT_MODEL,
    ASSISTANT_PROVIDER,
    IMAGE_PROVIDER,
    IMAGE_MODEL,
)


def _get_db_path() -> str:
    """Get the database path based on environment.

    Duplicates the logic from flask_app.config.get_db_path() so that
    core/ has no import-time dependency on flask_app/.
    """
    if os.path.exists('/app/data'):
        return '/app/data/poker_games.db'
    return os.path.join(os.path.dirname(__file__), '..', '..', 'poker_games.db')


@lru_cache(maxsize=1)
def _get_config_persistence():
    """Get a cached GamePersistence instance for config lookups.

    Note: This caches a single shared instance across all callers. This is safe
    because GamePersistence uses context managers for all DB operations and
    maintains no connection state between calls. Each operation opens and closes
    its own connection.

    If GamePersistence is ever modified to maintain persistent state (connection
    pools, cached transactions, etc.), this caching pattern must be revisited.
    """
    from poker.persistence import GamePersistence
    return GamePersistence(_get_db_path())


def _get_setting(key: str, default: str) -> str:
    """Get a setting value from DB, falling back to the provided default.

    Priority: 1. Database (app_settings), 2. default (from core.llm.config / env)
    """
    p = _get_config_persistence()
    db_value = p.get_setting(key, '')
    return db_value if db_value else default


def get_default_provider() -> str:
    return _get_setting('DEFAULT_PROVIDER', DEFAULT_PROVIDER)


def get_default_model() -> str:
    return _get_setting('DEFAULT_MODEL', DEFAULT_MODEL)


def get_fast_provider() -> str:
    return _get_setting('FAST_PROVIDER', FAST_PROVIDER)


def get_fast_model() -> str:
    return _get_setting('FAST_MODEL', FAST_MODEL)


def get_assistant_provider() -> str:
    return _get_setting('ASSISTANT_PROVIDER', ASSISTANT_PROVIDER)


def get_assistant_model() -> str:
    return _get_setting('ASSISTANT_MODEL', ASSISTANT_MODEL)


def get_image_provider() -> str:
    return _get_setting('IMAGE_PROVIDER', IMAGE_PROVIDER)


def get_image_model() -> str:
    return _get_setting('IMAGE_MODEL', IMAGE_MODEL)
