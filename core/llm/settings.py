"""DB-backed LLM settings.

Runtime getters that check app_settings in the database before falling back
to the static defaults in core.llm.config.  Both poker/ and flask_app/ can
import from here without circular dependencies.

The GamePersistence import is lazy (inside function body) so that core/
has no import-time dependency on poker/.
"""

from functools import lru_cache

from .config import DEFAULT_MODEL, ASSISTANT_MODEL, ASSISTANT_PROVIDER


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
    return GamePersistence()


def get_default_provider() -> str:
    """Get the default LLM provider from app_settings or environment.

    Priority: 1. Database (app_settings), 2. Default ('openai')
    """
    p = _get_config_persistence()
    db_value = p.get_setting('DEFAULT_PROVIDER', '')
    if db_value:
        return db_value
    return 'openai'


def get_default_model() -> str:
    """Get the default LLM model from app_settings or environment.

    Priority: 1. Database (app_settings), 2. core.llm.config.DEFAULT_MODEL
    """
    p = _get_config_persistence()
    db_value = p.get_setting('DEFAULT_MODEL', '')
    if db_value:
        return db_value
    return DEFAULT_MODEL


def get_assistant_provider() -> str:
    """Get the assistant provider from app_settings or environment.

    Priority: 1. Database (app_settings), 2. core.llm.config.ASSISTANT_PROVIDER
    """
    p = _get_config_persistence()
    db_value = p.get_setting('ASSISTANT_PROVIDER', '')
    if db_value:
        return db_value
    return ASSISTANT_PROVIDER


def get_assistant_model() -> str:
    """Get the assistant model from app_settings or environment.

    Priority: 1. Database (app_settings), 2. core.llm.config.ASSISTANT_MODEL
    """
    p = _get_config_persistence()
    db_value = p.get_setting('ASSISTANT_MODEL', '')
    if db_value:
        return db_value
    return ASSISTANT_MODEL
