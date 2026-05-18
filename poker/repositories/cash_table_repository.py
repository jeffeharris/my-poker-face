"""Repository for the v91 `cash_tables` persistence surface.

One row per persistent lobby table; the `seats_json` column carries
the 6 slot dicts (4 baseline AI + 2 open) that make the lobby's
multi-table view possible.

Distinct from `BankrollRepository` — table state is the lobby's
*identity* (who's seated, how many chips on the table) and crosses
sessions. Bankroll persistence handles the AI's off-table chips.

Spec: `docs/plans/CASH_MODE_LOBBY_HANDOFF.md` §"Persistent table state".
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from cash_mode.tables import (
    CashTableState,
    seats_from_json,
    seats_to_json,
)
from poker.repositories.base_repository import BaseRepository

logger = logging.getLogger(__name__)


def _parse_timestamp(value) -> Optional[datetime]:
    """Coerce a SQLite TIMESTAMP value to a datetime, or None.

    SQLite returns timestamps as strings under the default sqlite3
    type detection; legacy rows may also surface datetimes. The
    parsing is duck-typed (string → fromisoformat; datetime → passthrough).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        # SQLite default formats: "YYYY-MM-DD HH:MM:SS[.ffffff]"
        # `datetime.fromisoformat` handles both that and ISO 8601.
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            try:
                # Some legacy rows omit microseconds; the fromisoformat
                # call above already handles those. This branch is just
                # defense against truly broken values.
                return None
            except Exception:
                return None
    return None


class CashTableRepository(BaseRepository):
    """CRUD for `cash_tables`.

    Two reads (`load_table`, `list_all_tables`) and one write
    (`save_table`). No delete in v1.5 — tables are seeded once and
    never removed.

    Like other repositories, the schema is created by
    `SchemaManager.ensure_schema()`; this class only touches data.
    """

    def save_table(self, state: CashTableState, *, now: Optional[datetime] = None) -> None:
        """Upsert a cash table row.

        Bumps `last_activity_at` to `now` (default `datetime.utcnow()`)
        on every write — the refresh hook calls save_table after any
        movement decision so admin views can sort tables by recent
        activity.

        `created_at` is preserved on re-saves: if `state.created_at` is
        non-None we honor it, else SQL DEFAULT applies on first insert
        and existing rows keep their original timestamp via the COALESCE.
        """
        if now is None:
            now = datetime.utcnow()
        seats_blob = seats_to_json(state.seats)
        created_iso = state.created_at.isoformat() if state.created_at else None
        with self._get_connection() as conn:
            # Preserve created_at on upsert if a row exists; otherwise use
            # the provided value or fall back to SQL DEFAULT.
            existing = conn.execute(
                "SELECT created_at FROM cash_tables WHERE table_id = ?",
                (state.table_id,),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE cash_tables
                    SET stake_label = ?, seats_json = ?, last_activity_at = ?
                    WHERE table_id = ?
                    """,
                    (state.stake_label, seats_blob, now.isoformat(), state.table_id),
                )
            else:
                if created_iso is None:
                    conn.execute(
                        """
                        INSERT INTO cash_tables
                            (table_id, stake_label, seats_json, last_activity_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (state.table_id, state.stake_label, seats_blob, now.isoformat()),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO cash_tables
                            (table_id, stake_label, seats_json, created_at, last_activity_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            state.table_id,
                            state.stake_label,
                            seats_blob,
                            created_iso,
                            now.isoformat(),
                        ),
                    )

    def load_table(self, table_id: str) -> Optional[CashTableState]:
        """Load a single cash table by id, or None if it doesn't exist."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT table_id, stake_label, seats_json, created_at, last_activity_at
                FROM cash_tables
                WHERE table_id = ?
                """,
                (table_id,),
            ).fetchone()
            if not row:
                return None
            return _row_to_state(row)

    def list_all_tables(self) -> List[CashTableState]:
        """Return every persisted cash table, ordered by table_id.

        Deterministic order so the lobby UI renders consistently across
        polls and so tests can compare list equality without sorting.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT table_id, stake_label, seats_json, created_at, last_activity_at
                FROM cash_tables
                ORDER BY table_id
                """,
            ).fetchall()
            return [_row_to_state(r) for r in rows]


def _row_to_state(row) -> CashTableState:
    """Build a `CashTableState` from a `cash_tables` row."""
    seats = seats_from_json(row["seats_json"])
    return CashTableState(
        table_id=row["table_id"],
        stake_label=row["stake_label"],
        seats=seats,
        created_at=_parse_timestamp(row["created_at"]),
        last_activity_at=_parse_timestamp(row["last_activity_at"]),
    )
