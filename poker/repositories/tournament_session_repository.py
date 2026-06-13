"""Persistence for multi-table tournament (MTT) meta-state.

One row per tournament in the `tournaments` table (schema v123): the serialized
`TournamentSession` (the source of truth for field/seating/standings), the
human's live `game_id` (NULL until they sit), `status` ('active'|'complete'),
and `resolver_kind` ('fake'|'engine', rebuilt on rehydrate — resolvers aren't
serialized).

This is the durable backing for the in-memory `tournament_registry`: the
registry stays the hot path, the repo makes a tournament survive navigation /
TTL eviction / server restart. The live per-table hand state lives in the
`games` row (saved by the game repo); these two are persisted together at the
hand boundary so a crash between them can't desync stacks. See
`docs/plans/TOURNAMENT_PERSISTENCE_HANDOFF.md`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Optional

from .base_repository import BaseRepository


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat()


def _winner_and_field_size(session_json: Optional[str]) -> tuple[Optional[str], Optional[int]]:
    """`(winner_pid, field_size)` extracted from a serialized session, mirroring
    `TournamentField.winner()` / `.field_size`: the sole remaining stack is the
    champion; the field size is the entry count. `winner_pid` is None until the
    field collapses (so this is safe to call on every save — it only yields a
    winner once the tournament is actually decided). Best-effort: any shape
    mismatch returns `(None, None)`."""
    if not session_json:
        return None, None
    try:
        field = (json.loads(session_json) or {}).get('field') or {}
        entries = field.get('entries') or {}
        stacks = field.get('stacks') or {}
        winner = next(iter(stacks)) if len(stacks) == 1 else None
        return winner, (len(entries) or None)
    except (TypeError, ValueError, KeyError):
        return None, None


# Recency window for the double-presence exclusion: a tournament whose row hasn't
# been touched within this many hours no longer excludes its field from cash
# seating, so an abandoned/wedged-active tournament can't ghost-seat its personas
# forever. Generous — an actively-played tournament re-stamps `updated_at` every
# hand boundary, so only a genuinely-idle one ages out.
EXCLUSION_MAX_AGE_HOURS = 6


class TournamentSessionRepository(BaseRepository):
    """CRUD for the `tournaments` table. BaseRepository's `_get_connection`
    auto-commits on success and rolls back on error."""

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        keys = row.keys()
        return {
            'tournament_id': row['tournament_id'],
            'owner_id': row['owner_id'],
            'game_id': row['game_id'],
            'status': row['status'],
            'resolver_kind': row['resolver_kind'],
            'created_at': row['created_at'],
            'updated_at': row['updated_at'],
            'session_json': row['session_json'],
            # Economy columns (v132). Defensive defaults so a row read from a
            # pre-v132 schema (or a partial fixture) still has the keys.
            'buy_in': row['buy_in'] if 'buy_in' in keys else 0,
            'rake': row['rake'] if 'rake' in keys else 0,
            'bank_overlay': row['bank_overlay'] if 'bank_overlay' in keys else 0,
            'prize_pool': row['prize_pool'] if 'prize_pool' in keys else 0,
            'payout_status': row['payout_status'] if 'payout_status' in keys else 'skipped',
            # Denormalized champion + field size (Champions Roll). Stamped at the
            # save chokepoint once the field collapses; NULL on pre-stamp rows.
            'winner_pid': row['winner_pid'] if 'winner_pid' in keys else None,
            'field_size': row['field_size'] if 'field_size' in keys else None,
        }

    def save(
        self,
        *,
        tournament_id: str,
        owner_id: str,
        status: str,
        resolver_kind: str,
        session_json: str,
        created_at: str,
        game_id: Optional[str] = None,
    ) -> None:
        """Insert or update a tournament row. `created_at` is set on first
        insert and preserved on update; `updated_at` is stamped every save."""
        now = _utcnow_iso()
        # Stamp the champion + field size off the session being saved. `winner_pid`
        # stays None until the field collapses, so this is correct on every save;
        # COALESCE in the upsert keeps a stamped winner from being nulled by a
        # later save (none of the completion paths re-open a decided field, but the
        # guard makes the chokepoint robust regardless of save order).
        winner_pid, field_size = _winner_and_field_size(session_json)
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO tournaments
                    (tournament_id, owner_id, game_id, status, resolver_kind,
                     created_at, updated_at, session_json, winner_pid, field_size)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tournament_id) DO UPDATE SET
                    owner_id=excluded.owner_id,
                    game_id=excluded.game_id,
                    status=excluded.status,
                    resolver_kind=excluded.resolver_kind,
                    updated_at=excluded.updated_at,
                    session_json=excluded.session_json,
                    winner_pid=COALESCE(excluded.winner_pid, tournaments.winner_pid),
                    field_size=COALESCE(excluded.field_size, tournaments.field_size)
                """,
                (
                    tournament_id,
                    owner_id,
                    game_id,
                    status,
                    resolver_kind,
                    created_at,
                    now,
                    session_json,
                    winner_pid,
                    field_size,
                ),
            )

    def load(self, tournament_id: str) -> Optional[dict]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM tournaments WHERE tournament_id = ?",
                (tournament_id,),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def find_active_for_owner(self, owner_id: str) -> Optional[dict]:
        """The owner's most-recently-updated active *multi-table* tournament, if
        any. Excludes `resolver_kind='single'` envelope rows — those wrap an
        ordinary single-table game (still tracker-driven) and must never be
        rehydrated as an MTT session or shadow a real MTT in the lobby.

        Also excludes DECOUPLED (exhibition) tournaments: they are exempt from the
        one-active-per-owner guard, so they must never shadow the cash-circuit Main
        Event invite or surface as the resumable active event. The flag lives in
        session_json (no column), so the most-recent few rows are scanned and the
        first non-decoupled one is returned."""
        import json

        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tournaments
                WHERE owner_id = ? AND status = 'active'
                  AND resolver_kind != 'single'
                ORDER BY updated_at DESC
                """,
                (owner_id,),
            ).fetchall()
        for row in rows:
            try:
                decoupled = bool((json.loads(row['session_json']) or {}).get('decoupled', False))
            except (TypeError, ValueError):
                decoupled = False
            if not decoupled:
                return self._row_to_dict(row)
        return None

    def active_participant_pids(
        self, owner_id: str, *, active_since_iso: Optional[str] = None
    ) -> set:
        """Every entrant id across the owner's currently-ACTIVE, *recently-touched*
        tournaments.

        Derived from the serialized field (single source of truth — no separate
        participant table to drift). Used by the cash seat-filler to keep a persona
        who is in a tournament OUT of cash seats (the same exclusion vice/side-hustle
        get) — closing the double-presence / ghost-seat gap.

        Returns ONLY real persona ids: synthetic (`P01`) and human (`human:<id>`)
        seat ids are filtered out. They were never cash candidates (inert for the
        filler), but the whereabouts view also reads this set and rendered the raw
        `human:<owner>` id as an orphaned, `unknown_personality`-flagged row
        (T3-80 F2). Filtering here keeps every caller's ids clean.

        **Recency bound (ghost-seat guard):** only tournaments updated within
        `EXCLUSION_MAX_AGE_HOURS` count. An abandoned human tournament (never
        completed) or an autonomous one wedged at `max_rounds` would otherwise stay
        `status='active'` forever and exclude its whole field from cash seating for
        good. `updated_at` is bumped on every persist (hand boundary / advance), so
        an actively-played tournament keeps refreshing and stays excluded; only a
        genuinely-idle one ages out and releases its field. `active_since_iso`
        overrides the cutoff (for tests / a different policy)."""
        import json
        import re
        from datetime import datetime, timedelta

        # Real persona ids only — drop the human seat (`human:<owner>`) and the
        # legacy synthetic `/register` seats (`P01`..`PNN`). See docstring (F2).
        def _is_real_persona(pid: str) -> bool:
            return bool(pid) and not pid.startswith('human:') and not re.match(r'^P\d+$', pid)

        if active_since_iso is None:
            active_since_iso = (
                datetime.utcnow() - timedelta(hours=EXCLUSION_MAX_AGE_HOURS)
            ).isoformat()

        pids: set = set()
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT session_json FROM tournaments "
                "WHERE owner_id = ? AND status = 'active' AND updated_at >= ?",
                (owner_id, active_since_iso),
            ).fetchall()
        for row in rows:
            try:
                blob = json.loads(row['session_json']) or {}
            except (TypeError, ValueError):
                continue
            # A DECOUPLED (exhibition) tournament is isolated from the persistent
            # world: its real-persona field is for flavor only, so those personas
            # must stay AVAILABLE to cash games and real Main Event drafts (they
            # are not "in a tournament" in the double-presence sense). Skip the row.
            if blob.get('decoupled', False):
                continue
            entries = blob.get('field', {}).get('entries', {})
            pids.update(pid for pid in entries.keys() if _is_real_persona(pid))
        return pids

    def find_by_game_id(self, game_id: str) -> Optional[dict]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM tournaments WHERE game_id = ?",
                (game_id,),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def list_circuit_history_for_owner(self, owner_id: str, *, limit: int = 20) -> list[dict]:
        """Completed circuit Main Events for the owner — the Champions Roll.

        A tournament is a *circuit* event iff an invite linked it (every Main
        Event begins as an invite to the owner, whatever its disposition), so the
        JOIN both scopes to circuit events (excluding ad-hoc `/tournament` MTTs,
        which have no invite) and carries the player's disposition: an
        `accepted` invite means they played it, `declined`/`expired` means the
        field ran without them. Newest first. The winner's display NAME is
        resolved by the caller (repo has no personality access)."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT t.tournament_id, t.winner_pid, t.field_size, t.buy_in,
                       t.prize_pool, t.updated_at, MIN(i.status) AS invite_status
                FROM tournaments t
                JOIN tournament_invites i ON i.tournament_id = t.tournament_id
                WHERE i.owner_id = ? AND t.status = 'complete'
                -- One row per tournament: the invite→tournament link isn't unique
                -- at the schema level, so a stray second invite for the same
                -- tournament would otherwise fan out the JOIN and let LIMIT drop a
                -- real event. MIN(status) collapses it and prefers 'accepted'
                -- ('accepted' < 'declined' < 'expired') — if any invite for this
                -- tournament was accepted, the player played it. Normal play has
                -- exactly one invite, so this is a defensive no-op there.
                GROUP BY t.tournament_id
                ORDER BY t.updated_at DESC
                LIMIT ?
                """,
                (owner_id, int(limit)),
            ).fetchall()
        return [
            {
                'tournament_id': r['tournament_id'],
                'winner_pid': r['winner_pid'],
                'field_size': r['field_size'],
                'buy_in': r['buy_in'],
                'prize_pool': r['prize_pool'],
                'completed_at': r['updated_at'],
                # accepted → the player sat in it; declined/expired → it ran without them.
                'played': r['invite_status'] == 'accepted',
            }
            for r in rows
        ]

    def set_status(self, tournament_id: str, status: str) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE tournaments SET status = ?, updated_at = ? WHERE tournament_id = ?",
                (status, _utcnow_iso(), tournament_id),
            )

    def set_game_id(self, tournament_id: str, game_id: Optional[str]) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE tournaments SET game_id = ?, updated_at = ? WHERE tournament_id = ?",
                (game_id, _utcnow_iso(), tournament_id),
            )

    def set_economy(
        self,
        tournament_id: str,
        *,
        buy_in: int,
        rake: int,
        bank_overlay: int,
        prize_pool: int,
        payout_status: str,
    ) -> None:
        """Stamp the real-chip economy fields set at registration (v132).

        Kept separate from `save()` — which runs at every hand boundary — so a
        routine session persist can never wipe the economy data. Called once at
        register, after the buy-in/overlay/rake ledger writes."""
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE tournaments
                   SET buy_in = ?, rake = ?, bank_overlay = ?, prize_pool = ?,
                       payout_status = ?, updated_at = ?
                 WHERE tournament_id = ?
                """,
                (
                    int(buy_in),
                    int(rake),
                    int(bank_overlay),
                    int(prize_pool),
                    payout_status,
                    _utcnow_iso(),
                    tournament_id,
                ),
            )

    def list_stuck_payouts(self, *, older_than_iso: Optional[str] = None) -> list:
        """Tournaments wedged at `payout_status='in_progress'` — a crash mid-
        distribute left partial credits with no terminal transition. Drives the
        payout-reconcile watchdog. `older_than_iso` (compared against
        `updated_at`) is a grace window so a payout in-flight on another thread
        isn't reconciled out from under it. Oldest first."""
        sql = "SELECT * FROM tournaments WHERE payout_status = 'in_progress'"
        params: list = []
        if older_than_iso is not None:
            sql += " AND updated_at < ?"
            params.append(older_than_iso)
        sql += " ORDER BY updated_at ASC"
        with self._get_connection() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def claim_payout(self, tournament_id: str) -> bool:
        """Atomically claim the payout (compare-and-swap `pending` → `in_progress`).

        Returns True iff THIS call won the transition. Replaces the non-atomic
        read-`payout_status`→check→`set_payout_status('in_progress')` so a missed
        sandbox lock (or a future cross-worker path) can't let two callers both
        pass the guard and double-distribute the escrow (the cash double-settle
        lesson). The distributor proceeds only on True."""
        with self._get_connection() as conn:
            return (
                conn.execute(
                    "UPDATE tournaments SET payout_status = 'in_progress', updated_at = ? "
                    "WHERE tournament_id = ? AND payout_status = 'pending'",
                    (_utcnow_iso(), tournament_id),
                ).rowcount
                == 1
            )

    def set_payout_status(self, tournament_id: str, status: str) -> None:
        """Advance the payout idempotency guard (skipped|pending|in_progress|
        complete). Written `in_progress` before any bankroll write and `complete`
        after — a crash leaves `in_progress` for a reconcile pass, never a silent
        double-pay (the cash double-settle lesson)."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE tournaments SET payout_status = ?, updated_at = ? WHERE tournament_id = ?",
                (status, _utcnow_iso(), tournament_id),
            )

    def delete(self, tournament_id: str) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM tournaments WHERE tournament_id = ?",
                (tournament_id,),
            )
