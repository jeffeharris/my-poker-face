"""Async-friends seat mechanics.

Pure helpers for turning an AI-filled seat into a human one when a friend joins
an async game. Kept free of Flask/DB so the seat math is unit-testable in
isolation; the route layer owns controller cleanup, persistence, and membership
bookkeeping.

The PoC model: an async game is created as the owner (one human seat) plus AI
fill. A friend who joins claims the first still-AI seat, which becomes their
``HumanSeat``. "AI fill" is simply the seats nobody has claimed yet — there is
no separate reserved-empty-seat concept in the engine.
"""

from __future__ import annotations

from typing import Optional, Tuple

from poker.table.seat import HumanSeat


def find_open_seat(game_state) -> Optional[int]:
    """Index of the first AI (claimable) seat, or None if the table is all-human."""
    for idx, player in enumerate(game_state.players):
        if not getattr(player, 'is_human', False):
            return idx
    return None


def claim_open_seat(game_state, user_id: str, display_name: str) -> Tuple[object, int, str]:
    """Convert the first open AI seat into a human seat for ``user_id``.

    Returns ``(new_game_state, seat_index, previous_ai_name)`` so the caller can
    retire the AI controller that occupied the seat. The new human inherits the
    seat's stack and position; only its identity changes (``is_human``, display
    ``name``, typed ``seat_id``; ``personality_id`` cleared).

    Raises ``ValueError`` if there is no open seat to claim.
    """
    idx = find_open_seat(game_state)
    if idx is None:
        raise ValueError("No open seat available to claim")
    previous_ai_name = game_state.players[idx].name
    new_state = game_state.update_player(
        idx,
        is_human=True,
        name=display_name,
        personality_id=None,
        seat_id=HumanSeat(user_id),
    )
    return new_state, idx, previous_ai_name
