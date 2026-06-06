"""LLM-backed narration for AI side-hustle events.

The mirror of `vice_narration.py`. Returns the narration string from a
synchronous FAST-tier LLM call: the character invents a personality-
appropriate way they go off to *earn* money back (Napoleon flips a small
business, Bezos spins up a logistics side gig, Hemingway ghost-writes).
The duration the character is gone is **chosen system-side** (see
`ai_side_hustle.pick_hustle_duration_bucket`) and passed *into* this
narrator as `duration_bucket`, so the flavor line fits the chosen scale
(a quick gig vs founding a venture) without the LLM deciding the
economics. Narration is flavor-only now.

Fail-soft: any failure (network, parse, empty) returns the templated
fallback. The hustle still fires; only the character-specific flavor is
lost.

Unlike vice, the hustle narration takes no psych snapshot — it's flavored
by persona identity (how would *this* character scrape money together),
not emotional state.

Spec: `docs/plans/CASH_MODE_SIDE_HUSTLE.md`,
`docs/plans/ASYNC_TICKER_NARRATION.md`.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from cash_mode.ai_side_hustle import (
    DEFAULT_DURATION_BUCKET,
    _templated_narrate_fn,
)

logger = logging.getLogger(__name__)


_DURATION_HINTS = {
    'short': "a quick gig: a one-off job, a day's work, a quick sale.",
    'medium': "a real stint: a week of contract work, a market run.",
    'long': "a whole venture: founding a side business, a long tour, a big commission.",
}


_SYSTEM_PROMPT = """\
You write flavor for a fictional poker AI character who has run low on chips and is leaving the cash-mode lobby for a while to earn money back on the side — their version of taking a side gig.

Respond with JSON only, in the form:
{"narration": "one sentence"}

Narration rules:
  - ONE sentence describing how they go earn money, in character.
  - START WITH THE CHARACTER'S NAME — third-person past tense. Example: "Napoleon took a consulting gig restructuring a struggling vineyard." NOT: "Took a consulting gig." Without the name leading, the ticker reads as an unattributed quote.
  - In character (use their style, attitude, and personality anchors as cues). The gig should fit who they are — grandiose characters build empires, scrappy ones hustle small.
  - FIT THE GIVEN DURATION — a short hustle is a quick gig; a long one is a whole venture. The duration is decided for you; write a line that matches it.
  - Specific and slightly cheeky. It's a comedown from poker, so a little indignity is welcome.
  - No quotation marks. No preamble. No explanation. No leading dash, ellipsis, or honorific — just the name.
"""


def narrate_side_hustle(
    personality_id: str,
    amount: int,
    duration_bucket: str = DEFAULT_DURATION_BUCKET,
    *,
    personality_repo=None,
    game_id: Optional[str] = None,
    owner_id: Optional[str] = None,
) -> str:
    """Synchronous LLM call returning the narration string (flavor only).

    `duration_bucket` ('short'/'medium'/'long') is chosen system-side and
    passed in so the line fits the chosen scale — the LLM no longer
    decides the duration. `personality_repo` is optional — when present
    we pull the personality config to include style / attitude / verbal
    tics in the prompt. When absent we send just the personality_id and
    let the model produce a generic line.

    Fail-soft: any error falls back to the templated narrator
    (`_templated_narrate_fn`). The hustle economic / state path keeps
    working.
    """
    try:
        return _narrate_inner(
            personality_id=personality_id,
            amount=amount,
            duration_bucket=duration_bucket,
            personality_repo=personality_repo,
            game_id=game_id,
            owner_id=owner_id,
        )
    except Exception as exc:
        logger.warning(
            "[HUSTLE_NARRATION] narrate_side_hustle failed pid=%r: %s; " "using template",
            personality_id,
            exc,
        )
        return _templated_narrate_fn(personality_id, amount, duration_bucket)


def _narrate_inner(
    *,
    personality_id: str,
    amount: int,
    duration_bucket: str,
    personality_repo,
    game_id: Optional[str],
    owner_id: Optional[str],
) -> str:
    """The real LLM call. Raises on failure; the outer wrapper catches."""
    from core.llm import LLMClient, settings
    from core.llm.config import TICKER_LLM_TIMEOUT_SECONDS
    from core.llm.tracking import CallType

    user_prompt = _build_user_prompt(
        personality_id=personality_id,
        amount=amount,
        duration_bucket=duration_bucket,
        personality_repo=personality_repo,
    )

    client = LLMClient(
        provider=settings.get_fast_provider(),
        model=settings.get_fast_model(),
        # minimal reasoning: throwaway flavor. On a toggleable FAST model (xAI
        # grok-4-fast) the LLMClient default "low" resolves to the slow REASONING
        # variant; "minimal" selects the non-reasoning variant. See vice_narration.
        reasoning_effort="minimal",
        # PRH-21: ticker narration runs synchronously in the single shared
        # ticker greenlet (advances every sandbox) — a stall pauses the lobby
        # for ALL users. Pure flavor, so bound it tighter than an in-game call.
        default_timeout=TICKER_LLM_TIMEOUT_SECONDS,
    )
    response = client.complete(
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        json_format=True,
        call_type=CallType.SIDE_HUSTLE_NARRATION,
        game_id=game_id,
        owner_id=owner_id,
        player_name=personality_id,
        prompt_template='side_hustle_narration',
    )

    return _parse_response(response.content, personality_id, amount, duration_bucket)


def _build_user_prompt(
    *,
    personality_id: str,
    amount: int,
    duration_bucket: str,
    personality_repo,
) -> str:
    """Compose the per-call user message.

    Pulls character context from the personality config when available.
    Keeps the prompt short — the FAST tier should answer in ~300ms.
    """
    parts = [f"Character: {personality_id}"]
    config = _load_config_safe(personality_repo, personality_id)
    if config is not None:
        name = config.get("name") or personality_id
        parts = [f"Name: {name}"]
        for field in ("play_style", "default_attitude", "default_confidence"):
            val = config.get(field)
            if val:
                parts.append(f"{field.replace('_', ' ').title()}: {val}")
        anchors = config.get("anchors") or {}
        anchor_lines = []
        for axis in (
            "baseline_aggression",
            "baseline_looseness",
            "ego",
            "poise",
            "expressiveness",
            "risk_identity",
            "baseline_energy",
        ):
            if axis in anchors:
                anchor_lines.append(f"  {axis} = {anchors[axis]}")
        if anchor_lines:
            parts.append("Anchors:")
            parts.extend(anchor_lines)
        tics = config.get("verbal_tics")
        if isinstance(tics, list) and tics:
            parts.append("Verbal tics: " + " / ".join(tics[:3]))

    parts.append(f"Aiming to earn back: ${amount:,}")
    hint = _DURATION_HINTS.get(duration_bucket, _DURATION_HINTS[DEFAULT_DURATION_BUCKET])
    parts.append(f"Duration: {duration_bucket} — {hint}")
    parts.append("")
    parts.append(
        "Write a one-sentence line that fits this character AND the given "
        "duration. A quick sale is short; founding a side venture is long."
    )
    return "\n".join(parts)


def _load_config_safe(personality_repo, personality_id: str) -> Optional[Dict[str, Any]]:
    if personality_repo is None:
        return None
    try:
        cfg = personality_repo.load_personality_by_id(personality_id)
    except Exception:
        return None
    if not isinstance(cfg, dict):
        return None
    return cfg


def _parse_response(
    content: str,
    personality_id: str,
    amount: int,
    duration_bucket: str,
) -> str:
    """Validate the JSON shape; fall back on any deviation.

    Flavor-only now — the duration is chosen system-side, so we only
    extract and sanitize the narration string. Any deviation (non-JSON,
    missing/empty narration) triggers the templated narrator.
    """
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        logger.warning(
            "[HUSTLE_NARRATION] non-JSON response pid=%r: %r",
            personality_id,
            content[:200],
        )
        return _templated_narrate_fn(personality_id, amount, duration_bucket)

    if not isinstance(data, dict):
        return _templated_narrate_fn(personality_id, amount, duration_bucket)

    narration = data.get("narration")

    if not isinstance(narration, str) or not narration.strip():
        logger.warning(
            "[HUSTLE_NARRATION] missing/empty narration pid=%r",
            personality_id,
        )
        return _templated_narrate_fn(personality_id, amount, duration_bucket)

    return narration.strip().strip('"').strip("'").strip()
