"""Sponsor archetype pool + offer generation.

When a player is sponsor-eligible at a given stake (bankroll < that
table's min buy-in but ≥ the tier-below's min buy-in), they pick from
3 randomly sampled sponsor offers. Each offer encodes a loan with
three knobs:

  - `amount`: principal in chips, delivered directly to the table
    stack (never to bankroll — closes the "pocket the spare loan"
    exploit by construction).
  - `floor`: repayment multiplier on the principal. 1.00 = repay
    principal then split; 1.30 = repay 130% before any split kicks
    in. The "predatory" knob — high floor means small wins go
    entirely to the sponsor.
  - `rate`: sponsor's cut of post-floor remaining. 0.0 = player
    keeps everything past the floor; 0.50 = sponsor takes half.

Archetypes are parameterized by the table's `min_buy_in` and
`max_buy_in` so the pool scales across the stakes ladder without
hardcoded dollar amounts. Every archetype produces a valid amount
in `[min_buy_in, max_buy_in]` at every stake by construction.

Spec: `docs/plans/CASH_MODE_SPONSORSHIP_HANDOFF.md`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, List, Optional


@dataclass(frozen=True)
class SponsorOffer:
    """One concrete sponsor offer for a specific table.

    Concrete amounts (not multipliers) — already computed against the
    table's min/max buy-in. The frontend renders these directly; the
    `/api/cash/sponsor-and-sit` route validates that the loan params
    match an offer produced by the same archetype/table combo to
    prevent client-side tampering with terms.
    """

    archetype_id: str
    name: str
    amount: int
    floor: float
    rate: float
    flavor: str


@dataclass(frozen=True)
class _Archetype:
    """Template for producing a SponsorOffer at a given table.

    `amount_fn(min_buy_in, max_buy_in) -> int` lets each archetype
    pick its loan size relative to the table's buy-in window. The
    clamp to `[min_buy_in, max_buy_in]` is applied by the caller so
    pathological templates can't slip a value outside the valid
    range — defensive, not normally exercised.
    """

    id: str
    name: str
    amount_fn: Callable[[int, int], int]
    floor: float
    rate: float
    flavor: str


SPONSOR_ARCHETYPES: List[_Archetype] = [
    _Archetype(
        id="friendly_boost",
        name="Friendly Boost",
        amount_fn=lambda mn, mx: mn,
        floor=1.00,
        rate=0.20,
        flavor="Just enough to get back in. Small stake, small dues.",
    ),
    _Archetype(
        id="square_deal",
        name="Square Deal",
        amount_fn=lambda mn, mx: int(mn * 1.5),
        floor=1.10,
        rate=0.25,
        flavor="A little extra. Pay back with a tip, then we split.",
    ),
    _Archetype(
        id="the_premium",
        name="The Premium",
        amount_fn=lambda mn, mx: int(mx * 0.5),
        floor=1.30,
        rate=0.00,
        flavor="Pay 30% upfront. Keep every chip you win past that.",
    ),
    _Archetype(
        id="skin_in_the_game",
        name="Skin in the Game",
        amount_fn=lambda mn, mx: int(mx * 0.7),
        floor=1.15,
        rate=0.15,
        flavor="Deep stack. Mostly your win, mostly your loss.",
    ),
    _Archetype(
        id="whale_backer",
        name="Whale Backer",
        amount_fn=lambda mn, mx: mx,
        floor=1.00,
        rate=0.50,
        flavor="Maximum stake. Half the upside goes home.",
    ),
    _Archetype(
        id="loan_shark",
        name="Loan Shark",
        amount_fn=lambda mn, mx: int(mx * 0.8),
        floor=1.30,
        rate=0.40,
        flavor="Cheap to take. Brutal floor. Win big or owe everything.",
    ),
]


def _materialize(arch: _Archetype, min_buy_in: int, max_buy_in: int) -> SponsorOffer:
    """Concretize an archetype against a specific table's buy-in range.

    The amount is clamped to `[min_buy_in, max_buy_in]` defensively
    — every archetype's `amount_fn` is already constructed to land
    in-range, but the clamp ensures a typo in a future archetype
    can't produce an unbuyable amount.
    """
    raw = arch.amount_fn(min_buy_in, max_buy_in)
    amount = max(min_buy_in, min(max_buy_in, raw))
    return SponsorOffer(
        archetype_id=arch.id,
        name=arch.name,
        amount=amount,
        floor=arch.floor,
        rate=arch.rate,
        flavor=arch.flavor,
    )


def compute_offers_for_table(
    min_buy_in: int,
    max_buy_in: int,
    *,
    count: int = 3,
    rng: Optional[random.Random] = None,
) -> List[SponsorOffer]:
    """Sample `count` distinct sponsor offers for a table.

    `rng` lets tests pin the sample for determinism. Default uses
    a fresh `random.Random()` so each bust gets a different mix —
    keeps the sponsor screen feeling alive across sessions.

    Raises `ValueError` if `count` exceeds the archetype pool size
    — currently 6 archetypes, so up to 6 distinct offers.
    """
    if count > len(SPONSOR_ARCHETYPES):
        raise ValueError(
            f"Requested {count} offers but only {len(SPONSOR_ARCHETYPES)} archetypes exist"
        )
    rng = rng or random.Random()
    archetypes = list(SPONSOR_ARCHETYPES)
    rng.shuffle(archetypes)
    chosen = archetypes[:count]
    return [_materialize(a, min_buy_in, max_buy_in) for a in chosen]


def find_archetype(archetype_id: str) -> Optional[_Archetype]:
    """Look up an archetype by id — used by sponsor-and-sit to
    validate the offer the client claims to have accepted.

    Returns None if no archetype matches; caller should treat this
    as a client-tampering or stale-offer condition and reject.
    """
    for arch in SPONSOR_ARCHETYPES:
        if arch.id == archetype_id:
            return arch
    return None


def offer_for_archetype(
    archetype_id: str,
    min_buy_in: int,
    max_buy_in: int,
) -> Optional[SponsorOffer]:
    """Re-materialize a specific archetype against a table.

    Used server-side to recompute the offer terms when the client
    sends `{archetype_id, stake_label}` — the server never trusts
    client-provided amount/floor/rate, only the archetype id, and
    derives terms freshly from the table's buy-in range.
    """
    arch = find_archetype(archetype_id)
    if arch is None:
        return None
    return _materialize(arch, min_buy_in, max_buy_in)
