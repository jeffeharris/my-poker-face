"""Chip-flow plumbing for stake creation + settlement (Phase 1 Commit 5).

Follows the `BankrollChange` pattern from `cash_mode/movement.py`
(lobby-seed leak fix, commit f04e048b): pure functions emit dataclass
lists describing chip transfers; callers apply them in the right
order. This keeps the chip-ledger audit's instrumentation easy to
reason about — every transfer that crosses bank state has a single
clear application site.

Three flow categories:

  - **Stake creation** (sit-down) — `build_stake_creation_flows(stake)`.
    Personality / human stakes: staker bankroll → borrower seat (pure
    transfer). House stakes: central_bank → borrower seat (ledger
    `house_stake_issue`). Match-share: borrower bankroll → borrower
    seat for the borrower's contribution. Pure stakes: borrower
    bankroll → staker bankroll for the origination fee.

  - **Stake settlement** (leave-table) — `build_stake_settlement_flows`
    Personality / human stakes: borrower seat → staker bankroll for
    `staker_total`; borrower seat → borrower bankroll for
    `borrower_total`. House stakes: borrower seat → central_bank
    (ledger `house_stake_settle`) for `staker_total`; the
    `forgive_balance` annotation is already fired by
    `settle_stake_on_leave` (passing `chip_ledger_repo` there).

  - **Application** — there is no central `apply_chip_flows`
    dispatcher (an earlier plan called for one post-Phase-2; it was
    never built). Production callers (the sit / leave paths in
    `cash_routes.py`) walk the emitted flow list and dispatch each
    entry to the right repo / ledger call inline; test code inspects
    the flow list directly. If a single dispatcher is ever wanted,
    centralize that inline walk here.

Spec: `docs/plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md` Phase 1 Commit 5.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from cash_mode.stake_settlement import StakeSettlement
from cash_mode.stakes import (
    STAKE_FORMAT_MATCH_SHARE,
    STAKE_FORMAT_PURE,
    STAKER_KIND_HOUSE,
    STAKER_KIND_HUMAN,
    STAKER_KIND_PERSONALITY,
    Stake,
)

logger = logging.getLogger(__name__)


# --- Flow directions (string literals — cross the pure-helper boundary) ---

# Stake creation
DIRECTION_STAKER_TO_BORROWER_SEAT = "staker_to_borrower_seat"
DIRECTION_BORROWER_BANKROLL_TO_SEAT = "borrower_bankroll_to_seat"  # match_share
DIRECTION_HOUSE_TO_BORROWER_SEAT = "house_to_borrower_seat"  # fires house_stake_issue
DIRECTION_BORROWER_TO_STAKER_BANKROLL = "borrower_to_staker_bankroll"  # origination_fee

# Stake settlement
DIRECTION_BORROWER_SEAT_TO_STAKER_BANKROLL = "borrower_seat_to_staker_bankroll"
DIRECTION_BORROWER_SEAT_TO_BORROWER_BANKROLL = "borrower_seat_to_borrower_bankroll"
DIRECTION_BORROWER_SEAT_TO_HOUSE = "borrower_seat_to_house"  # fires house_stake_settle


@dataclass(frozen=True)
class StakeChipFlow:
    """One chip transfer in a stake create-or-settle sequence.

    Generalization of `BankrollChange` for stake flows that involve
    two non-AI parties (staker + borrower). The legacy BankrollChange
    only tracks one actor (an AI personality_id); staking deals need
    both sides identified so the application step knows which seat
    receives chips and which bankroll loses them.

    Fields:
      - `direction`: one of the DIRECTION_* constants above.
      - `staker_id`: source/destination on the staker side. NULL for
        house staker (chips appear from / disappear to central_bank).
      - `staker_kind`: 'house' | 'personality' | 'human'. Drives the
        repo dispatch (AI bankroll vs player bankroll vs ledger).
      - `borrower_id`: source/destination on the borrower side.
      - `borrower_kind`: 'human' | 'personality'.
      - `amount`: non-negative integer.
      - `context`: optional dict merged into ledger context_json (for
        house flows). Production callers stamp game_id + stake_id.
    """

    direction: str
    staker_id: Optional[str]
    staker_kind: str
    borrower_id: str
    borrower_kind: str
    amount: int
    context: Dict[str, Any] = field(default_factory=dict)


def build_stake_creation_flows(stake: Stake) -> List[StakeChipFlow]:
    """Return the chip flows to apply at sit-down for a new stake.

    Order matters when the caller dispatches: the borrower seat is the
    only chip sink in stake creation; the staker side is the source.
    For `pure` stakes with a non-zero origination_fee, the fee flow is
    appended after the principal flow because the fee is a separate
    borrower-bankroll deduction that shouldn't be mixed with the seat
    funding.

    `match_share` adds the borrower's match contribution as a separate
    flow (their chips → their seat) since the source-vs-sink shape
    differs from the staker contribution.
    """
    flows: List[StakeChipFlow] = []
    common_ctx = {
        'stake_id': stake.stake_id,
        'session_id': stake.session_id,
        'stake_tier': stake.stake_tier,
        'format': stake.format,
    }

    if stake.staker_kind == STAKER_KIND_HOUSE:
        flows.append(
            StakeChipFlow(
                direction=DIRECTION_HOUSE_TO_BORROWER_SEAT,
                staker_id=None,
                staker_kind=STAKER_KIND_HOUSE,
                borrower_id=stake.borrower_id,
                borrower_kind=stake.borrower_kind,
                amount=stake.principal,
                context=common_ctx,
            )
        )
    elif stake.staker_kind in (STAKER_KIND_PERSONALITY, STAKER_KIND_HUMAN):
        if stake.staker_id is None:
            raise ValueError(
                f"stake {stake.stake_id!r} has staker_kind={stake.staker_kind!r} "
                "but staker_id is NULL"
            )
        flows.append(
            StakeChipFlow(
                direction=DIRECTION_STAKER_TO_BORROWER_SEAT,
                staker_id=stake.staker_id,
                staker_kind=stake.staker_kind,
                borrower_id=stake.borrower_id,
                borrower_kind=stake.borrower_kind,
                amount=stake.principal,
                context=common_ctx,
            )
        )
    else:
        raise ValueError(f"unknown staker_kind {stake.staker_kind!r}")

    if stake.format == STAKE_FORMAT_MATCH_SHARE and stake.match_amount > 0:
        flows.append(
            StakeChipFlow(
                direction=DIRECTION_BORROWER_BANKROLL_TO_SEAT,
                staker_id=stake.staker_id,
                staker_kind=stake.staker_kind,
                borrower_id=stake.borrower_id,
                borrower_kind=stake.borrower_kind,
                amount=stake.match_amount,
                context=common_ctx,
            )
        )

    if stake.format == STAKE_FORMAT_PURE and stake.origination_fee > 0:
        if stake.staker_id is None:
            # Origination fees on house stakes would be money to
            # central_bank, which isn't a borrower-bankroll → bank
            # destruction shape the audit covers. Reject explicitly
            # rather than silently misroute.
            raise ValueError(
                f"stake {stake.stake_id!r} is a house stake with non-zero "
                "origination_fee; house stakes can't take origination fees"
            )
        flows.append(
            StakeChipFlow(
                direction=DIRECTION_BORROWER_TO_STAKER_BANKROLL,
                staker_id=stake.staker_id,
                staker_kind=stake.staker_kind,
                borrower_id=stake.borrower_id,
                borrower_kind=stake.borrower_kind,
                amount=stake.origination_fee,
                context=common_ctx,
            )
        )

    return flows


def build_stake_settlement_flows(
    settlement: StakeSettlement,
) -> List[StakeChipFlow]:
    """Return the chip flows to apply when settling a stake.

    Drains the borrower's seat into two destinations: `staker_total`
    goes to the staker (or central_bank for house stakes); the
    remainder `borrower_total` goes back to the borrower's bankroll.

    House stakes: the `staker_total` flow fires `house_stake_settle`
    instead of crediting an AI / player bankroll. The
    `forgive_balance` annotation for any unrecovered principal is
    NOT emitted here — `settle_stake_on_leave` fires it directly
    when given a `chip_ledger_repo`. Keeping the annotation co-located
    with the row-mutation that decided to forgive avoids two code
    paths having to agree on the forgiven amount.
    """
    flows: List[StakeChipFlow] = []
    common_ctx = {
        'stake_id': settlement.stake_id,
        'session_id': settlement.session_id,
        'new_status': settlement.new_status,
    }

    if settlement.staker_total > 0:
        if settlement.staker_kind == STAKER_KIND_HOUSE:
            flows.append(
                StakeChipFlow(
                    direction=DIRECTION_BORROWER_SEAT_TO_HOUSE,
                    staker_id=None,
                    staker_kind=STAKER_KIND_HOUSE,
                    borrower_id=settlement.borrower_id,
                    borrower_kind=settlement.borrower_kind,
                    amount=settlement.staker_total,
                    context=common_ctx,
                )
            )
        else:
            flows.append(
                StakeChipFlow(
                    direction=DIRECTION_BORROWER_SEAT_TO_STAKER_BANKROLL,
                    staker_id=settlement.staker_id,
                    staker_kind=settlement.staker_kind,
                    borrower_id=settlement.borrower_id,
                    borrower_kind=settlement.borrower_kind,
                    amount=settlement.staker_total,
                    context=common_ctx,
                )
            )

    if settlement.borrower_total > 0:
        flows.append(
            StakeChipFlow(
                direction=DIRECTION_BORROWER_SEAT_TO_BORROWER_BANKROLL,
                staker_id=settlement.staker_id,
                staker_kind=settlement.staker_kind,
                borrower_id=settlement.borrower_id,
                borrower_kind=settlement.borrower_kind,
                amount=settlement.borrower_total,
                context=common_ctx,
            )
        )

    return flows
