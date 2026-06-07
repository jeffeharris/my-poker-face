"""Monotonic version stamp for authoritative game-state frames.

A process-global, strictly-increasing counter stamped onto every full
game-state representation the server sends — the socket
``update_game_state`` push (``handlers/game_handler.py``) and the REST
``/api/game-state`` cold-load (``routes/game_routes.py``).

The client store (``gameStore.applyGameState``) drops any *socket* frame
whose version is older than the last one it applied, so a stale frame
from a leaked/orphaned socket or a late-draining sequencer beat can't
regress the UI to an earlier hand — the "two hands flickering" class of
bug (see ``docs/captains-log/bug-fix-tournament/2026-06-07-two-hand-flicker.md``).

A *global* counter (rather than per-game) guarantees every successive
serialization gets a strictly greater number with no per-game locking,
which is all the client's monotonic drop needs: versions are only ever
compared within one game, in receive order. ``itertools.count`` advances
atomically under CPython's GIL, so concurrent emit/REST threads each get
a unique value without a lock.

The counter resets on process restart. That's safe because the REST
cold-load is treated by the client as an authoritative *reset* of its
baseline (not a monotonic compare), so a post-restart epoch can never
wedge a client into dropping every frame.
"""

from __future__ import annotations

import itertools

_counter = itertools.count(1)


def next_state_version() -> int:
    """Return the next strictly-increasing global state version."""
    return next(_counter)
