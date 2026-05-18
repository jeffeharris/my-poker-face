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
    IdlePoolEntry,
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
        """Return every persisted cash table, ordered by stake (ascending).

        Ordering keys off `cash_mode.stakes.STAKES_ORDER` — the single
        source of truth for the stakes ladder. Adding a new stake (or
        reordering) only requires editing that list; this repo picks
        up the change with no edits here. Tables whose stake_label
        isn't in STAKES_ORDER (shouldn't happen — pinned by schema —
        but defensive) land at the end in insertion order.

        Secondary sort by `table_id` keeps order deterministic when
        multiple tables share a stake (v2 / Path C territory; v1.5
        invariant is one table per stake).
        """
        from cash_mode.stakes import STAKES_ORDER

        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT table_id, stake_label, seats_json, created_at, last_activity_at
                FROM cash_tables
                """,
            ).fetchall()

        # Python-side stable sort against STAKES_ORDER. SQL CASE would
        # hardcode the ladder a second time; this version stays in
        # lockstep with the canonical list.
        rank = {label: i for i, label in enumerate(STAKES_ORDER)}
        unknown = len(STAKES_ORDER)
        states = [_row_to_state(r) for r in rows]
        states.sort(key=lambda s: (rank.get(s.stake_label, unknown), s.table_id))
        return states

    # --- Idle pool ---

    def save_idle(self, entry: IdlePoolEntry) -> None:
        """Upsert one personality's idle-pool row.

        Writes `left_at`, `reason`, `target_stake` verbatim. Callers
        moving an AI from a table → idle should call `save_idle`
        (after the table is updated to mark the seat `"open"`); callers
        moving an AI from idle → table should call `delete_idle`.
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO cash_idle_pool
                    (personality_id, left_at, reason, target_stake)
                VALUES (?, ?, ?, ?)
                """,
                (
                    entry.personality_id,
                    entry.left_at.isoformat(),
                    entry.reason,
                    entry.target_stake,
                ),
            )

    def load_idle(self, personality_id: str) -> Optional[IdlePoolEntry]:
        """Load one personality's idle-pool row, or None if not idle."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT personality_id, left_at, reason, target_stake
                FROM cash_idle_pool
                WHERE personality_id = ?
                """,
                (personality_id,),
            ).fetchone()
            if not row:
                return None
            return _row_to_idle(row)

    def list_idle(self) -> List[IdlePoolEntry]:
        """Return every idle-pool row, ordered by `left_at` ASC.

        Oldest-first makes the re-entry tick natural: the AI who's been
        idle longest is the most likely to walk back up.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT personality_id, left_at, reason, target_stake
                FROM cash_idle_pool
                ORDER BY left_at ASC
                """,
            ).fetchall()
            return [_row_to_idle(r) for r in rows]

    def delete_idle(self, personality_id: str) -> bool:
        """Remove one personality's idle-pool row.

        Returns True if a row was deleted, False if no such row existed.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM cash_idle_pool WHERE personality_id = ?",
                (personality_id,),
            )
            return cursor.rowcount > 0


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


def _row_to_idle(row) -> IdlePoolEntry:
    """Build an `IdlePoolEntry` from a `cash_idle_pool` row."""
    left_at = _parse_timestamp(row["left_at"])
    # `left_at` is NOT NULL in the schema, so a None here would mean
    # a malformed row — surface it loudly rather than silently lying.
    if left_at is None:
        raise ValueError(
            f"cash_idle_pool row {row['personality_id']!r} has unparseable left_at"
        )
    return IdlePoolEntry(
        personality_id=row["personality_id"],
        left_at=left_at,
        reason=row["reason"],
        target_stake=row["target_stake"],
    )
