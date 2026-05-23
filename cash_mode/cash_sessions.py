"""CashSession dataclass — durable per-session record (schema v108).

One row per cash session: created at sit-down, finalised at leave-table.
Carries the buy-in, time-at-table, staking link, and end-of-session
stats that the in-memory `game_data` dict used to hold ephemerally —
so the leave-table summary survives Flask restart / TTL eviction and
stays correct across top-ups, rebuys, and staked sessions.

Persistence lives in `poker/repositories/cash_session_repository.py`.

Compare to `Stake` (`cash_mode/stakes.py`): a Stake exists only for
staked sessions and is owned by the staking subsystem. A CashSession
exists for every cash session (self-funded or staked) and is the
record the leave summary + future session-history view read from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# `closed_status` values
CLOSED_STATUS_LEFT = "left"
CLOSED_STATUS_GHOST_CLEANUP = "ghost_cleanup"


@dataclass(frozen=True)
class CashSession:
    """One row of the `cash_sessions` table.

    Frozen so the leave route can pass it around without worrying
    about a downstream mutation; mutations create new instances via
    `dataclasses.replace`.

    Field semantics:
      - `session_id`: the game_id (`cash-xxx`). Same value used by
        every cash route to identify the in-memory `game_data`.
      - `initial_buy_in`: chips the player put up at sit-down. Always
        0 for staked sessions (sponsor funded the principal).
      - `total_buy_in`: `initial_buy_in + Σ top-ups + Σ rebuys`. The
        denominator for self-funded P&L. Top-up / rebuy routes must
        increment this; without it, mid-session chip additions silently
        get counted as profit.
      - `sponsor_principal`: chips the sponsor put up. 0 for self-
        funded sessions. Surfaced separately from buy-in so the UI
        can label staked sessions correctly.
      - `stake_id`: link to the `stakes` row when staked. None for
        self-funded. Informational — leave-table math re-loads via
        `stake_repo.load_active_for_session(session_id)`.
      - End-of-session fields (`ended_at`, `final_chips_at_table`,
        `sponsor_repaid`, `player_take_home`, hand stats,
        `duration_seconds`, `closed_status`): None / 0 until the
        session is finalised at leave-table.
    """

    session_id: str
    owner_id: str
    sandbox_id: Optional[str]
    stake_label: str
    is_staked: bool
    stake_id: Optional[str]
    initial_buy_in: int
    total_buy_in: int
    sponsor_principal: int
    cash_table_id: Optional[str]
    cash_seat_index: Optional[int]
    started_at: datetime
    ended_at: Optional[datetime] = None
    final_chips_at_table: Optional[int] = None
    sponsor_repaid: int = 0
    player_take_home: Optional[int] = None
    hands_played: Optional[int] = None
    hands_won: Optional[int] = None
    biggest_pot_won: Optional[int] = None
    duration_seconds: Optional[int] = None
    closed_status: Optional[str] = None
