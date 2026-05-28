"""Canonical call surface for chip-ledger instrumentation.

Call sites do `from core.economy.ledger import record, bank, player, ai`
rather than reaching into `ChipLedgerRepository` directly. Two reasons:

  1. **Vocabulary stability.** The ledger reason strings are kept in
     `LEDGER_REASONS`; this module rejects writes with unknown reasons
     so typos turn into test failures, not silent drift.
  2. **Swap point.** Central bank v1 (if it ships) will replace the
     write path with one that consults a `reserves` value before
     allowing the creation. Call sites won't change — this module's
     signature does.

`record()` takes the repository explicitly. That keeps the module
side-effect-free and testable; flask routes / handlers pull the repo
from `flask_app.extensions` and pass it through.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from poker.repositories.chip_ledger_repository import (
    CENTRAL_BANK,
    ChipLedgerRepository,
)

logger = logging.getLogger(__name__)


# The full vocabulary. Adding a reason requires editing this set so
# anyone grepping for chip-flow categories sees them in one place.
LEDGER_REASONS = frozenset(
    {
        # Creations: central_bank → X
        'player_seed',  # first-time player entry into cash mode
        'ai_seed',  # first AI bankroll write in a given sandbox
        'ai_regen',  # AI bankroll write where projected > stored
        'house_stake_issue',  # house-archetype stake principal issued to borrower
        'pre_ledger_universe',  # one-shot seed at migration so day-1 drift is 0
        'tourist_injection',  # bank pool → fish bankroll (closed-economy refill)
        'side_hustle_earning',  # bank pool → broke AI bankroll. The side-hustle
        # faucet that replaces passive `ai_regen`: a broke
        # AI goes off-grid to earn, drawing a lump from the
        # recyclable pool (caller clamps to pool depth).
        # See CASH_MODE_SIDE_HUSTLE.md.
        'casino_seat_seed',  # bank pool → fish seat chips at casino spawn
        # (atomic seed event — chips land at the seat,
        # not the bankroll; same pool draw semantics
        # as tourist_injection just routed differently)
        'bank_pool_sim_seed',  # sim-only: central_bank → synthetic donor as
        # the creation half of a paired (creation +
        # bank_pool_deposit) seed flow. Paired form
        # keeps drift at 0 while inflating the pool
        # at sandbox start.
        # Destructions: X → central_bank
        'cap_clamp',  # DEPRECATED — historical entries only. Was emitted
        # when AI winnings would push bankroll above
        # `bankroll_cap`; that cap concept was retired when
        # `starting_bankroll` became a regen target rather
        # than a ceiling. Kept in the vocabulary so the
        # audit can still query historical entries.
        'house_stake_settle',  # leave-time settlement of a house-archetype stake
        'table_rake',  # per-hand pot rake skimmed at award time. Feeds
        # the recyclable bank pool (see
        # BANK_POOL_DEPOSIT_REASONS) — the chips are still
        # removed from circulation, but become drawable by
        # the side hustle / tourist injection rather than
        # evaporating. See CASH_MODE_SIDE_HUSTLE.md.
        'bank_pool_deposit',  # stub vice (and other operator-driven deposits)
        # → bank pool; the recyclable subset of central_bank
        # chips that fund `tourist_injection` /
        # `casino_seat_seed`.
        'vice_spending',  # AI voluntary spend-down (real vice mechanic).
        # Fires from the lobby refresh when a flush AI
        # rolls a vice. Per CASH_MODE_CLOSED_ECONOMY.md
        # this also feeds the bank pool — see
        # BANK_POOL_DEPOSIT_REASONS below.
        'casino_seat_return',  # ai → bank pool: residual seat chips returned
        # when a casino tears down (or a tourist leaves
        # mid-life). Mirror of `casino_seat_seed` —
        # ephemeral-tourist chips were never on a
        # bankroll, so the seat balance returns
        # straight to the pool to preserve drift==0.
        # Annotation (amount=0, audit reconciliation only)
        'forgive_balance',  # borrower left short of principal on a house stake
    }
)

# Pool of reasons that fund tourist injections / casino seat seeds —
# chips destroyed under any of these reasons are considered "recyclable"
# and may be drawn down by `BANK_POOL_DRAW_REASONS`. Closed-economy
# bank-pool depth is `Σ(deposit_reasons) − Σ(draw_reasons)`.
#
# `vice_spending` (real AI vice) and `bank_pool_deposit` (stub vice + sim
# seed) both deposit here, so the closed-economy loop is agnostic to
# which vice implementation is live.
#
# `table_rake` joined this set per CASH_MODE_SIDE_HUSTLE.md: rake used to
# be pure destruction (chips left the universe), but redirecting it into
# the recyclable pool is what funds the side hustle / tourist injection.
# The ledger entry direction is unchanged (winner → central_bank) — only
# its pool-depth classification moved.
BANK_POOL_DEPOSIT_REASONS = frozenset(
    {
        'bank_pool_deposit',
        'vice_spending',
        'casino_seat_return',
        'table_rake',
    }
)

# Pool draws — creations that pull from the recyclable pool. Adding a
# new draw reason (e.g. a per-hand fish subsidy) just appends to this
# set; depth math automatically subtracts it.
BANK_POOL_DRAW_REASONS = frozenset(
    {
        'tourist_injection',
        'casino_seat_seed',
        'side_hustle_earning',
    }
)


# Convenience constructors for source/sink strings. Keeps the format
# (e.g. 'player:<owner_id>') in one place — and the type system catches
# `player(None)` mistakes that the f-string equivalent would let
# through silently.


def bank() -> str:
    """The central bank as a source/sink."""
    return CENTRAL_BANK


def player(owner_id: str) -> str:
    """Format `owner_id` into the canonical `player:<owner_id>` form."""
    if not owner_id:
        raise ValueError("player() requires a non-empty owner_id")
    return f"player:{owner_id}"


def ai(personality_id: str) -> str:
    """Format `personality_id` into the canonical `ai:<personality_id>` form."""
    if not personality_id:
        raise ValueError("ai() requires a non-empty personality_id")
    return f"ai:{personality_id}"


def record(
    repo: ChipLedgerRepository,
    *,
    source: str,
    sink: str,
    amount: int,
    reason: str,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """Write one ledger entry. Returns the row id, or None on failure.

    Validation rules:
      - `reason` must be in `LEDGER_REASONS` (unknown reasons would
        leak into the audit endpoint's `by_reason` bucket and confuse
        the categorisation).
      - `amount` must be a non-negative int. Negative amounts are
        almost always a sign-error at the call site — flip the
        source/sink direction instead.
      - The entry must touch the central bank (source OR sink ==
        `central_bank`). Pure transfers between non-bank entities
        don't change the size of the universe and are out of scope
        for v0.

    `sandbox_id` is the Phase 2.5 v103 per-sandbox audit scope. When
    omitted the row writes `sandbox_id=NULL` (legacy / pre-v103
    bucket). Production callers should always pass it so per-sandbox
    audits can filter; one-shot migration helpers
    (`_migrate_v94_seed_pre_ledger_universe`) leave it NULL on purpose.

    Failures log a warning and return None — we never want a ledger
    bug to take down a chip-moving code path. The audit-side drift
    will flag the missed entry.
    """
    if reason not in LEDGER_REASONS:
        logger.warning(
            "chip ledger: rejecting record() with unknown reason=%r "
            "(amount=%s source=%s sink=%s); add to LEDGER_REASONS first",
            reason,
            amount,
            source,
            sink,
        )
        return None

    try:
        amount_int = int(amount)
    except (TypeError, ValueError):
        logger.warning(
            "chip ledger: rejecting record() with non-int amount=%r (reason=%s)",
            amount,
            reason,
        )
        return None

    if amount_int < 0:
        logger.warning(
            "chip ledger: rejecting record() with negative amount=%d "
            "(reason=%s source=%s sink=%s); flip source/sink instead",
            amount_int,
            reason,
            source,
            sink,
        )
        return None

    if source != CENTRAL_BANK and sink != CENTRAL_BANK:
        logger.warning(
            "chip ledger: rejecting record() with no central_bank side "
            "(source=%s sink=%s reason=%s); v0 tracks only creations/destructions",
            source,
            sink,
            reason,
        )
        return None

    try:
        return repo.record(
            source=source,
            sink=sink,
            amount=amount_int,
            reason=reason,
            context=context,
            sandbox_id=sandbox_id,
        )
    except Exception as e:
        # ERROR, not warning (PRH-11): validation has already passed, so this
        # is a real DB-write failure on a row a chip-moving caller expected to
        # land. Callers write the bankroll first, then this best-effort ledger
        # row — so a failure here means the chip move likely committed without
        # a ledger entry = conservation drift. Surface it loudly for alerting;
        # the audit's `drift` is the reconciliation backstop.
        logger.error(
            "[LEDGER] DRIFT RISK: record() DB write failed "
            "(reason=%s amount=%d source=%s sink=%s): %s",
            reason,
            amount_int,
            source,
            sink,
            e,
        )
        return None


# --- Reason-specific helpers ---
#
# Thin sugar over `record()`. They exist so call sites read as
# `ledger.record_ai_regen(...)` rather than re-stating the reason
# string and source/sink direction. If any of these grow real logic
# (e.g. central bank v1 reserves check), it lives here once.


def record_player_seed(
    repo: Optional[ChipLedgerRepository],
    *,
    owner_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """First-time entry: central_bank → player. Accepts repo=None (no-op).

    `sandbox_id` is the Phase 2.5 per-sandbox audit scope; omit to
    write NULL (pre-v103 legacy bucket).
    """
    if repo is None:
        return None
    return record(
        repo,
        source=bank(),
        sink=player(owner_id),
        amount=amount,
        reason='player_seed',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_ai_seed(
    repo: Optional[ChipLedgerRepository],
    *,
    personality_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """First AI bankroll write in a sandbox: central_bank → ai.

    Closes the chip-ledger gap from `CASH_MODE_ECONOMY.md` Known
    Issues §2. Per-sandbox scoping (v102) makes this fire on every
    new sandbox's first write of each personality.

    No-op when `repo` is None or `amount <= 0`. Called from
    `BankrollRepository.save_ai_bankroll` when the existence check
    fires (first write per `(personality_id, sandbox_id)`).
    """
    if repo is None or amount <= 0:
        return None
    return record(
        repo,
        source=bank(),
        sink=ai(personality_id),
        amount=int(amount),
        reason='ai_seed',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_ai_regen(
    repo: Optional[ChipLedgerRepository],
    *,
    personality_id: str,
    stored_chips: int,
    projected_chips: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """central_bank → ai for the positive delta between stored and projected.

    No-op when `repo` is None or `projected_chips <= stored_chips`. Use at
    every `save_ai_bankroll` call site immediately after computing
    `projected_chips`.
    """
    if repo is None:
        return None
    delta = int(projected_chips) - int(stored_chips)
    if delta <= 0:
        return None
    return record(
        repo,
        source=bank(),
        sink=ai(personality_id),
        amount=delta,
        reason='ai_regen',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_house_stake_issue(
    repo: Optional[ChipLedgerRepository],
    *,
    owner_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """House-archetype stake principal: central_bank → borrower.

    Personality and human stake principals are pure transfers between
    non-bank entities (staker's bankroll → borrower's table stack) and
    aren't routed through here. Only the house archetype path creates
    chips out of the central bank.
    """
    if repo is None:
        return None
    return record(
        repo,
        source=bank(),
        sink=player(owner_id),
        amount=amount,
        reason='house_stake_issue',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_cap_clamp(
    repo: Optional[ChipLedgerRepository],
    *,
    personality_id: str,
    overflow: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """ai → central_bank for chips that would push past `starting_bankroll`.

    Fired by `credit_ai_cash_out` when the AI's table stack would
    push the bankroll past its cap; the excess effectively evaporates
    back into the bank. No-op when `overflow <= 0`.
    """
    if repo is None or overflow <= 0:
        return None
    return record(
        repo,
        source=ai(personality_id),
        sink=bank(),
        amount=overflow,
        reason='cap_clamp',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_table_rake(
    repo: Optional[ChipLedgerRepository],
    *,
    source: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """winner → central_bank for the per-hand rake skim.

    `source` is the canonical entity string the pot was drawn from —
    typically `ai(personality_id)` for sim hands or `player(owner_id)`
    for player-table hands. Constructed by the caller because rake
    targets a specific winner, which the caller already knows.

    No-op when `repo` is None or `amount <= 0` so call sites don't have
    to guard before invoking.
    """
    if repo is None or amount <= 0:
        return None
    return record(
        repo,
        source=source,
        sink=bank(),
        amount=amount,
        reason='table_rake',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_house_stake_settle(
    repo: Optional[ChipLedgerRepository],
    *,
    owner_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """borrower → central_bank for a house-archetype stake settle.

    The staker share (principal recovered + cut on upside) goes back
    to the bank on leave-time settlement. Personality and human stakes
    don't route here — the staker share credits the staker's persistent
    bankroll instead, a pure non-bank transfer.
    """
    if repo is None or amount <= 0:
        return None
    return record(
        repo,
        source=player(owner_id),
        sink=bank(),
        amount=amount,
        reason='house_stake_settle',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_forgive_balance(
    repo: Optional[ChipLedgerRepository],
    *,
    owner_id: str,
    forgiven_principal: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """Annotation row (amount=0) — house stake principal not recovered.

    Fired when the borrower leaves a house-stake session short of the
    principal. The unrecovered principal already exists in the universe
    (it flowed into other AIs' table stacks during play and gets caught
    at credit_ai_cash_out). This annotation only exists so the audit
    endpoint can reconcile: `sum(house_stake_issue) -
    sum(house_stake_settle) - sum(forgive_balance.context.forgiven_principal)`
    equals outstanding house-stake principal.

    Always source=player, sink=bank to keep the central-bank-side
    rule simple. Amount is 0 by construction.

    Skips the write when `forgiven_principal <= 0` — the annotation
    is meaningful only when chips were actually forgiven. Without
    this guard, every successful stake settlement would generate a
    noise-row with amount=0 and forgiven_principal=0 that adds
    audit clutter for no signal.
    """
    if repo is None or forgiven_principal <= 0:
        return None
    ctx = dict(context or {})
    ctx['forgiven_principal'] = int(forgiven_principal)
    return record(
        repo,
        source=player(owner_id),
        sink=bank(),
        amount=0,
        reason='forgive_balance',
        context=ctx,
        sandbox_id=sandbox_id,
    )


def record_bank_pool_deposit(
    repo: Optional[ChipLedgerRepository],
    *,
    source: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """source → central_bank for chips deposited into the closed-economy pool.

    `source` is the canonical entity string the chips came from —
    `ai(personality_id)` for stub vice (sim testbed) and
    `player(owner_id)` for future player vice. The deposit lands in
    the recyclable subset of central_bank reserves that funds
    `tourist_injection` / `casino_seat_seed`. Bank pool depth (per
    sandbox) is `Σ(BANK_POOL_DEPOSIT_REASONS) − Σ(BANK_POOL_DRAW_REASONS)`.

    Real AI vice writes via `record_vice_spending`; both feed the
    same pool (both reasons are in `BANK_POOL_DEPOSIT_REASONS`).

    No-op when `repo` is None or `amount <= 0`.
    """
    if repo is None or amount <= 0:
        return None
    return record(
        repo,
        source=source,
        sink=bank(),
        amount=int(amount),
        reason='bank_pool_deposit',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_vice_spending(
    repo: Optional[ChipLedgerRepository],
    *,
    personality_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """ai → central_bank for a vice spend (real AI vice mechanic).

    Fired by `resolve_ai_vice_spending` when a flush AI rolls a vice.
    The chips move from the AI's bankroll to the central bank as part
    of the standard destruction pattern; the AI then sits off-grid for
    the vice duration before returning. No-op when `amount <= 0`.

    Per `CASH_MODE_CLOSED_ECONOMY.md` the destination is the recyclable
    bank pool (not pure destruction) — `vice_spending` is in
    `BANK_POOL_DEPOSIT_REASONS` so the pool depth accounting picks
    these up the same way it picks up `bank_pool_deposit`.

    Mirrors `record_cap_clamp`'s shape (single-personality destruction).
    """
    if repo is None or amount <= 0:
        return None
    return record(
        repo,
        source=ai(personality_id),
        sink=bank(),
        amount=int(amount),
        reason='vice_spending',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_tourist_injection(
    repo: Optional[ChipLedgerRepository],
    *,
    personality_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """central_bank → ai for a fish bankroll refill from the bank pool.

    Caller is responsible for verifying that the bank pool has enough
    reserves before drawing — `record_tourist_injection` itself just
    writes the ledger row (the pool is virtual; depth is computed,
    not gated by a row count).

    No-op when `repo` is None or `amount <= 0`.
    """
    if repo is None or amount <= 0:
        return None
    return record(
        repo,
        source=bank(),
        sink=ai(personality_id),
        amount=int(amount),
        reason='tourist_injection',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_side_hustle_earning(
    repo: Optional[ChipLedgerRepository],
    *,
    personality_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """central_bank → ai for a side-hustle payout drawn from the bank pool.

    The faucet that replaces passive `ai_regen` (see
    CASH_MODE_SIDE_HUSTLE.md): a broke AI goes off-grid to earn and
    returns with a lump credited to its bankroll. `side_hustle_earning`
    is in `BANK_POOL_DRAW_REASONS`, so it draws down pool depth the same
    way `tourist_injection` does.

    Caller is responsible for clamping `amount` to available pool
    reserves before drawing — this helper just writes the row (the pool
    is virtual; depth is computed, not gated by a row count). Mirror of
    `record_tourist_injection`.

    No-op when `repo` is None or `amount <= 0`.
    """
    if repo is None or amount <= 0:
        return None
    return record(
        repo,
        source=bank(),
        sink=ai(personality_id),
        amount=int(amount),
        reason='side_hustle_earning',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_bank_pool_sim_seed_pair(
    repo: Optional[ChipLedgerRepository],
    *,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """Sim-only: inflate the bank pool by `amount` without touching real holders.

    Writes a paired creation + destruction so the audit's `drift == 0`
    invariant survives. Both rows reference a synthetic donor entity
    (`ai:bank_pool_sim_donor`) that has no bankroll row, so neither
    `actual_outstanding` changes.

    Result: bank pool depth gains `amount` chips. ledger_outstanding
    unchanged (creation cancels destruction). drift unchanged.

    Returns the entry id of the deposit (the second row), or None on
    skip. Intended for `SimConfig.initial_bank_pool_seed` and tests
    that want a pre-loaded pool.
    """
    if repo is None or amount <= 0:
        return None
    donor = ai('bank_pool_sim_donor')
    record(
        repo,
        source=bank(),
        sink=donor,
        amount=int(amount),
        reason='bank_pool_sim_seed',
        context=context,
        sandbox_id=sandbox_id,
    )
    return record(
        repo,
        source=donor,
        sink=bank(),
        amount=int(amount),
        reason='bank_pool_deposit',
        context=dict(context or {}, site='bank_pool_sim_seed'),
        sandbox_id=sandbox_id,
    )


def record_casino_seat_seed(
    repo: Optional[ChipLedgerRepository],
    *,
    personality_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """central_bank → ai for a fish seat buy-in at casino spawn.

    The casino-provisioning resolver pays out the buy-in for each fish
    seat directly from the bank pool. The chips land in the AI entity's
    accounting (`ai:<personality_id>`); the caller is responsible for
    physically placing them in the seat (vs the bankroll) — this row
    only ledgers the chip creation.

    Same pool-draw semantics as `tourist_injection`; separate reason
    so the audit / trajectory can distinguish 'casino spawned' from
    'fish bankroll topped up.'

    No-op when `repo` is None or `amount <= 0`.
    """
    if repo is None or amount <= 0:
        return None
    return record(
        repo,
        source=bank(),
        sink=ai(personality_id),
        amount=int(amount),
        reason='casino_seat_seed',
        context=context,
        sandbox_id=sandbox_id,
    )


def record_casino_seat_return(
    repo: Optional[ChipLedgerRepository],
    *,
    personality_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """ai → central_bank for residual seat chips returned to the pool.

    Mirror of `record_casino_seat_seed`. Fires at casino teardown for any
    seat with chips > 0, and at any other point where a tourist leaves
    with residual chips on the seat. Ephemeral tourists have no bankroll,
    so chips that were `casino_seat_seed`'d to the seat must return
    directly to the bank pool — never to a bankroll — to keep the
    conservation invariant (`drift == 0`).

    `casino_seat_return` is a `BANK_POOL_DEPOSIT_REASON`, so audit math
    correctly absorbs the chips back into pool depth.

    No-op when `repo` is None or `amount <= 0`.
    """
    if repo is None or amount <= 0:
        return None
    return record(
        repo,
        source=ai(personality_id),
        sink=bank(),
        amount=int(amount),
        reason='casino_seat_return',
        context=context,
        sandbox_id=sandbox_id,
    )
