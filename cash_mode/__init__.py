"""Cash mode package — persistent bankrolls + sit/leave/topup accounting.

Cash games run through the existing tournament game infrastructure
(state machine, controllers, action route, SocketIO, UI) — flagged
with `cash_mode=True` on the game_data dict. Cash-specific behavior
lives in:

  - `bankroll`: PlayerBankrollState / AIBankrollState dataclasses +
    `project_bankroll` (passive regen).
  - `table`: CashTable dataclass (mostly vestigial post-refactor;
    seating accounting works directly on the hand engine's
    PokerGameState now). Retained only for `PLAYER_SEAT_ID`.

The old pure sit/leave/topup transition layer (`seating.py`) was
removed — the live routes operate directly on the hand engine's
PokerGameState + the `cash_tables` seats blob (under the per-sandbox
seat lock), so that layer was dead code whose function names implied
atomicity guarantees nothing actually used.

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
    "IDLE_REASONS",
    "IdlePoolEntry",
    "OPEN_SEATS",
    "PLAYER_SEAT_ID",
    "PlayerBankrollState",
    "TABLE_SEAT_COUNT",
    "ai_slot",
    "credit_ai_cash_out",
    "human_slot",
    "new_table",
    "open_slot",
    "project_bankroll",
    "seats_from_json",
    "seats_to_json",
]
