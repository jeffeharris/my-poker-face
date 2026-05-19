"""Cash-mode stake ladder + sponsor eligibility — pure data and rules.

Extracted from the Flask route module so tests and other call sites
can use these without pulling in `flask_app.routes` (and its import
chain, which depends on a configured limiter).

This module used to be `cash_mode/stakes.py`; renamed to make room
for `cash_mode/stakes.py` to hold the `Stake` dataclass (Phase 1 of
the backing system). Ladder + buy-in window helpers + sponsor
eligibility rule moved here verbatim.

Spec: `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 2.
"""

from __future__ import annotations

from typing import Dict, Tuple


# --- Stakes ladder: SINGLE SOURCE OF TRUTH ---
#
# To add a new stake (or reorder), edit ONLY this dict. Insertion
# order is preserved (Python 3.7+) and `STAKES_ORDER` derives from
# it below, so every downstream consumer — sort order in the lobby
# repo, eligibility checks, sponsor offers, route validation — picks
# up the new entry automatically.
#
# Frontend has its own `STAKES` constant in
# `react/.../components/cash/types.ts` for type safety; that one
# must be edited too when adding a stake (TypeScript literal unions
# can't be generated from a server response). Keep them in lockstep.

STAKES_LADDER: Dict[str, Dict[str, int]] = {
    "$2":   {"big_blind": 2},
    "$10":  {"big_blind": 10},
    "$50":  {"big_blind": 50},
    "$200": {"big_blind": 200},
    "$1000": {"big_blind": 1000},
}

STAKES_ORDER: list[str] = list(STAKES_LADDER.keys())
"""Stake labels in ascending order — derived from STAKES_LADDER's
insertion order. Used for tier-eligibility checks and lobby sort."""

MIN_BUY_IN_BB = 40
MAX_BUY_IN_BB = 100


def table_buy_in_window(stake_label: str) -> Tuple[int, int, int]:
    """Return `(big_blind, min_buy_in, max_buy_in)` for a stake.

    Derived from STAKES_LADDER + MIN_BUY_IN_BB / MAX_BUY_IN_BB.
    Raises KeyError if `stake_label` isn't a valid ladder entry.
    """
    big_blind = STAKES_LADDER[stake_label]["big_blind"]
    return big_blind, big_blind * MIN_BUY_IN_BB, big_blind * MAX_BUY_IN_BB


def is_sponsor_eligible(bankroll_chips: int, stake_label: str) -> bool:
    """Decide whether the player can take a sponsor for this stake.

    Rule (locked in design discussion):
      sponsor-eligible iff
        bankroll < this tier's min_buy_in
        AND (this is the lowest tier
             OR bankroll >= previous tier's min_buy_in)

    The first clause prevents the "preserve capital" exploit
    (sponsoring at a table you could self-afford is strictly +EV
    versus self-funding — kills the loan economy). The second
    enforces step-by-step tier climbing: you can only sponsor one
    tier above what you can already afford yourself.

    Volatile: based on CURRENT bankroll only. No persistent unlock
    tracking — if you bleed back down, you lose access to higher
    sponsor tiers until you re-earn the lower-tier floor.
    """
    if stake_label not in STAKES_LADDER:
        return False
    _, this_min, _ = table_buy_in_window(stake_label)
    if bankroll_chips >= this_min:
        return False
    tier_idx = STAKES_ORDER.index(stake_label)
    if tier_idx == 0:
        return True
    prev_label = STAKES_ORDER[tier_idx - 1]
    _, prev_min, _ = table_buy_in_window(prev_label)
    return bankroll_chips >= prev_min
