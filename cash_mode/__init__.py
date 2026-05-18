"""Cash mode package — single-table cash game with persistent bankrolls.

v1 surface area:
  - `bankroll`: AIBankrollState / PlayerBankrollState / BankrollKnobs
    dataclasses, `project_bankroll` projection-on-read, knob defaults.
  - `table`: CashTable dataclass + helpers (immutable per-seat state).
  - `seating`: pure transitions for the 8 rows of Part 2's bankroll
    accounting matrix (sit/topup/leave/bust/settlement/quit/disconnect).
  - Later commits add `session` (hand orchestration).

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
    "top_up",
]
