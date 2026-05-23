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


def _has_column(conn, table_name: str, column_name: str) -> bool:
    return any(
        row[1] == column_name
        for row in conn.execute(f"PRAGMA table_info({table_name})")
    )


class CashTableRepository(BaseRepository):
    """CRUD for `cash_tables`.

    Two reads (`load_table`, `list_all_tables`) and one write
    (`save_table`). No delete in v1.5 — tables are seeded once and
    never removed.

    Like other repositories, the schema is created by
    `SchemaManager.ensure_schema()`; this class only touches data.
    """

    def save_table(
        self,
        state: CashTableState,
        *,
        sandbox_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> None:
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
            scoped = _has_column(conn, "cash_tables", "sandbox_id")
            named = _has_column(conn, "cash_tables", "name")
            typed = _has_column(conn, "cash_tables", "table_type")
            has_closing = _has_column(conn, "cash_tables", "closing_hand_countdown")
            if scoped and not sandbox_id:
                raise ValueError("sandbox_id is required for cash_tables writes")
            # Preserve created_at on upsert if a row exists; otherwise use
            # the provided value or fall back to SQL DEFAULT.
            if scoped:
                existing = conn.execute(
                    """
                    SELECT created_at FROM cash_tables
                    WHERE table_id = ? AND sandbox_id = ?
                    """,
                    (state.table_id, sandbox_id),
                ).fetchone()
            else:
                existing = conn.execute(
                    "SELECT created_at FROM cash_tables WHERE table_id = ?",
                    (state.table_id,),
                ).fetchone()
            # Build column-list dynamically so this repo stays compatible
            # with pre-v111 schemas (used by some legacy fixtures).
            extra_set_cols = []
            extra_set_vals: list = []
            extra_ins_cols = []
            extra_ins_vals: list = []
            if named:
                extra_set_cols.append("name = ?")
                extra_set_vals.append(state.name)
                extra_ins_cols.append("name")
                extra_ins_vals.append(state.name)
            if typed:
                extra_set_cols.append("table_type = ?")
                extra_set_vals.append(state.table_type)
                extra_ins_cols.append("table_type")
                extra_ins_vals.append(state.table_type)
            if has_closing:
                # Round-trip the closing countdown verbatim. NULL means
                # "active" (lobby tables and active casinos); integer
                # means "closing with N hands remaining."
                extra_set_cols.append("closing_hand_countdown = ?")
                extra_set_vals.append(state.closing_hand_countdown)
                extra_ins_cols.append("closing_hand_countdown")
                extra_ins_vals.append(state.closing_hand_countdown)
            extra_set_clause = (", " + ", ".join(extra_set_cols)) if extra_set_cols else ""
            extra_ins_col_clause = (", " + ", ".join(extra_ins_cols)) if extra_ins_cols else ""
            extra_ins_qmark_clause = (", " + ", ".join("?" * len(extra_ins_cols))) if extra_ins_cols else ""

            if existing:
                if scoped:
                    conn.execute(
                        f"""
                        UPDATE cash_tables
                        SET stake_label = ?, seats_json = ?, dealer_idx = ?,
                            last_activity_at = ?{extra_set_clause}
                        WHERE table_id = ? AND sandbox_id = ?
                        """,
                        (
                            state.stake_label, seats_blob, int(state.dealer_idx),
                            now.isoformat(),
                            *extra_set_vals,
                            state.table_id, sandbox_id,
                        ),
                    )
                else:
                    conn.execute(
                        f"""
                        UPDATE cash_tables
                        SET stake_label = ?, seats_json = ?, dealer_idx = ?,
                            last_activity_at = ?{extra_set_clause}
                        WHERE table_id = ?
                        """,
                        (
                            state.stake_label, seats_blob, int(state.dealer_idx),
                            now.isoformat(),
                            *extra_set_vals,
                            state.table_id,
                        ),
                    )
            else:
                if created_iso is None:
                    if scoped:
                        conn.execute(
                            f"""
                            INSERT INTO cash_tables
                                (table_id, sandbox_id, stake_label, seats_json,
                                 dealer_idx, last_activity_at{extra_ins_col_clause})
                            VALUES (?, ?, ?, ?, ?, ?{extra_ins_qmark_clause})
                            """,
                            (
                                state.table_id, sandbox_id, state.stake_label,
                                seats_blob, int(state.dealer_idx),
                                now.isoformat(),
                                *extra_ins_vals,
                            ),
                        )
                    else:
                        conn.execute(
                            f"""
                            INSERT INTO cash_tables
                                (table_id, stake_label, seats_json, dealer_idx,
                                 last_activity_at{extra_ins_col_clause})
                            VALUES (?, ?, ?, ?, ?{extra_ins_qmark_clause})
                            """,
                            (
                                state.table_id, state.stake_label, seats_blob,
                                int(state.dealer_idx), now.isoformat(),
                                *extra_ins_vals,
                            ),
                        )
                else:
                    if scoped:
                        conn.execute(
                            f"""
                            INSERT INTO cash_tables
                                (table_id, sandbox_id, stake_label, seats_json,
                                 dealer_idx, created_at, last_activity_at{extra_ins_col_clause})
                            VALUES (?, ?, ?, ?, ?, ?, ?{extra_ins_qmark_clause})
                            """,
                            (
                                state.table_id, sandbox_id, state.stake_label,
                                seats_blob, int(state.dealer_idx), created_iso,
                                now.isoformat(),
                                *extra_ins_vals,
                            ),
                        )
                    else:
                        conn.execute(
                            f"""
                            INSERT INTO cash_tables
                                (table_id, stake_label, seats_json, dealer_idx,
                                 created_at, last_activity_at{extra_ins_col_clause})
                            VALUES (?, ?, ?, ?, ?, ?{extra_ins_qmark_clause})
                            """,
                            (
                                state.table_id, state.stake_label, seats_blob,
                                int(state.dealer_idx), created_iso,
                                now.isoformat(),
                                *extra_ins_vals,
                            ),
                        )

    def load_table(
        self,
        table_id: str,
        *,
        sandbox_id: Optional[str] = None,
    ) -> Optional[CashTableState]:
        """Load a single cash table by id, or None if it doesn't exist."""
        with self._get_connection() as conn:
            if _has_column(conn, "cash_tables", "sandbox_id"):
                if not sandbox_id:
                    raise ValueError("sandbox_id is required for cash_tables reads")
                row = conn.execute(
                    """
                    SELECT *
                    FROM cash_tables
                    WHERE table_id = ? AND sandbox_id = ?
                    """,
                    (table_id, sandbox_id),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT *
                    FROM cash_tables
                    WHERE table_id = ?
                    """,
                    (table_id,),
                ).fetchone()
            if not row:
                return None
            return _row_to_state(row)

    def set_closing_countdown(
        self,
        table_id: str,
        *,
        sandbox_id: Optional[str] = None,
        countdown: Optional[int],
    ) -> bool:
        """Set the `closing_hand_countdown` column for a casino table.

        `countdown=None` resets to NULL (active). `countdown=int` puts
        the table in 'closing' state with N hands remaining (smooth
        shutdown from `casino_provisioning`).

        Returns True if a row was updated. Returns False (and logs at
        debug) if the column isn't present (pre-v113 schema) so legacy
        fixtures don't error — the closing state just doesn't persist.
        """
        with self._get_connection() as conn:
            if not _has_column(conn, "cash_tables", "closing_hand_countdown"):
                logger.debug(
                    "cash_tables has no closing_hand_countdown column "
                    "(pre-v113 schema); set_closing_countdown is a no-op"
                )
                return False
            scoped = _has_column(conn, "cash_tables", "sandbox_id")
            if scoped:
                if not sandbox_id:
                    raise ValueError(
                        "sandbox_id is required for cash_tables writes"
                    )
                cursor = conn.execute(
                    "UPDATE cash_tables SET closing_hand_countdown = ? "
                    "WHERE table_id = ? AND sandbox_id = ?",
                    (countdown, table_id, sandbox_id),
                )
            else:
                cursor = conn.execute(
                    "UPDATE cash_tables SET closing_hand_countdown = ? "
                    "WHERE table_id = ?",
                    (countdown, table_id),
                )
            return cursor.rowcount > 0

    def get_closing_countdown(
        self,
        table_id: str,
        *,
        sandbox_id: Optional[str] = None,
    ) -> Optional[int]:
        """Read the `closing_hand_countdown` column. None when active,
        when the row doesn't exist, or when the column isn't present."""
        with self._get_connection() as conn:
            if not _has_column(conn, "cash_tables", "closing_hand_countdown"):
                return None
            scoped = _has_column(conn, "cash_tables", "sandbox_id")
            if scoped:
                if not sandbox_id:
                    return None
                row = conn.execute(
                    "SELECT closing_hand_countdown FROM cash_tables "
                    "WHERE table_id = ? AND sandbox_id = ?",
                    (table_id, sandbox_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT closing_hand_countdown FROM cash_tables "
                    "WHERE table_id = ?",
                    (table_id,),
                ).fetchone()
            if not row:
                return None
            raw = row["closing_hand_countdown"]
            return int(raw) if raw is not None else None

    def delete_table(
        self,
        table_id: str,
        *,
        sandbox_id: Optional[str] = None,
    ) -> bool:
        """Delete a cash table row. Returns True if a row was removed.

        Used by ephemeral table types (currently `casino` — see
        `cash_mode/casino_provisioning.py`) that spawn and tear down
        based on bank-pool depth. Lobby tables (`table_type='lobby'`)
        aren't deleted in normal flows — they're seeded once and
        persist.

        No-op (returns False) when the row doesn't exist. Callers
        treat that as success in idempotent flows.
        """
        with self._get_connection() as conn:
            scoped = _has_column(conn, "cash_tables", "sandbox_id")
            if scoped:
                if not sandbox_id:
                    raise ValueError("sandbox_id is required for cash_tables deletes")
                cursor = conn.execute(
                    "DELETE FROM cash_tables WHERE table_id = ? AND sandbox_id = ?",
                    (table_id, sandbox_id),
                )
            else:
                cursor = conn.execute(
                    "DELETE FROM cash_tables WHERE table_id = ?",
                    (table_id,),
                )
            return cursor.rowcount > 0

    def list_all_tables(
        self,
        *,
        sandbox_id: Optional[str] = None,
    ) -> List[CashTableState]:
        """Return every persisted cash table, ordered by stake (ascending).

        Ordering keys off `cash_mode.stakes_ladder.STAKES_ORDER` — the single
        source of truth for the stakes ladder. Adding a new stake (or
        reordering) only requires editing that list; this repo picks
        up the change with no edits here. Tables whose stake_label
        isn't in STAKES_ORDER (shouldn't happen — pinned by schema —
        but defensive) land at the end in insertion order.

        Secondary sort by `table_id` keeps order deterministic when
        multiple tables share a stake (v2 / Path C territory; v1.5
        invariant is one table per stake).
        """
        from cash_mode.stakes_ladder import STAKES_ORDER

        with self._get_connection() as conn:
            if sandbox_id is None:
                # Admin / audit cross-sandbox aggregation.
                rows = conn.execute(
                    """
                    SELECT *
                    FROM cash_tables
                    """,
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM cash_tables
                    WHERE sandbox_id = ?
                    """,
                    (sandbox_id,),
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

    def save_idle(
        self,
        entry: IdlePoolEntry,
        *,
        sandbox_id: Optional[str] = None,
    ) -> None:
        """Upsert one personality's idle-pool row.

        Writes `left_at`, `reason`, `target_stake` verbatim. Callers
        moving an AI from a table → idle should call `save_idle`
        (after the table is updated to mark the seat `"open"`); callers
        moving an AI from idle → table should call `delete_idle`.
        """
        with self._get_connection() as conn:
            if _has_column(conn, "cash_idle_pool", "sandbox_id"):
                if not sandbox_id:
                    raise ValueError("sandbox_id is required for cash_idle_pool writes")
                conn.execute(
                    """
                    INSERT OR REPLACE INTO cash_idle_pool
                        (personality_id, sandbox_id, left_at, reason, target_stake)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        entry.personality_id, sandbox_id,
                        entry.left_at.isoformat(), entry.reason,
                        entry.target_stake,
                    ),
                )
            else:
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

    def load_idle(
        self,
        personality_id: str,
        *,
        sandbox_id: Optional[str] = None,
    ) -> Optional[IdlePoolEntry]:
        """Load one personality's idle-pool row, or None if not idle."""
        with self._get_connection() as conn:
            if _has_column(conn, "cash_idle_pool", "sandbox_id"):
                if not sandbox_id:
                    raise ValueError("sandbox_id is required for cash_idle_pool reads")
                row = conn.execute(
                    """
                    SELECT personality_id, left_at, reason, target_stake
                    FROM cash_idle_pool
                    WHERE personality_id = ? AND sandbox_id = ?
                    """,
                    (personality_id, sandbox_id),
                ).fetchone()
            else:
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

    def list_idle(
        self,
        *,
        sandbox_id: Optional[str] = None,
    ) -> List[IdlePoolEntry]:
        """Return every idle-pool row, ordered by `left_at` ASC.

        Oldest-first makes the re-entry tick natural: the AI who's been
        idle longest is the most likely to walk back up.
        """
        with self._get_connection() as conn:
            if sandbox_id is None:
                rows = conn.execute(
                    """
                    SELECT personality_id, left_at, reason, target_stake
                    FROM cash_idle_pool
                    ORDER BY left_at ASC
                    """,
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT personality_id, left_at, reason, target_stake
                    FROM cash_idle_pool
                    WHERE sandbox_id = ?
                    ORDER BY left_at ASC
                    """,
                    (sandbox_id,),
                ).fetchall()
            return [_row_to_idle(r) for r in rows]

    def delete_idle(
        self,
        personality_id: str,
        *,
        sandbox_id: Optional[str] = None,
    ) -> bool:
        """Remove one personality's idle-pool row.

        Returns True if a row was deleted, False if no such row existed.
        """
        with self._get_connection() as conn:
            if _has_column(conn, "cash_idle_pool", "sandbox_id"):
                if not sandbox_id:
                    raise ValueError("sandbox_id is required for cash_idle_pool writes")
                cursor = conn.execute(
                    """
                    DELETE FROM cash_idle_pool
                    WHERE personality_id = ? AND sandbox_id = ?
                    """,
                    (personality_id, sandbox_id),
                )
            else:
                cursor = conn.execute(
                    "DELETE FROM cash_idle_pool WHERE personality_id = ?",
                    (personality_id,),
                )
            return cursor.rowcount > 0


def _row_to_state(row) -> CashTableState:
    """Build a `CashTableState` from a `cash_tables` row."""
    seats = seats_from_json(row["seats_json"])
    # `dealer_idx` was added in schema v96. The migration has DEFAULT 0
    # so existing rows backfill cleanly; this guard handles any path
    # where the row predates the migration in tests or partial restores.
    try:
        dealer_idx = int(row["dealer_idx"] or 0)
    except (KeyError, IndexError):
        dealer_idx = 0
    # v111 columns. sqlite3.Row raises IndexError for missing columns
    # under SELECT *; treat absence as the default value.
    try:
        name = row["name"]
    except (KeyError, IndexError):
        name = None
    try:
        table_type = row["table_type"] or 'lobby'
    except (KeyError, IndexError):
        table_type = 'lobby'
    # v113 column. Same KeyError/IndexError treatment for older schemas.
    try:
        raw = row["closing_hand_countdown"]
        closing_hand_countdown = int(raw) if raw is not None else None
    except (KeyError, IndexError):
        closing_hand_countdown = None
    return CashTableState(
        table_id=row["table_id"],
        stake_label=row["stake_label"],
        seats=seats,
        created_at=_parse_timestamp(row["created_at"]),
        last_activity_at=_parse_timestamp(row["last_activity_at"]),
        dealer_idx=dealer_idx,
        name=name,
        table_type=table_type,
        closing_hand_countdown=closing_hand_countdown,
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
