"""DB-backed LLM settings.

Runtime getters that check app_settings in the database before falling back
to the static defaults in core.llm.config.  Both poker/ and flask_app/ can
import from here without circular dependencies.

The repository imports are lazy (inside function body) so that core/
has no import-time dependency on poker/.
"""

import logging
from functools import lru_cache

from .config import (
    ASSISTANT_MODEL,
    ASSISTANT_PROVIDER,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    FAST_MODEL,
    FAST_PROVIDER,
    IMAGE_MODEL,
    IMAGE_PROVIDER,
    NANO_MODEL,
    NANO_PROVIDER,
)


def _get_db_path() -> str:
    """Get the database path based on environment."""
    from poker.db_utils import get_default_db_path

    return get_default_db_path()


@lru_cache(maxsize=1)
def _get_config_persistence():
    """Get a cached SettingsRepository instance for config lookups.

    Note: This caches a single shared instance across all callers. This is safe
    because SettingsRepository uses context managers for all DB operations and
    maintains no connection state between calls. Each operation opens and closes
    its own connection.
    """
    from poker.repositories import SchemaManager, SettingsRepository

    db_path = _get_db_path()
    SchemaManager(db_path).ensure_schema()
    return SettingsRepository(db_path)


_logger = logging.getLogger(__name__)


def _get_setting(key: str, default: str) -> str:
    """Get a setting value from DB, falling back to the provided default.

    Priority: 1. Database (app_settings), 2. default (from core.llm.config / env)
    """
    try:
        p = _get_config_persistence()
        db_value = p.get_setting(key, '')
        return db_value if db_value else default
    except Exception:
        _logger.debug("DB unavailable for setting %s, using default", key)
        return default


# --- Gameplay: live-tunable AI talk-volume dial ---
# Higher => AI players speak (post-hand commentary) on more hands. Read at
# hand-end by commentary_generator._should_speak so the table's chattiness can
# be tuned from the admin Settings UI WITHOUT a restart. Default 1.3 ≈ a
# speaker on ~44% of hands at chattiness 0.5 (scripts/drama_gate_calibration.py).
DRAMA_SPEAK_SCORE_WEIGHT_DEFAULT = 1.3


def get_drama_speak_score_weight() -> float:
    """Live AI talk-volume dial (post-hand commentary speak gate)."""
    try:
        return float(
            _get_setting('DRAMA_SPEAK_SCORE_WEIGHT', str(DRAMA_SPEAK_SCORE_WEIGHT_DEFAULT))
        )
    except (TypeError, ValueError):
        return DRAMA_SPEAK_SCORE_WEIGHT_DEFAULT


# In-hand counterpart of the post-hand dial — scales how often AIs speak AND
# gesture DURING a hand (drama-gated via speak_gate). Higher => chattier table
# mid-hand; lower => routine folds/checks stay silent (and skip the expression
# LLM call on the tiered path). Read per decision by compute_narration_gate.
MIDGAME_SPEAK_WEIGHT_DEFAULT = 1.3


def get_midgame_speak_weight() -> float:
    """Live AI talk-volume dial (in-hand narration gate: speech + gesture)."""
    try:
        return float(_get_setting('MIDGAME_SPEAK_WEIGHT', str(MIDGAME_SPEAK_WEIGHT_DEFAULT)))
    except (TypeError, ValueError):
        return MIDGAME_SPEAK_WEIGHT_DEFAULT


def get_default_provider() -> str:
    return _get_setting('DEFAULT_PROVIDER', DEFAULT_PROVIDER)


def get_default_model() -> str:
    return _get_setting('DEFAULT_MODEL', DEFAULT_MODEL)


def get_fast_provider() -> str:
    return _get_setting('FAST_PROVIDER', FAST_PROVIDER)


def get_fast_model() -> str:
    return _get_setting('FAST_MODEL', FAST_MODEL)


def get_nano_provider() -> str:
    return _get_setting('NANO_PROVIDER', NANO_PROVIDER)


def get_nano_model() -> str:
    return _get_setting('NANO_MODEL', NANO_MODEL)


def get_assistant_provider() -> str:
    return _get_setting('ASSISTANT_PROVIDER', ASSISTANT_PROVIDER)


def get_assistant_model() -> str:
    return _get_setting('ASSISTANT_MODEL', ASSISTANT_MODEL)


def get_image_provider() -> str:
    return _get_setting('IMAGE_PROVIDER', IMAGE_PROVIDER)


def get_image_model() -> str:
    return _get_setting('IMAGE_MODEL', IMAGE_MODEL)
