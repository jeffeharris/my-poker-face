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
from core.economy.ledger import player, tournament
from tournament.economy import compute_payout_schedule

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


def _position_to_player(session) -> dict:
    """Map every finishing position → player_id (1 = winner). Built from the
    field's eliminations plus the live winner. Only meaningful once complete."""
    mapping = {e.finishing_position: e.player_id for e in session.field.eliminations}
    winner = session.winner()
    if winner is not None:
        mapping[1] = winner
    return mapping


def apply_payout_on_complete(
    *,
    tournament_id: str,
    session,
    human_owner_id: Optional[str],
    sandbox_id: str,
    bankroll_repo,
    ledger_repo,
    session_repo,
    payout_curve=None,
) -> bool:
    """Distribute the escrow per the payout split — an I6 idempotent terminal
    transition. Safe to call from every completion path (boundary, advance,
    play-out): the `payout_status` guard makes a second call a no-op.

    v1 (synthetic AI field): the human seat (`session.human_id`, only when
    `human_owner_id` is set — i.e. a real human registered) is paid for real;
    every other finisher is a synthetic field entrant with no persistent
    bankroll, so its share is swept back to the bank pool (`tournament_return`),
    keeping the escrow at 0 and restoring the overlay it cancels to reserves.
    The configured rake is skimmed separately (`table_rake`). When real-persona
    fields ship, the synthetic branch becomes a real `ai:<pid>` payout.

    Returns True iff a distribution ran (i.e. status advanced pending→complete).
    Caller holds `get_sandbox_lock(sandbox_id)`. Never raises — a mid-flight
    failure logs and leaves status `in_progress` for a reconcile pass (the cash
    double-settle lesson: status flag before any bankroll write).
    """
    if session_repo is None:
        return False
    try:
        row = session_repo.load(tournament_id)
    except Exception:  # noqa: BLE001
        logger.exception("payout: failed to load tournament %s", tournament_id)
        return False
    if row is None:
        return False

    status = row.get('payout_status')
    if status != 'pending':
        return False  # skipped | in_progress | complete → idempotent no-op
    if not session.is_complete():
        return False  # positions aren't all locked yet

    prize_pool = int(row.get('prize_pool') or 0)
    if prize_pool <= 0:
        session_repo.set_payout_status(tournament_id, 'skipped')
        return False

    # Narrow the crash window: flag in_progress BEFORE any bankroll write.
    session_repo.set_payout_status(tournament_id, 'in_progress')
    try:
        schedule = compute_payout_schedule(session.field.field_size, prize_pool, payout_curve)
        pos_to_player = _position_to_player(session)
        human_id = session.human_id

        for entry in schedule:
            amount = entry['amount']
            if amount <= 0:
                continue
            pid = pos_to_player.get(entry['finishing_position'])
            is_real_human = human_owner_id is not None and pid == human_id
            if is_real_human:
                bankroll = bankroll_repo.load_player_bankroll(human_owner_id)
                chips = bankroll.chips if bankroll else 0
                starting = bankroll.starting_bankroll if bankroll else chips
                bankroll_repo.save_player_bankroll(
                    PlayerBankrollState(
                        player_id=human_owner_id,
                        chips=chips + amount,
                        starting_bankroll=starting,
                    )
                )
                chip_ledger.record_tournament_payout(
                    ledger_repo,
                    sink=player(human_owner_id),
                    tournament_id=tournament_id,
                    amount=amount,
                    context={'site': 'payout', 'finishing_position': entry['finishing_position']},
                    sandbox_id=sandbox_id,
                )
            # else: synthetic AI finisher — left in escrow, swept below.

        # Skim the configured rake (escrow → bank pool: the refill lever).
        rake = int(row.get('rake') or 0)
        if rake > 0:
            chip_ledger.record_table_rake(
                ledger_repo,
                source=tournament(tournament_id),
                amount=rake,
                context={'site': 'tournament_rake', 'tournament_id': tournament_id},
                sandbox_id=sandbox_id,
            )

        # Sweep whatever remains (synthetic-AI shares + rounding) back to the
        # pool so the escrow nets to exactly 0.
        remaining = ledger_repo.balance_of(tournament(tournament_id), sandbox_id=sandbox_id)
        if remaining > 0:
            chip_ledger.record_tournament_return(
                ledger_repo,
                tournament_id=tournament_id,
                amount=remaining,
                context={'site': 'payout_sweep'},
                sandbox_id=sandbox_id,
            )

        final_balance = ledger_repo.balance_of(tournament(tournament_id), sandbox_id=sandbox_id)
        if final_balance != 0:
            logger.error(
                "[TOURNAMENT] escrow %s did not net to 0 after payout (residual=%d)",
                tournament_id,
                final_balance,
            )

        session_repo.set_payout_status(tournament_id, 'complete')
        return True
    except Exception:  # noqa: BLE001 — never crash the game; leave in_progress
        logger.exception(
            "payout failed for %s; status left 'in_progress' for reconcile", tournament_id
        )
        return False


def verify_tournament_conservation(
    tournament_id: str, ledger_repo, *, sandbox_id: Optional[str] = None
) -> dict:
    """Post-event audit: the escrow must net to 0 once distribution completes.

    Cheap (one `balance_of`), not a hot path. Surfaced to tests + the chip-
    economy admin audit."""
    balance = ledger_repo.balance_of(tournament(tournament_id), sandbox_id=sandbox_id)
    return {'tournament_id': tournament_id, 'escrow_balance': balance, 'balanced': balance == 0}
