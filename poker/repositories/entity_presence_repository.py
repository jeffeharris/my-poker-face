"""Repository for the `entity_presence` table (Cut 3 of the state-model plan).

The durable backing store for the Presence state machine
(`cash_mode/presence.py`). One row per `(entity_id, sandbox_id)`; the row's
single `state` value is what makes `seated_and_idle` unrepresentable, and the DB
constraints (compound PK + partial unique seat index) make `double_seat`
unrepresentable.

ADDITIVE AND DORMANT: nothing in the live cash-mode codepaths calls this yet.
It exists so a later, human-reviewed phase can reroute the seat / idle-pool /
hustle / vice writers through the machine (see
`docs/plans/CASH_MODE_PRESENCE_MIGRATION.md`).

Concurrency contract (design §6.1): the pure machine and this repository do NOT
acquire locks. A transition that spans presence + chip-custody + session must run
inside one `get_sandbox_lock(sandbox_id)` critical section held by the CALLER, so
the read → transition → persist cycle commits atomically. `persist_transition`
re-loads, applies the pure `transition`, and writes within a single DB
connection/transaction, but the *cross-row* atomicity (presence + ledger +
session) is the caller's responsibility, not this method's.

The repository imports the pure machine from the `cash_mode` package so the
write path and the in-memory transition share one source of truth for legality.
"""

import logging
import sqlite3
from typing import List, Optional

from cash_mode.presence import (
    Presence,
    PresenceEvent,
    PresenceState,
    PresenceState_,
    offline,
    transition,
)

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


def _row_to_state(row: sqlite3.Row) -> PresenceState:
    """Build a ``PresenceState`` from a DB row. Validates the seat/state
    invariant via ``PresenceState.__post_init__``."""
    return PresenceState(
        entity_id=row["entity_id"],
        sandbox_id=row["sandbox_id"],
        state=PresenceState_(row["state"]),
        table_id=row["table_id"],
        seat_index=row["seat_index"],
        updated_at=row["updated_at"],
    )


class EntityPresenceRepository(BaseRepository):
    """Read/write the ``entity_presence`` table.

    Methods are pure I/O around the pure machine — no locking, no clock policy
    beyond letting SQLite stamp ``updated_at`` via its column default when the
    caller does not supply one.
    """

    def __init__(self, db_path: str):
        super().__init__(db_path)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def load(self, entity_id: str, sandbox_id: str) -> PresenceState:
        """Return the entity's current presence, or an ``OFFLINE`` default if no
        row exists yet (OFFLINE is the absence of a record — §5.1)."""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM entity_presence WHERE entity_id = ? AND sandbox_id = ?",
                (entity_id, sandbox_id),
            ).fetchone()
        if row is None:
            return offline(entity_id, sandbox_id)
        return _row_to_state(row)

    def list_for_sandbox(self, sandbox_id: str) -> List[PresenceState]:
        """All known presence rows in a sandbox (excludes implicit OFFLINE
        entities that have no row)."""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM entity_presence WHERE sandbox_id = ? ORDER BY entity_id",
                (sandbox_id,),
            ).fetchall()
        return [_row_to_state(r) for r in rows]

    def seat_occupant(
        self, sandbox_id: str, table_id: str, seat_index: int
    ) -> Optional[PresenceState]:
        """Return the entity SEATED at a given seat, or ``None`` if empty. The
        partial unique index guarantees at most one."""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM entity_presence "
                "WHERE sandbox_id = ? AND table_id = ? AND seat_index = ? "
                "AND state = 'seated'",
                (sandbox_id, table_id, seat_index),
            ).fetchone()
        return _row_to_state(row) if row is not None else None

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def save(self, state: PresenceState) -> None:
        """Upsert a presence row from an already-computed ``PresenceState``.

        Prefer ``persist_transition`` for state changes — it enforces a legal
        edge. ``save`` exists for backfill / seeding / tests where the new state
        is known to be valid (it is still re-validated by the table's CHECK
        constraints).

        OFFLINE is the absence of a record: saving an ``OFFLINE`` state DELETES
        the row, keeping "no row == offline" the single representation.
        """
        with self._get_connection() as conn:
            if state.state is Presence.OFFLINE:
                conn.execute(
                    "DELETE FROM entity_presence WHERE entity_id = ? AND sandbox_id = ?",
                    (state.entity_id, state.sandbox_id),
                )
                return
            conn.execute(
                """
                INSERT INTO entity_presence
                    (entity_id, sandbox_id, state, table_id, seat_index, updated_at)
                VALUES (?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
                ON CONFLICT(entity_id, sandbox_id) DO UPDATE SET
                    state      = excluded.state,
                    table_id   = excluded.table_id,
                    seat_index = excluded.seat_index,
                    updated_at = excluded.updated_at
                """,
                (
                    state.entity_id,
                    state.sandbox_id,
                    state.state.value,
                    state.table_id,
                    state.seat_index,
                    state.updated_at,
                ),
            )

    def persist_transition(
        self,
        entity_id: str,
        sandbox_id: str,
        event: PresenceEvent,
        *,
        table_id: Optional[str] = None,
        seat_index: Optional[int] = None,
        updated_at: Optional[str] = None,
    ) -> PresenceState:
        """Load → apply the pure ``transition`` → persist, returning the new state.

        Raises ``cash_mode.presence.IllegalPresenceTransition`` for an illegal
        edge (the load + transition happen before any write, so an illegal
        attempt leaves the row untouched).

        **CALLER MUST HOLD ``get_sandbox_lock(sandbox_id)``** for the duration —
        this method does a read-modify-write across one row, and a true cash
        "transition" also touches chip-custody and session rows that must commit
        in the same critical section (design §6.1). The DB constraints are the
        last-line backstop (e.g. a concurrent double-seat raises
        ``sqlite3.IntegrityError``), not a substitute for the lock.
        """
        current = self.load(entity_id, sandbox_id)
        new_state = transition(
            current,
            event,
            table_id=table_id,
            seat_index=seat_index,
            updated_at=updated_at,
        )
        self.save(new_state)
        return new_state
