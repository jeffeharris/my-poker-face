"""LLM-backed narration for AI vice spending events.

Returns `(narration, duration_bucket)` from a synchronous FAST-tier
LLM call. The character picks how long they're gone — Buddha goes
for a long retreat, Hemingway hits the bar for a short visit. The
duration bucket is part of the response because it has to be known
before the `ai_vice_state` row can be written (the `ends_at` timer).

Fail-soft: any failure (network, parse, unknown duration value)
returns the templated fallback. The vice still fires; only the
character-specific flavor is lost.

Spec: `docs/plans/CASH_MODE_AI_VICE_SPENDING.md`.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple

from cash_mode.ai_vice_spending import (
    DEFAULT_DURATION_BUCKET,
    DURATION_RANGES,
    _templated_narrate_fn,
)

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """\
You write flavor for a fictional poker AI character who is about to disappear from the cash-mode lobby for a while to indulge in a personal vice.

Respond with JSON only, in the form:
{"narration": "one sentence", "duration": "short" | "medium" | "long"}

Narration rules:
  - ONE sentence describing what they're doing.
  - START WITH THE CHARACTER'S NAME — third-person past tense. Example: "Napoleon commissioned an oversized bronze bust of himself." NOT: "Commissioned an oversized bronze bust." NOT: "Pre-ordered a flight." Without the name leading, the ticker reads as an unattributed quote.
  - In character (use their style, attitude, and personality anchors as cues).
  - Specific and slightly cheeky.
  - No quotation marks. No preamble. No explanation. No leading dash, ellipsis, or honorific — just the name.

Duration buckets (the character picks based on what they're indulging in):
  - "short"  — a quick indulgence: a bar visit, a haircut, a meal.
  - "medium" — an afternoon: a shopping trip, a massage, a concert.
  - "long"   — a real getaway: a private trip, a retreat, a commission.
"""


def narrate_vice(
    personality_id: str,
    amount: int,
    psychology_snapshot: Optional[Dict[str, float]],
    *,
    personality_repo=None,
    game_id: Optional[str] = None,
    owner_id: Optional[str] = None,
) -> Tuple[str, str]:
    """Synchronous LLM call returning `(narration, duration_bucket)`.

    `psychology_snapshot` is the current `{confidence, composure,
    energy}` dict from the vice-fire path; the LLM uses it as cue
    context. `personality_repo` is optional — when present we pull
    the personality config to include style / attitude / verbal tics
    in the prompt. When absent we send just the personality_id and
    let the model produce a generic line.

    Fail-soft: any error falls back to the templated narrator
    (`_templated_narrate_fn`) and the medium bucket. The vice
    economic / state path keeps working.
    """
    try:
        return _narrate_inner(
            personality_id=personality_id,
            amount=amount,
            psychology_snapshot=psychology_snapshot,
            personality_repo=personality_repo,
            game_id=game_id,
            owner_id=owner_id,
        )
    except Exception as exc:
        logger.warning(
            "[VICE_NARRATION] narrate_vice failed pid=%r: %s; using template",
            personality_id, exc,
        )
        return _templated_narrate_fn(
            personality_id, amount, psychology_snapshot,
        )


def _narrate_inner(
    *,
    personality_id: str,
    amount: int,
    psychology_snapshot: Optional[Dict[str, float]],
    personality_repo,
    game_id: Optional[str],
    owner_id: Optional[str],
) -> Tuple[str, str]:
    """The real LLM call. Raises on failure; the outer wrapper catches."""
    from core.llm import LLMClient, settings
    from core.llm.tracking import CallType

    user_prompt = _build_user_prompt(
        personality_id=personality_id,
        amount=amount,
        psychology_snapshot=psychology_snapshot,
        personality_repo=personality_repo,
    )

    client = LLMClient(
        provider=settings.get_fast_provider(),
        model=settings.get_fast_model(),
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

    return _parse_response(response.content, personality_id, amount, psychology_snapshot)


def _build_user_prompt(
    *,
    personality_id: str,
    amount: int,
    psychology_snapshot: Optional[Dict[str, float]],
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
            "baseline_aggression", "baseline_looseness", "ego",
            "poise", "expressiveness", "risk_identity", "baseline_energy",
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
    parts.append("")
    parts.append(
        "Pick a duration that matches both the character AND what they "
        "indulge in. Buddha's retreats are long; Hemingway's bar nights "
        "are short; Bezos books private flights (long)."
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
) -> Tuple[str, str]:
    """Validate the JSON shape; fall back on any deviation.

    Defensive — even if the model returns valid JSON, the duration
    field could be missing, mis-cased, or outside the allowed set.
    Any of those triggers the templated narrator.
    """
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        logger.warning(
            "[VICE_NARRATION] non-JSON response pid=%r: %r",
            personality_id, content[:200],
        )
        return _templated_narrate_fn(personality_id, amount, psychology_snapshot)

    if not isinstance(data, dict):
        return _templated_narrate_fn(personality_id, amount, psychology_snapshot)

    narration = data.get("narration")
    duration = data.get("duration")

    if not isinstance(narration, str) or not narration.strip():
        logger.warning(
            "[VICE_NARRATION] missing/empty narration pid=%r", personality_id,
        )
        return _templated_narrate_fn(personality_id, amount, psychology_snapshot)

    # Normalize duration; fall back to default on any unknown value.
    if isinstance(duration, str):
        duration = duration.lower().strip()
    if duration not in DURATION_RANGES:
        logger.info(
            "[VICE_NARRATION] unknown duration %r pid=%r; defaulting to %s",
            duration, personality_id, DEFAULT_DURATION_BUCKET,
        )
        duration = DEFAULT_DURATION_BUCKET

    # Trim any stray quotation marks the model may have included
    # despite the system prompt asking for none.
    narration = narration.strip().strip('"').strip("'").strip()

    return narration, duration
