"""Leave-time settlement math for sponsor loans.

When a player with an active sponsor loan leaves a cash table, three
amounts get computed from `chips_at_table`:

  floor       = int(loan_amount * loan_floor)   # what must be repaid
                                                # before any split
  to_floor    = min(floor, chips_at_table)      # paid to sponsor
                                                # toward floor
  remaining   = chips_at_table - to_floor       # post-floor pool
  sponsor_cut = int(remaining * loan_rate)      # sponsor's share of
                                                # the rest
  to_player   = remaining - sponsor_cut         # back to bankroll

Edge cases (v1):
  - `chips_at_table == 0` → sponsor gets 0, player gets 0, loan is
    forgiven (no reputation hit yet).
  - `chips_at_table < floor` → entire stack goes to sponsor, balance
    of the floor is forgiven.
  - `loan_amount == 0` → no loan path; full stack returns to bankroll.

Loan fields always reset to 0/0.0/0.0 on the returned bankroll, by
design — loans are session-scoped in v1.

Path B extension: when `active_loan_lender_id` is set on the bankroll
AND a `bankroll_repo` is passed, `sponsor_total` credits back to the
named AI lender's persistent bankroll (clamped to their cap). NULL
lender_id (anonymous house loan) routes sponsor_total to the ether,
same as v1 sponsorship.

Spec: `docs/plans/CASH_MODE_SPONSORSHIP_HANDOFF.md` §"Leave-time math",
extended by `docs/plans/CASH_MODE_PATH_B_HANDOFF.md` §B.5.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from cash_mode.bankroll import PlayerBankrollState, credit_ai_cash_out

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoanSettlement:
    """Result of applying leave-time loan math.

    `new_bankroll` carries the post-settlement chips and zero'd loan
    fields. `sponsor_total` and `returned_chips` are returned
    separately so the route can echo a clean receipt back to the
    frontend (e.g., "your $800 stack: $520 to sponsor, $280 to your
    bankroll").
    """

    new_bankroll: PlayerBankrollState
    sponsor_total: int
    returned_chips: int


def classify_loan_outcome(
    bankroll: PlayerBankrollState,
    chips_at_table: int,
) -> str:
    """Categorize the settlement outcome for relationship-event emission.

    Returns one of:
      - "no_loan": there was no active loan; no event needs firing.
      - "no_chips_no_event": player walked away with 0 chips at the
        table; loan is forgiven by the v1 rule, no reputation hit
        (matches the "busted, sponsor gets 0" branch).
      - "repaid": chips_at_table covered the full floor; the lender
        was made whole (and possibly took a cut on the upside) — fire
        STAKE_REPAID.
      - "defaulted": chips_at_table fell short of the floor; the
        lender is short — fire STAKE_DEFAULTED.

    The route uses this label to know which RelationshipEvent to emit.
    Distinct from the math (`sponsor_total`, `returned_chips`); the
    classification is a separate concern. Spec: Path B handoff §B.4.
    """
    if bankroll.active_loan_amount <= 0:
        return "no_loan"
    if chips_at_table <= 0:
        # v1 rule: no chips, no event — defer the edge case.
        return "no_chips_no_event"
    floor = int(bankroll.active_loan_amount * bankroll.active_loan_floor)
    if chips_at_table >= floor:
        return "repaid"
    return "defaulted"


def settle_loan_on_leave(
    bankroll: PlayerBankrollState,
    chips_at_table: int,
    *,
    bankroll_repo=None,
    chip_ledger_repo=None,
    ledger_context: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> LoanSettlement:
    """Apply the loan-floor + sponsor-cut math; return new bankroll.

    Pure-ish function: does the math purely, and (Path B) optionally
    fires the side effect of crediting the AI lender's bankroll when
    `active_loan_lender_id` is set AND `bankroll_repo` is provided.
    Caller saves the returned `new_bankroll`.

    Safe to call with no active loan — the no-loan branch just routes
    chips_at_table back to bankroll verbatim, no side effects.

    `bankroll_repo` is optional so legacy callers (tests, anonymous
    house loans) can omit it and skip AI-lender credit. When the
    lender_id is set but `bankroll_repo` is None, a warning logs and
    the credit is skipped — defensive: indicates a caller forgot to
    wire the seam.

    `now` defaults to `datetime.utcnow()` — explicit `now` lets tests
    pin the AI bankroll's `last_regen_tick` for stable assertions.
    """
    if bankroll.active_loan_amount <= 0:
        new_chips = bankroll.chips + chips_at_table
        return LoanSettlement(
            new_bankroll=PlayerBankrollState(
                player_id=bankroll.player_id,
                chips=new_chips,
                starting_bankroll=bankroll.starting_bankroll,
            ),
            sponsor_total=0,
            returned_chips=chips_at_table,
        )

    floor = int(bankroll.active_loan_amount * bankroll.active_loan_floor)
    to_floor = min(floor, chips_at_table)
    remaining = chips_at_table - to_floor
    sponsor_cut = int(remaining * bankroll.active_loan_rate)
    to_player = remaining - sponsor_cut
    sponsor_total = to_floor + sponsor_cut

    # Path B: credit the AI lender's bankroll when a personality_id
    # is pinned. The clamp-to-cap rule is the same one Path A's
    # cash-out helper enforces — winnings above cap evaporate.
    if bankroll.active_loan_lender_id and sponsor_total > 0:
        if bankroll_repo is not None:
            credit_ai_cash_out(
                bankroll_repo,
                bankroll.active_loan_lender_id,
                sponsor_total,
                now=now,
                chip_ledger_repo=chip_ledger_repo,
                ledger_context=ledger_context,
            )
        else:
            logger.warning(
                "[CASH] settle_loan_on_leave skipped AI credit for lender %r "
                "(sponsor_total=%d) — no bankroll_repo provided",
                bankroll.active_loan_lender_id, sponsor_total,
            )

    # Path A: house-archetype stake. sponsor_total goes back to the
    # bank (chips leave the universe). If chips_at_table < floor,
    # the remaining principal was effectively forgiven (those chips
    # were lost in play to other AI seats; they still exist in the
    # universe, just not in borrower or bank). Annotate so the audit
    # endpoint can reconcile house_stake_issue against the actual
    # outstanding principal.
    if not bankroll.active_loan_lender_id and chip_ledger_repo is not None:
        from core.economy import ledger as chip_ledger
        ctx = {'site': 'settle_loan_on_leave'}
        if ledger_context:
            ctx.update(ledger_context)
        if sponsor_total > 0:
            chip_ledger.record_house_stake_settle(
                chip_ledger_repo,
                owner_id=bankroll.player_id,
                amount=sponsor_total,
                context=dict(ctx, loan_amount=bankroll.active_loan_amount),
            )
        forgiven = max(0, floor - chips_at_table)
        if forgiven > 0:
            chip_ledger.record_forgive_balance(
                chip_ledger_repo,
                owner_id=bankroll.player_id,
                forgiven_principal=forgiven,
                context=dict(
                    ctx,
                    loan_amount=bankroll.active_loan_amount,
                    chips_at_table=chips_at_table,
                    floor=floor,
                ),
            )

    return LoanSettlement(
        new_bankroll=PlayerBankrollState(
            player_id=bankroll.player_id,
            chips=bankroll.chips + to_player,
            starting_bankroll=bankroll.starting_bankroll,
            # Loans always clear on leave — session-scoped per v1.
            # lender_id resets to None along with the rest of the
            # loan fields (default in the dataclass).
        ),
        sponsor_total=sponsor_total,
        returned_chips=to_player,
    )
