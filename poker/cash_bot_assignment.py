"""Map a personality to (bot_type, llm_config) for cash / career mode.

Career mode ships **one** core controller: ``sharp`` (the tiered
solver-table bot). Every non-fish personality plays as tiered, so the
opponents you face are exactly what the bb/100 sim measures, decisions
are instant (table lookups, no per-move LLM call), and difficulty
variety comes from each personality's archetype deviation rather than
from running a weaker engine.

Two escape hatches are preserved:

  1. ``config_json.bot_profile`` — an explicit per-personality override
     (chaos / standard / sharp). This is the sandbox experiment lever:
     pin a specific character to a different engine without forking
     career's default.
  2. ``mode="sandbox"`` — a future "let it happen" career-without-the-
     walkthrough mode. There, ``anchors.poise`` re-enables an engine MIX
     (composed personalities play sharp, erratic ones play chaos/standard)
     so the LLM-driven bots get to play for fun. Career mode never does
     this — it is always tiered unless a personality carries an override.

Fish are handled upstream (``cash_routes``) and routed to RuleBot before
``assign_bot`` is ever called, so they never reach this logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

_VALID_BOT_TYPES = {"chaos", "standard", "sharp"}

# Career default: the tiered solver bot.
DEFAULT_BOT_TYPE = "sharp"

# Per-bucket LLM defaults. For tiered ("sharp") the LLM is used only for
# expression/narration, so a cheap, fast model is the right default.
# Override on a personality by setting config_json.bot_profile.{provider,model}.
BUCKET_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "chaos": {"provider": "openai", "model": "gpt-5-nano"},
    "standard": {"provider": "openai", "model": "gpt-5-nano"},
    "sharp": {"provider": "groq", "model": "llama-3.1-8b-instant"},
}

DEFAULT_LLM_CONFIG: Dict[str, Any] = dict(BUCKET_DEFAULTS[DEFAULT_BOT_TYPE])

# Only consulted in mode="sandbox" (the future let-it-happen mode).
POISE_SHARP_THRESHOLD = 0.65
POISE_STANDARD_THRESHOLD = 0.40


@dataclass(frozen=True)
class BotAssignment:
    bot_type: str
    llm_config: Dict[str, Any]


def _bucket_from_poise(poise: float) -> str:
    """Sandbox-mode-only: map a personality's poise to an engine bucket.

    Retained to power a future "let it happen" sandbox mode where the
    LLM-driven bots (chaos/standard) get to play alongside tiered.
    Career mode does not use this — see :func:`assign_bot`.
    """
    if poise >= POISE_SHARP_THRESHOLD:
        return "sharp"
    if poise >= POISE_STANDARD_THRESHOLD:
        return "standard"
    return "chaos"


def assign_bot(
    personality_config: Optional[Dict[str, Any]],
    *,
    mode: str = "career",
) -> BotAssignment:
    """Return the sticky bot+LLM assignment for a personality.

    ``personality_config`` is the personality's config dict (as returned
    by :meth:`PersonalityRepository.load_personality_by_id`). When
    ``None`` or malformed, falls back to the tiered default.

    Args:
        personality_config: the personality's ``config_json`` dict.
        mode: ``"career"`` (default) always returns tiered unless the
            personality carries a ``bot_profile`` override. ``"sandbox"``
            re-enables the poise-based engine mix for the future
            let-it-happen mode.

    Override format inside ``config_json``::

        "bot_profile": {
            "bot_type": "chaos",                # one of chaos/standard/sharp
            "provider": "openai",               # optional, bucket default otherwise
            "model": "gpt-5-nano"               # optional, bucket default otherwise
        }
    """
    if not isinstance(personality_config, dict):
        return BotAssignment(DEFAULT_BOT_TYPE, dict(DEFAULT_LLM_CONFIG))

    # 1. Explicit per-personality override always wins (the sandbox lever).
    override = personality_config.get("bot_profile")
    if isinstance(override, dict):
        bot_type = override.get("bot_type")
        if bot_type in _VALID_BOT_TYPES:
            bucket_default = BUCKET_DEFAULTS[bot_type]
            llm = {
                "provider": override.get("provider", bucket_default["provider"]),
                "model": override.get("model", bucket_default["model"]),
            }
            return BotAssignment(bot_type, llm)

    # 2. Sandbox mode re-enables the engine mix from poise.
    if mode == "sandbox":
        anchors = personality_config.get("anchors")
        if isinstance(anchors, dict):
            poise = anchors.get("poise")
            if isinstance(poise, int | float):
                bot_type = _bucket_from_poise(float(poise))
                return BotAssignment(bot_type, dict(BUCKET_DEFAULTS[bot_type]))

    # 3. Career default: tiered.
    return BotAssignment(DEFAULT_BOT_TYPE, dict(DEFAULT_LLM_CONFIG))
