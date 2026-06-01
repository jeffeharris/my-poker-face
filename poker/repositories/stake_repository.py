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
from typing import Dict, List, Optional

from cash_mode.staker_history import StakerHistoryStats
from cash_mode.stakes import (
    STAKE_STATUS_ACTIVE,
    STAKE_STATUS_CARRY,
    STAKE_STATUS_DEFAULTED,
    STAKE_STATUS_SETTLED,
    Stake,
)
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
                     created_at, settled_at,
                     staker_payout, borrower_payout, table_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    stake.staker_payout,
                    stake.borrower_payout,
                    stake.table_id,
                ),
            )

    def update_payouts(
        self,
        stake_id: str,
        *,
        staker_payout: int,
        borrower_payout: int,
    ) -> bool:
        """Capture settlement chip flows on a stake row.

        Called once by `settle_stake_on_leave` after the math runs but
        before the status transition is committed (carry vs settled).
        These values are what the Net Worth history surface reads to
        compute per-stake P&L from the staker / borrower POVs.

        Returns True if a row was updated. The fields are nullable in
        the schema (v106) so callers that don't carry settlement data
        (legacy pre-v106 paths) can omit this call without surfacing
        errors.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE stakes " "SET staker_payout = ?, borrower_payout = ? " "WHERE stake_id = ?",
                (int(staker_payout), int(borrower_payout), stake_id),
            )
            return cursor.rowcount > 0

    def load_stake(self, stake_id: str) -> Optional[Stake]:
        """Load one stake by id, or None if not found."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT stake_id, session_id, staker_id, staker_kind,
                       borrower_id, borrower_kind, format,
                       principal, match_amount, origination_fee, cut,
                       status, carry_amount, stake_tier,
                       created_at, settled_at, forgiveness_last_asked,
                       staker_payout, borrower_payout,
                       pending_forgiveness_ask, table_id
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
                       created_at, settled_at, forgiveness_last_asked,
                       staker_payout, borrower_payout,
                       pending_forgiveness_ask, table_id
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

    def load_active_for_borrower(
        self,
        borrower_id: str,
        borrower_kind: str,
    ) -> Optional[Stake]:
        """Load the borrower's single active stake, or None.

        Phase 4 Commit 3 needs this for AI borrowers: when an AI's
        movement decision triggers leave, we need to find their
        active stake row to settle it. Humans use
        `load_active_for_session(game_id)` because they have a
        canonical session_id; AI sessions have synthesized
        `ai_session_*` ids that aren't reconstructible from outside.

        The invariant from Phase 1 — one active stake per borrower at
        a time — makes this a single-row read. If multiple match
        (shouldn't happen), returns the most recently created.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT stake_id, session_id, staker_id, staker_kind,
                       borrower_id, borrower_kind, format,
                       principal, match_amount, origination_fee, cut,
                       status, carry_amount, stake_tier,
                       created_at, settled_at, forgiveness_last_asked,
                       staker_payout, borrower_payout,
                       pending_forgiveness_ask, table_id
                FROM stakes
                WHERE borrower_id = ? AND borrower_kind = ?
                  AND status = 'active'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (borrower_id, borrower_kind),
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
                       created_at, settled_at, forgiveness_last_asked,
                       staker_payout, borrower_payout,
                       pending_forgiveness_ask, table_id
                FROM stakes
                WHERE session_id = ?
                ORDER BY created_at ASC
                """,
                (session_id,),
            ).fetchall()
            return [_row_to_stake(r) for r in rows]

    def list_carries_for_borrower(
        self,
        borrower_id: str,
        borrower_kind: str,
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
                       created_at, settled_at, forgiveness_last_asked,
                       staker_payout, borrower_payout,
                       pending_forgiveness_ask, table_id
                FROM stakes
                WHERE borrower_id = ? AND borrower_kind = ?
                  AND status = ?
                ORDER BY created_at ASC
                """,
                (borrower_id, borrower_kind, STAKE_STATUS_CARRY),
            ).fetchall()
            return [_row_to_stake(r) for r in rows]

    def list_active_stakes_for_staker(self, staker_id: str) -> List[Stake]:
        """Return every active stake row this staker is funding.

        Phase 5 addition (2026-05-21) — Net Worth receivables surface
        the player's *in-flight* stakes alongside the residual carry
        debts. An active stake's `principal` represents chips currently
        on the borrower's seat that the staker has claim to at
        settlement; it's not "owed debt" but it IS a tracked position
        the player needs visibility into.

        Distinct from `list_carries_for_staker` which is settled-but-
        unrecovered chips. The two together form the full receivables
        picture from the staker's POV.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT stake_id, session_id, staker_id, staker_kind,
                       borrower_id, borrower_kind, format,
                       principal, match_amount, origination_fee, cut,
                       status, carry_amount, stake_tier,
                       created_at, settled_at, forgiveness_last_asked,
                       staker_payout, borrower_payout,
                       pending_forgiveness_ask, table_id
                FROM stakes
                WHERE staker_id = ? AND status = ?
                ORDER BY created_at ASC
                """,
                (staker_id, STAKE_STATUS_ACTIVE),
            ).fetchall()
            return [_row_to_stake(r) for r in rows]

    def list_recent_closed_for_owner(
        self,
        owner_id: str,
        *,
        limit: int = 20,
    ) -> List[Stake]:
        """Return recently closed stakes touching `owner_id` on either side.

        Phase 5 addition (2026-05-21). Surfaces a Net Worth history
        view for `settled` and `defaulted` rows where the player was
        either staker or borrower. `carry` rows are NOT included —
        those are still open positions and live in the active
        receivables/payables surfaces; this method is for resolved
        history.

        Ordered by `settled_at DESC` (most recent close first), capped
        at `limit` (default 20 — enough for a "recent activity" feel
        without scrolling fatigue). Older history is intentionally
        truncated; a future "see all" admin view could relax the cap.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT stake_id, session_id, staker_id, staker_kind,
                       borrower_id, borrower_kind, format,
                       principal, match_amount, origination_fee, cut,
                       status, carry_amount, stake_tier,
                       created_at, settled_at, forgiveness_last_asked,
                       staker_payout, borrower_payout,
                       pending_forgiveness_ask, table_id
                FROM stakes
                WHERE (staker_id = ? OR borrower_id = ?)
                  AND status IN ('settled', 'defaulted')
                ORDER BY settled_at DESC
                LIMIT ?
                """,
                (owner_id, owner_id, int(limit)),
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
                       created_at, settled_at, forgiveness_last_asked,
                       staker_payout, borrower_payout,
                       pending_forgiveness_ask, table_id
                FROM stakes
                WHERE staker_id = ? AND status = ?
                ORDER BY created_at ASC
                """,
                (staker_id, STAKE_STATUS_CARRY),
            ).fetchall()
            return [_row_to_stake(r) for r in rows]

    def update_status(
        self,
        stake_id: str,
        status: str,
        *,
        settled_at: Optional[datetime] = None,
        expected_status: Optional[str] = None,
    ) -> bool:
        """Transition a stake to a new status, optionally stamping
        `settled_at`.

        Returns True if a row was updated, False if no such stake.
        Callers should typically pair this with `update_carry_amount`
        when transitioning to 'carry' — the two columns are independent
        on purpose so the explicit-default action (Phase 2) can zero
        `carry_amount` and flip `status='defaulted'` in one call site
        without forcing settled_at semantics.

        When `expected_status` is given the UPDATE is a compare-and-swap:
        it only fires (and returns True) if the row is currently in that
        status. This lets a caller atomically *claim* a transition —
        e.g. settle a 'carry' stake — so two concurrent requests can't
        both pass a read-then-write check and double-move chips. The
        loser sees False and must not perform the side effect.
        """
        with self._get_connection() as conn:
            where = "WHERE stake_id = ?"
            tail_params: tuple = (stake_id,)
            if expected_status is not None:
                where += " AND status = ?"
                tail_params = (stake_id, expected_status)
            if settled_at is not None:
                cursor = conn.execute(
                    f"UPDATE stakes SET status = ?, settled_at = ? {where}",
                    (status, settled_at.isoformat(), *tail_params),
                )
            else:
                cursor = conn.execute(
                    f"UPDATE stakes SET status = ? {where}",
                    (status, *tail_params),
                )
            return cursor.rowcount > 0

    def sum_active_principal_for_humans(self) -> int:
        """Sum (principal + match_amount) across active stakes to humans.

        Drives the chip-ledger audit's `active_loans_principal` term —
        chips currently sitting on a human session seat from an active
        stake. Other surfaces (AI bankrolls, AI seats, live AI stacks)
        already capture chips on those ends; humans need this explicit
        sum because `_sum_live_session_ai_stacks` filters humans out.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(principal + match_amount), 0)
                FROM stakes
                WHERE status = 'active' AND borrower_kind = 'human'
                """
            ).fetchone()
            return int(row[0] or 0)

    def aggregate_receivables_by_staker(self) -> Dict[str, int]:
        """Per-staker receivable totals (chips others owe / hold for them).

        Drives the admin net-worth view's `receivable` column. For each
        `staker_id`, sums two surfaces in one pass:

          * active stakes: `principal + match_amount` — chips currently on
            a borrower's seat the staker has claim to at settlement.
          * carry stakes: `carry_amount` — settled-but-unrecovered debt
            still owed back to the staker.

        Global (the `stakes` table has no `sandbox_id`). House stakes
        (`staker_id IS NULL`) are excluded — they have no entity to credit.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    staker_id,
                    SUM(CASE WHEN status = ?
                        THEN principal + match_amount ELSE 0 END)
                  + SUM(CASE WHEN status = ?
                        THEN carry_amount ELSE 0 END) AS receivable
                FROM stakes
                WHERE staker_id IS NOT NULL
                GROUP BY staker_id
                """,
                (STAKE_STATUS_ACTIVE, STAKE_STATUS_CARRY),
            ).fetchall()
        return {row['staker_id']: int(row['receivable'] or 0) for row in rows}

    def aggregate_outstanding_by_borrower(self) -> Dict[str, int]:
        """Per-borrower outstanding carry debt (chips the entity owes).

        Drives the admin net-worth view's `outstanding` column — the
        residual `carry_amount` a borrower still owes across all their
        carry rows. Global (no `sandbox_id`). Only `carry` rows carry
        residual debt; active stakes are the staker's claim, not the
        borrower's liability, and settled/defaulted rows are closed.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT borrower_id, SUM(carry_amount) AS outstanding
                FROM stakes
                WHERE status = ?
                GROUP BY borrower_id
                """,
                (STAKE_STATUS_CARRY,),
            ).fetchall()
        return {row['borrower_id']: int(row['outstanding'] or 0) for row in rows}

    def aggregate_staking_pnl_by_staker(self) -> Dict[str, int]:
        """Per-staker realized P&L from backing others (signed).

        `pnl = SUM(staker_payout - principal + origination_fee)` over the
        staker's CLOSED stakes — `settled` and `defaulted` only. Drives the
        admin net-worth view's "Staking" column.

        Open `carry` stakes are excluded: their outcome is still pending
        and their not-yet-recovered value is already a receivable (Recv /
        net worth), so scoring them as a loss would double-count. Legacy
        rows without a recorded `staker_payout` (pre-v106) can't be scored
        and are skipped via the `IS NOT NULL` guard. House stakes
        (`staker_id IS NULL`) are excluded — they have no entity to credit.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT staker_id,
                       SUM(staker_payout - principal + origination_fee) AS pnl
                FROM stakes
                WHERE staker_id IS NOT NULL
                  AND staker_payout IS NOT NULL
                  AND status IN (?, ?)
                GROUP BY staker_id
                """,
                (STAKE_STATUS_SETTLED, STAKE_STATUS_DEFAULTED),
            ).fetchall()
        return {row['staker_id']: int(row['pnl'] or 0) for row in rows}

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

    def get_active_personality_participants(self) -> List[str]:
        """Personality_ids currently in any active stake.

        Returns AI participants on either side — borrower or staker —
        of an active (non-settled, non-carry) stake. Phase 4's lobby
        glyph uses the result to annotate AI seats currently in a
        live stake position. Returns a list (not a set) so callers
        can pass it directly to UI serializers; dedup is done
        server-side via SQL UNION.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT borrower_id FROM stakes
                  WHERE status = 'active' AND borrower_kind = 'personality'
                UNION
                SELECT staker_id FROM stakes
                  WHERE status = 'active' AND staker_kind = 'personality'
                """
            ).fetchall()
            return [row[0] for row in rows if row[0]]

    def aggregate_history_for_staker(
        self,
        staker_id: str,
    ) -> Dict[str, StakerHistoryStats]:
        """Return per-borrower outcome counts for every borrower this
        staker has interacted with.

        Used by the lobby's weighted AI-staker selection (the matching
        weight reflects each candidate's lived history with the busting
        borrower — see `cash_mode/staker_history.py`). Single SQL
        aggregate so the cost is one query per staker regardless of
        history depth.

        Only `settled`, `carry`, and `defaulted` rows count — `active`
        stakes don't yet have an outcome to score. House stakes
        (`staker_id IS NULL`) are excluded naturally by the equality
        comparison.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT borrower_id, status, COUNT(*) AS n
                FROM stakes
                WHERE staker_id = ?
                  AND status IN (?, ?, ?)
                GROUP BY borrower_id, status
                """,
                (
                    staker_id,
                    STAKE_STATUS_SETTLED,
                    STAKE_STATUS_CARRY,
                    STAKE_STATUS_DEFAULTED,
                ),
            ).fetchall()
        by_borrower: Dict[str, Dict[str, int]] = {}
        for row in rows:
            by_borrower.setdefault(row["borrower_id"], {})[row["status"]] = int(row["n"])
        return {
            bid: StakerHistoryStats(
                settled_count=counts.get(STAKE_STATUS_SETTLED, 0),
                carry_count=counts.get(STAKE_STATUS_CARRY, 0),
                defaulted_count=counts.get(STAKE_STATUS_DEFAULTED, 0),
            )
            for bid, counts in by_borrower.items()
        }

    def mark_forgiveness_asked(
        self,
        stake_id: str,
        asked_at: datetime,
    ) -> bool:
        """Stamp the most-recent forgiveness-ask timestamp on a stake.

        Used by POST /api/cash/stakes/<id>/request-forgiveness to
        rate-limit asks at one per stake per 24 hours (locked decision
        spec'd in Phase 3 Commit 3). Stamped on BOTH the granted and
        refused paths so back-to-back attempts can't accidentally
        cross the relationship-axes threshold via lucky timing.

        Returns True if a row was updated, False if no such stake.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE stakes SET forgiveness_last_asked = ? " "WHERE stake_id = ?",
                (asked_at.isoformat(), stake_id),
            )
            return cursor.rowcount > 0

    def update_pending_forgiveness_ask(
        self,
        stake_id: str,
        asked_at: Optional[datetime],
    ) -> bool:
        """Set or clear the pending-forgiveness-ask timestamp.

        Pass a datetime to stamp the ask (AI requesting forgiveness
        from a human staker); pass None to clear it (user grants or
        refuses, or carry transitions out of 'carry' status). The
        column is the single source-of-truth for "AI is waiting on
        the player" — the lobby badge counts rows where this is
        non-NULL AND status='carry' AND staker_kind='human'.

        Returns True if a row was updated, False if no such stake.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE stakes SET pending_forgiveness_ask = ? " "WHERE stake_id = ?",
                (asked_at.isoformat() if asked_at is not None else None, stake_id),
            )
            return cursor.rowcount > 0

    def list_pending_forgiveness_for_staker(
        self,
        staker_id: str,
    ) -> List[Stake]:
        """Return carry rows where this human staker has a pending ask.

        Drives the wallet-badge count + the NetWorthDrawer's
        Forgiveness Requests section. Filters to `staker_kind='human'`
        explicitly so a future AI-staker pending-ask flow (if added)
        wouldn't accidentally leak into player-facing surfaces.
        Returns oldest-pending first so longstanding asks surface at
        the top of the list.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT stake_id, session_id, staker_id, staker_kind,
                       borrower_id, borrower_kind, format,
                       principal, match_amount, origination_fee, cut,
                       status, carry_amount, stake_tier,
                       created_at, settled_at, forgiveness_last_asked,
                       staker_payout, borrower_payout,
                       pending_forgiveness_ask, table_id
                FROM stakes
                WHERE staker_id = ?
                  AND staker_kind = 'human'
                  AND status = ?
                  AND pending_forgiveness_ask IS NOT NULL
                ORDER BY pending_forgiveness_ask ASC
                """,
                (staker_id, STAKE_STATUS_CARRY),
            ).fetchall()
            return [_row_to_stake(r) for r in rows]


def _row_to_stake(row) -> Stake:
    """Build a `Stake` from a `stakes` row."""
    created_at = _parse_timestamp(row["created_at"])
    if created_at is None:
        # `created_at` is NOT NULL in schema, so a None here means a
        # malformed row — surface loudly rather than silently lying.
        raise ValueError(f"stakes row {row['stake_id']!r} has unparseable created_at")
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
        forgiveness_last_asked=_parse_timestamp(row["forgiveness_last_asked"]),
        pending_forgiveness_ask=_parse_timestamp(row["pending_forgiveness_ask"]),
        staker_payout=(int(row["staker_payout"]) if row["staker_payout"] is not None else None),
        borrower_payout=(
            int(row["borrower_payout"]) if row["borrower_payout"] is not None else None
        ),
        table_id=row["table_id"],
    )
