"""Functional core for the staking OBLIGATION dimension.

Mirrors `cash_mode/stake_chip_flow.py` (the chip dimension): **pure** flow
emitters that compute WHAT principal debt moves as a stake travels its
lifecycle, returning `ObligationFlow` descriptions, plus a single thin
**effectful interpreter** (`apply_obligation_flows`) that is the only place
those descriptions become ledger writes. Functional core / imperative shell —
the same split the poker engine and the chip-flow emitters already use.

The per-contract conservation invariant is a pure function over the emitted
flows (`net_principal_delta`): an origination followed by full
extinguish/forgive/cancel nets to zero, checkable with no DB read.

The obligation tracks PRINCIPAL only — the staker's profit share is a chip-only
flow (see stake_chip_flow.py), never debt. See
docs/plans/CASH_MODE_STAKING_OBLIGATION_LEDGER.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Obligation operations. Each maps 1:1 to a `core.economy.ledger.record_stake_*`
# writer in the imperative shell below.
OP_ORIGINATE = "originate"  # debt is born:    oblig_genesis  -> oblig:<id>
OP_EXTINGUISH = "extinguish"  # principal recovered: oblig:<id> -> oblig_settled
OP_FORGIVE = "forgive"  # residual written off: oblig:<id> -> oblig_forgiven
OP_CANCEL = "cancel"  # origination reversed: oblig:<id> -> oblig_genesis

# Sign each op contributes to the oblig:<id> balance — the substrate for the
# pure conservation check.
_BALANCE_SIGN: Dict[str, int] = {
    OP_ORIGINATE: +1,
    OP_EXTINGUISH: -1,
    OP_FORGIVE: -1,
    OP_CANCEL: -1,
}


@dataclass(frozen=True)
class ObligationFlow:
    """One principal-debt movement on a stake's `oblig:<id>` account.

    Pure data — no ledger write has happened. `op` is one of the `OP_*`
    constants; `amount` is a non-negative principal quantity.
    """

    op: str
    stake_id: str
    amount: int


# --- pure emitters ---------------------------------------------------------


def flows_on_originate(stake_id: str, principal: int) -> List[ObligationFlow]:
    """The debt is born: `principal` owed to the staker. Empty for a
    non-positive principal (nothing is owed)."""
    if principal <= 0:
        return []
    return [ObligationFlow(OP_ORIGINATE, stake_id, int(principal))]


def flows_on_settle(
    stake_id: str,
    *,
    principal: int,
    staker_total: int,
    is_carry: bool,
) -> List[ObligationFlow]:
    """Settle the debt from the leave-time outcome.

    Extinguish the principal RECOVERED (`min(staker_total, principal)`) — never
    `staker_total`, whose excess is the staker's profit share (a chip-only flow,
    not debt repayment). On a non-carry terminal, forgive any residual so the
    debt fully closes; on a carry, leave the residual as the live `oblig:<id>`
    balance (it rolls forward).
    """
    recovered = max(0, min(int(staker_total), int(principal)))
    flows: List[ObligationFlow] = []
    if recovered > 0:
        flows.append(ObligationFlow(OP_EXTINGUISH, stake_id, recovered))
    if not is_carry:
        residual = int(principal) - recovered
        if residual > 0:
            flows.append(ObligationFlow(OP_FORGIVE, stake_id, residual))
    return flows


def flows_on_cancel(stake_id: str, principal: int) -> List[ObligationFlow]:
    """The stake never came to exist (funding rolled back): reverse origination
    exactly. Empty for a non-positive principal."""
    if principal <= 0:
        return []
    return [ObligationFlow(OP_CANCEL, stake_id, int(principal))]


def flows_on_carry_payment(stake_id: str, payment: int) -> List[ObligationFlow]:
    """A borrower repays `payment` toward a CARRIED debt after the stake already
    settled — that much principal is recovered, drawing the carry balance down.
    Empty for a non-positive payment."""
    if payment <= 0:
        return []
    return [ObligationFlow(OP_EXTINGUISH, stake_id, int(payment))]


def flows_on_forgive(stake_id: str, amount: int) -> List[ObligationFlow]:
    """A staker forgives `amount` of a carried debt — write it off as bad debt.
    Empty for a non-positive amount."""
    if amount <= 0:
        return []
    return [ObligationFlow(OP_FORGIVE, stake_id, int(amount))]


def net_principal_delta(flows: List[ObligationFlow]) -> int:
    """The net change to `oblig:<id>` the flows imply (+originate, −extinguish,
    −forgive, −cancel).

    The per-contract conservation handle, computed without a DB read: an
    origination fully closed (extinguish + forgive, or cancel) nets to 0; a
    carry nets to the residual that rolls forward.
    """
    return sum(_BALANCE_SIGN[f.op] * int(f.amount) for f in flows)


# --- imperative shell: the ONE place flows become ledger writes -------------


def apply_obligation_flows(
    flows: List[ObligationFlow],
    repo,
    *,
    sandbox_id: Optional[str],
    context: Optional[dict] = None,
    conn=None,
) -> None:
    """Apply `ObligationFlow` descriptions to the ledger — the single effectful
    interpreter for the obligation dimension. Every obligation write funnels
    through here, so the emitters above stay pure and testable without a DB.

    Best-effort and bank-neutral (each underlying `record_stake_*` is a
    no-central-bank transfer): a dropped row is a forensics gap, never a chip
    mint. Pass `conn` to commit in the caller's unit of work (atomic with the
    chip flows); omit it for the best-effort error/settle paths that aren't yet
    a single transaction.
    """
    from core.economy.ledger import (
        record_stake_cancel,
        record_stake_extinguish,
        record_stake_forgive,
        record_stake_originate,
    )

    writers: Dict[str, Callable[[ObligationFlow], object]] = {
        OP_ORIGINATE: lambda f: record_stake_originate(
            repo,
            stake_id=f.stake_id,
            principal=f.amount,
            context=context,
            sandbox_id=sandbox_id,
            conn=conn,
        ),
        OP_EXTINGUISH: lambda f: record_stake_extinguish(
            repo,
            stake_id=f.stake_id,
            amount=f.amount,
            context=context,
            sandbox_id=sandbox_id,
            conn=conn,
        ),
        OP_FORGIVE: lambda f: record_stake_forgive(
            repo,
            stake_id=f.stake_id,
            amount=f.amount,
            context=context,
            sandbox_id=sandbox_id,
            conn=conn,
        ),
        OP_CANCEL: lambda f: record_stake_cancel(
            repo,
            stake_id=f.stake_id,
            principal=f.amount,
            context=context,
            sandbox_id=sandbox_id,
            conn=conn,
        ),
    }
    for flow in flows:
        writer = writers.get(flow.op)
        if writer is None:
            # Unknown op (a typo) would silently break conservation — skip the
            # write (never mint), but surface it so it can't hide.
            logger.warning(
                "apply_obligation_flows: unknown op %r on stake %r", flow.op, flow.stake_id
            )
            continue
        writer(flow)


def is_originated(repo, stake_id: str, *, sandbox_id: Optional[str]) -> bool:
    """True iff a `stake_originate` row exists for this stake — i.e. it entered
    the obligation dimension. Legacy stakes created before the ledger shipped
    return False; their CLOSE flows must be skipped (see `apply_close_flows`)."""
    if repo is None:
        return False
    rows = repo.entries_for_stake(stake_id, sandbox_id=sandbox_id)
    return any(r.get("reason") == "stake_originate" for r in rows)


def apply_close_flows(
    flows: List[ObligationFlow],
    repo,
    stake_id: str,
    *,
    sandbox_id: Optional[str],
    context: Optional[dict] = None,
    conn=None,
) -> bool:
    """Apply CLOSE flows (extinguish / forgive) ONLY if the stake was originated.

    A legacy stake (active before the obligation ledger shipped) has no
    `stake_originate` row; emitting an extinguish/forgive against it would drive
    `oblig:<id>` negative and pollute the contra totals with debt that never
    existed. So gate every close on `is_originated`: originated → apply and
    return True; legacy → skip and return False. Originations and cancels do NOT
    use this — they go straight through `apply_obligation_flows` (an originate is
    itself the entry into the dimension; a cancel always pairs a same-run
    originate). See CASH_MODE_STAKING_OBLIGATION_LEDGER.md.
    """
    if not is_originated(repo, stake_id, sandbox_id=sandbox_id):
        return False
    apply_obligation_flows(flows, repo, sandbox_id=sandbox_id, context=context, conn=conn)
    return True


def obligation_balance(
    repo,
    stake_id: str,
    *,
    sandbox_id: Optional[str],
    assume_originated: bool = False,
) -> Optional[int]:
    """The ledger-derived `oblig:<stake_id>` balance (outstanding principal owed).

    Returns `None` when the stake was never ORIGINATED in the obligation
    dimension — i.e. a legacy stake created before the obligation ledger shipped
    (no `stake_originate` row). Callers MUST treat `None` as "can't check, skip"
    rather than 0: a legacy stake that later extinguishes would otherwise read a
    spurious negative balance. A stake that was originated returns its real
    balance (0 when fully closed, the carry residual when carried).

    `assume_originated=True` skips the `entries_for_stake` origination check (a
    `context_json LIKE` table scan) when the caller already knows the stake was
    originated — e.g. `apply_close_flows` having just returned True for it. Saves
    the second identical scan in the close → assert sequence.
    """
    if repo is None:
        return None
    if not assume_originated:
        rows = repo.entries_for_stake(stake_id, sandbox_id=sandbox_id)
        if not any(r.get("reason") == "stake_originate" for r in rows):
            return None
    from core.economy.ledger import oblig

    return repo.balance_of(oblig(stake_id), sandbox_id=sandbox_id)
