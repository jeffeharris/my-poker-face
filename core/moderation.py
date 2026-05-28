"""Text content moderation via OpenAI's (free) omni-moderation endpoint.

Screens user-supplied free text that is shown to other players or fed into an
LLM prompt — currently the human profile bio (AI-visible + table-visible) and
the avatar-generation prompt. Moderation is free and runs per-save, so the
cost/latency is negligible.

Policy:
- **Fail-closed on a positive classification**: a FLAGGED result blocks the save.
- **Fail-open on any error** (no key, API down/slow/timeout): return not-flagged
  and log, so a moderation outage never blocks a legitimate user.
- **No-op** (allowed) when disabled via ``MODERATION_ENABLED=false`` or when no
  ``OPENAI_API_KEY`` is configured.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)

_MODEL = os.environ.get("MODERATION_MODEL", "omni-moderation-latest")
_client = None
_client_lock = threading.Lock()


@dataclass
class ModerationResult:
    """Outcome of a moderation check.

    ``checked`` is False when moderation did not actually run (disabled, no key,
    empty text, or an API error) — callers treat that as allowed (fail-open).
    """

    flagged: bool
    categories: List[str] = field(default_factory=list)
    checked: bool = True


def is_enabled() -> bool:
    """Whether moderation will actually run (env opt-out + a key present)."""
    if os.environ.get("MODERATION_ENABLED", "true").strip().lower() in ("0", "false", "no", "off"):
        return False
    return bool(os.environ.get("OPENAI_API_KEY"))


def _get_client():
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            from openai import OpenAI

            from core.llm.providers.http_client import shared_http_client

            _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], http_client=shared_http_client)
        return _client


def moderate_text(text: str) -> ModerationResult:
    """Classify ``text``. Allowed (fail-open) on disabled / empty / error."""
    text = (text or "").strip()
    if not text or not is_enabled():
        return ModerationResult(flagged=False, checked=False)
    try:
        resp = _get_client().moderations.create(model=_MODEL, input=text)
        result = resp.results[0]
        try:
            categories = [name for name, on in result.categories.model_dump().items() if on]
        except Exception:
            categories = []
        return ModerationResult(flagged=bool(result.flagged), categories=categories)
    except Exception as e:
        # Never block a save on a moderation outage; surface for visibility.
        logger.warning("[MODERATION] check failed, allowing content: %s", e)
        return ModerationResult(flagged=False, checked=False)
