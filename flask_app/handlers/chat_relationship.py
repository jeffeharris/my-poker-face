"""Chat-send → relationship-event dispatch.

The Flask chat-send route extracts structured `(tone, intensity,
addressing)` from quick-chat messages and forwards to
`dispatch_chat_relationship_event`. This module maps the tone to a
`RelationshipEvent` via `poker.memory.chat_intent` and fires
`OpponentModelManager.record_event` with the right bilateral targets
and multiplier.

Lives in `handlers/` (not in the route file) so it can be unit-tested
without booting Flask's blueprint + limiter machinery. Same convention
the existing `message_handler.py` follows.
"""

from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


def dispatch_chat_relationship_event(
    game_data: dict,
    sender: str,
    addressing: Optional[List[str]],
    tone: Optional[str],
    intensity: Optional[str],
) -> None:
    """Map quick-chat tone to a RelationshipEvent and fire it.

    Documented no-op when:
      - tone is missing / not in the recognized vocabulary (free-form
        chat — no structured intent to dispatch).
      - addressing is missing / empty (table-broadcast — no specific
        target; ambiguous attribution is worse than no event).
      - memory_manager isn't on game_data (older flows, replay paths).
      - relationship_repo isn't wired on the manager (in-memory tests).

    Self-addressed messages (actor_id == target_id) are skipped — the
    route is meant to be human-to-AI but a misrouted self-target
    shouldn't write any state.

    Wraps the dispatch in a single try/except so a failure here can't
    block the message delivery the caller has already confirmed.
    Relationship-axis movement is a side-effect; the chat send is the
    primary action.
    """
    if not addressing or not tone:
        return

    try:
        from poker.memory.chat_intent import map_tone

        mapping = map_tone(tone, intensity)
        if mapping is None:
            return

        memory_manager = game_data.get('memory_manager')
        if memory_manager is None:
            return
        opponent_manager = memory_manager.get_opponent_model_manager()
        if opponent_manager is None or not opponent_manager.has_relationship_repo:
            return

        actor_id = opponent_manager.resolve_player_id(sender)
        for target_name in addressing:
            target_id = opponent_manager.resolve_player_id(target_name)
            if not actor_id or not target_id or actor_id == target_id:
                continue
            opponent_manager.record_event(
                actor_id=actor_id,
                target_id=target_id,
                event=mapping.event,
                context_multiplier=mapping.multiplier,
                narrative=f"{sender} → {target_name}: {tone}",
            )
    except Exception:
        logger.exception("[chat] relationship dispatch failed")
