"""Repository for the v98 `stakes` persistence surface.

One row per session-scoped stake deal. Drives the post-Phase-1 cash
mode economy: the staker puts up `principal` chips at sit-down, the
borrower plays them, settlement at leave-table either marks the row
`settled` (clean), `carry` (residual debt rolls forward), or
`defaulted` (explicit default action — Phase 2).

Distinct from `BankrollRepository.save_player_bankroll`'s
`active_loan_*` columns — those persist a single "loan" snapshot per
player and are the legacy persistence surface this repository
replaces. The columns stick around through Phase 1 as a safety net
(Commit 3 migrates their data into rows here) and are dropped in
Phase 2 once the new settlement path is live.

Spec: `docs/plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md` Phase 1
Commit 2.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from cash_mode.stakes import Stake, STAKE_STATUS_CARRY
from poker.repositories.base_repository import BaseRepository

logger = logging.getLogger(__name__)


def _parse_timestamp(value) -> Optional[datetime]:
    """Coerce a SQLite TIMESTAMP value to a datetime, or None.

    Same pattern as `cash_table_repository._parse_timestamp` — SQLite
    returns timestamps as ISO strings under default type detection;
    legacy rows may surface as datetimes. Duck-typed conversion keeps
    both branches honest.
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


class StakeRepository(BaseRepository):
    """CRUD for `stakes`.

    Five reads (`load_stake`, `load_active_for_session`,
    `list_carries_for_borrower`, `list_carries_for_staker`,
    `list_stakes_for_session`) and three writes (`create_stake`,
    `update_status`, `update_carry_amount`). No bulk-delete — stakes
    are append-only history; settled rows stay for the Net Worth view
    and analytics on default rates by stake size (`stake_tier`).

    Like other repositories, the schema is created by
    `SchemaManager.ensure_schema()` (v98 migration); this class only
    touches data.
    """

    def create_stake(self, stake: Stake) -> None:
        """Insert one stake row.

        Raises `sqlite3.IntegrityError` on duplicate `stake_id` —
        caller is responsible for generating unique ids (uuid4-style
        is fine; the column is a string PK with no auto-increment).
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO stakes
                    (stake_id, session_id, staker_id, staker_kind,
                     borrower_id, borrower_kind, format,
                     principal, match_amount, origination_fee, cut,
                     status, carry_amount, stake_tier,
                     created_at, settled_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stake.stake_id,
                    stake.session_id,
                    stake.staker_id,
                    stake.staker_kind,
                    stake.borrower_id,
                    stake.borrower_kind,
                    stake.format,
                    stake.principal,
                    stake.match_amount,
                    stake.origination_fee,
                    stake.cut,
                    stake.status,
                    stake.carry_amount,
                    stake.stake_tier,
                    stake.created_at.isoformat(),
                    stake.settled_at.isoformat() if stake.settled_at else None,
                ),
            )

    def load_stake(self, stake_id: str) -> Optional[Stake]:
        """Load one stake by id, or None if not found."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT stake_id, session_id, staker_id, staker_kind,
                       borrower_id, borrower_kind, format,
                       principal, match_amount, origination_fee, cut,
                       status, carry_amount, stake_tier,
                       created_at, settled_at
                FROM stakes
                WHERE stake_id = ?
                """,
                (stake_id,),
            ).fetchone()
            if not row:
                return None
            return _row_to_stake(row)

    def load_active_for_session(self, session_id: str) -> Optional[Stake]:
        """Load the single active stake for a session, or None.

        The Phase 1 invariant is one active stake per session (deal
        struck at sit-down, settled at leave-table). If multiple rows
        match — which shouldn't happen — return the most recently
        created. Callers that need the full list should use
        `list_stakes_for_session` instead.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT stake_id, session_id, staker_id, staker_kind,
                       borrower_id, borrower_kind, format,
                       principal, match_amount, origination_fee, cut,
                       status, carry_amount, stake_tier,
                       created_at, settled_at
                FROM stakes
                WHERE session_id = ? AND status = 'active'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            if not row:
                return None
            return _row_to_stake(row)

    def list_stakes_for_session(self, session_id: str) -> List[Stake]:
        """Return every stake row for a session, oldest first.

        Used by post-session debug + analytics paths. Phase 1's
        settlement only walks the single active stake; this method
        exists for inspection rather than settlement.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT stake_id, session_id, staker_id, staker_kind,
                       borrower_id, borrower_kind, format,
                       principal, match_amount, origination_fee, cut,
                       status, carry_amount, stake_tier,
                       created_at, settled_at
                FROM stakes
                WHERE session_id = ?
                ORDER BY created_at ASC
                """,
                (session_id,),
            ).fetchall()
            return [_row_to_stake(r) for r in rows]

    def list_carries_for_borrower(
        self, borrower_id: str, borrower_kind: str,
    ) -> List[Stake]:
        """Return every active carry row for a borrower.

        Drives Phase 2's tier resolution (aggregate `carry_load` over
        the returned list) and Phase 3's Net Worth `payables` column.
        The partial index `idx_stakes_borrower_carry` covers this
        query.

        `borrower_kind` is part of the filter so the human / personality
        spaces don't collide on shared ids (unlikely in practice — the
        ids are namespaced — but cheap defense).
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT stake_id, session_id, staker_id, staker_kind,
                       borrower_id, borrower_kind, format,
                       principal, match_amount, origination_fee, cut,
                       status, carry_amount, stake_tier,
                       created_at, settled_at
                FROM stakes
                WHERE borrower_id = ? AND borrower_kind = ?
                  AND status = ?
                ORDER BY created_at ASC
                """,
                (borrower_id, borrower_kind, STAKE_STATUS_CARRY),
            ).fetchall()
            return [_row_to_stake(r) for r in rows]

    def list_carries_for_staker(self, staker_id: str) -> List[Stake]:
        """Return every active carry row owed TO a staker.

        Drives Phase 3's Net Worth `receivables` column once Phase 5
        ships and humans can stake AIs. Also used by Phase 2's
        per-staker garnishment check — when a borrower with carry
        history with the same staker takes a new stake, the cut bumps
        up until the old carry clears.

        `staker_id IS NULL` (house stakes) is intentionally not
        queryable here: house stakes never create carries by design
        (locked decision #3). Passing `staker_id=None` would return an
        empty list under SQL's NULL-comparison semantics, which is the
        correct answer for the house case anyway.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT stake_id, session_id, staker_id, staker_kind,
                       borrower_id, borrower_kind, format,
                       principal, match_amount, origination_fee, cut,
                       status, carry_amount, stake_tier,
                       created_at, settled_at
                FROM stakes
                WHERE staker_id = ? AND status = ?
                ORDER BY created_at ASC
                """,
                (staker_id, STAKE_STATUS_CARRY),
            ).fetchall()
            return [_row_to_stake(r) for r in rows]

    def update_status(
        self, stake_id: str, status: str,
        *, settled_at: Optional[datetime] = None,
    ) -> bool:
        """Transition a stake to a new status, optionally stamping
        `settled_at`.

        Returns True if a row was updated, False if no such stake.
        Callers should typically pair this with `update_carry_amount`
        when transitioning to 'carry' — the two columns are independent
        on purpose so the explicit-default action (Phase 2) can zero
        `carry_amount` and flip `status='defaulted'` in one call site
        without forcing settled_at semantics.
        """
        with self._get_connection() as conn:
            if settled_at is not None:
                cursor = conn.execute(
                    "UPDATE stakes SET status = ?, settled_at = ? "
                    "WHERE stake_id = ?",
                    (status, settled_at.isoformat(), stake_id),
                )
            else:
                cursor = conn.execute(
                    "UPDATE stakes SET status = ? WHERE stake_id = ?",
                    (status, stake_id),
                )
            return cursor.rowcount > 0

    def update_carry_amount(self, stake_id: str, carry_amount: int) -> bool:
        """Set `carry_amount` on a stake. Returns True if updated.

        Used at settlement time when transitioning to 'carry' (set the
        residual debt), and at carry-clearing time (set to 0 when the
        borrower pays off voluntarily or the staker forgives — Phase 3).
        The status column transition is a separate call so the carry
        amount can be inspected before flipping to settled / defaulted.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE stakes SET carry_amount = ? WHERE stake_id = ?",
                (int(carry_amount), stake_id),
            )
            return cursor.rowcount > 0


def _row_to_stake(row) -> Stake:
    """Build a `Stake` from a `stakes` row."""
    created_at = _parse_timestamp(row["created_at"])
    if created_at is None:
        # `created_at` is NOT NULL in schema, so a None here means a
        # malformed row — surface loudly rather than silently lying.
        raise ValueError(
            f"stakes row {row['stake_id']!r} has unparseable created_at"
        )
    return Stake(
        stake_id=row["stake_id"],
        session_id=row["session_id"],
        staker_id=row["staker_id"],
        staker_kind=row["staker_kind"],
        borrower_id=row["borrower_id"],
        borrower_kind=row["borrower_kind"],
        format=row["format"],
        principal=int(row["principal"]),
        match_amount=int(row["match_amount"]),
        origination_fee=int(row["origination_fee"]),
        cut=float(row["cut"]),
        status=row["status"],
        carry_amount=int(row["carry_amount"]),
        stake_tier=row["stake_tier"],
        created_at=created_at,
        settled_at=_parse_timestamp(row["settled_at"]),
    )
