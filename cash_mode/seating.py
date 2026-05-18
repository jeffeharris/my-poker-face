"""Seating + accounting transitions for cash mode.

Eight pure-function transitions, one per row of Part 2's accounting
order table. Every state object passed in is treated as immutable;
the functions return new instances, so atomicity is implicit — if a
function raises, the caller never received the partially-updated
state, and persistence writes never happen.

The eight rows of the accounting matrix:

  1. Sit down (buy-in)              — debit bankroll, set table stack to buy-in
  2. Top up (between hands)         — debit bankroll, credit stack (capped to max_buy_in)
  3. Leave table (between hands)    — credit bankroll with stack, clear seat
  4. Bust at table (in-hand loss)   — clear seat; no bankroll change
  5. Full bankroll bust             — reset player bankroll to starting_bankroll
  6. Mid-hand quit                  — seat cleared, stack forfeited to pot
  7. Disconnect timeout             — identical to #6 after grace expires
  8. Hand settlement (winnings)     — credit (or debit) chips to table stack

Rows 1, 2, 3 are blocked while `hand_in_progress` is True per spec
§"Sit / leave rules". Rows 4-8 fire only inside a hand or right at
settlement; they do not check the flag.

Functions return new state — callers persist via BankrollRepository
when the call succeeds. If a function raises, no persistence happens,
and the prior state is unchanged. That gives us "atomic; rolls back
together if seat allocation fails" (spec row 1) for free.

Spec: `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 2
  §"Bankroll accounting order", §"Sit / leave rules", §"Bust semantics".
"""

from __future__ import annotations

from typing import Tuple

from cash_mode.bankroll import PlayerBankrollState
from cash_mode.table import CashTable


class SeatingError(Exception):
    """A seating transition failed validation.

    Raised for: insufficient bankroll, occupied seat, unknown seat,
    invalid buy-in amount, max-buy-in cap violations. The caller's
    transaction is implicitly rolled back — no state was returned, so
    no persistence write fires.
    """


class HandInProgressError(SeatingError):
    """A between-hands action attempted during an active hand.

    Sit, leave, and top-up are blocked while `hand_in_progress=True`.
    The session layer flips this flag at hand start and clears it at
    settlement; callers should retry after settlement.
    """


# --- Row 1: Sit down (buy-in) ---


def sit_down(
    table: CashTable,
    seat_index: int,
    seat_id: str,
    buy_in: int,
    bankroll: PlayerBankrollState,
) -> Tuple[CashTable, PlayerBankrollState]:
    """Seat a player at `seat_index` with `buy_in` chips.

    Validates:
      - `hand_in_progress` is False (between hands only)
      - target seat is empty
      - buy_in is within [min_buy_in, max_buy_in]
      - bankroll has at least `buy_in` chips

    Returns (new_table, new_bankroll). Persistence is the caller's
    responsibility — if this raises, neither is written.

    Atomic per spec row 1: if any validation fails, no state changes.
    """
    if table.hand_in_progress:
        raise HandInProgressError("Cannot sit during an active hand")
    if seat_index < 0 or seat_index >= table.seat_count:
        raise SeatingError(f"seat_index {seat_index} out of range")
    if table.seats[seat_index] is not None:
        raise SeatingError(f"seat {seat_index} is occupied by {table.seats[seat_index]!r}")
    if table.is_seated(seat_id):
        raise SeatingError(f"{seat_id!r} is already seated at this table")
    if buy_in < table.min_buy_in:
        raise SeatingError(
            f"buy_in {buy_in} below table min_buy_in {table.min_buy_in}"
        )
    if buy_in > table.max_buy_in:
        raise SeatingError(
            f"buy_in {buy_in} exceeds table max_buy_in {table.max_buy_in}"
        )
    if bankroll.chips < buy_in:
        raise SeatingError(
            f"bankroll {bankroll.chips} insufficient for buy_in {buy_in}"
        )

    new_table = table.with_seat(seat_index, seat_id).with_stack(seat_id, buy_in)
    new_bankroll = PlayerBankrollState(
        player_id=bankroll.player_id,
        chips=bankroll.chips - buy_in,
        starting_bankroll=bankroll.starting_bankroll,
    )
    return new_table, new_bankroll


# --- Row 2: Top up (between hands) ---


def top_up(
    table: CashTable,
    seat_id: str,
    topup_amount: int,
    bankroll: PlayerBankrollState,
) -> Tuple[CashTable, PlayerBankrollState]:
    """Move `topup_amount` chips from bankroll to the seat's table stack.

    Capped: the resulting stack cannot exceed `max_buy_in`. If the
    requested topup would overflow, the function raises rather than
    silently clamping — clamping behavior would mask "I asked for X,
    got Y" UI bugs. The caller can pre-compute the legal headroom via
    `max_buy_in - stack_of(seat_id)` and adjust the request.

    Validates:
      - `hand_in_progress` is False
      - seat is occupied by `seat_id`
      - topup_amount > 0
      - resulting stack ≤ max_buy_in
      - bankroll has at least `topup_amount` chips
    """
    if table.hand_in_progress:
        raise HandInProgressError("Cannot top up during an active hand")
    if not table.is_seated(seat_id):
        raise SeatingError(f"{seat_id!r} is not seated at this table")
    if topup_amount <= 0:
        raise SeatingError(f"topup_amount {topup_amount} must be positive")
    current_stack = table.stack_of(seat_id)
    if current_stack + topup_amount > table.max_buy_in:
        raise SeatingError(
            f"topup_amount {topup_amount} would push stack "
            f"{current_stack + topup_amount} above max_buy_in {table.max_buy_in}"
        )
    if bankroll.chips < topup_amount:
        raise SeatingError(
            f"bankroll {bankroll.chips} insufficient for topup_amount {topup_amount}"
        )

    new_table = table.with_stack(seat_id, current_stack + topup_amount)
    new_bankroll = PlayerBankrollState(
        player_id=bankroll.player_id,
        chips=bankroll.chips - topup_amount,
        starting_bankroll=bankroll.starting_bankroll,
    )
    return new_table, new_bankroll


# --- Row 3: Leave table (between hands) ---


def leave_table(
    table: CashTable,
    seat_id: str,
    bankroll: PlayerBankrollState,
) -> Tuple[CashTable, PlayerBankrollState]:
    """Stand up between hands: full table stack returns to bankroll.

    Stack returns home in full (spec row 3). The seat goes empty
    (occupant=None), the stack entry is removed.

    Validates:
      - `hand_in_progress` is False
      - seat is occupied by `seat_id`
    """
    if table.hand_in_progress:
        raise HandInProgressError("Cannot leave during an active hand")
    seat_index = table.seat_index_of(seat_id)
    if seat_index is None:
        raise SeatingError(f"{seat_id!r} is not seated at this table")

    returning_chips = table.stack_of(seat_id)
    new_table = table.with_seat(seat_index, None).without_stack(seat_id)
    new_bankroll = PlayerBankrollState(
        player_id=bankroll.player_id,
        chips=bankroll.chips + returning_chips,
        starting_bankroll=bankroll.starting_bankroll,
    )
    return new_table, new_bankroll


# --- Row 4: Bust at table (lost final chips in hand) ---


def bust_at_table(table: CashTable, seat_id: str) -> CashTable:
    """Clear an empty-stack seat after the hand settled it to zero.

    Fires at hand-end when a seat's stack went to 0 during the hand
    (lost all-in showdown, called and lost, etc.). The settlement
    transition (row 8) already moved chips into other seats / the
    pot; this transition just frees the seat slot so the player can
    be repropted (rebuy or sit out).

    No bankroll movement — the chips were already debited at sit
    down / top up, and they're gone to opponents. This is the spec's
    "Bankroll was already debited at buy-in/top-up; nothing returns."

    Idempotent: clearing a seat that's already empty is a no-op.
    """
    seat_index = table.seat_index_of(seat_id)
    if seat_index is None:
        return table
    return table.with_seat(seat_index, None).without_stack(seat_id)


# --- Row 5: Full bankroll bust ---


def full_bankroll_bust(bankroll: PlayerBankrollState) -> PlayerBankrollState:
    """Reset bankroll to `starting_bankroll` for a player with no
    chips left anywhere.

    Spec row 5: "Player is between sessions when this fires." The
    session layer detects the condition (bankroll.chips == 0 AND
    not seated at any table) and calls this; this function trusts
    the caller's gate rather than re-validating.

    Per spec §"Bust semantics", v1 has no cooldown — fresh grant is
    immediate.
    """
    return PlayerBankrollState(
        player_id=bankroll.player_id,
        chips=bankroll.starting_bankroll,
        starting_bankroll=bankroll.starting_bankroll,
    )


# --- Row 6 / 7: Mid-hand quit + disconnect timeout ---


def mid_hand_quit(table: CashTable, seat_id: str) -> Tuple[CashTable, int]:
    """Forfeit the seat's full table stack to the pot mid-hand.

    Returns (new_table, forfeit_chips). The session layer applies
    `forfeit_chips` to the pot before continuing settlement.

    No bankroll movement — the bankroll was already debited at sit
    down/top up. The table stack is lost to the players who finish
    the hand. Per spec row 6: "Stack lost to opponents in the hand."

    Disconnect timeout (row 7) uses this same function — the spec
    notes both rows are identical after grace expires.
    """
    seat_index = table.seat_index_of(seat_id)
    if seat_index is None:
        raise SeatingError(f"{seat_id!r} is not seated at this table")
    forfeit_chips = table.stack_of(seat_id)
    new_table = table.with_seat(seat_index, None).without_stack(seat_id)
    return new_table, forfeit_chips


# Alias for naming clarity in the disconnect grace path. Identical
# behavior, separate name so call sites communicate the cause.
def disconnect_timeout(table: CashTable, seat_id: str) -> Tuple[CashTable, int]:
    """Same accounting as `mid_hand_quit`; spec rows 6 and 7 share
    behavior after the grace window expires.
    """
    return mid_hand_quit(table, seat_id)


# --- Row 8: Hand settlement (winnings / losses) ---


def apply_settlement(table: CashTable, seat_id: str, delta: int) -> CashTable:
    """Credit (or debit) `delta` chips to the seat's table stack.

    `delta > 0` is winnings; `delta < 0` is losses. Per spec row 8,
    settlement happens before any sit/leave/topup can fire — the
    session layer flips `hand_in_progress` to False only after every
    settlement transition has applied.

    A resulting stack of 0 leaves the seat occupied with a zero
    stack — the partial-all-in survival case (spec §"Player bankroll
    edge cases"). The session layer decides whether to bust_at_table
    (clearing the seat) or wait for the next hand.

    Negative resulting stacks are clamped to 0 — defensive guard
    against arithmetic bugs in the settlement upstream. They aren't
    *expected* to happen (settlement should never debit below 0),
    but if it does, we'd rather show 0 than carry a negative.
    """
    if not table.is_seated(seat_id):
        raise SeatingError(f"{seat_id!r} is not seated at this table")
    new_chips = max(0, table.stack_of(seat_id) + delta)
    return table.with_stack(seat_id, new_chips)
