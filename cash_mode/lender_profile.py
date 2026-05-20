"""Lender profile dataclass — Path B AI-sponsorship knobs.

Each personality has a `lender_profile` sub-dict inside `config_json`
(sibling to `bankroll_knobs` and `anchors`) describing how they lend:

  - `willing`: do they lend at all? Chaos personalities (Mime,
    Cheshire Cat) refuse outright.
  - `max_loan_pct_of_bankroll`: largest loan they'll extend, as a
    fraction of their *projected* bankroll. Capacity = pct × bankroll,
    further clamped to the table's `[min_buy_in, max_buy_in]` window.
  - `floor_anchor`: default floor multiplier on the loan principal
    (e.g., 1.10 = "repay 110% before any split"). Adjusted at offer
    time by relationship-axis trims.
  - `rate_anchor`: default sponsor's cut of post-floor remaining
    chips. Adjusted at offer time by relationship-axis trims.
  - `respect_floor`: refuse the loan if the lender's `respect`
    toward the borrower is below this. Negative numbers tolerate
    some prior friction; high numbers gate-keep harder.
  - `heat_ceiling`: refuse the loan if the lender's `heat` toward
    the borrower is above this. 1.0 = never refuses on heat alone;
    0.0 = refuses on any heat at all.

Defaults are **deliberately conservative** so a personality without
an explicit `lender_profile` sub-dict still behaves predictably:
small cap-fraction, modest floor/rate, easy-to-trip respect floor
and heat ceiling. This mirrors the BankrollRepository's per-field
fallback for `bankroll_knobs`.

Spec: `docs/plans/CASH_MODE_PATH_B_HANDOFF.md` §B.1.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LenderProfile:
    """Per-personality knobs that shape an AI's loan offers.

    Frozen because lender profiles are read-only at offer time —
    relationship-aware adjustments produce a fresh
    `PersonalitySponsorOffer` rather than mutating the profile.
    """

    willing: bool
    max_loan_pct_of_bankroll: float
    floor_anchor: float
    rate_anchor: float
    respect_floor: float
    heat_ceiling: float


# Conservative defaults — a personality without an explicit
# `lender_profile` sub-dict defaults to a cautious small-stake lender.
# These match the §"Defaults" line in the handoff doc.
LENDER_PROFILE_DEFAULTS = LenderProfile(
    willing=True,
    max_loan_pct_of_bankroll=0.05,
    floor_anchor=1.20,
    rate_anchor=0.30,
    respect_floor=-0.5,
    heat_ceiling=0.7,
)


# --- Borrower profile (Phase 4 of the backing system) ---------------------


@dataclass(frozen=True)
class BorrowerProfile:
    """Per-personality knobs that shape an AI's willingness to BE staked.

    Phase 4 introduces AIs as stake borrowers. When an AI hits the
    `forced_leave` threshold (chips below 30% of the buy-in), the
    movement decision can opt to take a stake from another AI with
    capacity instead of leaving. The borrower-side gate is this
    profile.

    Fields:
      - `willing`: do they accept stakes when busting? Stoic /
        principled personalities (Lincoln, Buddha) refuse outright;
        most AIs accept. This is "do I borrow at all?" — not a
        relationship gate.

    Frozen because the profile is read-only at decision time —
    relationship-aware adjustments happen on the staker side, not here.

    Phase 5 (humans as stakers) will add a `willingness_threshold`
    field for "would I accept THIS offer from a player?" gating. The
    Phase 4 borrower path (AI accepting a peer's offer to avoid bust)
    doesn't need it — a busting AI takes whatever they can get.

    Spec: `docs/plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md` Phase 4
    Commit 1.
    """

    willing: bool


# Default — most personalities accept stakes when busting. Stoic
# personalities override `willing=False` in their config sub-dict.
BORROWER_PROFILE_DEFAULTS = BorrowerProfile(
    willing=True,
)
