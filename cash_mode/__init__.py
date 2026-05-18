"""Cash mode package — single-table cash game with persistent bankrolls.

Top-level package surface kept pure of `poker.repositories` /
`poker.memory` imports so the package can be imported by repository
modules without circular-import risk. (`BankrollRepository` imports
`cash_mode.bankroll`; if this `__init__` reached back through
`seat_filler` or `session` it would close the cycle.)

For the orchestration surface, import explicitly:

    from cash_mode.seat_filler import fill_seats
    from cash_mode.session import CashSession, HandResult

The leaf modules `bankroll`, `table`, `seating` are repo-free and
re-exported here.

Spec: `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 2.
"""

from cash_mode.bankroll import (
    AIBankrollState,
    BANKROLL_KNOB_DEFAULTS,
    BankrollKnobs,
    PlayerBankrollState,
    project_bankroll,
)
from cash_mode.seating import (
    HandInProgressError,
    SeatingError,
    apply_settlement,
    bust_at_table,
    disconnect_timeout,
    full_bankroll_bust,
    leave_table,
    mid_hand_quit,
    sit_down,
    sit_down_ai,
    top_up,
)
from cash_mode.table import (
    PLAYER_SEAT_ID,
    CashTable,
    new_table,
)

__all__ = [
    "AIBankrollState",
    "BANKROLL_KNOB_DEFAULTS",
    "BankrollKnobs",
    "CashTable",
    "HandInProgressError",
    "PLAYER_SEAT_ID",
    "PlayerBankrollState",
    "SeatingError",
    "apply_settlement",
    "bust_at_table",
    "disconnect_timeout",
    "full_bankroll_bust",
    "leave_table",
    "mid_hand_quit",
    "new_table",
    "project_bankroll",
    "sit_down",
    "sit_down_ai",
    "top_up",
]
