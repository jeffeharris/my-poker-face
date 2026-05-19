"""One-shot data migration: `active_loan_*` columns → `stakes` rows.

Walks every `player_bankroll_state` row with `active_loan_amount > 0`
and produces an equivalent `stakes` row. After this migration runs,
the new Phase 1 settlement path (Commit 4) can find existing in-flight
loans in the new persistence surface.

Two outcomes per row:
  - **Active session present.** Caller's `resolve_active_session`
    returns `(session_id, stake_tier)` → stake row gets
    `status='active'`, `session_id` = the live cash game id, terms
    transferred verbatim. The new settlement code processes it on
    leave-table like any other active stake.
  - **No active session.** Defensive — shouldn't normally happen
    post-leave because settlement zeros the `active_loan_*` fields.
    The row is treated as a stranded carry: `status='carry'`,
    `carry_amount = active_loan_amount`, `session_id` set to a
    synthetic `_orphan_<player_id>` so the row is uniquely keyed and
    the borrower's carry-load math (Phase 2) picks it up.

Idempotent via deterministic `stake_id = f"migrated_v98_{player_id}"`.
Re-runs hit a primary-key conflict on rows already migrated and skip
silently. Callers must NOT reset `active_loan_*` columns after this
runs — the columns stay in place through Phase 1 as a safety net and
are dropped in Phase 2 once the new settlement is live.

Spec: `docs/plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md` Phase 1 Commit 3.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from cash_mode.stakes import (
    BORROWER_KIND_HUMAN,
    STAKE_FORMAT_HOUSE,
    STAKE_FORMAT_PURE,
    STAKE_STATUS_ACTIVE,
    STAKE_STATUS_CARRY,
    STAKER_KIND_HOUSE,
    STAKER_KIND_PERSONALITY,
    Stake,
)

logger = logging.getLogger(__name__)


# Stable tier label used for stranded carries that have no live cash
# session to look up the tier from. The Phase 2 tier-resolution code
# treats unknown labels as the lowest tier when computing carry caps;
# this is the conservative default for an orphan row.
UNKNOWN_STAKE_TIER = "unknown"


@dataclass(frozen=True)
class MigrationResult:
    """Summary of a `migrate_active_loans_to_stakes` run.

    Counters are returned (not logged-only) so callers can assert on
    them in tests and surface them in admin views.
    """

    active_created: int = 0
    carry_created: int = 0
    skipped_existing: int = 0
    errors: int = 0


# Caller-supplied function signature:
#   resolve_active_session(player_id) -> Optional[(session_id, stake_tier)]
# Returning None means no active cash session for that player; the
# migration treats the row as a stranded carry. Returning a tuple
# means the player is currently sitting at the named session at the
# named tier; the migration produces an active stake row.
ResolveActiveSession = Callable[[str], Optional[Tuple[str, str]]]


def migrate_active_loans_to_stakes(
    *,
    bankroll_repo,
    stake_repo,
    resolve_active_session: ResolveActiveSession,
    now: Optional[datetime] = None,
) -> MigrationResult:
    """Convert legacy `active_loan_*` rows into `stakes` rows.

    Args:
        bankroll_repo: BankrollRepository — source of the legacy rows.
        stake_repo: StakeRepository — destination.
        resolve_active_session: Caller-provided lookup. Returns
            `(session_id, stake_tier)` for an in-flight session, or
            None when the player has no active session (orphan path).
            Splitting this out lets the migration stay decoupled from
            the cash_game_id / state_machine plumbing — tests pass a
            fake; production wires it through `game_state_service`.
        now: timestamp stamped onto `created_at`. Defaults to
            `datetime.utcnow()`.

    Returns:
        `MigrationResult` with per-bucket counts.

    Failures on individual rows log a warning and increment `errors`
    rather than aborting the run — the migration should be best-effort
    so one bad row doesn't strand the rest.
    """
    if now is None:
        now = datetime.utcnow()

    result = MigrationResult()
    legacy_rows = bankroll_repo.iter_player_bankrolls_with_active_loan()
    logger.info(
        "[STAKE_MIGRATION] found %d legacy active_loan_* rows",
        len(legacy_rows),
    )

    for bankroll in legacy_rows:
        stake_id = _stake_id_for_player(bankroll.player_id)

        # Idempotency: skip if a row with this synthetic id is already
        # present. Re-running the migration is safe.
        if stake_repo.load_stake(stake_id) is not None:
            result = _bump(result, skipped_existing=1)
            continue

        try:
            stake = _build_stake_for_legacy_row(
                bankroll=bankroll,
                resolve_active_session=resolve_active_session,
                now=now,
            )
        except Exception as e:
            logger.warning(
                "[STAKE_MIGRATION] failed to build stake for player_id=%r: %s",
                bankroll.player_id, e,
            )
            result = _bump(result, errors=1)
            continue

        try:
            stake_repo.create_stake(stake)
        except sqlite3.IntegrityError:
            # PK conflict — a row landed between the load_stake check
            # above and the create. Treat as "already migrated."
            result = _bump(result, skipped_existing=1)
            continue
        except Exception as e:
            logger.warning(
                "[STAKE_MIGRATION] create_stake failed for player_id=%r: %s",
                bankroll.player_id, e,
            )
            result = _bump(result, errors=1)
            continue

        if stake.status == STAKE_STATUS_ACTIVE:
            result = _bump(result, active_created=1)
        else:
            result = _bump(result, carry_created=1)

    logger.info(
        "[STAKE_MIGRATION] done: active=%d carry=%d skipped=%d errors=%d",
        result.active_created, result.carry_created,
        result.skipped_existing, result.errors,
    )
    return result


def _stake_id_for_player(player_id: str) -> str:
    """Deterministic stake_id so re-runs are idempotent.

    Lives outside `Stake` because the id-shape is a migration concern,
    not a stake-model concern. Other callers create stakes via uuid4.
    """
    return f"migrated_v98_{player_id}"


def _build_stake_for_legacy_row(
    *,
    bankroll,
    resolve_active_session: ResolveActiveSession,
    now: datetime,
) -> Stake:
    """Translate one legacy bankroll row into a `Stake` instance."""
    lender_id = bankroll.active_loan_lender_id
    if lender_id is None:
        staker_kind = STAKER_KIND_HOUSE
        format = STAKE_FORMAT_HOUSE
    else:
        staker_kind = STAKER_KIND_PERSONALITY
        format = STAKE_FORMAT_PURE

    # Translate floor (legacy multiplier on principal) + rate (cut on
    # post-floor remainder) into the stakes model's single `cut` field.
    # We can't perfectly preserve both — the stakes model collapses
    # them into one number. Best effort: use the legacy `active_loan_rate`
    # directly as `cut`. It already represents the staker's share of
    # winnings, just measured against the post-floor pool. For active
    # rows this is good enough because the new settlement code uses
    # `cut` against `net_winnings = chips_at_leave - principal`, which
    # is the cleaner abstraction the spec wants.
    cut = float(bankroll.active_loan_rate)

    session_resolution = resolve_active_session(bankroll.player_id)
    if session_resolution is not None:
        session_id, stake_tier = session_resolution
        return Stake(
            stake_id=_stake_id_for_player(bankroll.player_id),
            session_id=session_id,
            staker_id=lender_id,
            staker_kind=staker_kind,
            borrower_id=bankroll.player_id,
            borrower_kind=BORROWER_KIND_HUMAN,
            format=format,
            principal=int(bankroll.active_loan_amount),
            match_amount=0,
            origination_fee=0,
            cut=cut,
            status=STAKE_STATUS_ACTIVE,
            carry_amount=0,
            stake_tier=stake_tier,
            created_at=now,
        )

    # Orphan path — no active session. Treat the principal as a stranded
    # carry so the borrower's carry-load math (Phase 2) picks it up.
    return Stake(
        stake_id=_stake_id_for_player(bankroll.player_id),
        session_id=f"_orphan_{bankroll.player_id}",
        staker_id=lender_id,
        staker_kind=staker_kind,
        borrower_id=bankroll.player_id,
        borrower_kind=BORROWER_KIND_HUMAN,
        format=format,
        principal=int(bankroll.active_loan_amount),
        match_amount=0,
        origination_fee=0,
        cut=cut,
        status=STAKE_STATUS_CARRY,
        carry_amount=int(bankroll.active_loan_amount),
        stake_tier=UNKNOWN_STAKE_TIER,
        created_at=now,
        settled_at=now,  # the "settle" already happened back when they left
    )


def _bump(
    result: MigrationResult,
    *,
    active_created: int = 0,
    carry_created: int = 0,
    skipped_existing: int = 0,
    errors: int = 0,
) -> MigrationResult:
    """Return a new MigrationResult with the listed counters incremented."""
    return MigrationResult(
        active_created=result.active_created + active_created,
        carry_created=result.carry_created + carry_created,
        skipped_existing=result.skipped_existing + skipped_existing,
        errors=result.errors + errors,
    )
