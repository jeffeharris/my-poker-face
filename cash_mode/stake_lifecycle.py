"""Stake lifecycle — the single, conservation-safe site that funds an AI
aspiration ("get staked to climb a tier") grubstake.

Why this module exists
----------------------
The aspiration path historically funded the climb by calling
``debit_bankroll_for_seat(staker_id, principal)``, whose ``ai_buy_in`` ledger
row credited the STAKER's OWN seat (``seat:ai:<sandbox>:<staker>``).
Settlement, however, drains the CLIMBER's seat. Funding and settlement
therefore touched *different seats*: the climber's seat was drained for a
principal it never received (minting chips into the staker's payout) while the
staker's seat held an orphaned positive. Across a sandbox this summed to a
large negative aggregate seat balance — the prod chip drift investigated
2026-06-08 (≈ −1.3M in one sandbox).

``fund_climb_stake`` takes the staker and the climber as *distinct* arguments
and always credits ``ai_seat(sandbox, climber_id)``, so the principal lands on
the exact seat that settlement drains. Routing every climb through this one
function is what makes "fund the wrong seat" structurally unwriteable — the
caller can no longer accidentally pass a helper that infers the seat from the
debited personality.

See ``docs/plans/CASH_MODE_STAKE_STATE_MACHINE.md``.

Lifecycle note (separate, not addressed here)
---------------------------------------------
An aspiration stake also tends to settle in the SAME world tick it is created,
because the climb-vacate appends a ``from_seat`` change that
``_settle_table_stakes`` reads as a session end. That is a *behavioral* bug
(the climber gets skimmed instead of playing the staked session) — but once
funding is correct it no longer mints chips, so it is tracked as a follow-up
rather than bundled here.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def fund_climb_stake(
    *,
    staker_id: str,
    climber_id: str,
    principal: int,
    stake_id: str,
    bankroll_repo,
    chip_ledger_repo,
    sandbox_id: Optional[str],
    now: Optional[datetime] = None,
):
    """Debit the STAKER's bankroll and fund the CLIMBER's seat with the grubstake.

    The single funding site for AI aspiration climbs. A regen-safe atomic
    debit that mirrors ``cash_mode.bankroll.debit_bankroll_for_seat`` but
    routes the seat credit to ``climber_id`` (the seat settlement drains)
    rather than to the debited personality's own seat — that mismatch is the
    bug this function exists to prevent.

    Returns the post-debit staker ``AIBankrollState`` on success, or ``None``
    on insufficiency / missing bankroll row. ``None`` carries the same
    contract as ``debit_bankroll_for_seat``: the caller MUST skip the ask
    without touching any seat, or chips mint.

    Conservation: the staker bankroll int drops by ``principal``; the
    ``stake_fund`` row records that the chips landed on the climber's seat.
    The climber's ``from_seat`` cash-out (``seat_chips + principal``) and/or
    the later ``stake_payoff`` settlement drain that same seat, so the
    ``+principal`` here is exactly cancelled — no orphaned seat balance.
    """
    if now is None:
        now = datetime.utcnow()

    from cash_mode import economy_flags
    from cash_mode.bankroll import (
        AIBankrollState,
        chip_unit_of_work,
        project_bankroll,
    )
    from core.economy.ledger import (
        ai,
        ai_seat,
        record_ai_regen,
        record_stake_fund,
    )

    try:
        stored = bankroll_repo.load_ai_bankroll(staker_id, sandbox_id=sandbox_id)
    except TypeError as e:
        if "sandbox_id" not in str(e):
            raise
        stored = bankroll_repo.load_ai_bankroll(staker_id)
    if stored is None:
        logger.warning(
            "[CASH][STAKE] climb funding skipped — no bankroll row for staker %r",
            staker_id,
        )
        return None

    # Atomicity mirrors debit_bankroll_for_seat: the pending-regen row, the int
    # debit, and the stake_fund row all commit in ONE transaction. `conn` is
    # None for no-ledger / test callers, in which case each write commits on
    # its own. The refuse paths return None before any write.
    with chip_unit_of_work(bankroll_repo, ledger_repo=chip_ledger_repo) as conn:
        if chip_ledger_repo is not None:
            knobs = bankroll_repo.load_personality_knobs(staker_id)
            projected = project_bankroll(
                stored,
                knobs.starting_bankroll,
                knobs.bankroll_rate,
                now,
            )
            if projected < principal:
                logger.warning(
                    "[CASH][STAKE] climb funding refused: staker=%s sandbox=%s "
                    "projected=%d principal=%d (shortfall=%d)",
                    staker_id,
                    sandbox_id,
                    projected,
                    principal,
                    principal - projected,
                )
                return None
            record_ai_regen(
                chip_ledger_repo,
                personality_id=staker_id,
                stored_chips=stored.chips,
                projected_chips=projected,
                context={
                    'site': 'fund_climb_stake',
                    'sandbox_id': sandbox_id,
                    'stake_id': stake_id,
                },
                sandbox_id=sandbox_id,
                conn=conn,
            )
            new_chips = projected - principal
        else:
            if stored.chips < principal:
                logger.warning(
                    "[CASH][STAKE] climb funding refused (no ledger): staker=%s "
                    "stored=%d principal=%d (shortfall=%d)",
                    staker_id,
                    stored.chips,
                    principal,
                    principal - stored.chips,
                )
                return None
            new_chips = stored.chips - principal

        new_state = AIBankrollState(
            personality_id=staker_id,
            chips=new_chips,
            last_regen_tick=now,
        )
        if conn is not None:
            bankroll_repo.save_ai_bankroll(new_state, sandbox_id=sandbox_id, conn=conn)
        else:
            try:
                bankroll_repo.save_ai_bankroll(new_state, sandbox_id=sandbox_id)
            except TypeError as e:
                if "sandbox_id" not in str(e):
                    raise
                bankroll_repo.save_ai_bankroll(new_state)

        # THE FIX: credit the CLIMBER's seat — the seat settlement drains —
        # not the staker's own seat. `staker_id` and `climber_id` are distinct
        # arguments so this sink can never silently fall back to the debited
        # personality. Gated identically to the ai_buy_in parity write.
        if (
            chip_ledger_repo is not None
            and sandbox_id is not None
            and economy_flags.CHIP_CUSTODY_ENABLED
        ):
            record_stake_fund(
                chip_ledger_repo,
                source=ai(staker_id),
                sink=ai_seat(sandbox_id, climber_id),
                amount=principal,
                context={
                    'site': 'ai_aspire_grubstake',
                    'stake_id': stake_id,
                    'sandbox_id': sandbox_id,
                },
                sandbox_id=sandbox_id,
                conn=conn,
            )
        return new_state


def unwind_climb_funding(
    *,
    staker_id: str,
    climber_id: str,
    principal: int,
    stake_id: str,
    debited,
    bankroll_repo,
    chip_ledger_repo,
    sandbox_id: Optional[str],
) -> None:
    """Reverse a `fund_climb_stake` when the stake row write fails afterwards.

    Mirror image of the funding: restore the staker's bankroll int (the
    `+principal` transfer is reversed; any regen the debit committed is real
    and stays) AND reverse the `stake_fund` ledger credit on the climber's
    seat. Without the second half the climber's seat would keep the orphaned
    `+principal` while the staker is made whole — a `+principal` drift (the
    latent bug the old int-only refund carried, just on the staker's seat).

    Best-effort: each step is guarded so a secondary failure is logged, not
    raised — the caller is already on an error path and will `continue`.
    """
    from cash_mode import economy_flags
    from cash_mode.bankroll import AIBankrollState

    try:
        bankroll_repo.save_ai_bankroll(
            AIBankrollState(
                personality_id=staker_id,
                chips=debited.chips + principal,
                last_regen_tick=debited.last_regen_tick,
            ),
            sandbox_id=sandbox_id,
        )
    except TypeError as te:
        if "sandbox_id" not in str(te):
            logger.warning(
                "[CASH][STAKE] climb unwind: staker int refund failed staker=%r: %s",
                staker_id,
                te,
            )
        else:
            bankroll_repo.save_ai_bankroll(
                AIBankrollState(
                    personality_id=staker_id,
                    chips=debited.chips + principal,
                    last_regen_tick=debited.last_regen_tick,
                )
            )
    except Exception as exc:
        logger.warning(
            "[CASH][STAKE] climb unwind: staker int refund failed staker=%r: %s",
            staker_id,
            exc,
        )

    if (
        chip_ledger_repo is not None
        and sandbox_id is not None
        and economy_flags.CHIP_CUSTODY_ENABLED
    ):
        from core.economy.ledger import ai, ai_seat, record_stake_payoff

        try:
            record_stake_payoff(
                chip_ledger_repo,
                source=ai_seat(sandbox_id, climber_id),
                sink=ai(staker_id),
                amount=principal,
                context={
                    'site': 'ai_aspire_grubstake_unwind',
                    'stake_id': stake_id,
                    'sandbox_id': sandbox_id,
                },
                sandbox_id=sandbox_id,
            )
        except Exception as exc:
            logger.warning(
                "[CASH][STAKE] climb unwind: ledger reversal failed stake=%r: %s",
                stake_id,
                exc,
            )
