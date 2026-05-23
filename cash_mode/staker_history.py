"""Staker incentive scoring — drives weighted AI-staker selection.

Replaces the random-among-qualified pick in `find_ai_staker_for` with
a weighted random where each candidate's pick-probability reflects
their incentive to deploy capital. Two drivers feed the composite
weight:

  - **Wealth-overflow pressure**: AIs sitting above their starting
    bankroll feel some pull to put chips to work. Computed as a
    capped multiple of `excess_ratio = (bankroll - starting) / starting`.

  - **Skill belief from per-pair history**: per (staker, borrower)
    outcomes weight the score. Settled stakes are a positive signal,
    open carry is mildly negative, explicit defaults strongly negative.

Plus an additive **relationship warmth** contribution that re-uses
the axes already loaded by `find_ai_staker_for`'s respect/heat gates.

`StakerHistoryStats` is the per-pair counts shape the lobby aggregates
once per refresh (via `StakeRepository.aggregate_history_for_staker`)
and passes into the matcher.

Spec: `docs/plans/CASH_MODE_AI_STAKER_INCENTIVES.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


# --- Wealth-overflow pressure -----------------------------------------------
#
# `excess_ratio = max(0, (bankroll - starting_bankroll) / starting_bankroll)`.
# Floor at 1.0× starting (any amount above the seed grant counts) — staking
# is "investment," not "vice," so even modestly flush AIs should feel some
# pull. The vice-spending sibling uses a higher floor (1.2×) intentionally.
EXCESS_INCENTIVE_WEIGHT = 0.4
MAX_EXCESS_BONUS = 2.0

# --- Skill belief from per-pair stake outcomes ------------------------------
SETTLED_WEIGHT = 1.0     # clean repayment is the gold standard
CARRY_WEIGHT = -0.5      # currently open carry — borrower hasn't paid yet
DEFAULTED_WEIGHT = -1.5  # explicit default is the strongest negative signal
BELIEF_SCALE = 0.3       # per-event contribution before clamp
MAX_BELIEF_BONUS = 1.5   # symmetric cap — belief shifts weight but can't dominate

# --- Relationship warmth ----------------------------------------------------
#
# Composes likability + respect as positive, heat as negative. Distinct from
# `player_staking._relationship_score`'s formula (0.5/0.4/0.3 weighting) —
# that's for forgiveness probability; we want a different shape for "would
# this staker pick this borrower."
HEAT_PENALTY_WEIGHT = 0.4
WARMTH_WEIGHT = 1.0
MAX_WARMTH_BONUS = 1.0
RELATIONSHIP_WARMTH_BASELINE = 0.3  # "no prior interaction" default

# --- Composite weight -------------------------------------------------------
BASE_WEIGHT = 1.0   # everyone starts above zero — preserves "stranger backer" texture
MIN_WEIGHT = 0.01   # safety floor so a clamped-negative candidate still draws


@dataclass(frozen=True)
class StakerHistoryStats:
    """Aggregated stake outcomes between one staker and one borrower.

    Counts settled/carry/defaulted rows from the `stakes` table for a
    specific (staker, borrower) pair. `carry_count` represents *currently
    open* carry debt — a carry that later resolves becomes `settled` in
    the schema, not `carry`. So this is "how many of my stakes to this
    borrower are still bad debt right now," which matches the negative-
    signal intent: open carry is a sign of trouble, not just historical
    noise.
    """

    settled_count: int
    carry_count: int
    defaulted_count: int


def _excess_pressure(bankroll: int, starting_bankroll: int) -> float:
    """Weight contribution from wealth above the comfort floor.

    Zero for any bankroll at or below `starting_bankroll` — only AIs
    sitting above their seed grant feel staking-as-deployment pressure.
    Capped at `MAX_EXCESS_BONUS` so a runaway-rich AI doesn't always
    win every match (the cap is the safety valve playtest will tune).
    """
    if starting_bankroll <= 0:
        return 0.0
    excess_ratio = max(0.0, (bankroll - starting_bankroll) / starting_bankroll)
    return min(MAX_EXCESS_BONUS, excess_ratio * EXCESS_INCENTIVE_WEIGHT)


def _belief_score(stats: Optional[StakerHistoryStats]) -> float:
    """Per-pair history → symmetric, clamped weight contribution.

    `None` (no prior history) returns 0 — the cold-start case is
    explicitly neutral, not slightly positive. Adding a cold-start
    bonus would create a perverse incentive to hop borrowers and
    never build a track record.
    """
    if stats is None:
        return 0.0
    raw = (
        stats.settled_count * SETTLED_WEIGHT
        + stats.carry_count * CARRY_WEIGHT
        + stats.defaulted_count * DEFAULTED_WEIGHT
    )
    scaled = raw * BELIEF_SCALE
    return max(-MAX_BELIEF_BONUS, min(MAX_BELIEF_BONUS, scaled))


def _relationship_warmth(
    rel: Optional[Tuple[float, float, float]],
) -> float:
    """Warmth contribution from relationship axes (staker → borrower).

    `rel` is the same `(likability, respect, heat)` tuple
    `find_ai_staker_for` already loads for its respect/heat gates;
    accepting it directly avoids re-querying the relationship repo.
    `None` (no prior interaction) returns a small positive baseline so
    cold-start matches aren't penalized vs known-warm pairs — it's the
    "give an unknown borrower a chance" baseline.
    """
    if rel is None:
        return RELATIONSHIP_WARMTH_BASELINE
    likability, respect, heat = rel
    warmth = (likability + respect) / 2.0 - heat * HEAT_PENALTY_WEIGHT
    scaled = warmth * WARMTH_WEIGHT
    return max(0.0, min(MAX_WARMTH_BONUS, scaled))


def candidate_weight(
    *,
    bankroll: Optional[int],
    starting_bankroll: Optional[int],
    history_stats: Optional[StakerHistoryStats],
    relationship_axes: Optional[Tuple[float, float, float]],
) -> float:
    """Composite weight for one candidate's pick-probability.

    Sum of `BASE_WEIGHT + excess_pressure + belief + warmth`, floored
    at `MIN_WEIGHT` so a clamped-negative belief score doesn't starve
    the candidate to zero (stdlib `random.choices` raises on all-zero
    weights, so the floor doubles as a safety guard).

    Either `bankroll` or `starting_bankroll` being None skips the
    excess contribution — callers without bankroll plumbing wired
    still get belief + warmth scoring rather than falling all the
    way back to uniform random.
    """
    if bankroll is not None and starting_bankroll is not None:
        excess_part = _excess_pressure(bankroll, starting_bankroll)
    else:
        excess_part = 0.0
    belief_part = _belief_score(history_stats)
    warmth_part = _relationship_warmth(relationship_axes)
    return max(
        MIN_WEIGHT,
        BASE_WEIGHT + excess_part + belief_part + warmth_part,
    )
