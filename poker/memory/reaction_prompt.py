"""Recent-reactions → prompt-context formatter.

Pure read-side helper that turns the per-message `reactions` dicts on
recent AI chat messages into a short block surfacing "who reacted how
to your last comments." Parallel in shape to `relationship_prompt.py`
— it takes the live message list (already in the controller via
`_current_game_messages`), filters for the AI's own outgoing
messages, and emits a compact summary the LLM can integrate.

Why a separate module from `relationship_prompt.py`:
  - That module reads from `OpponentModelManager` (axes + memorable
    hands). This one reads from `game_data['messages']` directly.
    Different data sources, different stability windows (axes are
    durable, message reactions are session-scoped).
  - Reactions surfaced here are about a *specific recent comment*,
    not a long-term relationship label. The framing the prompt needs
    is different.

Returns the empty string when no AI message has reactions yet — a
documented "nothing worth mentioning" signal callers can short-
circuit on.
"""

from __future__ import annotations

from typing import Iterable, List, Optional


# How many of the AI's most recent outgoing messages to scan for
# reactions. Two is enough to surface "they reacted to your last
# couple of chirps" without flooding the prompt; tunable later.
DEFAULT_LOOKBACK = 2


def _is_ai_message_from(msg: dict, ai_name: str) -> bool:
    """A message is the AI's own outgoing chirp if its type is `'ai'`
    AND the sender name matches. The type guard alone isn't enough —
    multiple AIs share the same `'ai'` type.
    """
    return (
        msg.get('message_type') == 'ai'
        and msg.get('sender') == ai_name
    )


def _format_reactor_list(reactions: dict) -> str:
    """Render a `{reactor: {emoji, sentiment}}` dict as
    `"Alice ❤️, Bob 😴"`. Ordering matches dict-insertion order,
    which mirrors the order reactors clicked — recent reactor first
    in a Python ≥3.7 dict, which is what we get from the JSON
    deserialize on the in-memory store.
    """
    parts = []
    for reactor, record in reactions.items():
        emoji = record.get('emoji') if isinstance(record, dict) else None
        if not emoji:
            continue
        parts.append(f"{reactor} {emoji}")
    return ", ".join(parts)


def summarize_recent_reactions(
    messages: Iterable[dict],
    ai_name: str,
    *,
    lookback: int = DEFAULT_LOOKBACK,
) -> str:
    """Return a short prompt block summarizing recent reactions to
    `ai_name`'s own AI chat messages, or `""` when no reactions exist.

    `messages` is the raw `game_data['messages']` list (each message a
    dict with `id, sender, message_type, content, reactions`). We
    iterate from the most-recent end backwards because the canonical
    in-memory store appends new messages — the AI's latest chirp is
    closest to the tail.

    `lookback` caps how many of the AI's outgoing messages we
    consider. Older reactions are skipped both for token economy and
    because reactions on stale messages have lower bearing on the
    current decision.
    """
    if not messages or not ai_name:
        return ""

    # Walk newest-first; collect at most `lookback` AI messages from
    # this speaker that have non-empty reactions. We use the
    # `content` field as the snippet because that's the canonical
    # backend storage (the React-side rename to `message` is a
    # frontend transform).
    collected: List[tuple[str, str]] = []  # (snippet, reactor_list)
    for msg in reversed(list(messages)):
        if len(collected) >= lookback:
            break
        if not _is_ai_message_from(msg, ai_name):
            continue
        reactions = msg.get('reactions') or {}
        if not isinstance(reactions, dict) or not reactions:
            continue
        snippet = (msg.get('content') or '').strip()
        # Truncate so a wordy comment doesn't blow up the block. The
        # prompt only needs enough context for the LLM to know which
        # comment was reacted to.
        if len(snippet) > 60:
            snippet = snippet[:57] + '...'
        reactor_summary = _format_reactor_list(reactions)
        if not reactor_summary:
            continue
        collected.append((snippet, reactor_summary))

    if not collected:
        return ""

    lines = [
        f'- "{snippet}" → {reactor_summary}'
        for snippet, reactor_summary in collected
    ]
    return "RECENT REACTIONS TO YOUR COMMENTS:\n" + "\n".join(lines)
