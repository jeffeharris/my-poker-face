"""Repository for the v108 `cash_sessions` persistence surface.

One row per cash session: created at sit-down, finalised at leave-
table. Backs the leave-table summary and (future) session-history
view. See `cash_mode/cash_sessions.py` for the dataclass shape and
`SchemaManager._migrate_v108_add_cash_sessions` for the schema.

Six reads (`load`, `load_active_for_owner`, `list_for_owner`) and
three writes (`create`, `update_total_buy_in`, `finalise`). No bulk
delete — sessions are append-only history.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from cash_mode.cash_sessions import (
    SESSION_STATE_ACTIVE,
    SESSION_STATE_CLOSED,
    CashSession,
)
from poker.repositories.base_repository import BaseRepository

logger = logging.getLogger(__name__)


def _parse_timestamp(value) -> Optional[datetime]:
    """Coerce a SQLite TIMESTAMP value to a datetime, or None.

    Same duck-typed pattern as `stake_repository._parse_timestamp` —
    SQLite returns timestamps as ISO strings under default type
    detection; some legacy rows surface as datetimes.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _row_to_session(row) -> CashSession:
    """Hydrate a DB row into a CashSession dataclass."""
    # `session_state` / `last_load_error` are Tier 3 additions (v119).
    # Guard the column access so a row read against a pre-v119 schema
    # (shouldn't happen post-migration, but cheap insurance) degrades to
    # the dataclass defaults instead of raising.
    keys = set(row.keys())
    session_state = (
        row["session_state"]
        if "session_state" in keys and row["session_state"] is not None
        else SESSION_STATE_ACTIVE
    )
    last_load_error = row["last_load_error"] if "last_load_error" in keys else None
    return CashSession(
        session_id=row["session_id"],
        owner_id=row["owner_id"],
        sandbox_id=row["sandbox_id"],
        stake_label=row["stake_label"],
        is_staked=bool(row["is_staked"]),
        stake_id=row["stake_id"],
        initial_buy_in=int(row["initial_buy_in"]),
        total_buy_in=int(row["total_buy_in"]),
        sponsor_principal=int(row["sponsor_principal"]),
        cash_table_id=row["cash_table_id"],
        cash_seat_index=(
            int(row["cash_seat_index"]) if row["cash_seat_index"] is not None else None
        ),
        started_at=_parse_timestamp(row["started_at"]),
        ended_at=_parse_timestamp(row["ended_at"]),
        final_chips_at_table=(
            int(row["final_chips_at_table"]) if row["final_chips_at_table"] is not None else None
        ),
        sponsor_repaid=int(row["sponsor_repaid"] or 0),
        player_take_home=(
            int(row["player_take_home"]) if row["player_take_home"] is not None else None
        ),
        hands_played=(int(row["hands_played"]) if row["hands_played"] is not None else None),
        hands_won=(int(row["hands_won"]) if row["hands_won"] is not None else None),
        biggest_pot_won=(
            int(row["biggest_pot_won"]) if row["biggest_pot_won"] is not None else None
        ),
        duration_seconds=(
            int(row["duration_seconds"]) if row["duration_seconds"] is not None else None
        ),
        closed_status=row["closed_status"],
        session_state=session_state,
        last_load_error=last_load_error,
    )


class CashSessionRepository(BaseRepository):
    """CRUD for `cash_sessions`."""

    def create(self, session: CashSession) -> None:
        """Insert one cash_sessions row.

        Raises `sqlite3.IntegrityError` on duplicate session_id —
        caller is responsible for not double-inserting (the cash-route
        leave path purges any prior cash session for the owner before
        sit-down lands).
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO cash_sessions
                    (session_id, owner_id, sandbox_id, stake_label,
                     is_staked, stake_id,
                     initial_buy_in, total_buy_in, sponsor_principal,
                     cash_table_id, cash_seat_index,
                     started_at, ended_at,
                     final_chips_at_table, sponsor_repaid, player_take_home,
                     hands_played, hands_won, biggest_pot_won,
                     duration_seconds, closed_status,
                     session_state, last_load_error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.owner_id,
                    session.sandbox_id,
                    session.stake_label,
                    1 if session.is_staked else 0,
                    session.stake_id,
                    int(session.initial_buy_in),
                    int(session.total_buy_in),
                    int(session.sponsor_principal),
                    session.cash_table_id,
                    session.cash_seat_index,
                    session.started_at.isoformat(),
                    session.ended_at.isoformat() if session.ended_at else None,
                    session.final_chips_at_table,
                    int(session.sponsor_repaid),
                    session.player_take_home,
                    session.hands_played,
                    session.hands_won,
                    session.biggest_pot_won,
                    session.duration_seconds,
                    session.closed_status,
                    session.session_state or SESSION_STATE_ACTIVE,
                    session.last_load_error,
                ),
            )

    def load(self, session_id: str) -> Optional[CashSession]:
        """Load one session by id, or None if not found."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM cash_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if not row:
                return None
            return _row_to_session(row)

    def load_active_for_owner(self, owner_id: str) -> Optional[CashSession]:
        """The owner's active (un-ended) session, or None.

        Invariant: one active cash session per owner at a time
        (enforced by `_find_active_cash_game_id` + the leave route's
        purge). If two rows somehow match, returns the most recent.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM cash_sessions
                WHERE owner_id = ? AND ended_at IS NULL
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (owner_id,),
            ).fetchone()
            if not row:
                return None
            return _row_to_session(row)

    def find_blocking_session_id_for_owner(self, owner_id: str) -> Optional[str]:
        """The session_id of the owner's blocking (active/paused/abandoning)
        session, or None. Authoritative source for the sit guard (Codex
        review #4): a direct, unbounded, state-filtered lookup — unlike a
        capped `games` scan it can't miss a real session past row N. A
        `closed`/`broken` row is terminal and never returned. Most-recent
        wins if two somehow match (the one-session invariant should hold).
        """
        from cash_mode.cash_sessions import SESSION_STATES_BLOCKING

        states = sorted(SESSION_STATES_BLOCKING)
        placeholders = ",".join("?" for _ in states)
        with self._get_connection() as conn:
            row = conn.execute(
                f"SELECT session_id FROM cash_sessions "
                f"WHERE owner_id = ? AND session_state IN ({placeholders}) "
                f"ORDER BY started_at DESC LIMIT 1",
                (owner_id, *states),
            ).fetchone()
            return row["session_id"] if row else None

    def list_for_owner(
        self,
        owner_id: str,
        *,
        limit: int = 50,
    ) -> List[CashSession]:
        """Owner's recent sessions, newest first.

        Defaults to the 50 most recent so the (future) Net Worth /
        Session History view has a reasonable working set without
        loading everything.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM cash_sessions
                WHERE owner_id = ?
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (owner_id, int(limit)),
            ).fetchall()
            return [_row_to_session(r) for r in rows]

    def sum_hands_for_owner(self, owner_id: str, *, sandbox_id: Optional[str] = None) -> int:
        """Total cash hands the owner has played, summed across finalised
        sessions (the `hands_played` finalise field). Cheap aggregate read.

        Used as the play-based clock for the Career-M2 vouch trickle — the
        cooldown spaces vouches by hands played. NB: a live, not-yet-finalised
        session's hands aren't counted until leave, so this advances at session
        boundaries (a coarse-but-fine cadence for a slow trickle). Sandbox-scoped
        when `sandbox_id` is given, else cross-sandbox.
        """
        sql = "SELECT COALESCE(SUM(hands_played), 0) FROM cash_sessions WHERE owner_id = ?"
        params: list = [owner_id]
        if sandbox_id is not None:
            sql += " AND sandbox_id = ?"
            params.append(sandbox_id)
        with self._get_connection() as conn:
            row = conn.execute(sql, params).fetchone()
            return int(row[0] or 0)

    def list_completed_for_sandbox(
        self,
        owner_id: str,
        sandbox_id: str,
    ) -> List[CashSession]:
        """All completed sessions for owner in this sandbox, oldest first.

        "Completed" = `ended_at IS NOT NULL`. Used by the prestige
        aggregator to derive renown inputs: highest stake tier reached,
        tenure (Σ hands_played), and the high-stakes-win flag. Unbounded —
        renown is a lifetime ratchet, so capping would under-count tenure
        and could miss an early high-stakes session that fell out of a
        recent-N window. Per-sandbox completed sessions stay modest.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM cash_sessions
                WHERE owner_id = ? AND sandbox_id = ? AND ended_at IS NOT NULL
                ORDER BY started_at ASC
                """,
                (owner_id, sandbox_id),
            ).fetchall()
            return [_row_to_session(r) for r in rows]

    def update_total_buy_in(self, session_id: str, total_buy_in: int) -> bool:
        """Replace `total_buy_in` for an active session.

        Called by top-up and rebuy routes. Returns True if a row
        was updated. The route layer is responsible for computing the
        new total (load → add → save) so the increment is explicit
        in the caller (no race-prone in-DB +=).
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE cash_sessions SET total_buy_in = ? "
                "WHERE session_id = ? AND ended_at IS NULL",
                (int(total_buy_in), session_id),
            )
            return cursor.rowcount > 0

    def finalise(
        self,
        session_id: str,
        *,
        ended_at: datetime,
        final_chips_at_table: int,
        sponsor_repaid: int,
        player_take_home: int,
        hands_played: int,
        hands_won: int,
        biggest_pot_won: int,
        duration_seconds: int,
        closed_status: str,
    ) -> bool:
        """Stamp end-of-session fields and mark the row closed.

        Single UPDATE so the row goes from active → closed atomically.
        Also flips `session_state` to `closed` (Tier 3) so the sit guard
        stops treating it as a blocking session. Returns True if a row
        was updated. Idempotent against a re-leave: the `ended_at IS NULL`
        guard skips already-finalised rows so a retry doesn't overwrite
        the first leave's numbers.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE cash_sessions
                SET ended_at = ?,
                    final_chips_at_table = ?,
                    sponsor_repaid = ?,
                    player_take_home = ?,
                    hands_played = ?,
                    hands_won = ?,
                    biggest_pot_won = ?,
                    duration_seconds = ?,
                    closed_status = ?,
                    session_state = ?
                WHERE session_id = ? AND ended_at IS NULL
                """,
                (
                    ended_at.isoformat(),
                    int(final_chips_at_table),
                    int(sponsor_repaid),
                    int(player_take_home),
                    int(hands_played),
                    int(hands_won),
                    int(biggest_pot_won),
                    int(duration_seconds),
                    closed_status,
                    SESSION_STATE_CLOSED,
                    session_id,
                ),
            )
            return cursor.rowcount > 0

    def set_session_state(self, session_id: str, state: str) -> bool:
        """Set the explicit lifecycle `session_state` (Tier 3).

        Used to mark a session `broken` (cleanup couldn't converge) or
        `abandoning` (teardown in flight) without going through the full
        `finalise` (which also stamps end-of-session numbers). Unlike
        `finalise`, this is NOT gated on `ended_at IS NULL` — marking a
        row broken must work even after a partial finalise. Returns True
        if a row was updated.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE cash_sessions SET session_state = ? WHERE session_id = ?",
                (state, session_id),
            )
            return cursor.rowcount > 0

    def set_last_load_error(self, session_id: str, error: Optional[str]) -> bool:
        """Stash (or clear) the last cold-load failure for a session.

        `error` is a short string (error class + timestamp); pass None to
        clear it after a successful load. Best-effort debugging aid —
        callers swallow failures. Returns True if a row was updated.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE cash_sessions SET last_load_error = ? WHERE session_id = ?",
                (error, session_id),
            )
            return cursor.rowcount > 0

    # --- Lifecycle events (Tier 3, v120) -------------------------------

    def record_event(
        self,
        session_id: str,
        event: str,
        *,
        owner_id: Optional[str] = None,
        sandbox_id: Optional[str] = None,
        detail: Optional[dict] = None,
        now: Optional[datetime] = None,
    ) -> None:
        """Append one lifecycle event to `cash_session_events`.

        Persisted telemetry (started / resumed / left_clean / left_ghost
        / swept / broken). Distinct from `cash_mode/activity.py`'s
        in-memory cosmetic ticker. Best-effort: a write failure is logged,
        never raised — emitting an event must not break a leave or a sweep.
        """
        import json as _json

        if now is None:
            now = datetime.utcnow()
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO cash_session_events
                        (session_id, owner_id, sandbox_id, event, detail_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        owner_id,
                        sandbox_id,
                        event,
                        _json.dumps(detail) if detail is not None else None,
                        now.isoformat(),
                    ),
                )
        except Exception as e:
            logger.warning(
                "[CASH] cash_session_events write failed for %r/%r: %s",
                session_id,
                event,
                e,
            )

    def list_events(
        self,
        *,
        session_id: Optional[str] = None,
        sandbox_id: Optional[str] = None,
        event: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 200,
    ) -> List[dict]:
        """Read lifecycle events, newest first, for ops / the admin widget.

        All filters are optional and AND-combined. Returns plain dicts
        (not a dataclass) since this is a read-only telemetry surface.
        """
        clauses = []
        params: list = []
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if sandbox_id is not None:
            clauses.append("sandbox_id = ?")
            params.append(sandbox_id)
        if event is not None:
            clauses.append("event = ?")
            params.append(event)
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(since.isoformat())
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(int(limit))
        with self._get_connection() as conn:
            rows = conn.execute(
                f"SELECT session_id, owner_id, sandbox_id, event, detail_json, created_at "
                f"FROM cash_session_events{where} ORDER BY created_at DESC, event_id DESC LIMIT ?",
                tuple(params),
            ).fetchall()
            return [dict(r) for r in rows]

    def event_counts(
        self,
        *,
        since: Optional[datetime] = None,
        sandbox_id: Optional[str] = None,
    ) -> dict:
        """Count lifecycle events by type (optionally within a window /
        sandbox). Backs the admin Session Lifecycle card (Tier 4.3).

        Returns `{event: count}`. SQL GROUP BY so it's accurate over the
        whole window, not limited like `list_events`.
        """
        clauses = []
        params: list = []
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(since.isoformat())
        if sandbox_id is not None:
            clauses.append("sandbox_id = ?")
            params.append(sandbox_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._get_connection() as conn:
            rows = conn.execute(
                f"SELECT event, COUNT(*) AS n FROM cash_session_events{where} GROUP BY event",
                tuple(params),
            ).fetchall()
            return {r["event"]: int(r["n"]) for r in rows}

    def state_counts(self, *, sandbox_id: Optional[str] = None) -> dict:
        """Count sessions by current `session_state` (optionally scoped).

        Surfaces outstanding `broken` sessions (cleanup that couldn't
        converge) and live `active`/`paused` ones for the admin card.
        Returns `{session_state: count}`.
        """
        clauses = []
        params: list = []
        if sandbox_id is not None:
            clauses.append("sandbox_id = ?")
            params.append(sandbox_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._get_connection() as conn:
            rows = conn.execute(
                f"SELECT session_state, COUNT(*) AS n FROM cash_sessions{where} "
                f"GROUP BY session_state",
                tuple(params),
            ).fetchall()
            return {r["session_state"]: int(r["n"]) for r in rows}

    def delete(self, session_id: str) -> bool:
        """Remove a row by session_id.

        Used by the leave route's `_purge_other_cash_rows` cleanup
        path so orphaned active sessions (e.g. a sit-down that hit a
        downstream error and never finalised) don't accumulate. The
        normal leave path uses `finalise` instead — closed rows stay
        as history.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM cash_sessions WHERE session_id = ?",
                (session_id,),
            )
            return cursor.rowcount > 0
