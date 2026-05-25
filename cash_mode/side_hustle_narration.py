"""LLM-backed narration for AI side-hustle events.

The mirror of `vice_narration.py`. Returns `(narration, duration_bucket)`
from a synchronous FAST-tier LLM call: the character invents a
personality-appropriate way they go off to *earn* money back (Napoleon
flips a small business, Bezos spins up a logistics side gig, Hemingway
ghost-writes). The duration bucket is part of the response because it has
to be known before the `ai_side_hustle_state` row can be written (the
`ends_at` timer).

Fail-soft: any failure (network, parse, unknown duration value) returns
the templated fallback. The hustle still fires; only the character-
specific flavor is lost.

Unlike vice, the hustle narration takes no psych snapshot — it's flavored
by persona identity (how would *this* character scrape money together),
not emotional state.

Spec: `docs/plans/CASH_MODE_SIDE_HUSTLE.md`.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple

from cash_mode.ai_side_hustle import (
    DEFAULT_DURATION_BUCKET,
    DURATION_RANGES,
    _templated_narrate_fn,
)

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """\
You write flavor for a fictional poker AI character who has run low on chips and is leaving the cash-mode lobby for a while to earn money back on the side — their version of taking a side gig.

Respond with JSON only, in the form:
{"narration": "one sentence", "duration": "short" | "medium" | "long"}

Narration rules:
  - ONE sentence describing how they go earn money, in character.
  - START WITH THE CHARACTER'S NAME — third-person past tense. Example: "Napoleon took a consulting gig restructuring a struggling vineyard." NOT: "Took a consulting gig." Without the name leading, the ticker reads as an unattributed quote.
  - In character (use their style, attitude, and personality anchors as cues). The gig should fit who they are — grandiose characters build empires, scrappy ones hustle small.
  - Specific and slightly cheeky. It's a comedown from poker, so a little indignity is welcome.
  - No quotation marks. No preamble. No explanation. No leading dash, ellipsis, or honorific — just the name.

Duration buckets (the character picks based on the scale of their hustle):
  - "short"  — a quick gig: a one-off job, a day's work, a quick sale.
  - "medium" — a real stint: a week of contract work, a market run.
  - "long"   — a whole venture: founding a side business, a long tour, a big commission.
"""


def narrate_side_hustle(
    personality_id: str,
    amount: int,
    *,
    personality_repo=None,
    game_id: Optional[str] = None,
    owner_id: Optional[str] = None,
) -> Tuple[str, str]:
    """Synchronous LLM call returning `(narration, duration_bucket)`.

    `personality_repo` is optional — when present we pull the personality
    config to include style / attitude / verbal tics in the prompt. When
    absent we send just the personality_id and let the model produce a
    generic line.

    Fail-soft: any error falls back to the templated narrator
    (`_templated_narrate_fn`) and the medium bucket. The hustle economic /
    state path keeps working.
    """
    try:
        return _narrate_inner(
            personality_id=personality_id,
            amount=amount,
            personality_repo=personality_repo,
            game_id=game_id,
            owner_id=owner_id,
        )
    except Exception as exc:
        logger.warning(
            "[HUSTLE_NARRATION] narrate_side_hustle failed pid=%r: %s; "
            "using template", personality_id, exc,
        )
        return _templated_narrate_fn(personality_id, amount)


def _narrate_inner(
    *,
    personality_id: str,
    amount: int,
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
        call_type=CallType.SIDE_HUSTLE_NARRATION,
        game_id=game_id,
        owner_id=owner_id,
        player_name=personality_id,
        prompt_template='side_hustle_narration',
    )

    return _parse_response(response.content, personality_id, amount)


def _build_user_prompt(
    *,
    personality_id: str,
    amount: int,
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

    parts.append(f"Aiming to earn back: ${amount:,}")
    parts.append("")
    parts.append(
        "Pick a duration that matches both the character AND the scale of "
        "the hustle. A quick sale is short; founding a side venture is long."
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
) -> Tuple[str, str]:
    """Validate the JSON shape; fall back on any deviation.

    Defensive — even valid JSON could have a missing, mis-cased, or
    out-of-set duration field. Any of those triggers the templated
    narrator.
    """
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        logger.warning(
            "[HUSTLE_NARRATION] non-JSON response pid=%r: %r",
            personality_id, content[:200],
        )
        return _templated_narrate_fn(personality_id, amount)

    if not isinstance(data, dict):
        return _templated_narrate_fn(personality_id, amount)

    narration = data.get("narration")
    duration = data.get("duration")

    if not isinstance(narration, str) or not narration.strip():
        logger.warning(
            "[HUSTLE_NARRATION] missing/empty narration pid=%r", personality_id,
        )
        return _templated_narrate_fn(personality_id, amount)

    if isinstance(duration, str):
        duration = duration.lower().strip()
    if duration not in DURATION_RANGES:
        logger.info(
            "[HUSTLE_NARRATION] unknown duration %r pid=%r; defaulting to %s",
            duration, personality_id, DEFAULT_DURATION_BUCKET,
        )
        duration = DEFAULT_DURATION_BUCKET

    narration = narration.strip().strip('"').strip("'").strip()
    return narration, duration
