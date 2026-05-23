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

from cash_mode.cash_sessions import CashSession
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
            int(row["cash_seat_index"])
            if row["cash_seat_index"] is not None
            else None
        ),
        started_at=_parse_timestamp(row["started_at"]),
        ended_at=_parse_timestamp(row["ended_at"]),
        final_chips_at_table=(
            int(row["final_chips_at_table"])
            if row["final_chips_at_table"] is not None
            else None
        ),
        sponsor_repaid=int(row["sponsor_repaid"] or 0),
        player_take_home=(
            int(row["player_take_home"])
            if row["player_take_home"] is not None
            else None
        ),
        hands_played=(
            int(row["hands_played"])
            if row["hands_played"] is not None
            else None
        ),
        hands_won=(
            int(row["hands_won"])
            if row["hands_won"] is not None
            else None
        ),
        biggest_pot_won=(
            int(row["biggest_pot_won"])
            if row["biggest_pot_won"] is not None
            else None
        ),
        duration_seconds=(
            int(row["duration_seconds"])
            if row["duration_seconds"] is not None
            else None
        ),
        closed_status=row["closed_status"],
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
                     duration_seconds, closed_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def list_for_owner(
        self, owner_id: str, *, limit: int = 50,
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
        Returns True if a row was updated. Idempotent against a
        re-leave: the `ended_at IS NULL` guard skips already-finalised
        rows so a retry doesn't overwrite the first leave's numbers.
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
                    closed_status = ?
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
                    session_id,
                ),
            )
            return cursor.rowcount > 0

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
