"""Repository for AI side-hustle state.

`ai_side_hustle_state` (schema v114) holds one row per active AI side
hustle — the mirror of `ai_vice_state`. Rows are inserted by
`cash_mode.ai_side_hustle.resolve_ai_side_hustle` when a broke AI goes
off-grid to earn, and deleted by `tick_side_hustle_expirations` once
`ends_at` has passed (the expiry pass also credits the pool-drawn
payout).

Keyed `(personality_id, sandbox_id)` so an AI can only be on one hustle
at a time per sandbox. Cross-sandbox hustle state is independent — the
same AI may be hustling in sandbox A and seated in sandbox B.

`amount` is the *rolled target* set at departure. The actual payout is
re-clamped to live bank-pool depth at return time (the pool may have
drained while the AI was away), so the credited amount can be smaller
than this stored target.

See `docs/plans/CASH_MODE_SIDE_HUSTLE.md` for the design.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SideHustleState:
    """Active side-hustle row.

    `duration_bucket` is the LLM-chosen tier — `'short'`, `'medium'`, or
    `'long'`. `narration` is the lobby-ticker line (or the templated
    fallback if the LLM call failed). `amount` is the rolled target the
    AI is aiming to earn; the credited payout is re-clamped to live pool
    depth at return time.
    """

    personality_id: str
    sandbox_id: str
    started_at: datetime
    ends_at: datetime
    amount: int
    duration_bucket: str
    narration: str


def _parse_timestamp(value) -> datetime:
    """Coerce a stored timestamp back to a `datetime`.

    SQLite stores ISO-format strings (we write `.isoformat()`); some
    legacy paths persist via SQLite's native TIMESTAMP affinity which
    can round-trip a `datetime` directly. Tolerate both.
    """
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


class SideHustleStateRepository(BaseRepository):
    """CRUD for `ai_side_hustle_state`.

    All methods are per-sandbox-scoped. The repo never falls back to a
    default sandbox — callers must pass `sandbox_id` explicitly.
    """

    def insert_side_hustle_state(self, state: SideHustleState) -> None:
        """Insert a new side-hustle row.

        Uses INSERT OR REPLACE so a re-insert against the same key is
        idempotent — defensive against the (rare) case where a hustle
        fires for an AI whose previous row didn't get cleaned up. In
        normal flow the candidate filter excludes already-hustling AIs,
        so the REPLACE branch shouldn't fire.
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO ai_side_hustle_state
                    (personality_id, sandbox_id, started_at, ends_at,
                     amount, duration_bucket, narration)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state.personality_id,
                    state.sandbox_id,
                    state.started_at.isoformat(),
                    state.ends_at.isoformat(),
                    int(state.amount),
                    state.duration_bucket,
                    state.narration,
                ),
            )

    def list_active(
        self,
        *,
        sandbox_id: str,
        now: datetime,
    ) -> List[SideHustleState]:
        """Return hustles whose `ends_at > now` in the sandbox."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT personality_id, sandbox_id, started_at, ends_at,
                       amount, duration_bucket, narration
                FROM ai_side_hustle_state
                WHERE sandbox_id = ? AND ends_at > ?
                ORDER BY ends_at ASC
                """,
                (sandbox_id, now.isoformat()),
            ).fetchall()
        return [_row_to_side_hustle_state(r) for r in rows]

    def list_expired(
        self,
        *,
        sandbox_id: str,
        now: datetime,
    ) -> List[SideHustleState]:
        """Return hustles whose `ends_at <= now` in the sandbox."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT personality_id, sandbox_id, started_at, ends_at,
                       amount, duration_bucket, narration
                FROM ai_side_hustle_state
                WHERE sandbox_id = ? AND ends_at <= ?
                ORDER BY ends_at ASC
                """,
                (sandbox_id, now.isoformat()),
            ).fetchall()
        return [_row_to_side_hustle_state(r) for r in rows]

    def load(
        self,
        personality_id: str,
        *,
        sandbox_id: str,
    ) -> Optional[SideHustleState]:
        """Return the active side-hustle row for this AI, or None."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT personality_id, sandbox_id, started_at, ends_at,
                       amount, duration_bucket, narration
                FROM ai_side_hustle_state
                WHERE personality_id = ? AND sandbox_id = ?
                """,
                (personality_id, sandbox_id),
            ).fetchone()
        if row is None:
            return None
        return _row_to_side_hustle_state(row)

    def delete(self, personality_id: str, *, sandbox_id: str) -> bool:
        """Delete the side-hustle row. Returns True iff a row was removed."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM ai_side_hustle_state
                WHERE personality_id = ? AND sandbox_id = ?
                """,
                (personality_id, sandbox_id),
            )
            return cursor.rowcount > 0

    def is_on_hustle(
        self,
        personality_id: str,
        *,
        sandbox_id: str,
        now: datetime,
    ) -> bool:
        """True iff there's an unexpired side-hustle row for this AI.

        Used by eligibility gates (idle pool, seating, vice candidates)
        to exclude AIs that are currently off-grid earning.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM ai_side_hustle_state
                WHERE personality_id = ? AND sandbox_id = ? AND ends_at > ?
                LIMIT 1
                """,
                (personality_id, sandbox_id, now.isoformat()),
            ).fetchone()
        return row is not None

    def active_pids(
        self,
        *,
        sandbox_id: str,
        now: datetime,
    ) -> set:
        """Return the set of personality_ids currently on a side hustle.

        Cheaper than `list_active` when callers only need the IDs for a
        candidate-filter check.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT personality_id FROM ai_side_hustle_state
                WHERE sandbox_id = ? AND ends_at > ?
                """,
                (sandbox_id, now.isoformat()),
            ).fetchall()
        return {r["personality_id"] for r in rows}


def _row_to_side_hustle_state(row: sqlite3.Row) -> SideHustleState:
    return SideHustleState(
        personality_id=row["personality_id"],
        sandbox_id=row["sandbox_id"],
        started_at=_parse_timestamp(row["started_at"]),
        ends_at=_parse_timestamp(row["ends_at"]),
        amount=int(row["amount"]),
        duration_bucket=row["duration_bucket"],
        narration=row["narration"],
    )
