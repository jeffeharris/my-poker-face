"""In-memory registry of active multi-table tournaments.

Mirrors `flask_app/services/game_state_service.py`: a process-local dict keyed by
`tournament_id`, with per-tournament locks. v1 keeps one active tournament per
owner (like cash mode's one active session). No persistence yet — tournaments
live for the process lifetime; persistence lands once the standings UX clarifies
what data is worth keeping.

A record is a plain dict:
    {
        'session': TournamentSession,
        'owner_id': str,
        'created_at': str (iso),
        'resolver': HandResolver,   # also used to drive the human table pre-bridge
        'resolver_kind': str,       # 'fake' | 'engine'
    }
"""

from __future__ import annotations

import secrets
import threading
from typing import Optional

_tournaments: dict[str, dict] = {}
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def new_tournament_id() -> str:
    return "tourney_" + secrets.token_urlsafe(12)


def get(tournament_id: str) -> Optional[dict]:
    return _tournaments.get(tournament_id)


def put(tournament_id: str, record: dict) -> None:
    _tournaments[tournament_id] = record


def delete(tournament_id: str) -> Optional[dict]:
    with _guard:
        _locks.pop(tournament_id, None)
    return _tournaments.pop(tournament_id, None)


def get_lock(tournament_id: str) -> threading.Lock:
    with _guard:
        lock = _locks.get(tournament_id)
        if lock is None:
            lock = threading.Lock()
            _locks[tournament_id] = lock
        return lock


def find_active_for_owner(owner_id: str) -> Optional[str]:
    """The owner's first not-yet-complete tournament, if any."""
    for tid, rec in _tournaments.items():
        if rec.get('owner_id') == owner_id and not rec['session'].is_complete():
            return tid
    return None


def list_for_owner(owner_id: str) -> list[str]:
    return [tid for tid, rec in _tournaments.items() if rec.get('owner_id') == owner_id]


def clear() -> None:
    """Test helper: drop all tournaments."""
    with _guard:
        _tournaments.clear()
        _locks.clear()
