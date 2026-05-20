"""Leave-time settlement math for stakes (Phase 1 Commit 4).

Replaces `cash_mode/loan_settlement.py:settle_loan_on_leave` for the
new stakes-table-backed model. The legacy module stays in place
through Phase 1 as a safety net; Commit 5 wires this function into
the route + ledger plumbing.

The single active stake for a session (one stake per session by
design) settles at leave-table:

  net_winnings = chips_at_leave - principal - match_amount

When `net_winnings >= 0` (clean settle):
  staker_total   = principal + cut × net_winnings
  borrower_total = match_amount + (1 - cut) × net_winnings
  status         = 'settled'

When `net_winnings < 0` AND `chips_at_leave > 0` (partial carry):
  staker_total   = min(chips_at_leave, principal)
  borrower_total = max(0, chips_at_leave - staker_total)
  carry_amount   = principal - staker_total
  status         = 'carry'

When `chips_at_leave == 0` (full bust → full carry):
  staker_total   = 0
  borrower_total = 0
  carry_amount   = principal
  status         = 'carry'

The function updates the stake row in place (via `stake_repo`) and
returns a `StakeSettlement` dataclass carrying the chip flows so the
caller can apply them to bankrolls / ledger. Staying pure on the
bankroll side keeps the chip-ledger audit's instrumentation easy to
wire — Commit 5 handles the dispatch:
  - house staker → ledger `house_stake_settle` + `forgive_balance`
  - personality staker → pure transfer to lender's bankroll
  - human staker (Phase 5) → pure transfer to player's bankroll

The `staker_id` and `staker_kind` on the returned settlement carry
exactly what the caller needs to dispatch; this function doesn't take
on that complexity.

Spec: `docs/plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md` Phase 1 Commit 4.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from cash_mode.stakes import (
    STAKE_STATUS_ACTIVE,
    STAKE_STATUS_CARRY,
    STAKE_STATUS_SETTLED,
    STAKER_KIND_HOUSE,
    Stake,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StakeSettlement:
    """Result of settling one stake at leave-table.

    Pure data — no chip flows have been applied yet. The caller takes
    `staker_total` and `borrower_total` and routes them to the
    appropriate bankrolls. `forgiven_amount` is informational: it's
    the principal that couldn't be recovered (always = carry_amount,
    but named for the audit's reading — the chips are still in the
    universe, they're just lost to the table via gameplay).

    `new_status` is the stake's new status — already persisted to the
    DB by `settle_stake_on_leave` before this dataclass is returned.

    `staker_id` and `staker_kind` are carried through so the route
    layer can dispatch chip routing without re-loading the stake row.
    """

    stake_id: str
    session_id: str
    staker_id: Optional[str]
    staker_kind: str
    borrower_id: str
    borrower_kind: str
    new_status: str
    staker_total: int
    borrower_total: int
    carry_amount: int
    forgiven_amount: int


def settle_stake_on_leave(
    stake_id: str,
    chips_at_leave: int,
    *,
    stake_repo,
    chip_ledger_repo=None,
    ledger_context: Optional[dict] = None,
    sandbox_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[StakeSettlement]:
    """Apply leave-time settlement to the single active stake.

    Loads the stake, computes the chip-flow math, persists the new
    status (+ settled_at) + carry_amount via `stake_repo`, and returns
    the StakeSettlement for the caller to act on.

    House-stake special case (locked decision #3): house stakes never
    carry. When the math says 'carry' for a house stake, the status
    flips to 'settled' (carry_amount stays 0) and — if a
    `chip_ledger_repo` is provided — a `forgive_balance` annotation
    fires for the unrecovered principal. The annotation is amount=0
    (the chips are still in the universe, redistributed to other
    seats via gameplay) and the `forgiven_principal` lives in
    `context_json` so the audit's house-stake reconciliation works.

    Returns None if the stake doesn't exist OR isn't active — the
    caller is responsible for finding the right `stake_id` (typically
    via `stake_repo.load_active_for_session(session_id)`) and feeding
    it in.

    Args:
        stake_id: Stake to settle.
        chips_at_leave: Borrower's table stack at the leave moment.
        stake_repo: Repository for the update.
        chip_ledger_repo: Optional. When provided AND the stake is a
            house bust, fires the `forgive_balance` annotation. Other
            stake kinds never touch the ledger from this function;
            their chip flows route between bankrolls (pure non-bank
            transfers, no entry needed for audit math).
        ledger_context: Optional dict merged into the
            `forgive_balance` annotation's context_json for tracing
            (e.g., `{'game_id': ..., 'site': 'leave_table'}`).
        now: Settlement timestamp. Defaults to `datetime.utcnow()`.
    """
    if now is None:
        now = datetime.utcnow()

    stake = stake_repo.load_stake(stake_id)
    if stake is None:
        logger.warning(
            "[STAKE] settle_stake_on_leave called for missing stake_id=%r",
            stake_id,
        )
        return None
    if stake.status != STAKE_STATUS_ACTIVE:
        # Already settled / carried / defaulted — idempotent no-op
        # protects against double-settle on a retry path.
        logger.warning(
            "[STAKE] settle_stake_on_leave stake_id=%r has non-active status %r; "
            "skipping settlement",
            stake_id, stake.status,
        )
        return None

    chips_at_leave = max(0, int(chips_at_leave))
    math = _compute_chip_flows(stake, chips_at_leave)

    # House-stake override: never carry; forgive instead.
    if (
        stake.staker_kind == STAKER_KIND_HOUSE
        and math.new_status == STAKE_STATUS_CARRY
    ):
        # Preserve the forgiven_amount the kernel computed; flip the
        # row state so the audit's outstanding-house-stake math
        # ((house_stake_issue - house_stake_settle - forgive_balance.context.forgiven_principal))
        # reconciles. The chips themselves are still in other seats.
        math = _Math(
            new_status=STAKE_STATUS_SETTLED,
            staker_total=math.staker_total,
            borrower_total=math.borrower_total,
            carry_amount=0,
            forgiven_amount=math.forgiven_amount,
        )
        if chip_ledger_repo is not None and math.forgiven_amount > 0:
            _emit_forgive_balance(
                chip_ledger_repo=chip_ledger_repo,
                stake=stake,
                forgiven_amount=math.forgiven_amount,
                chips_at_leave=chips_at_leave,
                ledger_context=ledger_context,
                sandbox_id=sandbox_id,
            )

    # Persist the new status + carry_amount in one logical step. The
    # repo methods don't ship in a single transaction here (separate
    # UPDATEs) because the BaseRepository's connection scope handles
    # commit on clean exit — SQLite still runs them as one connection.
    if math.new_status == STAKE_STATUS_CARRY:
        stake_repo.update_carry_amount(stake_id, math.carry_amount)
        stake_repo.update_status(stake_id, STAKE_STATUS_CARRY, settled_at=now)
    else:
        # Clean settle (or house-forgive override). carry_amount stays
        # at 0 (the default on the active row); just transition status
        # and stamp settled_at.
        stake_repo.update_status(stake_id, STAKE_STATUS_SETTLED, settled_at=now)

    return StakeSettlement(
        stake_id=stake.stake_id,
        session_id=stake.session_id,
        staker_id=stake.staker_id,
        staker_kind=stake.staker_kind,
        borrower_id=stake.borrower_id,
        borrower_kind=stake.borrower_kind,
        new_status=math.new_status,
        staker_total=math.staker_total,
        borrower_total=math.borrower_total,
        carry_amount=math.carry_amount,
        forgiven_amount=math.forgiven_amount,
    )


def _emit_forgive_balance(
    *,
    chip_ledger_repo,
    stake: Stake,
    forgiven_amount: int,
    chips_at_leave: int,
    ledger_context: Optional[dict],
    sandbox_id: Optional[str] = None,
) -> None:
    """Fire the `forgive_balance` ledger annotation for a house bust.

    Lazy-imported to avoid making `core.economy.ledger` a hard
    dependency of this pure-math module — the import only happens on
    the rare bust path where we actually annotate.
    """
    from core.economy import ledger as chip_ledger

    ctx = {'site': 'settle_stake_on_leave', 'stake_id': stake.stake_id}
    if ledger_context:
        ctx.update(ledger_context)
    ctx.update({
        'principal': stake.principal,
        'chips_at_leave': chips_at_leave,
        'stake_tier': stake.stake_tier,
        'sandbox_id': sandbox_id,
    })
    chip_ledger.record_forgive_balance(
        chip_ledger_repo,
        owner_id=stake.borrower_id,
        forgiven_principal=int(forgiven_amount),
        context=ctx,
        sandbox_id=sandbox_id,
    )


# --- internals ---


@dataclass(frozen=True)
class _Math:
    """Internal value-type for the math output before we wrap it in the
    public StakeSettlement (which also carries identity fields).
    Keeps `_compute_chip_flows` testable in isolation without faking
    a full Stake."""

    new_status: str
    staker_total: int
    borrower_total: int
    carry_amount: int
    forgiven_amount: int


def _compute_chip_flows(stake: Stake, chips_at_leave: int) -> _Math:
    """The pure-math kernel. No side effects."""
    principal = int(stake.principal)
    match_amount = int(stake.match_amount)
    cut = float(stake.cut)
    invested = principal + match_amount

    # Full bust path — borrower walked away with nothing. Whole
    # principal becomes carry; no chips for the staker. match_amount
    # is also lost from the borrower's side (their contribution went
    # to the table and didn't come back).
    if chips_at_leave <= 0:
        return _Math(
            new_status=STAKE_STATUS_CARRY,
            staker_total=0,
            borrower_total=0,
            carry_amount=principal,
            forgiven_amount=principal,
        )

    net_winnings = chips_at_leave - invested
    if net_winnings >= 0:
        # Clean settle: split upside per `cut`.
        staker_winnings_cut = int(net_winnings * cut)
        staker_total = principal + staker_winnings_cut
        borrower_total = match_amount + (net_winnings - staker_winnings_cut)
        return _Math(
            new_status=STAKE_STATUS_SETTLED,
            staker_total=staker_total,
            borrower_total=borrower_total,
            carry_amount=0,
            forgiven_amount=0,
        )

    # Partial-bust carry path: chips on the table fell short of the
    # combined investment. Staker recovers first (their principal is
    # the protected layer), borrower gets whatever's left.
    staker_total = min(chips_at_leave, principal)
    borrower_total = max(0, chips_at_leave - staker_total)
    carry_amount = principal - staker_total
    return _Math(
        new_status=STAKE_STATUS_CARRY,
        staker_total=staker_total,
        borrower_total=borrower_total,
        carry_amount=carry_amount,
        forgiven_amount=carry_amount,
    )
