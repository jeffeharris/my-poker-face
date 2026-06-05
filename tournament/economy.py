"""Pure prize-structure math for the tournament economy (P2 step 4).

Zero I/O, zero ledger knowledge — the runner emits a *payout split* and the
sandbox (the real-chip authority) applies it. This module is the split:

  - `compute_payout_schedule(field_size, prize_pool, curve)` — top ~30%,
    front-loaded, **rounding residual → 1st place** (no chip leakage).
  - `payout_for_position(position, schedule)` — a finisher's prize (0 if OTM).

See `docs/plans/MULTI_TABLE_TOURNAMENT_P2_ECONOMY.md` §"Pure prize math" and
`TOURNAMENT_ECONOMY_ON_STATE_MODEL.md` §"escrow + payout-split contract".
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

# Fraction of the field that finishes in the money for *payouts*. The P2 economy
# design front-loads the top ~30% (distinct from the session's display-only 15%
# ITM cutoff in `session.IN_THE_MONEY_FRACTION`, which predates real payouts).
PAYOUT_FRACTION: float = 0.30

# Front-loaded default curve: winner 38% / 2nd 24% / 3rd 15%; the remaining 23%
# is shared equally among the rest of the paid places. Tunable per tournament
# via the `payout_curve` argument (no code change).
DEFAULT_PAYOUT_CURVE: tuple[float, ...] = (0.38, 0.24, 0.15)


def paid_places_for(field_size: int) -> int:
    """How many places are paid for a given field (≥1, ≤ field_size)."""
    if field_size < 1:
        return 0
    return min(field_size, max(1, round(field_size * PAYOUT_FRACTION)))


def _resolve_fractions(paid: int, curve: Optional[Sequence[float]] = None) -> List[float]:
    """Normalised per-place fractions (length == paid, sums to ~1.0).

    Uses as many explicit front fractions as fit; any leftover share is split
    equally among the remaining places. When fewer places pay than the curve
    has entries, the head is truncated and renormalised so it still sums to 1.
    """
    front = list(curve or DEFAULT_PAYOUT_CURVE)
    if paid <= len(front):
        head = front[:paid]
        total = sum(head) or 1.0
        return [f / total for f in head]
    remainder = max(0.0, 1.0 - sum(front))
    rest_n = paid - len(front)
    each = remainder / rest_n if rest_n else 0.0
    fractions = front + [each] * rest_n
    # Normalise to sum to exactly 1.0. A caller-supplied curve whose front sums
    # to >1.0 would otherwise leave `fractions` summing >1.0, making the integer
    # amounts exceed the prize pool → a NEGATIVE rounding residual → 1st place's
    # payout goes negative and the escrow over-drains (conservation break). For
    # the default curve (front sums <1.0, the remainder fills the rest to 1.0)
    # this is a no-op.
    total = sum(fractions) or 1.0
    return [f / total for f in fractions]


def compute_payout_schedule(
    field_size: int,
    prize_pool: int,
    payout_curve: Optional[Sequence[float]] = None,
) -> List[Dict[str, int]]:
    """Per-position payout amounts that sum EXACTLY to `prize_pool`.

    Returns `[{'finishing_position': p, 'amount': a}, …]` for positions 1..paid
    (1 = winner) with positive amounts only. The rounding residual from integer
    truncation is added to 1st place, so `sum(amounts) == prize_pool` exactly
    (no chip leakage — the escrow drains to 0). Empty when there is nothing to
    pay.
    """
    if prize_pool <= 0 or field_size < 1:
        return []
    paid = paid_places_for(field_size)
    fractions = _resolve_fractions(paid, payout_curve)
    amounts = [int(f * prize_pool) for f in fractions]
    if not amounts:
        return []
    residual = prize_pool - sum(amounts)
    amounts[0] += residual  # rounding crumbs → the winner; escrow nets to 0
    return [
        {'finishing_position': i + 1, 'amount': amt} for i, amt in enumerate(amounts) if amt > 0
    ]


def payout_for_position(position: int, schedule: List[Dict[str, int]]) -> int:
    """The prize for `position` (1 = winner), or 0 if out of the money."""
    for entry in schedule:
        if entry['finishing_position'] == position:
            return entry['amount']
    return 0
