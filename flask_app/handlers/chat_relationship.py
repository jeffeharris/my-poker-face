"""Chat-send → relationship-event dispatch.

The Flask chat-send route extracts structured `(tone, intensity,
addressing)` from quick-chat messages and forwards to
`dispatch_chat_relationship_event`. This module maps the tone to a
`RelationshipEvent` via `poker.memory.chat_intent` and fires
`OpponentModelManager.record_event` with the right bilateral targets
and multiplier.

Also hosts `fire_bilateral_axis_event` — the shared one-shot
bilateral-axis dispatcher reused by `reaction_handler.py` so the
guard sequence (memory_manager presence → has_relationship_repo →
id resolution → self-target skip → record_event) lives in exactly
one place.

Lives in `handlers/` (not in the route file) so it can be unit-tested
without booting Flask's blueprint + limiter machinery. Same convention
the existing `message_handler.py` follows.
"""

from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


def fire_bilateral_axis_event(
    game_data: dict,
    actor: str,
    target: str,
    event,
    multiplier: float,
    narrative: str,
) -> None:
    """Fire one bilateral relationship-axis update, swallowing
    failures silently.

    The single legal entry point for any chat-style or reaction
    signal that needs to move a (reactor, target) pair's axes —
    chat dispatch and emoji-reaction dispatch both route through
    here so the guard sequence (memory_manager presence,
    has_relationship_repo, id resolution, self-target skip) can
    only ever drift in one place.

    Documented silent no-ops (the axis update is a side-effect of
    a primary action like a message send; a failure here must not
    block that action):
      - `memory_manager` absent from `game_data` (older flows /
        replay paths).
      - `relationship_repo` not wired on the manager (in-memory
        unit tests).
      - actor or target name doesn't resolve to a stable
        personality id.
      - actor == target (misrouted self-event).

    `event` is left untyped so this module doesn't pull in the
    `RelationshipEvent` enum just to forward it through to
    `record_event`. Callers always pass a `RelationshipEvent`
    value; the type contract is enforced at the call site.
    """
    try:
        memory_manager = game_data.get('memory_manager')
        if memory_manager is None:
            return
        opponent_manager = memory_manager.get_opponent_model_manager()
        if opponent_manager is None or not opponent_manager.has_relationship_repo:
            return

        actor_id = opponent_manager.resolve_player_id(actor)
        target_id = opponent_manager.resolve_player_id(target)
        if not actor_id or not target_id or actor_id == target_id:
            return

        # `hand_count` is best-effort: missing means `record_event`
        # skips the MemorableHand sidecar, axes still update.
        hand_id = getattr(memory_manager, 'hand_count', None) or None

        opponent_manager.record_event(
            actor_id=actor_id,
            target_id=target_id,
            event=event,
            context_multiplier=multiplier,
            narrative=narrative,
            hand_id=hand_id,
        )
    except Exception:
        logger.exception("[axis] bilateral dispatch failed")


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

    from poker.memory.chat_intent import map_tone
    mapping = map_tone(tone, intensity)
    if mapping is None:
        return

    for target_name in addressing:
        fire_bilateral_axis_event(
            game_data,
            actor=sender,
            target=target_name,
            event=mapping.event,
            multiplier=mapping.multiplier,
            narrative=f"{sender} → {target_name}: {tone}",
        )
