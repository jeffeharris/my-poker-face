"""Turn-state mirroring + "it's your turn" notification policy.

Sits between the progression loop and the notification dispatcher. On reaching a
human's turn we:

  1. mirror the live turn onto the ``games`` row (lobby badge + notify dedupe),
  2. skip if no human is on the clock or the player is currently connected
     (they'll see the live socket update), and
  3. otherwise push exactly one notification for this turn.

Dedupe is per-turn: ``set_turn_state(advance_turn_clock=True)`` clears
``last_notified_turn_at`` whenever the actor changes, and we stamp it after a
send — so a turn is notified at most once even if progression re-enters.
"""

from __future__ import annotations

import logging

from flask_app import extensions
from flask_app.services import membership_service

logger = logging.getLogger(__name__)


def refresh_turn_state(game_id: str, game_state, *, previous_turn_user=None):
    """Mirror the current turn onto the games row; return the resolved turn user.

    Advances the turn clock (and re-arms the notify dedupe) only when the actor
    actually changed, so an incidental refresh doesn't move the deadline.
    """
    turn_user = membership_service.resolve_turn_user(game_state)
    advanced = turn_user is not None and turn_user != previous_turn_user
    try:
        extensions.game_repo.set_turn_state(game_id, turn_user, advance_turn_clock=advanced)
    except Exception as e:  # pragma: no cover - never block play on a write
        logger.debug("[TURN] state refresh failed for %s: %s", game_id, e)
    return turn_user


def notify_turn_if_offline(game_id: str, game_state) -> bool:
    """Refresh turn state and, if the actor is offline, push one turn alert.

    Returns True if a notification was dispatched. Best-effort; never raises.
    """
    try:
        meta = extensions.game_repo.get_async_meta(game_id) or {}
        previous_turn_user = meta.get('current_turn_user_id')
        turn_user = refresh_turn_state(
            game_id, game_state, previous_turn_user=previous_turn_user
        )
        if not turn_user:
            return False

        # Connected players get the live socket update — no push needed.
        from flask_app.services import presence

        if presence.is_active(turn_user):
            return False

        # One push per turn: bail if this turn was already notified.
        fresh = extensions.game_repo.get_async_meta(game_id) or {}
        if fresh.get('last_notified_turn_at'):
            return False

        from flask_app.services.notifications import dispatcher

        dispatcher.notify_turn(game_id, turn_user)
        extensions.game_repo.mark_turn_notified(game_id)
        return True
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("[TURN] notify_turn_if_offline failed for %s: %s", game_id, e)
        return False
