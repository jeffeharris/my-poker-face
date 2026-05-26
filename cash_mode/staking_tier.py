"""Borrower tier resolution from aggregate carry load (Phase 2 Commit 1).

The tier label is one of four buckets that bias `compute_personality_offers`:

  - **premium** — full lender pool, normal cuts.
  - **standard** — low-likability / low-respect lenders drop out; cuts
    bump by ~7-8%.
  - **restricted** — only high-likability AND high-respect lenders
    surface; cuts bump by ~20%.
  - **house_only** — no personality offers; the house archetypes are
    the only available stake source.

Buckets are driven by the borrower's total outstanding carry load
relative to a cap pegged to the borrower's current playing tier
(locked decision #8 — `10 × min_buy_in @ current tier`). The cap
drops when the borrower drops tiers; over-cap → house-only.

Spec: `docs/plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md` Phase 2 Commit 1.
"""

from __future__ import annotations

import logging

from cash_mode.stakes import BORROWER_KIND_HUMAN
from cash_mode.stakes_ladder import MIN_BUY_IN_BB, STAKES_LADDER

logger = logging.getLogger(__name__)


# --- Tier labels (string literals — cross repo / route / frontend) ---

TIER_PREMIUM = "premium"
TIER_STANDARD = "standard"
TIER_RESTRICTED = "restricted"
TIER_HOUSE_ONLY = "house_only"

# Convenience for callers that need to enumerate all valid labels (e.g.,
# response shape validators, frontend type generation).
ALL_TIERS = (TIER_PREMIUM, TIER_STANDARD, TIER_RESTRICTED, TIER_HOUSE_ONLY)


# --- Thresholds (tunable; defaults per the handoff) ---

# Ratio of carry_load / max_carry that gates each tier transition.
# A borrower at exactly THRESHOLD_X is on the lower side of the gate
# (i.e., 0.20 = standard, not premium).
THRESHOLD_PREMIUM_TO_STANDARD = 0.20
THRESHOLD_STANDARD_TO_RESTRICTED = 0.60
THRESHOLD_RESTRICTED_TO_HOUSE_ONLY = 1.00

# Cap multiplier — borrower's carry headroom is `MULT × min_buy_in @
# current playing tier`. Locked decision #8.
CARRY_CAP_MULTIPLIER = 10


def compute_carry_load(
    *,
    borrower_id: str,
    borrower_kind: str,
    stake_repo,
) -> int:
    """Total chips owed across all of this borrower's active carries.

    Sums `carry_amount` over every row returned by
    `stake_repo.list_carries_for_borrower`. Always non-negative —
    by-construction since `update_carry_amount` rejects negatives at
    the repo level via SQLite type coercion.
    """
    carries = stake_repo.list_carries_for_borrower(borrower_id, borrower_kind)
    return sum(int(c.carry_amount) for c in carries)


def max_carry_for_tier(stake_label: str) -> int:
    """Carry cap (`MULT × min_buy_in`) for a stake label. 0 if unknown.

    The cap is what `resolve_tier` divides `carry_load` by to bucket.
    Unknown stake labels return 0 so the resolver defensively falls
    through to `house_only` — safer than picking a wrong tier from a
    typo'd label.
    """
    entry = STAKES_LADDER.get(stake_label)
    if entry is None:
        return 0
    big_blind = entry["big_blind"]
    min_buy_in = big_blind * MIN_BUY_IN_BB
    return CARRY_CAP_MULTIPLIER * min_buy_in


def resolve_tier(
    *,
    borrower_id: str,
    borrower_kind: str = BORROWER_KIND_HUMAN,
    current_stake_label: str,
    stake_repo,
) -> str:
    """Return the borrower's tier label for the given playing stake.

    Args:
        borrower_id: owner_id (human) or personality_id (AI in Phase 4+).
        borrower_kind: 'human' (default) | 'personality'.
        current_stake_label: the stake the borrower is sitting at OR
            considering. Drives the carry-cap denominator since the cap
            is tied to the borrower's currently-active tier.
        stake_repo: StakeRepository for the carries lookup.

    Returns one of `ALL_TIERS`. A zero carry_load AND a valid stake
    label yields 'premium'; an unknown stake label or an over-cap
    carry_load yields 'house_only'.
    """
    max_carry = max_carry_for_tier(current_stake_label)
    if max_carry <= 0:
        # Unknown stake — defensive. House is always available.
        return TIER_HOUSE_ONLY

    carry_load = compute_carry_load(
        borrower_id=borrower_id,
        borrower_kind=borrower_kind,
        stake_repo=stake_repo,
    )
    ratio = carry_load / max_carry

    if ratio >= THRESHOLD_RESTRICTED_TO_HOUSE_ONLY:
        return TIER_HOUSE_ONLY
    if ratio >= THRESHOLD_STANDARD_TO_RESTRICTED:
        return TIER_RESTRICTED
    if ratio >= THRESHOLD_PREMIUM_TO_STANDARD:
        return TIER_STANDARD
    return TIER_PREMIUM
