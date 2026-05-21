"""Apply an emoji reaction to an AI chat message.

The Flask route extracts `(message_id, sentiment)` from the request,
this handler does the work:

  1. Locate the target message in the in-memory ring buffer.
  2. Toggle / swap / set the reactor's entry on the message's
     `reactions` dict (one reaction per reactor; clicking the same
     sentiment removes it; clicking the opposite sentiment swaps).
  3. When a reaction is added or swapped, roll the weighted pool to
     pick the displayed emoji + the `RelationshipEvent` and
     multiplier, then fire `OpponentModelManager.record_event` so the
     axis update flows through the same path typed quick-chat uses.
  4. Return the updated reactions dict so the route can echo it back
     to the caller and broadcast it over Socket.IO.

Lives in `handlers/` (not the route file) so unit tests can target it
without booting the Flask blueprint + auth machinery. Same convention
as `chat_relationship.py`.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from poker.memory.reaction_intent import is_valid_sentiment, roll_reaction

from .chat_relationship import fire_bilateral_axis_event

logger = logging.getLogger(__name__)


class ReactionError(Exception):
    """Raised when the request can't be applied to the target message.

    Carries an HTTP-friendly `status_code` so the route layer can
    translate without re-classifying the failure here. The handler
    raises rather than returns sentinels because the route needs
    different responses (404 vs. 400) for each failure mode.
    """

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _find_message(game_data: dict, message_id: str) -> Optional[dict]:
    """Locate a message by UUID in `game_data['messages']`.

    Returns the live dict (mutable — the caller mutates `reactions`
    directly to keep the change inside the canonical message store).
    """
    for msg in game_data.get('messages', []):
        if msg.get('id') == message_id:
            return msg
    return None


def apply_reaction(
    game_data: dict,
    message_id: str,
    reactor: str,
    sentiment: Optional[str],
) -> Dict[str, Any]:
    """Apply a reactor's reaction to a specific message and return
    the updated reactions dict for broadcast.

    Toggle / swap semantics:
      - Reactor has no prior reaction, sentiment given: roll an emoji
        from the sentiment's pool, store it, fire the axis shift.
      - Reactor has prior reaction with same sentiment: remove their
        entry (no axis shift fires — there is no inverse event in the
        relationship layer, so retraction is a no-op on axes).
      - Reactor has prior reaction with different sentiment: swap —
        replace their entry with a fresh roll from the new pool, fire
        the new sentiment's axis shift. The prior shift is NOT undone;
        the cumulative axis state reflects the sequence of clicks,
        which is the truthful representation of an indecisive reactor.

    Raises:
        ReactionError: when the target message is missing (404), is
            not an AI message (400 — reactions are AI-only by spec),
            or `sentiment` is non-null and invalid (400).
    """
    target_msg = _find_message(game_data, message_id)
    if target_msg is None:
        raise ReactionError("Message not found", status_code=404)
    if target_msg.get('message_type') != 'ai':
        # Reactions are AI-only by design: reacting to system / table
        # / human messages has no relationship target to apply the
        # axis shift against, and surfacing reactions on those types
        # would confuse the UI affordance.
        raise ReactionError("Reactions only allowed on AI messages", status_code=400)

    if sentiment is not None and not is_valid_sentiment(sentiment):
        raise ReactionError("Invalid sentiment", status_code=400)

    reactions: Dict[str, Dict[str, str]] = target_msg.setdefault('reactions', {})
    prior = reactions.get(reactor)
    prior_sentiment = prior.get('sentiment') if prior else None

    # `sentiment=None` is the explicit-remove signal; the toggle path
    # below also handles "click same sentiment twice" without needing
    # the caller to compute the new state.
    if sentiment is None or sentiment == prior_sentiment:
        reactions.pop(reactor, None)
        return reactions

    roll = roll_reaction(sentiment)
    if roll is None:
        # is_valid_sentiment passed but the pool lookup didn't — would
        # only happen if the sentiment vocabulary and pool registry
        # drift. Surface as 400 rather than silently no-op so the
        # drift is noisy in logs.
        raise ReactionError("No pool registered for sentiment", status_code=400)

    reactions[reactor] = {
        "emoji": roll.emoji,
        "sentiment": sentiment,
    }

    ai_sender = target_msg.get('sender', '')
    if ai_sender:
        fire_bilateral_axis_event(
            game_data,
            actor=reactor,
            target=ai_sender,
            event=roll.event,
            multiplier=roll.multiplier,
            narrative=f"{reactor} reacted {roll.emoji} to {ai_sender}",
        )

    return reactions


__all__ = ["ReactionError", "apply_reaction"]
