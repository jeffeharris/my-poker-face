"""LLM-generated exit narration for AI cash-mode leaves.

When an AI leaves a table (busted, booking a win, bored, tired), the
lobby ticker can carry a short in-character beat instead of just the
default "X left the Y table" line. This module owns:

  - `generate_leave_comment(ctx)` — synchronous LLM call that returns
    a short rendered comment (joined `dramatic_sequence`). Tagged with
    `CallType.COMMENTARY` and `prompt_template='leave_narrative'` so
    the prompt viewer surfaces it alongside decision captures.

  - `queue_leave_comment(...)` / `get_leave_comment(...)` —
    fire-and-forget worker pool. The lobby refresh emits the event
    immediately with `comment=None`; the worker fills it in within a
    second or two. Subsequent ticker reads pick it up.

Out of the hot path: lobby refresh never blocks on the LLM. If the
worker is slow or fails, the event still shows up with the default
message — the comment is decoration.
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

from core.llm import LLMClient, CallType
from core.llm.config import FAST_MODEL, FAST_PROVIDER
from poker.prompt_manager import DRAMATIC_SEQUENCE_GUIDANCE

logger = logging.getLogger(__name__)


# Per-signal hints injected into the prompt so the LLM knows WHY the
# AI is walking. Two keys per signal: `hint` is the situational fact,
# `tone` shapes the dramatic intensity. Keep these flat strings — they
# get string-formatted into the prompt without any nesting.
_SIGNAL_HINTS: Dict[str, Dict[str, str]] = {
    "bust": {
        "hint": "Your stack is busted. You came in with chips, now you're walking out with empty hands.",
        "tone": "climactic — savor the loss, one or two beats of theater.",
    },
    "stake_up": {
        "hint": "You won big at this stake and you're moving up to play higher. Book the win.",
        "tone": "triumphant — confident, looking ahead to the bigger table.",
    },
    "short": {
        "hint": "You're down to a short stack and you don't have the heart to rebuy. Calling it a session.",
        "tone": "weary — tired and bruised, no theatrics.",
    },
    "stake_up_blocked": {
        "hint": "You're up at this stake but can't afford the next tier. Booking the win and stepping away.",
        "tone": "satisfied — quietly pleased with the win.",
    },
    "detached": {
        "hint": "Cards have been cold and you've been folding for a while. Time to find a livelier table.",
        "tone": "restless — bored, ready for a change of scenery.",
    },
    "tenure": {
        "hint": "Long session. You're tired, energy is spent, calling it a night.",
        "tone": "tired — low-key, ready for bed.",
    },
    "": {
        "hint": "You're stepping away from the table.",
        "tone": "neutral — one short beat.",
    },
}


@dataclass(frozen=True)
class LeaveNarrativeContext:
    """All inputs the LLM needs to write an in-character exit beat.

    Built at the lobby emission site where personality data, chips,
    and decision are already on hand.
    """

    personality_name: str
    play_style: str
    default_attitude: str
    verbal_tics: Tuple[str, ...] = field(default_factory=tuple)
    physical_tics: Tuple[str, ...] = field(default_factory=tuple)
    decision: str = ""          # 'forced_leave' | 'stake_up_queued' | 'take_break' | 'bored_move'
    dominant_signal: str = ""   # 'bust' | 'stake_up' | 'short' | 'stake_up_blocked' | 'detached' | 'tenure'
    stake_label: str = ""
    chips_at_exit: int = 0
    min_buy_in: int = 0


def _build_messages(ctx: LeaveNarrativeContext) -> list:
    """Build the chat messages for the leave-narrative call.

    System prompt embeds DRAMATIC_SEQUENCE_GUIDANCE so the model
    produces beats in the same shape as in-hand decisions. User
    message carries the situational hint, tone, and personality color.
    """
    signal = ctx.dominant_signal or ""
    sig_info = _SIGNAL_HINTS.get(signal, _SIGNAL_HINTS[""])
    verbal = ", ".join(ctx.verbal_tics[:3]) if ctx.verbal_tics else "(none provided)"
    physical = ", ".join(ctx.physical_tics[:3]) if ctx.physical_tics else "(none provided)"

    system_prompt = (
        f"You are {ctx.personality_name}, a poker player with this play style: "
        f"{ctx.play_style}. Your default attitude is {ctx.default_attitude}. "
        "You are leaving a cash-game table. Respond with a short in-character "
        "dramatic_sequence describing your exit — what you say or do as you "
        "stand up and walk away. Stay in character.\n\n"
        f"{DRAMATIC_SEQUENCE_GUIDANCE}\n\n"
        'Return ONLY JSON: {"dramatic_sequence": ["...", "..."]} with 1-3 beats.'
    )

    user_msg = (
        f"Situation: {sig_info['hint']}\n"
        f"Tone: {sig_info['tone']}\n"
        f"Table: {ctx.stake_label}. You're walking away with ${ctx.chips_at_exit:,} "
        f"(table min buy-in ${ctx.min_buy_in:,}).\n"
        f"Your verbal tics: {verbal}\n"
        f"Your physical tics: {physical}\n\n"
        'Produce your dramatic_sequence now.'
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]


def _render_sequence(payload: Any) -> Optional[str]:
    """Pull dramatic_sequence list from LLM JSON and flatten to a string.

    Each beat already carries its own asterisks for actions and plain
    text for speech — joining with a space preserves the formatting
    so the frontend can render them inline.
    """
    if not isinstance(payload, dict):
        return None
    seq = payload.get("dramatic_sequence")
    if not isinstance(seq, list):
        return None
    beats = [str(b).strip() for b in seq if str(b).strip()]
    if not beats:
        return None
    return " ".join(beats[:4])


def generate_leave_comment(
    ctx: LeaveNarrativeContext,
    *,
    llm_client: Optional[LLMClient] = None,
    owner_id: Optional[str] = None,
) -> Optional[str]:
    """Synchronous LLM call. Returns the rendered comment or None.

    Tagged with `CallType.COMMENTARY` and `prompt_template='leave_narrative'`
    so the prompt viewer surfaces these alongside in-hand captures.
    `owner_id` plumbs the lobby caller's user id so multi-user installs
    attribute the call correctly.
    """
    client = llm_client or LLMClient(provider=FAST_PROVIDER, model=FAST_MODEL)
    messages = _build_messages(ctx)
    try:
        # 800 tokens leaves room for GPT-5-nano's "minimal" reasoning
        # budget (~300 tokens) plus the actual JSON output. 300 was
        # too tight — reasoning consumed the whole budget and the
        # output came back empty.
        response = client.complete(
            messages=messages,
            json_format=True,
            max_tokens=800,
            call_type=CallType.COMMENTARY,
            prompt_template="leave_narrative",
            player_name=ctx.personality_name,
            owner_id=owner_id,
        )
    except Exception as exc:
        logger.debug("leave_narrative: LLM call failed for %s: %s",
                     ctx.personality_name, exc)
        return None

    content = (response.content or "").strip()
    if not content:
        return None

    import json as _json
    try:
        payload = _json.loads(content)
    except _json.JSONDecodeError:
        logger.debug("leave_narrative: non-JSON response for %s: %r",
                     ctx.personality_name, content[:120])
        return None

    return _render_sequence(payload)


# --- Worker pool + result dict -------------------------------------------------

# Two workers is enough — leaves are bursty (5-10 per tick at most)
# but each call is ~1s, so two workers clear a burst in under 5s.
# Daemon threads so a Flask shutdown doesn't hang.
_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()

# Results keyed by `(table_id, personality_id, created_at_iso)`. The
# created_at ISO string matches the LobbyEvent.created_at field so the
# lookup at serialization time is trivial. Bounded by `_MAX_RESULTS`
# entries — older comments drop silently. 200 is enough for the 50-
# entry ring buffer with churn.
_results_lock = threading.Lock()
_results: Dict[Tuple[str, str, str], str] = {}
_MAX_RESULTS = 200


def _get_executor() -> ThreadPoolExecutor:
    """Lazy-create the worker pool on first use."""
    global _executor
    if _executor is None:
        with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(
                    max_workers=2,
                    thread_name_prefix="leave-narrative",
                )
    return _executor


def _store_result(key: Tuple[str, str, str], comment: str) -> None:
    with _results_lock:
        _results[key] = comment
        # Trim oldest entries if we've blown past the cap. Dict
        # iteration order is insertion order in 3.7+, so popping the
        # first key drops the oldest.
        while len(_results) > _MAX_RESULTS:
            _results.pop(next(iter(_results)))


def _worker(key: Tuple[str, str, str], ctx: LeaveNarrativeContext,
            owner_id: Optional[str]) -> None:
    try:
        comment = generate_leave_comment(ctx, owner_id=owner_id)
    except Exception as exc:
        logger.debug("leave_narrative worker crashed: %s", exc)
        return
    if comment:
        _store_result(key, comment)


def is_disabled() -> bool:
    """True when the env disables leave-narrative LLM calls.

    Set `CASH_LEAVE_NARRATIVE_DISABLED=1` (default in pytest via
    conftest) so integration tests of the lobby don't fire real LLM
    calls during the suite. The lobby still emits leave events; they
    just go out without comments.
    """
    return os.environ.get("CASH_LEAVE_NARRATIVE_DISABLED", "").lower() in (
        "1", "true", "yes",
    )


def queue_leave_comment(
    table_id: str,
    personality_id: str,
    created_at: str,
    ctx: LeaveNarrativeContext,
    *,
    owner_id: Optional[str] = None,
) -> None:
    """Fire-and-forget submit. Caller never blocks.

    The matching `get_leave_comment(table_id, personality_id, created_at)`
    returns None until the worker finishes, then the rendered string.
    No-op when `is_disabled()` is true.
    """
    if is_disabled():
        return
    key = (table_id, personality_id, created_at)
    try:
        _get_executor().submit(_worker, key, ctx, owner_id)
    except RuntimeError as exc:
        # Executor was shut down (test teardown / process exit). Skip
        # silently — leaves still surface without comments.
        logger.debug("leave_narrative: queue rejected: %s", exc)


def get_leave_comment(
    table_id: str,
    personality_id: str,
    created_at: str,
) -> Optional[str]:
    """Return the rendered comment if the worker has finished, else None."""
    key = (table_id, personality_id, created_at)
    with _results_lock:
        return _results.get(key)


def clear_results() -> None:
    """Drop all stored comments. Test helper."""
    with _results_lock:
        _results.clear()
