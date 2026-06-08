"""In-memory registry of active multi-table tournaments, backed by the DB.

Mirrors `flask_app/services/game_state_service.py`: a process-local dict keyed by
`tournament_id`, with per-tournament locks. v1 keeps one active tournament per
owner (like cash mode's one active session).

The in-memory dict is the hot path; `TournamentSessionRepository` is the durable
backing. Reads (`get`, `find_active_for_owner`) fall back to the repo on a memory
miss and rehydrate into memory, so a tournament survives navigation, TTL
eviction, and server restart (mirroring how cash sessions cold-load). Writes are
explicit save points: `persist` / `persist_session`, called from the routes and
the hand-boundary hook. See `docs/plans/TOURNAMENT_PERSISTENCE_HANDOFF.md`.

A record is a plain dict:
    {
        'session': TournamentSession,
        'owner_id': str,
        'created_at': str (iso),
        'resolver': HandResolver,   # also drives the human table pre-bridge
        'resolver_kind': str,       # 'fake' | 'engine'
        'game_id': str | None,      # human's live table (None until they sit)
    }
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_tournaments: dict[str, dict] = {}
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def new_tournament_id() -> str:
    return "tourney_" + secrets.token_urlsafe(12)


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


# ── durable backing ──────────────────────────────────────────────────────────


def _repo():
    """The session repository, or None if persistence isn't wired (registry
    unit tests run memory-only)."""
    try:
        from flask_app.extensions import tournament_session_repo

        return tournament_session_repo
    except Exception:  # noqa: BLE001 — degrade to memory-only
        return None


def _rebuild_resolver(kind: str, entries: dict[str, str]):
    if kind == 'engine':
        from tournament.engine_resolver import EngineHandResolver

        return EngineHandResolver(entries)
    from tournament.director import FakeHandResolver

    return FakeHandResolver()


def _record_is_decoupled(rec: dict) -> bool:
    """True if an in-memory registry record is a decoupled (exhibition)
    tournament. Reads the propagated `decoupled` key, falling back to the
    session's flag for records put before propagation (belt-and-suspenders)."""
    if rec.get('decoupled'):
        return True
    session = rec.get('session')
    return bool(getattr(session, 'decoupled', False))


def _rehydrate(row: dict) -> dict:
    """Rebuild an in-memory record from a stored row (resolver rebuilt from
    resolver_kind; conservation asserted by `from_dict`)."""
    from tournament.session import TournamentSession

    d = json.loads(row['session_json'])
    entries = d['field']['entries']
    resolver = _rebuild_resolver(row['resolver_kind'], entries)
    session = TournamentSession.from_dict(d, resolver)
    return {
        'session': session,
        'owner_id': row['owner_id'],
        'created_at': row['created_at'],
        'resolver': resolver,
        'resolver_kind': row['resolver_kind'],
        'game_id': row['game_id'],
        # Propagate the decoupled flag from the stored session blob into the
        # in-memory record so the active-guard exemption + cold-load see it
        # without re-parsing the JSON (#7).
        'decoupled': bool(d.get('decoupled', False)),
    }


# ── reads (fall back to the repo on a memory miss) ───────────────────────────


def get(tournament_id: str) -> Optional[dict]:
    rec = _tournaments.get(tournament_id)
    if rec is not None:
        return rec
    repo = _repo()
    if repo is None:
        return None
    row = repo.load(tournament_id)
    if row is None:
        return None
    try:
        rec = _rehydrate(row)
    except Exception:  # noqa: BLE001 — a corrupt row shouldn't 500 the lobby
        logger.exception("failed to rehydrate tournament %s", tournament_id)
        return None
    _tournaments[tournament_id] = rec
    return rec


def find_active_for_owner(owner_id: str) -> Optional[str]:
    """The owner's first not-yet-complete tournament, checking memory then the
    repo (rehydrating into memory on a hit)."""
    for tid, rec in _tournaments.items():
        if rec.get('resolver_kind') == SINGLE_KIND:
            continue  # single-table envelopes are not resumable MTT events
        if _record_is_decoupled(rec):
            continue  # exhibition events are exempt from the one-active guard
        if rec.get('owner_id') == owner_id and not rec['session'].is_complete():
            return tid
    repo = _repo()
    if repo is None:
        return None
    row = repo.find_active_for_owner(owner_id)
    if row is None:
        return None
    tid = row['tournament_id']
    if tid not in _tournaments:
        try:
            _tournaments[tid] = _rehydrate(row)
        except Exception:  # noqa: BLE001
            logger.exception("failed to rehydrate tournament %s", tid)
            return None
    return tid


def list_for_owner(owner_id: str) -> list[str]:
    return [tid for tid, rec in _tournaments.items() if rec.get('owner_id') == owner_id]


# ── writes ───────────────────────────────────────────────────────────────────


def put(tournament_id: str, record: dict) -> None:
    _tournaments[tournament_id] = record


def persist_session(
    *,
    tournament_id: Optional[str],
    owner_id: Optional[str],
    session,
    resolver_kind: Optional[str],
    game_id: Optional[str] = None,
    created_at: Optional[str] = None,
) -> None:
    """Save a session to the durable store. Status is derived from the session
    (complete once the field collapses to a winner). Best-effort: the in-memory
    state stays authoritative for the live process if the write fails."""
    repo = _repo()
    if repo is None or not tournament_id or session is None:
        return
    status = 'complete' if session.is_complete() else 'active'
    try:
        repo.save(
            tournament_id=tournament_id,
            owner_id=owner_id or '',
            status=status,
            resolver_kind=resolver_kind or 'fake',
            session_json=json.dumps(session.to_dict()),
            created_at=created_at or _now_iso(),
            game_id=game_id,
        )
    except Exception:  # noqa: BLE001 — durability layer, never break the game
        logger.exception("failed to persist tournament %s", tournament_id)


def persist(tournament_id: str) -> None:
    """Persist the current in-memory state of a tournament."""
    rec = _tournaments.get(tournament_id)
    if rec is None:
        return
    persist_session(
        tournament_id=tournament_id,
        owner_id=rec.get('owner_id'),
        session=rec.get('session'),
        resolver_kind=rec.get('resolver_kind'),
        game_id=rec.get('game_id'),
        created_at=rec.get('created_at'),
    )


# ── single-table "envelope" rows ─────────────────────────────────────────────
# Every ordinary (non-cash) game is conceptually a one-table tournament. We
# record that with a lightweight `tournaments` row (`resolver_kind='single'`)
# so all games share one durable identity in the same table the multi-table
# field uses. These envelopes are an INDEX/identity record only — they are NOT
# rehydrated into a `TournamentSession` and are NOT attached to game_data, so
# the single-table game keeps running on its `TournamentTracker` (the legacy
# elimination/`TournamentResult` completion path). Collapsing the tracker into a
# real 1-table session — and unifying completion — is deferred to step 3
# (`docs/plans/TOURNAMENT_UNIFICATION_STEP3.md`).


SINGLE_KIND = 'single'


def single_envelope_id(game_id: str) -> str:
    """Deterministic tournament_id for a game's single-table envelope, so
    create-on-new-game and lazy-wrap-on-load upsert the same row (idempotent)."""
    return f"single-{game_id}"


# Game-id prefixes that own a dedicated tournament/session record and must
# never be wrapped as a single-table envelope: cash sessions and multi-table
# tournament tables (whose field lives in a real `TournamentSession` row).
_NON_SINGLE_PREFIXES = ('cash-', 'tourney-')


def persist_single_envelope(*, game_id: str, owner_id: Optional[str]) -> None:
    """Upsert the single-table tournament envelope for an ordinary game.
    Best-effort and idempotent; memory-only when persistence isn't wired.
    No-op for cash games and multi-table tournament tables (by id prefix) — an
    orphaned `tourney-` table with a missing session must not be mislabeled
    `single`."""
    repo = _repo()
    if repo is None or not game_id or game_id.startswith(_NON_SINGLE_PREFIXES):
        return
    try:
        repo.save(
            tournament_id=single_envelope_id(game_id),
            owner_id=owner_id or '',
            status='active',
            resolver_kind=SINGLE_KIND,
            session_json=json.dumps({'single': True, 'game_id': game_id}),
            created_at=_now_iso(),
            game_id=game_id,
        )
    except Exception:  # noqa: BLE001 — durability layer, never break game create/load
        logger.exception("failed to persist single-table envelope for %s", game_id)


def persist_single_session(*, game_id: str, owner_id: Optional[str], session) -> None:
    """Upsert the durable row for a single-table game's TournamentSession
    (resolver_kind='single', a REAL serialized session). Same `single-<game_id>`
    id as the lightweight envelope, so this just enriches it. Best-effort."""
    repo = _repo()
    if repo is None or not game_id or session is None or game_id.startswith(_NON_SINGLE_PREFIXES):
        return
    try:
        status = 'complete' if session.is_complete() else 'active'
        repo.save(
            tournament_id=single_envelope_id(game_id),
            owner_id=owner_id or '',
            status=status,
            resolver_kind=SINGLE_KIND,
            session_json=json.dumps(session.to_dict()),
            created_at=_now_iso(),
            game_id=game_id,
        )
    except Exception:  # noqa: BLE001 — durability layer, never break play
        logger.exception("failed to persist single-table session for %s", game_id)


def delete_single_envelope(game_id: str) -> None:
    """Remove a game's single-table envelope (called when the game is deleted).
    Only touches the `single-<game_id>` row, never a multi-table session."""
    repo = _repo()
    if repo is None or not game_id:
        return
    try:
        repo.delete(single_envelope_id(game_id))
    except Exception:  # noqa: BLE001
        logger.exception("failed to delete single-table envelope for %s", game_id)


def delete(tournament_id: str) -> Optional[dict]:
    with _guard:
        _locks.pop(tournament_id, None)
    repo = _repo()
    if repo is not None:
        try:
            repo.delete(tournament_id)
        except Exception:  # noqa: BLE001
            logger.exception("failed to delete tournament %s", tournament_id)
    return _tournaments.pop(tournament_id, None)


# ── locking / test helpers ───────────────────────────────────────────────────


def get_lock(tournament_id: str) -> threading.Lock:
    with _guard:
        lock = _locks.get(tournament_id)
        if lock is None:
            lock = threading.Lock()
            _locks[tournament_id] = lock
        return lock


def clear() -> None:
    """Test helper: drop all in-memory tournaments (does not touch the DB)."""
    with _guard:
        _tournaments.clear()
        _locks.clear()
