"""LLM-backed narration for AI vice spending events.

Returns the narration string from a synchronous FAST-tier LLM call.
The duration the character is gone is **chosen system-side** (see
`ai_vice_spending.pick_duration_bucket`) and passed *into* this
narrator as `duration_bucket`, so the flavor line fits the chosen
length (Buddha's long retreat reads differently from Hemingway's
quick bar visit) without the LLM deciding the economics. Narration is
flavor-only now.

Fail-soft: any failure (network, parse, empty) returns the templated
fallback. The vice still fires; only the character-specific flavor is
lost.

Spec: `docs/plans/CASH_MODE_AI_VICE_SPENDING.md`,
`docs/plans/ASYNC_TICKER_NARRATION.md`.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from cash_mode.ai_vice_spending import (
    DEFAULT_DURATION_BUCKET,
    _templated_narrate_fn,
)

logger = logging.getLogger(__name__)


_DURATION_HINTS = {
    'short': "a quick indulgence: a bar visit, a haircut, a meal.",
    'medium': "an afternoon: a shopping trip, a massage, a concert.",
    'long': "a real getaway: a private trip, a retreat, a commission.",
}


_SYSTEM_PROMPT = """\
You write flavor for a fictional poker AI character who is about to disappear from the cash-mode lobby for a while to indulge in a personal vice.

Respond with JSON only, in the form:
{"narration": "one sentence"}

Narration rules:
  - ONE sentence describing what they're doing.
  - START WITH THE CHARACTER'S NAME — third-person past tense. Example: "Napoleon commissioned an oversized bronze bust of himself." NOT: "Commissioned an oversized bronze bust." NOT: "Pre-ordered a flight." Without the name leading, the ticker reads as an unattributed quote.
  - In character (use their style, attitude, and personality anchors as cues).
  - FIT THE GIVEN DURATION — a short escape is a quick indulgence; a long one is a real getaway. The duration is decided for you; write a line that matches it.
  - Specific and slightly cheeky.
  - No quotation marks. No preamble. No explanation. No leading dash, ellipsis, or honorific — just the name.
"""


def narrate_vice(
    personality_id: str,
    amount: int,
    psychology_snapshot: Optional[Dict[str, float]],
    duration_bucket: str = DEFAULT_DURATION_BUCKET,
    *,
    personality_repo=None,
    game_id: Optional[str] = None,
    owner_id: Optional[str] = None,
) -> str:
    """Synchronous LLM call returning the narration string (flavor only).

    `psychology_snapshot` is the current `{confidence, composure,
    energy}` dict from the vice-fire path; the LLM uses it as cue
    context. `duration_bucket` ('short'/'medium'/'long') is chosen
    system-side and passed in so the line fits the chosen length —
    the LLM no longer decides the duration. `personality_repo` is
    optional — when present we pull the personality config to include
    style / attitude / verbal tics in the prompt. When absent we send
    just the personality_id and let the model produce a generic line.

    Fail-soft: any error falls back to the templated narrator
    (`_templated_narrate_fn`). The vice economic / state path keeps
    working.
    """
    try:
        return _narrate_inner(
            personality_id=personality_id,
            amount=amount,
            psychology_snapshot=psychology_snapshot,
            duration_bucket=duration_bucket,
            personality_repo=personality_repo,
            game_id=game_id,
            owner_id=owner_id,
        )
    except Exception as exc:
        logger.warning(
            "[VICE_NARRATION] narrate_vice failed pid=%r: %s; using template",
            personality_id,
            exc,
        )
        return _templated_narrate_fn(
            personality_id,
            amount,
            psychology_snapshot,
            duration_bucket,
        )


def _narrate_inner(
    *,
    personality_id: str,
    amount: int,
    psychology_snapshot: Optional[Dict[str, float]],
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
        psychology_snapshot=psychology_snapshot,
        duration_bucket=duration_bucket,
        personality_repo=personality_repo,
    )

    client = LLMClient(
        provider=settings.get_fast_provider(),
        model=settings.get_fast_model(),
        # minimal reasoning: this is throwaway flavor text. Critically, on a
        # toggleable FAST model (e.g. xAI grok-4-fast) the LLMClient default
        # "low" resolves to the REASONING variant (~10-20s, up to ~98s in prod),
        # while "minimal" selects the non-reasoning variant (~1-2s). Without this
        # the ticker narration silently ran the slow model.
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
        call_type=CallType.VICE_NARRATION,
        game_id=game_id,
        owner_id=owner_id,
        player_name=personality_id,
        prompt_template='vice_narration',
    )

    return _parse_response(
        response.content, personality_id, amount, psychology_snapshot, duration_bucket
    )


def _build_user_prompt(
    *,
    personality_id: str,
    amount: int,
    psychology_snapshot: Optional[Dict[str, float]],
    duration_bucket: str,
    personality_repo,
) -> str:
    """Compose the per-call user message.

    Pulls character context from the personality config when
    available. Keeps the prompt short — the FAST tier should answer
    in ~300ms.
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

    parts.append(f"Just spent: ${amount:,}")
    if psychology_snapshot:
        parts.append(
            "Current psych state (0=low, 1=high): "
            f"confidence={psychology_snapshot.get('confidence', 0.5):.2f}, "
            f"composure={psychology_snapshot.get('composure', 0.5):.2f}, "
            f"energy={psychology_snapshot.get('energy', 0.5):.2f}"
        )
    hint = _DURATION_HINTS.get(duration_bucket, _DURATION_HINTS[DEFAULT_DURATION_BUCKET])
    parts.append(f"Duration: {duration_bucket} — {hint}")
    parts.append("")
    parts.append(
        "Write a one-sentence line that fits this character AND the given "
        "duration. A short escape is a quick indulgence; a long one is a "
        "real getaway."
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
    psychology_snapshot: Optional[Dict[str, float]],
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
            "[VICE_NARRATION] non-JSON response pid=%r: %r",
            personality_id,
            content[:200],
        )
        return _templated_narrate_fn(personality_id, amount, psychology_snapshot, duration_bucket)

    if not isinstance(data, dict):
        return _templated_narrate_fn(personality_id, amount, psychology_snapshot, duration_bucket)

    narration = data.get("narration")

    if not isinstance(narration, str) or not narration.strip():
        logger.warning(
            "[VICE_NARRATION] missing/empty narration pid=%r",
            personality_id,
        )
        return _templated_narrate_fn(personality_id, amount, psychology_snapshot, duration_bucket)

    # Trim any stray quotation marks the model may have included
    # despite the system prompt asking for none.
    return narration.strip().strip('"').strip("'").strip()
