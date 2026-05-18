"""Cash mode package — single-table cash game with persistent bankrolls.

v1 surface area lives here:
  - `bankroll`: AIBankrollState / PlayerBankrollState dataclasses,
    `project_bankroll` projection-on-read, knob defaults.
  - Later commits add `table`, `seating`, `session`.

Spec: `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 2.
"""

from cash_mode.bankroll import (
    AIBankrollState,
    BANKROLL_KNOB_DEFAULTS,
    BankrollKnobs,
    PlayerBankrollState,
    project_bankroll,
)

__all__ = [
    "AIBankrollState",
    "BANKROLL_KNOB_DEFAULTS",
    "BankrollKnobs",
    "PlayerBankrollState",
    "project_bankroll",
]
