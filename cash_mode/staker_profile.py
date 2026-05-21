"""Staker profile dataclass — Path B AI-sponsorship knobs.

Each personality has a `staker_profile` sub-dict inside `config_json`
(sibling to `bankroll_knobs` and `anchors`) describing how they stake:

  - `willing`: do they stake at all? Chaos personalities (Mime,
    Cheshire Cat) refuse outright.
  - `max_loan_pct_of_bankroll`: largest loan they'll extend, as a
    fraction of their *projected* bankroll. Capacity = pct × bankroll,
    further clamped to the table's `[min_buy_in, max_buy_in]` window.
  - `floor_anchor`: default floor multiplier on the loan principal
    (e.g., 1.10 = "repay 110% before any split"). Adjusted at offer
    time by relationship-axis trims.
  - `rate_anchor`: default sponsor's cut of post-floor remaining
    chips. Adjusted at offer time by relationship-axis trims.
  - `respect_floor`: refuse the loan if the staker's `respect`
    toward the borrower is below this. Negative numbers tolerate
    some prior friction; high numbers gate-keep harder.
  - `heat_ceiling`: refuse the loan if the staker's `heat` toward
    the borrower is above this. 1.0 = never refuses on heat alone;
    0.0 = refuses on any heat at all.

Defaults are **deliberately conservative** so a personality without
an explicit `staker_profile` sub-dict still behaves predictably:
small cap-fraction, modest floor/rate, easy-to-trip respect floor
and heat ceiling. This mirrors the BankrollRepository's per-field
fallback for `bankroll_knobs`.

Spec: `docs/plans/CASH_MODE_PATH_B_HANDOFF.md` §B.1.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StakerProfile:
    """Per-personality knobs that shape an AI's loan offers.

    Frozen because staker profiles are read-only at offer time —
    relationship-aware adjustments produce a fresh
    `PersonalitySponsorOffer` rather than mutating the profile.
    """

    willing: bool
    max_loan_pct_of_bankroll: float
    floor_anchor: float
    rate_anchor: float
    respect_floor: float
    heat_ceiling: float


STAKER_PROFILE_DEFAULTS = StakerProfile(
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

    Phase 4 introduces AIs as stake borrowers (peer bailout when bust).
    Phase 5 introduces humans as stakers (player offering a stake to
    an AI of their choosing). The two paths share this profile.

    Fields:
      - `willing`: do they accept stakes at all? Stoic / principled
        personalities (Lincoln, Buddha) refuse outright; most AIs
        accept. The Phase 4 take_stake path checks this directly —
        a busting AI takes whatever they can get from a willing
        staker (no further gating on the offer's terms).
      - `willingness_threshold`: Phase 5 — the minimum relationship-
        axes score the AI requires from a HUMAN staker before
        accepting a stake offer. Mirrors the human-side forgiveness
        score (`L×0.5 + R×0.4 - H×0.3`). Below the threshold, the
        offer is refused regardless of terms. AI-to-AI bailout
        stakes ignore this field (different decision path). Default
        0.30 — broadly accepting; a player with even mild goodwill
        clears it. Stoic AIs can raise their threshold to ~0.50+ to
        require meaningful goodwill before accepting.

    Frozen because the profile is read-only at decision time —
    relationship-aware adjustments happen on the staker side, not here.

    Spec: `docs/plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md` Phase 4
    Commit 1 + Phase 5 Commit 1.
    """

    willing: bool
    willingness_threshold: float = 0.30


# Default — most personalities accept stakes when busting. Stoic
# personalities override `willing=False` in their config sub-dict.
BORROWER_PROFILE_DEFAULTS = BorrowerProfile(
    willing=True,
    willingness_threshold=0.30,
)


# Ego-derived willingness-threshold calibration. Personalities don't
# all need hand-tuned willingness_thresholds; deriving from the
# already-curated `ego` anchor gives every character a defensible
# default that respects their identity.
#
# Slope and clamps tuned to match the relationship-axes range:
#   - Max plausible score (likability=1, respect=1, heat=0) ≈ 0.90,
#     so the upper clamp (0.50) leaves room for "friendship gets
#     through" even at the proudest tier.
#   - Lower clamp (0.15) keeps even the humblest AI from accepting
#     literally anything (they still need a non-hostile vibe).
WILLINGNESS_THRESHOLD_BASE = 0.30
WILLINGNESS_THRESHOLD_SLOPE = 0.50
WILLINGNESS_THRESHOLD_MIN = 0.15
WILLINGNESS_THRESHOLD_MAX = 0.50


def compute_default_willingness_threshold(ego: float) -> float:
    """Derive a borrower's willingness_threshold from their `ego` anchor.

    Higher ego → harder to convince (proud AIs don't take handouts
    from strangers). Lower ego → easier (humble AIs more comfortable
    accepting help). Clamped to a sensible band so the math always
    leaves room for both "anyone clears this with effort" and "even
    maxed-goodwill from a friend gets through."

    The pattern: every personality already has a curated `ego` anchor
    (0..1) in their `config.anchors` sub-dict. Threading that into
    willingness here lets us populate the field for the whole roster
    without per-personality JSON edits, while still allowing explicit
    `borrower_profile.willingness_threshold` overrides to win when
    present (handled at the loader layer).

    Sample calibrations:
      - ego 0.36 (Lincoln-style humble) → ~0.23
      - ego 0.50 (baseline)             → 0.30
      - ego 0.86 (Napoleon-style proud) → ~0.48
    """
    raw = (
        WILLINGNESS_THRESHOLD_BASE
        + WILLINGNESS_THRESHOLD_SLOPE * (float(ego) - 0.5)
    )
    return max(
        WILLINGNESS_THRESHOLD_MIN,
        min(WILLINGNESS_THRESHOLD_MAX, raw),
    )
