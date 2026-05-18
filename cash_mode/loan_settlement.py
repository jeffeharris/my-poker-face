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

Spec: `docs/plans/CASH_MODE_SPONSORSHIP_HANDOFF.md` §"Leave-time math".
"""

from __future__ import annotations

from dataclasses import dataclass

from cash_mode.bankroll import PlayerBankrollState


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


def settle_loan_on_leave(
    bankroll: PlayerBankrollState,
    chips_at_table: int,
) -> LoanSettlement:
    """Apply the loan-floor + sponsor-cut math; return new bankroll.

    Pure function; does not write to the database. Caller saves the
    returned `new_bankroll`. Safe to call with no active loan — the
    no-loan branch just routes chips_at_table back to bankroll
    verbatim.
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

    return LoanSettlement(
        new_bankroll=PlayerBankrollState(
            player_id=bankroll.player_id,
            chips=bankroll.chips + to_player,
            starting_bankroll=bankroll.starting_bankroll,
            # Loans always clear on leave — session-scoped per v1.
        ),
        sponsor_total=sponsor_total,
        returned_chips=to_player,
    )
