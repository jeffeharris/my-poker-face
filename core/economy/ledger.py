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
LEDGER_REASONS = frozenset({
    # Creations: central_bank → X
    'player_seed',         # first-time player entry into cash mode
    'ai_regen',            # AI bankroll write where projected > stored
    'house_loan_issue',    # anonymous-house sponsor loan accepted
    'pre_ledger_universe', # one-shot seed at migration so day-1 drift is 0

    # Destructions: X → central_bank
    'cap_clamp',           # AI bankroll write where projected > bankroll_cap
    'house_loan_settle',   # leave-time settlement of an anonymous loan

    # Annotation (amount=0, audit reconciliation only)
    'forgive_balance',     # player left with chips < floor on a house loan
})


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

    Failures log a warning and return None — we never want a ledger
    bug to take down a chip-moving code path. The audit-side drift
    will flag the missed entry.
    """
    if reason not in LEDGER_REASONS:
        logger.warning(
            "chip ledger: rejecting record() with unknown reason=%r "
            "(amount=%s source=%s sink=%s); add to LEDGER_REASONS first",
            reason, amount, source, sink,
        )
        return None

    try:
        amount_int = int(amount)
    except (TypeError, ValueError):
        logger.warning(
            "chip ledger: rejecting record() with non-int amount=%r (reason=%s)",
            amount, reason,
        )
        return None

    if amount_int < 0:
        logger.warning(
            "chip ledger: rejecting record() with negative amount=%d "
            "(reason=%s source=%s sink=%s); flip source/sink instead",
            amount_int, reason, source, sink,
        )
        return None

    if source != CENTRAL_BANK and sink != CENTRAL_BANK:
        logger.warning(
            "chip ledger: rejecting record() with no central_bank side "
            "(source=%s sink=%s reason=%s); v0 tracks only creations/destructions",
            source, sink, reason,
        )
        return None

    try:
        return repo.record(
            source=source,
            sink=sink,
            amount=amount_int,
            reason=reason,
            context=context,
        )
    except Exception as e:
        logger.warning(
            "chip ledger: record() failed (reason=%s amount=%d): %s",
            reason, amount_int, e,
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
) -> Optional[int]:
    """First-time entry: central_bank → player. Accepts repo=None (no-op)."""
    if repo is None:
        return None
    return record(
        repo,
        source=bank(),
        sink=player(owner_id),
        amount=amount,
        reason='player_seed',
        context=context,
    )


def record_ai_regen(
    repo: Optional[ChipLedgerRepository],
    *,
    personality_id: str,
    stored_chips: int,
    projected_chips: int,
    context: Optional[Dict[str, Any]] = None,
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
    )


def record_house_loan_issue(
    repo: Optional[ChipLedgerRepository],
    *,
    owner_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """Anonymous-house sponsor loan acceptance: central_bank → player.

    Personality-loan principal is a pure transfer between non-bank
    entities (AI lender's bankroll → player's table stack) and isn't
    routed through here.
    """
    if repo is None:
        return None
    return record(
        repo,
        source=bank(),
        sink=player(owner_id),
        amount=amount,
        reason='house_loan_issue',
        context=context,
    )
