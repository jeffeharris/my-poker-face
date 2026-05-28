"""Disposition-aware 'opponent read' for quick-chat suggestions.

When the human picks a specific AI to needle, the suggestion LLM does
better with a one-line tell about how that character takes a verbal jab.
This derives the tell from the target's social disposition (the same
`PlayerPsychology._classify_social_disposition()` that drives the actual
emotional reaction), so the human's tools and the AI's reactions are
reading from the same model.

Lives in `handlers/` (not the route file) so it can be unit-tested
without booting Flask's blueprint + limiter machinery — same convention
as `chat_relationship.py` / `message_handler.py`.
"""

from __future__ import annotations

# Keyed by the disposition string from
# PlayerPsychology._classify_social_disposition().
DISPOSITION_CHAT_READS = {
    'stung': "{target} is proud and takes things personally — a barb lands hard and they show it.",
    'energized': "{target} loves to banter and fires right back — give them something sharp worth a volley.",
    'stoic': "{target} is hard to rattle — blunt jabs roll off, so go for a clever or unexpected angle.",
}


def target_social_read(game_data: dict, target_player: str) -> str:
    """Return a one-line opponent read for the target's social disposition,
    or '' when the target isn't a seated AI we can classify."""
    if not target_player:
        return ''
    controller = (game_data.get('ai_controllers') or {}).get(target_player)
    psychology = getattr(controller, 'psychology', None)
    if psychology is None:
        return ''
    try:
        disposition = psychology._classify_social_disposition()
    except Exception:
        return ''
    template = DISPOSITION_CHAT_READS.get(disposition)
    return template.format(target=target_player) if template else ''
