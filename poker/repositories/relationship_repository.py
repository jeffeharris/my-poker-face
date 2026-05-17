"""Repository for relationship_states and cash_pair_stats tables.

Two cross-session/cross-game tables introduced in schema v87:

- `relationship_states`: per-(observer, opponent) affinity axes
  (heat, respect, likability). Heat decays via `project_heat`; the
  stored value is the "heat as of last_decay_tick" snapshot. Default
  read methods apply projection; raw reads are admin-only and
  explicitly named.

- `cash_pair_stats`: cash-mode-only cumulative PnL between pairs.
  Distinct from relationship_states because PnL is meaningless in
  tournaments.

All persistence APIs use **stable personality_ids** for keys, not
display names. The relationship layer's read paths consume
`RelationshipState` instances; the projection-on-read pattern means
callers can't accidentally observe stale "heat as of last event"
values when they want "live heat now" — they're forced to go
through `load_*` rather than reading the column directly.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Optional

from poker.repositories.base_repository import BaseRepository
from poker.memory.opponent_model import (
    CashPairStats,
    RelationshipState,
    project_heat,
)

logger = logging.getLogger(__name__)


def _parse_timestamp(value) -> Optional[datetime]:
    """SQLite returns timestamps as strings; coerce back to datetime.

    Returns None for NULL rows (no event ever recorded), the most
    common state for newly observed pairs.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        # SQLite default format: "YYYY-MM-DD HH:MM:SS[.ffffff]"
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _state_from_row(row, *, project_to: Optional[datetime] = None) -> RelationshipState:
    """Build a `RelationshipState` from a `relationship_states` row.

    When `project_to` is set, the returned state's `heat` field is
    the projected value (live heat now); the underlying stored
    snapshot remains accessible via `load_raw_relationship_state`
    for admin/analytics callers.
    """
    state = RelationshipState(
        heat=row['heat'],
        respect=row['respect'],
        likability=row['likability'],
        last_seen=_parse_timestamp(row['last_seen']),
        last_decay_tick=_parse_timestamp(row['last_decay_tick']),
    )
    if project_to is not None:
        state.heat = project_heat(state, project_to)
    return state


class RelationshipRepository(BaseRepository):
    """CRUD for relationship_states + cash_pair_stats.

    Schema is created by `SchemaManager.ensure_schema()`; this class
    only touches data. Callers go through this rather than emitting
    raw SQL so the projection-on-read invariant stays enforced at one
    location.
    """

    # --- relationship_states ---

    def save_relationship_state(
        self,
        observer_id: str,
        opponent_id: str,
        state: RelationshipState,
    ) -> None:
        """Upsert the (observer_id, opponent_id) row.

        Writes the stored `heat` snapshot verbatim — `record_event`
        is responsible for projecting heat through decay *before*
        applying event shifts, so the snapshot in the DB always
        represents "heat after most recent event."
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO relationship_states
                    (observer_id, opponent_id, heat, respect, likability,
                     last_seen, last_decay_tick)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observer_id,
                    opponent_id,
                    state.heat,
                    state.respect,
                    state.likability,
                    state.last_seen.isoformat() if state.last_seen else None,
                    state.last_decay_tick.isoformat() if state.last_decay_tick else None,
                ),
            )

    def load_relationship_state(
        self,
        observer_id: str,
        opponent_id: str,
        *,
        now: Optional[datetime] = None,
    ) -> Optional[RelationshipState]:
        """Load with projection applied to the heat axis.

        Returns None when no row exists for the pair (the no-event-
        ever state). Callers should treat None as "no relationship
        modifier applies" — equivalent to a default `RelationshipState`
        with no event history.

        `now` defaults to `datetime.utcnow()` if not supplied.
        Explicit `now` lets callers pin the projection point for
        replay/test stability.
        """
        if now is None:
            now = datetime.utcnow()
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT heat, respect, likability, last_seen, last_decay_tick
                FROM relationship_states
                WHERE observer_id = ? AND opponent_id = ?
                """,
                (observer_id, opponent_id),
            ).fetchone()
            return _state_from_row(row, project_to=now) if row else None

    def load_raw_relationship_state(
        self,
        observer_id: str,
        opponent_id: str,
    ) -> Optional[RelationshipState]:
        """Load without projection — "heat as of last_decay_tick."

        Admin / analytics only. Production read paths should use
        `load_relationship_state`. The name is intentionally
        verbose to discourage accidental use; the default
        `load_relationship_state` is the right hammer for almost
        every caller.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT heat, respect, likability, last_seen, last_decay_tick
                FROM relationship_states
                WHERE observer_id = ? AND opponent_id = ?
                """,
                (observer_id, opponent_id),
            ).fetchone()
            return _state_from_row(row, project_to=None) if row else None

    def load_all_relationships(
        self,
        observer_id: str,
        *,
        now: Optional[datetime] = None,
    ) -> Dict[str, RelationshipState]:
        """Load every (observer, *) row, projection applied.

        Returns `{opponent_id: RelationshipState}`. Useful at game
        startup when a controller wants its full affinity surface in
        one read rather than N round-trips during the hand.
        """
        if now is None:
            now = datetime.utcnow()
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT opponent_id, heat, respect, likability,
                       last_seen, last_decay_tick
                FROM relationship_states
                WHERE observer_id = ?
                """,
                (observer_id,),
            ).fetchall()
            return {
                row['opponent_id']: _state_from_row(row, project_to=now)
                for row in rows
            }

    # --- cash_pair_stats ---

    def save_cash_pair_stats(
        self,
        observer_id: str,
        opponent_id: str,
        stats: CashPairStats,
    ) -> None:
        """Upsert the (observer_id, opponent_id) cumulative stats row.

        Callers writing pair updates at hand resolution must write
        BOTH the (winner, loser) row AND the mirror (loser, winner)
        row with negated PnL in a single transaction, so the two
        views can't drift. This method writes one row; transaction
        management is the caller's responsibility.
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO cash_pair_stats
                    (observer_id, opponent_id, cumulative_pnl, hands_played_cash)
                VALUES (?, ?, ?, ?)
                """,
                (
                    observer_id,
                    opponent_id,
                    stats.cumulative_pnl,
                    stats.hands_played_cash,
                ),
            )

    def load_cash_pair_stats(
        self,
        observer_id: str,
        opponent_id: str,
    ) -> Optional[CashPairStats]:
        """Load the cumulative cash-mode stats for a pair.

        Returns None when no row exists (pair has never played in
        cash mode). Callers should treat None as "PnL = 0, hands = 0"
        — equivalent to a default-constructed CashPairStats.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT cumulative_pnl, hands_played_cash
                FROM cash_pair_stats
                WHERE observer_id = ? AND opponent_id = ?
                """,
                (observer_id, opponent_id),
            ).fetchone()
            if not row:
                return None
            return CashPairStats(
                observer_id=observer_id,
                opponent_id=opponent_id,
                cumulative_pnl=row['cumulative_pnl'],
                hands_played_cash=row['hands_played_cash'],
            )
