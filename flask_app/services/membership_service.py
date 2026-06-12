"""Membership + turn authorization for async-friends games.

Centralizes the authorization questions that the game routes/socket handlers
ask, so the swap from "owner only" to "any seated member, on their own turn"
lives in ONE place rather than being re-derived at every call site.

Two distinct checks:

  * ``is_member`` — may this user *access* this game at all? True for an admin,
    the game owner (back-compat: every existing single-human game authorizes
    unchanged), or anyone with a non-left ``game_members`` row.
  * ``is_users_turn`` — is it *this* user's turn to act right now? With N humans
    at a table we must verify the current seat belongs to the acting user, not
    merely that "a human" is to act.

``resolve_turn_user`` derives whose turn it is from the live game state (the
current seat's ``HumanSeat.owner_id``) — the single source the progression and
notification layers read to decide who to wait for / notify.
"""

from __future__ import annotations

import logging
from typing import Optional

from flask_app import extensions
from poker.authorization import get_authorization_service

logger = logging.getLogger(__name__)


def _is_admin(user_id: str) -> bool:
    """Whether the user has the admin-tools permission (mirrors game_routes).

    Defensive: a misconfigured/torn-down authorization singleton never blocks a
    legitimate owner/member, since this is consulted only as a last resort.
    """
    try:
        auth_service = get_authorization_service()
        return bool(auth_service and auth_service.has_permission(user_id, 'can_access_admin_tools'))
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("[MEMBERSHIP] admin check failed for %s: %s", user_id, e)
        return False


def is_member(
    game_id: str,
    user_id: Optional[str],
    *,
    owner_id: Optional[str] = None,
    is_admin: Optional[bool] = None,
) -> bool:
    """Whether ``user_id`` may access ``game_id``.

    Checks owner → membership ledger → admin, in that order. The admin lookup is
    LAST and lazy so an owner/seated member short-circuits without touching the
    authorization singleton (which keeps this immune to the global-pollution
    gotcha and avoids a permission query on the hot path).

    Args:
        owner_id: the game's owner if the caller already knows it (saves a DB
            hit). When None and the membership ledger misses, we look it up so a
            legacy single-human game still authorizes its owner.
        is_admin: pass an already-computed admin flag to skip the lookup here.
    """
    if not user_id:
        return False

    if owner_id is not None and user_id == owner_id:
        return True

    repo = extensions.membership_repo
    if repo is not None:
        try:
            if repo.is_member(game_id, user_id):
                return True
        except Exception as e:  # pragma: no cover - defensive, never block on a read
            logger.debug("[MEMBERSHIP] is_member lookup failed for %s/%s: %s", game_id, user_id, e)

    # Back-compat fallback: if we weren't handed an owner_id and the ledger had
    # no row (e.g. a pre-async single-human game), treat the persisted owner as
    # a member so existing games authorize exactly as before.
    if owner_id is None and extensions.game_repo is not None:
        info = extensions.game_repo.get_game_owner_info(game_id)
        if info is not None and info.get('owner_id') == user_id:
            return True

    # Admin override, computed last so owners/members never trigger it.
    if is_admin is None:
        is_admin = _is_admin(user_id)
    return bool(is_admin)


def resolve_turn_user(game_state) -> Optional[str]:
    """The owner_id of the human whose turn it is, or None.

    None means no human is on the clock — an AI seat, a phase transition, or a
    seat with no human identity. Only returns a user when the engine is actually
    awaiting an action at a human seat.
    """
    if not getattr(game_state, 'awaiting_action', False):
        return None
    players = getattr(game_state, 'players', None)
    idx = getattr(game_state, 'current_player_idx', None)
    if players is None or idx is None or not (0 <= idx < len(players)):
        return None
    seat = players[idx]
    if not getattr(seat, 'is_human', False):
        return None
    seat_id = getattr(seat, 'seat_id', None)
    return getattr(seat_id, 'owner_id', None)


def is_users_turn(game_state, user_id: Optional[str]) -> bool:
    """Whether it's ``user_id``'s turn to act in ``game_state`` right now."""
    if not user_id:
        return False
    return resolve_turn_user(game_state) == user_id
