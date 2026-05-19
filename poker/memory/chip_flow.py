"""Per-pot chip-flow allocation for relationship + cash_pair_stats.

Given the pot structure of a completed hand (each side pot's
winners + per-player contributions), produce the (winner, loser,
chips) tuples that:

  1. Drive `BIG_WIN` / `BIG_LOSS` events from `HandOutcomeDetector`.
  2. Feed `cash_pair_stats.cumulative_pnl` updates in cash mode.

Both consumers use the same allocation by design (spec at
`docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 1 §"Cash pair stats")
so the relationship layer and the cash bookkeeping never diverge on
who paid whom.

**Allocation rule.** For each side pot:

  - Winners split the pot equally (the existing settlement rule).
  - Each winner's *net gain* from this pot is `(their share) -
    (what they put into this pot)`.
  - That net gain is allocated to losers proportionally to each
    loser's contribution to **this pot**, using the largest-
    remainder method so integer chips sum exactly to the net
    gain — no drift across many hands.

Side pots resolve independently: a player who wasn't eligible for a
side pot doesn't appear as a loser in it. Flows are aggregated
**per (winner, loser) pair** across all pots so a hand with two
pots involving the same pair emits one entry, not two.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class PotShare:
    """One pot's winners + contribution map for allocation.

    `contributions` maps player name → chips that player paid into
    **this** pot. Includes both winners and losers (i.e., everyone
    eligible for / contributing to this pot). The allocator derives
    losers as `contributions.keys() - winners`.

    For a single-pot hand: one `PotShare` with `amount = total pot`
    and `contributions = per-player totals`. For a side-pot hand:
    one `PotShare` per pot, with each pot's amount and its own
    eligible-player contributions.
    """

    amount: int
    winners: Tuple[str, ...]
    contributions: Dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ChipFlow:
    """A net chip transfer from one loser to one winner.

    `chips` is always positive — the (winner, loser) ordering tells
    the direction. Aggregated across all pots in a hand: a pair
    appearing in main + side pot emits one `ChipFlow` summing both.
    """

    winner: str
    loser: str
    chips: int


def allocate_chip_flow(pots: List[PotShare]) -> List[ChipFlow]:
    """Allocate winner net gains to losers per the side-pot rule.

    Returns one `ChipFlow` per unique (winner, loser) pair across
    all pots — aggregated, so consumers don't have to re-sum. The
    return order is not contractually stable; consumers should not
    rely on it.

    Pots with no losers (e.g., uncalled-bet returns where the
    winner is the sole contributor) emit no flows. Pots with no
    winners or zero amount are skipped.
    """
    accum: Dict[Tuple[str, str], int] = {}

    for pot in pots:
        if pot.amount <= 0 or not pot.winners:
            continue

        # Winners split the pot equally; remainder chips go to the
        # first winner(s) so the integer split exactly equals
        # `pot.amount` (matches the existing settlement convention
        # in `poker_game._distribute_pot`, which gives the odd chip
        # to the first eligible position).
        num_winners = len(pot.winners)
        base_share = pot.amount // num_winners
        share_remainder = pot.amount - base_share * num_winners

        winner_set = set(pot.winners)
        loser_contribs = {
            name: contrib
            for name, contrib in pot.contributions.items()
            if name not in winner_set and contrib > 0
        }
        total_loser_contrib = sum(loser_contribs.values())
        if total_loser_contrib <= 0:
            # No losers contributed to this pot — nothing to allocate.
            # (Common case: blinds-only-walk pots where the BB is the
            # only contributor and also collects.)
            continue

        for i, winner in enumerate(pot.winners):
            share = base_share + (1 if i < share_remainder else 0)
            winner_contrib = pot.contributions.get(winner, 0)
            net_gain = share - winner_contrib
            if net_gain <= 0:
                # Winner collected less than they put in (possible in
                # split pots with uneven contributions) — no chip
                # flow to allocate from this pot for this winner.
                continue

            allocations = _proportional_int_split(
                total=net_gain,
                weights=loser_contribs,
            )
            for loser, chips in allocations.items():
                if chips > 0:
                    key = (winner, loser)
                    accum[key] = accum.get(key, 0) + chips

    return [
        ChipFlow(winner=w, loser=l, chips=c)
        for (w, l), c in accum.items()
    ]


def _proportional_int_split(
    total: int, weights: Dict[str, int],
) -> Dict[str, int]:
    """Largest-remainder split of `total` units across weighted keys.

    Returns `{key: chips}` such that `sum(values) == total` exactly
    (assuming `total >= 0` and at least one positive weight).
    Important for cash_pair_stats accuracy: naive
    `int(weight/total*share)` rounding loses chips across many
    hands and produces drift between the (winner, loser) and
    (loser, winner) views.
    """
    total_weight = sum(weights.values())
    if total_weight <= 0 or total <= 0:
        return {k: 0 for k in weights}

    exact = {k: (v * total) / total_weight for k, v in weights.items()}
    floored = {k: int(v) for k, v in exact.items()}
    assigned = sum(floored.values())
    leftover = total - assigned

    if leftover > 0:
        # Distribute leftover units to keys with the largest
        # fractional part. Ties broken by the iteration order of
        # `weights` (insertion order in CPython 3.7+), which is
        # stable across calls.
        remainders = sorted(
            ((k, exact[k] - floored[k]) for k in weights),
            key=lambda item: item[1],
            reverse=True,
        )
        for k, _ in remainders[:leftover]:
            floored[k] += 1

    return floored
