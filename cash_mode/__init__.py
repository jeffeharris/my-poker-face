"""Cash mode package — persistent bankrolls + sit/leave/topup accounting.

Cash games run through the existing tournament game infrastructure
(state machine, controllers, action route, SocketIO, UI) — flagged
with `cash_mode=True` on the game_data dict. Cash-specific behavior
lives in:

  - `bankroll`: PlayerBankrollState / AIBankrollState dataclasses +
    `project_bankroll` (passive regen).
  - `table`: CashTable dataclass (mostly vestigial post-refactor;
    seating accounting works directly on the hand engine's
    PokerGameState now).
  - `seating`: pure transitions for sit/leave/topup + bust accounting
    helpers. Used by the cash routes.

The orchestration (CashSession, seat_filler) that lived here briefly
in v0.5 has been replaced by direct use of the tournament flow —
that approach is far simpler and reuses every UI / animation / action
path automatically.

Spec: `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 2.
"""

from cash_mode.bankroll import (
    AIBankrollState,
    BANKROLL_KNOB_DEFAULTS,
    BankrollKnobs,
    PlayerBankrollState,
    credit_ai_cash_out,
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
    "credit_ai_cash_out",
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
