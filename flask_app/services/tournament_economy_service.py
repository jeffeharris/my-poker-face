"""Real-chip economy for multi-table tournaments — the effectful layer.

The tournament runner is a pure funny-money function; THIS module is the sole
real-chip authority (see `docs/plans/TOURNAMENT_ECONOMY_ON_STATE_MODEL.md`). It
owns the two chip-moving stages around a run:

  - **Escrow-in** (`apply_buy_in`): debit the human, draw the bank overlay, and
    earmark both at the `tournament:<id>` escrow. The overlay-vs-buy-in
    distinction is made HERE, by reason: a buy-in is a drift-invisible transfer,
    the overlay a real pool draw.
  - **Distribute** (step 4 — `apply_payout_on_complete`, added with the payout
    layer): drain the escrow per the payout split, an I6 idempotent terminal
    transition guarded by `payout_status`.

Both run under the caller's `get_sandbox_lock(sandbox_id)` so the read-signal →
decide → apply-transfers sequence commits atomically (the chairman discipline).
Pure policy lives in `core/economy/economy_signal.py`; this module only applies
its plans.
"""

from __future__ import annotations

import logging
from typing import Optional

from cash_mode.bankroll import PlayerBankrollState
from core.economy import economy_signal
from core.economy import ledger as chip_ledger
from core.economy.economy_signal import FundingPlan
from core.economy.ledger import player

logger = logging.getLogger(__name__)


class InsufficientFundsError(Exception):
    """Raised when the human can't cover the buy-in. Carries the amounts so the
    route can render a 402 `{required, available}` without re-loading."""

    def __init__(self, required: int, available: int):
        super().__init__(f"insufficient funds: need {required}, have {available}")
        self.required = required
        self.available = available


def plan_funding(
    *,
    ledger_repo,
    sandbox_id: str,
    field_size: int,
    buy_in: int,
    human_in: bool,
) -> FundingPlan:
    """Read ONE economy snapshot and return the funding plan (pure decide step).

    Caller holds the sandbox lock across this and `apply_buy_in` so the signal
    the plan was computed from is still current when the transfers apply.
    """
    state = economy_signal.signal(ledger_repo, sandbox_id=sandbox_id)
    return economy_signal.tournament_funding(
        state,
        field_size=field_size,
        seat_price=buy_in,
        human_in=human_in,
    )


def apply_buy_in(
    *,
    tournament_id: str,
    owner_id: str,
    sandbox_id: str,
    plan: FundingPlan,
    bankroll_repo,
    ledger_repo,
    session_repo,
) -> None:
    """Escrow-in: debit the human, stamp economy columns, write escrow ledger rows.

    Order is deliberate (the cash double-settle lesson):
      1. Debit the human bankroll — the ONLY hard chip move.
      2. `set_economy(... payout_status=pending|skipped)` — if this raises, the
         bankroll is re-credited and NO ledger rows exist yet (clean rollback).
      3. Ledger rows (buy-in transfer + overlay draw) — best-effort; a miss is
         audit drift, not a broken registration.

    Caller holds `get_sandbox_lock(sandbox_id)`. Raises `InsufficientFundsError`
    (re-guard) or re-raises a hard DB failure AFTER re-crediting the human.
    """
    human_buy_in = plan.human_buy_in
    debited_from: Optional[PlayerBankrollState] = None

    if human_buy_in > 0:
        bankroll = bankroll_repo.load_player_bankroll(owner_id)
        available = bankroll.chips if bankroll else 0
        if available < human_buy_in:
            raise InsufficientFundsError(required=human_buy_in, available=available)
        debited_from = bankroll
        bankroll_repo.save_player_bankroll(
            PlayerBankrollState(
                player_id=owner_id,
                chips=available - human_buy_in,
                starting_bankroll=bankroll.starting_bankroll,
            )
        )

    payout_status = 'pending' if plan.prize_pool > 0 else 'skipped'
    try:
        # session_repo is None in memory-only registry tests; the economy
        # columns are then simply not persisted (no durable backing to write).
        if session_repo is not None:
            session_repo.set_economy(
                tournament_id,
                buy_in=plan.human_buy_in,
                rake=plan.rake,
                bank_overlay=plan.bank_overlay,
                prize_pool=plan.prize_pool,
                payout_status=payout_status,
            )
    except Exception:
        # Nothing on the ledger yet — undo the only hard move and bail.
        if debited_from is not None:
            bankroll_repo.save_player_bankroll(debited_from)
        raise

    # Best-effort ledger rows (never raise out of here).
    if human_buy_in > 0:
        chip_ledger.record_tournament_buy_in(
            ledger_repo,
            source=player(owner_id),
            tournament_id=tournament_id,
            amount=human_buy_in,
            context={'site': 'register_tournament', 'owner_id': owner_id},
            sandbox_id=sandbox_id,
        )
    if plan.bank_overlay > 0:
        chip_ledger.record_tournament_overlay(
            ledger_repo,
            tournament_id=tournament_id,
            amount=plan.bank_overlay,
            context={'site': 'register_tournament'},
            sandbox_id=sandbox_id,
        )
