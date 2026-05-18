"""Map a personality to (bot_type, llm_config) for cash mode.

Cash mode is "sandbox": every personality plays as the same controller
across sessions (sticky), with a deterministic mapping derived from
authored anchors plus an optional per-personality override.

Lookup order:
  1. ``config_json.bot_profile`` — explicit override (authoritative)
  2. ``config_json.anchors.poise`` — quantile-bucketed
  3. Safe default: ``standard`` + ``openai/gpt-5-nano``

The starting lineup is intentionally narrow (chaos / standard / sharp).
Add other ``bot_type`` values to :data:`BUCKET_DEFAULTS` and the cash
route's dispatch when the sandbox grows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

_VALID_BOT_TYPES = {"chaos", "standard", "sharp"}

DEFAULT_BOT_TYPE = "standard"
DEFAULT_LLM_CONFIG: Dict[str, Any] = {"provider": "openai", "model": "gpt-5-nano"}

# Per-bucket LLM defaults. Override on a personality by setting
# config_json.bot_profile.{provider,model}.
BUCKET_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "chaos":    {"provider": "openai", "model": "gpt-5-nano"},
    "standard": {"provider": "openai", "model": "gpt-5-nano"},
    "sharp":    {"provider": "groq",   "model": "llama-3.1-8b-instant"},
}

POISE_SHARP_THRESHOLD = 0.65
POISE_STANDARD_THRESHOLD = 0.40


@dataclass(frozen=True)
class BotAssignment:
    bot_type: str
    llm_config: Dict[str, Any]


def _bucket_from_poise(poise: float) -> str:
    if poise >= POISE_SHARP_THRESHOLD:
        return "sharp"
    if poise >= POISE_STANDARD_THRESHOLD:
        return "standard"
    return "chaos"


def assign_bot(personality_config: Optional[Dict[str, Any]]) -> BotAssignment:
    """Return the sticky bot+LLM assignment for a personality.

    ``personality_config`` is the personality's config dict (as returned
    by :meth:`PersonalityRepository.load_personality_by_id`). When
    ``None`` or malformed, falls back to the safe default.

    Override format inside ``config_json``::

        "bot_profile": {
            "bot_type": "sharp",                # required, one of chaos/standard/sharp
            "provider": "groq",                 # optional, bucket default otherwise
            "model": "llama-3.1-8b-instant"     # optional, bucket default otherwise
        }
    """
    if not isinstance(personality_config, dict):
        return BotAssignment(DEFAULT_BOT_TYPE, dict(DEFAULT_LLM_CONFIG))

    override = personality_config.get("bot_profile")
    if isinstance(override, dict):
        bot_type = override.get("bot_type")
        if bot_type in _VALID_BOT_TYPES:
            bucket_default = BUCKET_DEFAULTS[bot_type]
            llm = {
                "provider": override.get("provider", bucket_default["provider"]),
                "model":    override.get("model",    bucket_default["model"]),
            }
            return BotAssignment(bot_type, llm)

    anchors = personality_config.get("anchors")
    if isinstance(anchors, dict):
        poise = anchors.get("poise")
        if isinstance(poise, (int, float)):
            bot_type = _bucket_from_poise(float(poise))
            return BotAssignment(bot_type, dict(BUCKET_DEFAULTS[bot_type]))

    return BotAssignment(DEFAULT_BOT_TYPE, dict(DEFAULT_LLM_CONFIG))
