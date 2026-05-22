"""Route-agnostic glue between cash routes and the cash_sessions table.

Three thin helpers that keep the SQL plumbing out of `cash_routes.py`
and let the lifecycle (sit-down → top-up → leave) be unit-tested
without the full Flask stack.

Each helper takes the `cash_session_repo` explicitly so tests can pass
a tempdb-backed instance. The route layer wraps these with module-
global access to `flask_app.extensions.cash_session_repo`.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from cash_mode.cash_sessions import CashSession

logger = logging.getLogger(__name__)


def record_cash_session_start(
    *,
    cash_session_repo,
    game_id: str,
    owner_id: str,
    sandbox_id: Optional[str],
    stake_label: str,
    initial_buy_in: int,
    sponsor_principal: int = 0,
    is_staked: bool = False,
    stake_id: Optional[str] = None,
    cash_table_id: Optional[str] = None,
    cash_seat_index: Optional[int] = None,
    now: Optional[datetime] = None,
) -> None:
    """Insert a `cash_sessions` row for a newly-created cash game.

    Self-funded sits pass `initial_buy_in` = buy-in chips and leave
    staking fields at defaults. Sponsored sits pass `initial_buy_in=0`,
    `sponsor_principal=offer_amount`, `is_staked=True`, and the
    `stake_id` of the just-created stake row.

    Best-effort: a DB write failure is logged but doesn't raise, so a
    sit-down doesn't fail just because the bookkeeping row couldn't
    be persisted. The in-memory path still works; we just lose
    restart-resilience and history for this session.
    """
    if cash_session_repo is None:
        return
    if now is None:
        now = datetime.utcnow()
    initial_buy_in = int(initial_buy_in)
    session = CashSession(
        session_id=game_id,
        owner_id=owner_id,
        sandbox_id=sandbox_id,
        stake_label=stake_label,
        is_staked=is_staked,
        stake_id=stake_id,
        initial_buy_in=initial_buy_in,
        total_buy_in=initial_buy_in,
        sponsor_principal=int(sponsor_principal),
        cash_table_id=cash_table_id,
        cash_seat_index=cash_seat_index,
        started_at=now,
    )
    try:
        cash_session_repo.create(session)
    except Exception as e:
        logger.warning(
            "[CASH] cash_sessions row create failed for %r: %s "
            "(session_summary will be best-effort on leave)",
            game_id, e,
        )


def increment_cash_session_buy_in(
    cash_session_repo, game_id: str, amount: int,
) -> None:
    """Add `amount` to the session's `total_buy_in`.

    Called from top-up and rebuy after the player's bankroll → table
    transfer goes through. Without this, leave-time P&L treats the
    added chips as winnings — overstating profit by every chip put in
    mid-session.

    Best-effort: a missing session row (legacy game predating
    cash_sessions, or a write that failed at sit-down) silently no-ops.
    """
    if cash_session_repo is None or amount <= 0:
        return
    try:
        session = cash_session_repo.load(game_id)
        if session is None:
            return
        cash_session_repo.update_total_buy_in(
            game_id, session.total_buy_in + int(amount),
        )
    except Exception as e:
        logger.warning(
            "[CASH] cash_sessions buy-in increment failed for %r (+%d): %s",
            game_id, amount, e,
        )


def finalise_cash_session(
    *,
    cash_session_repo,
    game_id: str,
    now: datetime,
    final_chips_at_table: int,
    sponsor_repaid: int,
    player_take_home: int,
    summary: Dict[str, Any],
    closed_status: str,
) -> None:
    """Stamp end-of-session fields onto the `cash_sessions` row.

    Skipped silently when the row doesn't exist (legacy session or
    sit-down write failure) — the summary returned to the client is
    still correct, we just don't keep history for this row.
    """
    if cash_session_repo is None:
        return
    try:
        cash_session_repo.finalise(
            game_id,
            ended_at=now,
            final_chips_at_table=int(final_chips_at_table),
            sponsor_repaid=int(sponsor_repaid),
            player_take_home=int(player_take_home),
            hands_played=int(summary.get("hands_played") or 0),
            hands_won=int(summary.get("hands_won") or 0),
            biggest_pot_won=int(summary.get("biggest_pot_won") or 0),
            duration_seconds=int(summary.get("duration_seconds") or 0),
            closed_status=closed_status,
        )
    except Exception as e:
        logger.warning(
            "[CASH] cash_sessions.finalise failed for %r: %s", game_id, e,
        )
