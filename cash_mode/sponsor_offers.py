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

import logging
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional

from cash_mode.lender_profile import LenderProfile

logger = logging.getLogger(__name__)


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


# --- Path B: AI-personality sponsorship --------------------------------


@dataclass(frozen=True)
class PersonalitySponsorOffer:
    """One concrete loan offer from a named AI personality.

    Distinct from `SponsorOffer` (anonymous archetypes) — this carries
    the lender's `personality_id` so the route can wire
    `active_loan_lender_id` on the bankroll, and so the modal can
    render avatar + name + relationship hint.

    Same `amount`/`floor`/`rate` shape as `SponsorOffer` — the rest of
    the system (leave-time math, lender-credit) is agnostic to whether
    the loan came from an archetype or a personality. `flavor` is the
    per-personality blurb shown on the offer card (may be generated
    on-the-fly or stored on the personality config in v2).
    """

    lender_id: str
    lender_name: str
    amount: int
    floor: float
    rate: float
    flavor: str
    relationship_hint: str
    capacity: int  # the lender's available bankroll at offer time,
                   # used for sorting and for capacity-disclosure UX in v2.


def _adjusted_terms(
    profile: LenderProfile,
    *,
    likability: float,
    heat: float,
    respect: float,
) -> tuple[float, float]:
    """Trim floor/rate by relationship axes (handoff §B.2 "Term adjustment").

    Returns `(floor, rate)` clamped to `[1.00, 1.50]` / `[0.00, 0.55]`.

    Adjustments are additive deltas off the profile's anchors:
      - High likability (>0.5): "friend tax" — floor and rate trim by
        0.05 each. Friends lend on softer terms.
      - High heat (>0.4): "I'll lend, but you'll pay" — floor and rate
        each bump up 0.10. Heat overrides likability when both fire.
      - High respect (>0.5): "I think you'll win, fair terms" — floor
        and rate trim by 0.03 each.

    Clamps prevent edge-case profiles from generating predatory or
    impossibly-generous offers via stacked modifiers.
    """
    floor = profile.floor_anchor
    rate = profile.rate_anchor
    if likability > 0.5:
        floor -= 0.05
        rate -= 0.05
    if heat > 0.4:
        floor += 0.10
        rate += 0.10
    if respect > 0.5:
        floor -= 0.03
        rate -= 0.03
    floor = max(1.00, min(1.50, floor))
    rate = max(0.00, min(0.55, rate))
    return floor, rate


def _relationship_hint(
    *,
    likability: float,
    heat: float,
    respect: float,
) -> str:
    """Generate a short relationship hint string for the offer card.

    Surfaces the underlying axes without exposing raw numbers. Most
    severe condition wins — heat dominates if present, then high
    respect, then likability, finally a neutral default. Empty
    string means "no special relationship vibe" — modal can omit the
    hint chip entirely.
    """
    if heat > 0.4:
        return "wants their money back"
    if heat > 0.2:
        return "watching you"
    if respect > 0.6 and likability > 0.5:
        return "trusts you"
    if respect > 0.5:
        return "respects your game"
    if likability > 0.5:
        return "friendly"
    return ""


def _capacity_for_lender(
    profile: LenderProfile,
    projected_bankroll: int,
    *,
    min_buy_in: int,
    max_buy_in: int,
) -> int:
    """Loan amount this lender will extend at this table.

    `pct × projected_bankroll`, clamped to the table's
    `[min_buy_in, max_buy_in]` window. The caller still checks
    capacity >= min_buy_in (eligibility gate 2 in the handoff) — a
    lender too poor to lend at the table's minimum is filtered out.
    """
    raw = int(profile.max_loan_pct_of_bankroll * projected_bankroll)
    return max(min_buy_in, min(max_buy_in, raw)) if raw >= min_buy_in else raw


def compute_personality_offers(
    *,
    player_owner_id: str,
    min_buy_in: int,
    max_buy_in: int,
    candidate_personalities: List[dict],
    bankroll_repo,
    relationship_repo,
    now: Optional[datetime] = None,
    count: int = 3,
) -> List[PersonalitySponsorOffer]:
    """Generate up to `count` AI-personality sponsor offers.

    Each candidate is a dict with at least `{"personality_id": str,
    "name": str}` — same shape `_build_cash_game` already builds for
    seated AIs (matches `cash_personality_ids` mapping).

    Eligibility gates per candidate (all must pass):
      1. `profile.willing == True`
      2. Loan capacity (`pct × projected_bankroll`, table-clamped) is
         at least the table's `min_buy_in`. A lender too poor to fund
         a min buy-in is filtered.
      3. `relationship.respect >= profile.respect_floor`
      4. `relationship.projected_heat <= profile.heat_ceiling`

    For each qualifying candidate, terms are trimmed by relationship
    axes (see `_adjusted_terms`).

    No-relationship-row case: `relationship_repo.load_relationship_state`
    returns None → treated as default neutral state (respect=0.5,
    heat=0.0, likability=0.5). This means a stranger lender extends
    their anchor terms unmodified.

    "No outstanding loan from THIS lender" gate (eligibility 5 in the
    handoff): the caller filters this — `compute_personality_offers`
    is pure and doesn't know the player's bankroll state. The route
    skips this gate when the player has no active loan (the common
    case for the sponsor screen, which fires at bankroll < min
    buy-in).

    Returns offers sorted by capacity descending — bigger-stake
    lenders surface first.

    `now` defaults to `datetime.utcnow()`; explicit `now` lets tests
    pin the projection point for stable results.
    """
    if now is None:
        now = datetime.utcnow()

    qualifying: List[PersonalitySponsorOffer] = []

    for entry in candidate_personalities:
        pid = entry.get("personality_id")
        name = entry.get("name") or pid
        if not pid:
            continue

        profile = bankroll_repo.load_lender_profile(pid)
        if not profile.willing:
            continue

        # Projected bankroll via projection-on-read.
        projected = bankroll_repo.load_ai_bankroll_current(pid, now=now)
        if projected is None:
            # No bankroll row yet — can't lend out of nothing. Skip.
            continue

        capacity = _capacity_for_lender(
            profile, projected,
            min_buy_in=min_buy_in, max_buy_in=max_buy_in,
        )
        if capacity < min_buy_in:
            continue

        # Relationship state — lender's POV of the player. None → default neutral.
        rel = relationship_repo.load_relationship_state(
            observer_id=pid, opponent_id=player_owner_id, now=now,
        )
        if rel is None:
            respect, heat, likability = 0.5, 0.0, 0.5
        else:
            respect = rel.respect
            heat = rel.heat
            likability = rel.likability

        if respect < profile.respect_floor:
            continue
        if heat > profile.heat_ceiling:
            continue

        floor, rate = _adjusted_terms(
            profile,
            likability=likability,
            heat=heat,
            respect=respect,
        )
        hint = _relationship_hint(
            likability=likability, heat=heat, respect=respect,
        )

        qualifying.append(PersonalitySponsorOffer(
            lender_id=pid,
            lender_name=name,
            amount=capacity,
            floor=floor,
            rate=rate,
            flavor=f"{name} offers you a loan.",
            relationship_hint=hint,
            capacity=capacity,
        ))

    qualifying.sort(key=lambda o: o.capacity, reverse=True)
    return qualifying[:count]
