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
    BANKROLL_KNOB_DEFAULTS,
    AIBankrollState,
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
    cash_out_ai_seat,
    disconnect_timeout,
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
from cash_mode.tables import (
    BASELINE_AI_SEATS,
    IDLE_REASONS,
    OPEN_SEATS,
    TABLE_SEAT_COUNT,
    CashTableState,
    IdlePoolEntry,
    ai_slot,
    human_slot,
    open_slot,
    seats_from_json,
    seats_to_json,
)

__all__ = [
    "AIBankrollState",
    "BANKROLL_KNOB_DEFAULTS",
    "BASELINE_AI_SEATS",
    "BankrollKnobs",
    "CashTable",
    "CashTableState",
    "HandInProgressError",
    "IDLE_REASONS",
    "IdlePoolEntry",
    "OPEN_SEATS",
    "PLAYER_SEAT_ID",
    "PlayerBankrollState",
    "SeatingError",
    "TABLE_SEAT_COUNT",
    "ai_slot",
    "apply_settlement",
    "bust_at_table",
    "cash_out_ai_seat",
    "credit_ai_cash_out",
    "disconnect_timeout",
    "human_slot",
    "leave_table",
    "mid_hand_quit",
    "new_table",
    "open_slot",
    "project_bankroll",
    "seats_from_json",
    "seats_to_json",
    "sit_down",
    "sit_down_ai",
    "top_up",
]
